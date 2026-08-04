"""Build Messages API handler — /v1/messages for Grok Build models.

将 Grok Build 上游 SSE 转换为 Anthropic Messages API 格式输出。
"""

import asyncio
from itertools import count
from typing import Any, AsyncGenerator

import orjson

from app.platform.logging.logger import logger
from app.platform.config.snapshot import get_config
from app.platform.errors import RateLimitError, UpstreamError
from app.platform.runtime.clock import now_s
from app.platform.tokens import estimate_prompt_tokens, estimate_tokens
from app.control.account.enums import FeedbackKind
from app.control.account.invalid_credentials import feedback_kind_for_error
from app.control.account.runtime import get_refresh_service
from app.control.model.registry import resolve as resolve_model
from app.dataplane.account.selector import current_strategy
from app.dataplane.reverse.protocol.xai_build import (
    build_build_responses_payload,
    BuildStreamAdapter,
)
from app.products._routing_policy import routing_attempt_policy

from app.products._account_selection import reserve_account, selection_max_retries
from app.products.openai.chat import _configured_retry_codes, _should_retry_upstream
from app.products.openai.build_chat import stream_build_chat


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {orjson.dumps(data).decode()}\n\n"


def _log_task_exception(task: "asyncio.Task") -> None:
    exc = task.exception() if not task.cancelled() else None
    if exc:
        logger.warning("background task failed: task={} error={}", task.get_name(), exc)


async def _quota_sync(token: str, mode_id: int) -> None:
    try:
        if current_strategy() != "quota" and mode_id != 6:
            return
        svc = get_refresh_service()
        if svc:
            await svc.refresh_call_async(token, mode_id)
    except Exception as exc:
        logger.warning(
            "build messages quota sync failed: token={}... mode_id={} error={}",
            token[:10],
            mode_id,
            exc,
        )


async def _fail_sync(
    token: str, mode_id: int, exc: BaseException | None = None
) -> None:
    try:
        svc = get_refresh_service()
        if svc:
            await svc.record_failure_async(token, mode_id, exc)
    except Exception as e:
        logger.warning(
            "build messages fail sync error: token={}... mode_id={} error={}",
            token[:10],
            mode_id,
            e,
        )


async def create(
    *,
    model: str,
    messages: list[dict],
    stream: bool,
    emit_think: bool,
    temperature: float,
    top_p: float,
    msg_id: str,
    agent_id: str | None = None,
) -> dict | AsyncGenerator[str, None]:
    """Build models /v1/messages handler (Anthropic format)."""

    cfg = get_config()
    spec = resolve_model(model)
    timeout_s = cfg.get_float("chat.timeout", 120.0)
    policy = routing_attempt_policy(selection_max_retries())
    retry_codes = _configured_retry_codes(cfg)
    effort = "low" if emit_think else "none"
    _agent_id = agent_id or "grok2api-default-agent"

    from app.dataplane.account import _directory as _acct_dir

    if _acct_dir is None:
        raise RateLimitError("Account directory not initialised")
    directory = _acct_dir

    # ── Streaming ─────────────────────────────────────────────────────────────
    if stream:

        async def _run_stream() -> AsyncGenerator[str, None]:
            excluded: list[str] = []
            for attempt in count():
                if not policy.allows(attempt):
                    break
                acct, selected_mode_id = await reserve_account(
                    directory,
                    spec,
                    now_s_override=now_s(),
                    exclude_tokens=excluded or None,
                )
                if acct is None:
                    raise RateLimitError("No available accounts for this model tier")

                token = acct.token
                success = False
                fail_exc: BaseException | None = None
                _retry = False
                adapter = BuildStreamAdapter()
                text_buf: list[str] = []

                try:
                    payload = build_build_responses_payload(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        top_p=top_p,
                        reasoning_effort=effort,
                        stream=True,
                    )

                    try:
                        # message_start
                        yield _sse(
                            "message_start",
                            {
                                "type": "message_start",
                                "message": {
                                    "id": msg_id,
                                    "type": "message",
                                    "role": "assistant",
                                    "model": model,
                                    "content": [],
                                    "stop_reason": None,
                                    "usage": {
                                        "input_tokens": estimate_prompt_tokens(
                                            messages
                                        ),
                                        "output_tokens": 0,
                                        "cache_creation_input_tokens": 0,
                                        "cache_read_input_tokens": 0,
                                    },
                                },
                            },
                        )
                        yield _sse("ping", {"type": "ping"})

                        # content_block_start
                        yield _sse(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": 0,
                                "content_block": {"type": "text", "text": ""},
                            },
                        )

                        yield ": heartbeat\n\n"
                        async for event_type, data in stream_build_chat(
                            token,
                            _agent_id,
                            payload,
                            model=model,
                            timeout_s=timeout_s,
                        ):
                            items = adapter.feed(event_type, data)
                            for item in items:
                                if item.get("type") == "text":
                                    delta = item.get("delta", "")
                                    if delta:
                                        text_buf.append(delta)
                                        yield _sse(
                                            "content_block_delta",
                                            {
                                                "type": "content_block_delta",
                                                "index": 0,
                                                "delta": {
                                                    "type": "text_delta",
                                                    "text": delta,
                                                },
                                            },
                                        )

                        # content_block_stop
                        yield _sse(
                            "content_block_stop",
                            {
                                "type": "content_block_stop",
                                "index": 0,
                            },
                        )

                        # message_delta
                        full_text = "".join(text_buf)
                        output_tokens = (
                            adapter.usage.get("output_tokens", 0)
                            if adapter.usage
                            else estimate_tokens(full_text)
                        )
                        yield _sse(
                            "message_delta",
                            {
                                "type": "message_delta",
                                "delta": {
                                    "stop_reason": "end_turn",
                                    "stop_sequence": None,
                                },
                                "usage": {
                                    "input_tokens": estimate_prompt_tokens(messages),
                                    "output_tokens": output_tokens,
                                    "cache_creation_input_tokens": 0,
                                    "cache_read_input_tokens": 0,
                                },
                            },
                        )

                        # message_stop
                        yield _sse("message_stop", {"type": "message_stop"})
                        yield "data: [DONE]\n\n"
                        success = True
                        logger.info(
                            "build messages stream completed: model={} text_len={} attempt={}/{}",
                            model,
                            len(full_text),
                            attempt + 1,
                            policy.total_attempts,
                        )

                    except UpstreamError as exc:
                        fail_exc = exc
                        if (
                            _should_retry_upstream(exc, retry_codes)
                            and policy.has_next(attempt)
                        ):
                            _retry = True
                            logger.warning(
                                "build messages retry: attempt={}/{} status={}",
                                attempt + 1,
                                policy.retry_budget,
                                exc.status,
                            )
                        else:
                            raise

                finally:
                    await directory.release(acct)
                    kind = (
                        FeedbackKind.SUCCESS
                        if success
                        else feedback_kind_for_error(fail_exc)
                        if fail_exc
                        else FeedbackKind.SERVER_ERROR
                    )
                    await directory.feedback(
                        token, kind, selected_mode_id, now_s_val=now_s()
                    )
                    if success:
                        asyncio.create_task(
                            _quota_sync(token, selected_mode_id)
                        ).add_done_callback(_log_task_exception)
                    else:
                        asyncio.create_task(
                            _fail_sync(token, selected_mode_id, fail_exc)
                        ).add_done_callback(_log_task_exception)

                if success or not _retry:
                    return
                excluded.append(token)

        return _run_stream()

    # ── Non-streaming ─────────────────────────────────────────────────────────
    excluded: list[str] = []
    for attempt in count():
        if not policy.allows(attempt):
            break
        acct, selected_mode_id = await reserve_account(
            directory,
            spec,
            now_s_override=now_s(),
            exclude_tokens=excluded or None,
        )
        if acct is None:
            raise RateLimitError("No available accounts for this model tier")

        token = acct.token
        success = False
        fail_exc: BaseException | None = None
        adapter = BuildStreamAdapter()

        try:
            payload = build_build_responses_payload(
                messages=messages,
                model=model,
                temperature=temperature,
                top_p=top_p,
                reasoning_effort=effort,
                stream=True,
            )

            try:
                async for event_type, data in stream_build_chat(
                    token,
                    _agent_id,
                    payload,
                    model=model,
                    timeout_s=timeout_s,
                ):
                    adapter.feed(event_type, data)

                full_text = adapter.full_text
                usage_data = adapter.usage
                input_tokens = (
                    usage_data.get("input_tokens", 0)
                    if usage_data
                    else estimate_prompt_tokens(messages)
                )
                output_tokens = (
                    usage_data.get("output_tokens", 0)
                    if usage_data
                    else estimate_tokens(full_text)
                )

                result = {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": full_text}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                }
                success = True
                logger.info(
                    "build messages non-stream completed: model={} text_len={}",
                    model,
                    len(full_text),
                )
                return result

            except UpstreamError as exc:
                fail_exc = exc
                if _should_retry_upstream(exc, retry_codes) and policy.has_next(attempt):
                    logger.warning(
                        "build messages non-stream retry: attempt={}/{} status={}",
                        attempt + 1,
                        policy.retry_budget,
                        exc.status,
                    )
                    excluded.append(token)
                    continue
                raise

        finally:
            await directory.release(acct)
            kind = (
                FeedbackKind.SUCCESS
                if success
                else feedback_kind_for_error(fail_exc)
                if fail_exc
                else FeedbackKind.SERVER_ERROR
            )
            await directory.feedback(token, kind, selected_mode_id, now_s_val=now_s())
            if success:
                asyncio.create_task(
                    _quota_sync(token, selected_mode_id)
                ).add_done_callback(_log_task_exception)
            else:
                asyncio.create_task(
                    _fail_sync(token, selected_mode_id, fail_exc)
                ).add_done_callback(_log_task_exception)

    raise RateLimitError("No available accounts after retries")


__all__ = ["create"]
