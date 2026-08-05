"""Build Responses API handler — /v1/responses for Grok Build models.

将 Grok Build 上游的 Responses API SSE 事件流转换为 OpenAI Responses API 格式输出。
"""

import asyncio
from itertools import count
from typing import Any, AsyncGenerator


from app.platform.logging.logger import logger
from app.platform.config.snapshot import get_config
from app.platform.errors import RateLimitError, UpstreamError
from app.platform.runtime.clock import now_s
from app.platform.tokens import (
    estimate_prompt_tokens,
    estimate_tokens,
    estimate_tool_call_tokens,
)
from app.control.account.enums import FeedbackKind
from app.control.account.invalid_credentials import feedback_kind_for_error
from app.control.account.runtime import get_refresh_service
from app.control.model.registry import resolve as resolve_model
from app.dataplane.account.selector import current_strategy
from app.dataplane.reverse.protocol.xai_build import (
    build_build_responses_payload,
    BuildStreamAdapter,
)
from app.dataplane.reverse.protocol.tool_prompt import (
    build_tool_system_prompt,
    extract_tool_names,
    inject_into_message,
)
from app.dataplane.reverse.protocol.tool_parser import (
    parse_tool_calls,
    schema_contains_reachable_integer,
)
from app.products._routing_policy import (
    new_routing_attempt_policy,
    routing_attempt_policy,
)

from app.products._account_selection import reserve_account, selection_max_retries
from app.products.openai.chat import _configured_retry_codes, _should_retry_upstream
from ._tool_sieve import ToolSieve
from ._format import (
    make_resp_object,
    build_resp_usage,
    format_sse,
)
from .build_chat import stream_build_chat


def _log_task_exception(task: "asyncio.Task") -> None:
    exc = task.exception() if not task.cancelled() else None
    if exc:
        logger.warning("background task failed: task={} error={}", task.get_name(), exc)


def _function_schemas(tools: list[dict] | None) -> dict[str, Any]:
    """Collect alias → parameters for function tools whose schema contains a
    reachable integer constraint (Go functionSchemas population, 8b5c1ed6)."""
    if not tools:
        return {}
    schemas: dict[str, Any] = {}
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        name = tool.get("name")
        parameters = tool.get("parameters")
        if (
            isinstance(name, str)
            and name
            and isinstance(parameters, dict)
            and schema_contains_reachable_integer(parameters)
        ):
            schemas[name] = parameters
    return schemas


async def _quota_sync(token: str, mode_id: int) -> None:
    try:
        if current_strategy() != "quota" and mode_id != 6:
            return
        svc = get_refresh_service()
        if svc:
            await svc.refresh_call_async(token, mode_id)
    except Exception as exc:
        logger.warning(
            "build responses quota sync failed: token={}... mode_id={} error={}",
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
            "build responses fail sync error: token={}... mode_id={} error={}",
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
    response_id: str,
    reasoning_id: str,
    message_id: str,
    tools: list[dict] | None = None,
    tool_choice: Any = None,
    agent_id: str | None = None,
    previous_response_id: str | None = None,
) -> dict | AsyncGenerator[str, None]:
    """Build models /v1/responses handler."""

    cfg = get_config()
    spec = resolve_model(model)
    timeout_s = cfg.get_float("chat.timeout", 120.0)
    # Go service.go: ownership != nil → newRoutingAttemptPolicy(1). Build is
    # the stored-responses provider upstream: keep the chain on one account.
    if previous_response_id:
        policy = new_routing_attempt_policy(1)
    else:
        policy = routing_attempt_policy(selection_max_retries())
    retry_codes = _configured_retry_codes(cfg)
    _agent_id = agent_id or "grok2api-default-agent"

    # ── Tool call setup ───────────────────────────────────────────────────────
    tool_names: list[str] = []
    if tools:
        tool_names = extract_tool_names(tools)
        tool_prompt = build_tool_system_prompt(tools, tool_choice)
        for msg in reversed(messages):
            if msg.get("role") == "user":
                msg["content"] = inject_into_message(
                    msg.get("content", ""), tool_prompt
                )
                break
        logger.info(
            "build responses tool injection: tool_names={} choice={}",
            tool_names,
            tool_choice,
        )

    effort = "low" if emit_think else "none"

    schemas = _function_schemas(tools)

    from app.dataplane.account import _directory as _acct_dir

    if _acct_dir is None:
        raise RateLimitError("Account directory not initialised")
    directory = _acct_dir

    # ── Streaming ─────────────────────────────────────────────────────────────
    if stream:

        async def _run_stream() -> AsyncGenerator[str, None]:
            from .responses import _build_fc_items, _emit_fc_events

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
                adapter = BuildStreamAdapter(schemas=schemas)
                text_buf: list[str] = []
                sieve = ToolSieve(tool_names) if tool_names else None
                tool_call_items: list[dict] | None = None
                native_fc_items: list[dict[str, Any]] = []
                ended = False

                try:
                    payload = build_build_responses_payload(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        top_p=top_p,
                        reasoning_effort=effort,
                        stream=True,
                        tools=tools,
                        tool_choice=tool_choice,
                        previous_response_id=previous_response_id,
                    )

                    try:
                        # response.created
                        yield format_sse(
                            "response.created",
                            {
                                "type": "response.created",
                                "response": make_resp_object(
                                    response_id, model, "in_progress", []
                                ),
                            },
                        )

                        # response.in_progress
                        yield format_sse(
                            "response.in_progress",
                            {
                                "type": "response.in_progress",
                                "response": make_resp_object(
                                    response_id, model, "in_progress", []
                                ),
                            },
                        )

                        # output_item.added (message)
                        yield format_sse(
                            "response.output_item.added",
                            {
                                "type": "response.output_item.added",
                                "output_index": 0,
                                "item": {
                                    "id": message_id,
                                    "type": "message",
                                    "role": "assistant",
                                    "status": "in_progress",
                                    "content": [],
                                },
                            },
                        )

                        # content_part.added
                        yield format_sse(
                            "response.content_part.added",
                            {
                                "type": "response.content_part.added",
                                "item_id": message_id,
                                "output_index": 0,
                                "content_index": 0,
                                "part": {
                                    "type": "output_text",
                                    "text": "",
                                    "annotations": [],
                                },
                            },
                        )

                        event_count = 0
                        yield ": heartbeat\n\n"
                        async for event_type, data in stream_build_chat(
                            token,
                            _agent_id,
                            payload,
                            model=model,
                            timeout_s=timeout_s,
                        ):
                            event_count += 1
                            items = adapter.feed(event_type, data)
                            for item in items:
                                item_type = item.get("type", "")
                                if item_type == "sse":
                                    if (
                                        item["event"] == "response.output_item.done"
                                        and isinstance(
                                            item["payload"].get("item"), dict
                                        )
                                        and item["payload"]["item"].get("type")
                                        == "function_call"
                                    ):
                                        native_fc_items.append(item["payload"]["item"])
                                    yield format_sse(item["event"], item["payload"])
                                    continue
                                if item_type == "text":
                                    delta = item.get("delta", "")
                                    if (
                                        sieve is not None
                                        and tool_call_items is None
                                        and not native_fc_items
                                    ):
                                        safe_text, parsed_calls = sieve.feed(delta)
                                        if parsed_calls is not None:
                                            fc_items = _build_fc_items(parsed_calls)
                                            tool_call_items = fc_items
                                            async for evt in _emit_fc_events(
                                                fc_items, 0
                                            ):
                                                yield evt
                                            ended = True
                                            break
                                        if safe_text:
                                            text_buf.append(safe_text)
                                            yield format_sse(
                                                "response.output_text.delta",
                                                {
                                                    "type": "response.output_text.delta",
                                                    "item_id": message_id,
                                                    "output_index": 0,
                                                    "content_index": 0,
                                                    "delta": safe_text,
                                                },
                                            )
                                    else:
                                        text_buf.append(delta)
                                        yield format_sse(
                                            "response.output_text.delta",
                                            {
                                                "type": "response.output_text.delta",
                                                "item_id": message_id,
                                                "output_index": 0,
                                                "content_index": 0,
                                                "delta": delta,
                                            },
                                        )

                            if ended:
                                break

                        # Flush sieve after stream ends
                        if (
                            sieve is not None
                            and tool_call_items is None
                            and not native_fc_items
                        ):
                            remaining = sieve.flush()
                            if remaining:
                                fc_items = _build_fc_items(remaining)
                                tool_call_items = fc_items
                                async for evt in _emit_fc_events(fc_items, 0):
                                    yield evt

                        logger.info(
                            "build responses stream raw: events={} text_tokens={} adapter_text_len={}",
                            event_count,
                            len(text_buf),
                            len(adapter.full_text),
                        )

                        final_items = native_fc_items or tool_call_items
                        if final_items:
                            output_tokens = estimate_tool_call_tokens(final_items)
                            yield format_sse(
                                "response.completed",
                                {
                                    "type": "response.completed",
                                    "response": make_resp_object(
                                        response_id,
                                        model,
                                        "completed",
                                        final_items,
                                        usage=build_resp_usage(
                                            estimate_prompt_tokens(messages),
                                            output_tokens,
                                        ),
                                    ),
                                },
                            )
                            yield "data: [DONE]\n\n"
                            success = True
                            logger.info(
                                "build responses stream tool_calls: model={} calls={} attempt={}/{}",
                                model,
                                len(final_items),
                                attempt + 1,
                                policy.total_attempts,
                            )
                        else:
                            full_text = "".join(text_buf)

                            yield format_sse(
                                "response.output_text.done",
                                {
                                    "type": "response.output_text.done",
                                    "item_id": message_id,
                                    "output_index": 0,
                                    "content_index": 0,
                                    "text": full_text,
                                },
                            )

                            yield format_sse(
                                "response.content_part.done",
                                {
                                    "type": "response.content_part.done",
                                    "item_id": message_id,
                                    "output_index": 0,
                                    "content_index": 0,
                                    "part": {
                                        "type": "output_text",
                                        "text": full_text,
                                        "annotations": [],
                                    },
                                },
                            )

                            yield format_sse(
                                "response.output_item.done",
                                {
                                    "type": "response.output_item.done",
                                    "output_index": 0,
                                    "item": {
                                        "id": message_id,
                                        "type": "message",
                                        "role": "assistant",
                                        "status": "completed",
                                        "content": [
                                            {"type": "output_text", "text": full_text}
                                        ],
                                    },
                                },
                            )

                            input_tokens = estimate_prompt_tokens(messages)
                            output_tokens = estimate_tokens(full_text)

                            output_items = [
                                {
                                    "id": message_id,
                                    "type": "message",
                                    "role": "assistant",
                                    "status": "completed",
                                    "content": [
                                        {"type": "output_text", "text": full_text}
                                    ],
                                }
                            ]
                            yield format_sse(
                                "response.completed",
                                {
                                    "type": "response.completed",
                                    "response": make_resp_object(
                                        response_id,
                                        model,
                                        "completed",
                                        output_items,
                                        usage=build_resp_usage(
                                            input_tokens, output_tokens
                                        ),
                                    ),
                                },
                            )
                            yield "data: [DONE]\n\n"
                            success = True
                            logger.info(
                                "build responses stream completed: model={} text_len={} attempt={}/{}",
                                model,
                                len(full_text),
                                attempt + 1,
                                policy.total_attempts,
                            )

                    except UpstreamError as exc:
                        fail_exc = exc
                        if _should_retry_upstream(exc, retry_codes) and policy.has_next(
                            attempt
                        ):
                            _retry = True
                            logger.warning(
                                "build responses retry: attempt={}/{} status={}",
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
                tools=tools,
                tool_choice=tool_choice,
                previous_response_id=previous_response_id,
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

                if tool_names:
                    from .responses import _build_fc_items

                    parse_result = parse_tool_calls(
                        full_text, tool_names, schemas=schemas
                    )
                    if parse_result.calls:
                        output_items = _build_fc_items(parse_result.calls)
                        output_tokens = estimate_tool_call_tokens(parse_result.calls)
                        success = True
                        logger.info(
                            "build responses non-stream tool_calls: model={} calls={}",
                            model,
                            len(parse_result.calls),
                        )
                        return make_resp_object(
                            response_id,
                            model,
                            "completed",
                            output_items,
                            usage=build_resp_usage(
                                estimate_prompt_tokens(messages), output_tokens
                            ),
                        )

                input_tokens = estimate_prompt_tokens(messages)
                output_tokens = estimate_tokens(full_text)

                output_items = [
                    {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": full_text}],
                    }
                ]
                result = make_resp_object(
                    response_id,
                    model,
                    "completed",
                    output_items,
                    usage=build_resp_usage(input_tokens, output_tokens),
                )
                success = True
                logger.info(
                    "build responses non-stream completed: model={} text_len={}",
                    model,
                    len(full_text),
                )
                return result

            except UpstreamError as exc:
                fail_exc = exc
                if _should_retry_upstream(exc, retry_codes) and policy.has_next(
                    attempt
                ):
                    logger.warning(
                        "build responses non-stream retry: attempt={}/{} status={}",
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
