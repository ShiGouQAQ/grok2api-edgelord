"""Build chat completion service — routes to cli-chat-proxy.grok.com/v1/responses.

通过 Grok Build (grok-shell) 端点访问模型，
使用 x.ai Bearer token 认证。

与 console_chat.py 的区别：
- 不走 console.x.ai 的 responses 端点
- 使用 Bearer token 认证（不是 Cookie）
- 需要 grok-shell 风格的 headers (x-grok-*, x-xai-token-auth)
"""

import asyncio
from itertools import count
from typing import Any, AsyncGenerator

import orjson

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
from app.dataplane.reverse.protocol.tool_parser import parse_tool_calls
from app.products._routing_policy import routing_attempt_policy

from app.products._account_selection import reserve_account, selection_max_retries
from app.products.openai.chat import _configured_retry_codes, _should_retry_upstream
from ._format import (
    make_response_id,
    make_stream_chunk,
    make_chat_response,
    build_usage,
    make_tool_call_chunk,
    make_tool_call_done_chunk,
    make_tool_call_response,
)
from ._tool_sieve import ToolSieve


def _body_excerpt(exc: BaseException) -> str:
    body = getattr(exc, "body", "") or ""
    if not body and hasattr(exc, "details"):
        details = getattr(exc, "details", None) or {}
        if isinstance(details, dict):
            body = details.get("body", "")
    return body or ""


def _log_task_exception(task: "asyncio.Task") -> None:
    exc = task.exception() if not task.cancelled() else None
    if exc:
        logger.warning("background task failed: task={} error={}", task.get_name(), exc)


async def _quota_sync(token: str, mode_id: int) -> None:
    """Fire-and-forget: 成功调用后持久化配额扣减和 usage_use_count。"""
    try:
        if current_strategy() != "quota" and mode_id != 6:
            return
        svc = get_refresh_service()
        if svc:
            await svc.refresh_call_async(token, mode_id)
    except Exception as exc:
        logger.warning(
            "build quota sync failed: token={}... mode_id={} error={}",
            token[:10],
            mode_id,
            exc,
        )


async def _fail_sync(
    token: str, mode_id: int, exc: BaseException | None = None
) -> None:
    """Fire-and-forget: 失败后持久化失败计数。"""
    try:
        svc = get_refresh_service()
        if svc:
            await svc.record_failure_async(token, mode_id, exc)
    except Exception as e:
        logger.warning(
            "build fail sync error: token={}... mode_id={} error={}",
            token[:10],
            mode_id,
            e,
        )


def _reasoning_effort_from_emit_think(emit_think: bool | None) -> str:
    if emit_think is False:
        return "none"
    return "low"


async def completions(
    *,
    model: str,
    messages: list[dict],
    stream: bool = True,
    emit_think: bool | None = None,
    temperature: float = 0.7,
    top_p: float = 0.95,
    tools: list[dict] | None = None,
    tool_choice: Any = None,
    agent_id: str | None = None,
) -> dict | AsyncGenerator[str, None]:
    """Entry point for Build (grok-shell) chat completions.

    Returns an async generator for streaming, or a dict for non-streaming.
    """
    cfg = get_config()
    spec = resolve_model(model)
    effort = _reasoning_effort_from_emit_think(emit_think)
    timeout_s = cfg.get_float("chat.timeout", 120.0)
    policy = routing_attempt_policy(selection_max_retries())
    retry_codes = _configured_retry_codes(cfg)
    response_id = make_response_id()
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
        "build chat request: model={} stream={} messages={}",
        model,
        stream,
        len(messages),
    )

    from app.dataplane.account import _directory as _acct_dir

    if _acct_dir is None:
        raise RateLimitError("Account directory not initialised")
    directory = _acct_dir

    # ── Streaming path ────────────────────────────────────────────────────────
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
                    )

                    try:
                        ended = False
                        sieve = ToolSieve(tool_names) if tool_names else None
                        tool_calls_emitted = False
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
                                if tool_calls_emitted:
                                    break
                                item_type = item.get("type", "")
                                if item_type == "text":
                                    delta = item.get("delta", "")
                                    if sieve:
                                        safe_text, parsed_calls = sieve.feed(delta)
                                        if safe_text:
                                            chunk = make_stream_chunk(
                                                response_id, model, safe_text
                                            )
                                            yield f"data: {orjson.dumps(chunk).decode()}\n\n"
                                        if parsed_calls is not None:
                                            for i, tc in enumerate(parsed_calls):
                                                chunk = make_tool_call_chunk(
                                                    response_id,
                                                    model,
                                                    i,
                                                    tc.call_id,
                                                    tc.name,
                                                    tc.arguments,
                                                    is_first=True,
                                                )
                                                yield f"data: {orjson.dumps(chunk).decode()}\n\n"
                                            done = make_tool_call_done_chunk(
                                                response_id, model
                                            )
                                            yield f"data: {orjson.dumps(done).decode()}\n\n"
                                            yield "data: [DONE]\n\n"
                                            tool_calls_emitted = True
                                            ended = True
                                            break
                                    else:
                                        chunk = make_stream_chunk(
                                            response_id, model, delta
                                        )
                                        yield f"data: {orjson.dumps(chunk).decode()}\n\n"
                            if ended:
                                break

                        # Stream ended — flush sieve for any buffered XML
                        flushed_calls: list | None = None
                        if sieve and not tool_calls_emitted:
                            flushed_calls = sieve.flush()
                            if flushed_calls:
                                for i, tc in enumerate(flushed_calls):
                                    chunk = make_tool_call_chunk(
                                        response_id,
                                        model,
                                        i,
                                        tc.call_id,
                                        tc.name,
                                        tc.arguments,
                                        is_first=True,
                                    )
                                    yield f"data: {orjson.dumps(chunk).decode()}\n\n"
                                done = make_tool_call_done_chunk(response_id, model)
                                yield f"data: {orjson.dumps(done).decode()}\n\n"
                                yield "data: [DONE]\n\n"
                                tool_calls_emitted = True

                        if tool_calls_emitted:
                            success = True
                            logger.info(
                                "build chat stream tool_calls: attempt={}/{} model={} call_count={}",
                                attempt + 1,
                                policy.total_attempts,
                                model,
                                len(flushed_calls) if flushed_calls else 0,
                            )
                            return

                        # 流结束，发送 final chunk
                        usage = build_usage(
                            estimate_prompt_tokens(messages),
                            estimate_tokens(adapter.full_text),
                        )
                        final = make_stream_chunk(response_id, model, "", is_final=True)
                        final["usage"] = usage
                        yield f"data: {orjson.dumps(final).decode()}\n\n"
                        yield "data: [DONE]\n\n"
                        success = True
                        logger.info(
                            "build chat stream completed: attempt={}/{} model={} text_len={}",
                            attempt + 1,
                            policy.total_attempts,
                            model,
                            len(adapter.full_text),
                        )

                    except UpstreamError as exc:
                        fail_exc = exc
                        if _should_retry_upstream(exc, retry_codes) and policy.has_next(
                            attempt
                        ):
                            _retry = True
                            logger.warning(
                                "build chat retry: attempt={}/{} status={} body={} token={}...",
                                attempt + 1,
                                policy.retry_budget,
                                exc.status,
                                _body_excerpt(exc),
                                token[:8],
                            )
                        else:
                            logger.warning(
                                "build chat upstream failed: model={} status={} attempt={}/{} body={}",
                                model,
                                exc.status,
                                attempt + 1,
                                policy.total_attempts,
                                _body_excerpt(exc),
                            )
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

    # ── Non-streaming path ────────────────────────────────────────────────────
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
                usage = build_usage(
                    estimate_prompt_tokens(messages),
                    estimate_tokens(full_text),
                )

                # ── Tool call detection (non-streaming) ──────────────────────────
                if tool_names:
                    parse_result = parse_tool_calls(full_text, tool_names)
                    if parse_result.calls:
                        logger.info(
                            "build chat non-stream tool calls: model={} calls={}",
                            model,
                            len(parse_result.calls),
                        )
                        pt = estimate_prompt_tokens(messages)
                        ct = estimate_tool_call_tokens(parse_result.calls)
                        result = make_tool_call_response(
                            model,
                            parse_result.calls,
                            response_id=response_id,
                            usage=build_usage(pt, ct),
                        )
                        success = True
                        return result

                result = make_chat_response(
                    model, full_text, response_id=response_id, usage=usage
                )
                success = True
                logger.info(
                    "build chat non-stream completed: model={} text_len={}",
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
                        "build chat non-stream retry: attempt={}/{} status={} body={}",
                        attempt + 1,
                        policy.retry_budget,
                        exc.status,
                        _body_excerpt(exc),
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


# ---------------------------------------------------------------------------
# Transport — POST to BUILD_RESPONSES with Build headers
# ---------------------------------------------------------------------------


async def stream_build_chat(
    token: str,
    agent_id: str,
    payload: dict[str, Any],
    *,
    model: str | None = None,
    timeout_s: float = 120.0,
) -> AsyncGenerator[tuple[str, str], None]:
    """POST to cli-chat-proxy.grok.com/v1/responses and yield (event_type, data) pairs."""
    from app.dataplane.proxy import get_proxy_runtime
    from app.dataplane.proxy.adapters.headers import build_build_headers
    from app.dataplane.proxy.adapters.session import (
        ResettableSession,
        build_session_kwargs,
    )
    from app.dataplane.reverse.runtime.endpoint_table import (
        BUILD_RESPONSES,
        BUILD_BASE,
    )

    proxy = await get_proxy_runtime()
    lease = await proxy.acquire(clearance_origin=BUILD_BASE)

    headers = build_build_headers(
        access_token=token,
        agent_id=agent_id,
        model=model,
        is_stream=True,
        is_trace=True,
    )
    payload_bytes = orjson.dumps(payload)
    # Go upstream uses standard TLS (no browser impersonation) for Build API.
    # Browser TLS fingerprint is reserved for Grok Web — see buildclient.go.
    session_kwargs = build_session_kwargs(lease=lease, disable_fingerprint=True)

    async with ResettableSession(**session_kwargs) as session:
        try:
            response = await session.post(
                BUILD_RESPONSES,
                headers=headers,
                data=payload_bytes,
                timeout=timeout_s,
                stream=True,
            )
        except Exception as exc:
            await proxy.feedback(lease, _transport_error_feedback())
            raise UpstreamError(f"Build transport failed: {exc}", status=502) from exc

        if response.status_code != 200:
            try:
                body = await response.atext()
            except Exception as exc:
                # Body drives _status_feedback classification (cf_challenge /
                # node_banned / transport_error) — a lost body mislabels errors.
                logger.warning(
                    "build upstream body read failed: status={} error={}",
                    response.status_code,
                    exc,
                )
                body = ""
            exc = UpstreamError.from_http_response(
                f"Build API returned {response.status_code}",
                status=response.status_code,
                body=body,
            )
            await proxy.feedback(
                lease, _status_feedback(response.status_code, body, exc=exc)
            )
            raise exc

        await proxy.feedback(lease, _success_feedback())

        current_event = ""
        try:
            async for raw_line in response.aiter_lines():
                if isinstance(raw_line, bytes):
                    try:
                        raw_line = raw_line.decode("utf-8")
                    except UnicodeDecodeError:
                        raw_line = raw_line.decode("utf-8", errors="replace")
                kind, value = _classify_build_line(raw_line)
                if kind == "event":
                    current_event = value
                elif kind == "data":
                    yield current_event, value
                    current_event = ""
                elif kind == "done":
                    return
        except Exception as exc:
            # Go 0893557a isUpstreamStreamFailure: a body stream that fails
            # AFTER a successful response header marks node failure on a fresh
            # baseline (MarkFailureAfterSuccess, status 502). A client cancel
            # (CancelledError, a BaseException) never reaches here.
            from app.control.proxy.models import (
                ProxyFeedback,
                ProxyFeedbackKind,
            )

            await proxy.mark_failure_after_success(
                lease,
                ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR, status_code=502),
            )
            raise UpstreamError(f"Build stream read failed: {exc}", status=502) from exc


def _classify_build_line(line: str) -> tuple[str, str]:
    """Parse a raw SSE line into (event_type, data)."""
    line = line.strip()
    if not line:
        return "skip", ""
    if line.startswith("event:"):
        return "event", line[6:].strip()
    if line.startswith("data:"):
        data = line[5:].strip()
        if data == "[DONE]":
            return "done", ""
        return "data", data
    return "skip", ""


def _success_feedback():
    from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind

    return ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS, status_code=200)


def _transport_error_feedback():
    from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind

    return ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)


def _status_feedback(status: int, body: str = "", exc: UpstreamError | None = None):
    """Map an upstream Build response to ProxyFeedback (account-level semantics).

    Account-level 403s (blocked user, invalid credentials, permanent denial)
    must stay FORBIDDEN — they are account problems, not Cloudflare issues, and
    must NOT invalidate the clearance bundle. Only body-marked CF challenge /
    node-banned responses rotate or invalidate.
    """
    from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind
    from app.dataplane.reverse.transport._proxy_feedback import (
        _is_cf_challenge,
        _is_node_banned,
        _is_transport_error,
    )

    if exc is not None:
        if status == 403:
            if exc.credential_rejected:
                kind = ProxyFeedbackKind.FORBIDDEN
            elif _is_node_banned(body):
                kind = ProxyFeedbackKind.NODE_BANNED
            elif _is_cf_challenge(body):
                kind = ProxyFeedbackKind.CHALLENGE
            else:
                kind = ProxyFeedbackKind.FORBIDDEN
        elif status == 429 or exc.quota_exhausted:
            kind = ProxyFeedbackKind.RATE_LIMITED
        elif status >= 500:
            if _is_transport_error(body):
                kind = ProxyFeedbackKind.TRANSPORT_ERROR
            else:
                kind = ProxyFeedbackKind.UPSTREAM_5XX
        else:
            kind = ProxyFeedbackKind.FORBIDDEN
        return ProxyFeedback(kind=kind, status_code=status, reason=exc.upstream_code)

    if status == 403:
        kind = ProxyFeedbackKind.FORBIDDEN
    elif status == 429:
        kind = ProxyFeedbackKind.RATE_LIMITED
    elif status >= 500:
        if body and _is_transport_error(body):
            kind = ProxyFeedbackKind.TRANSPORT_ERROR
        else:
            kind = ProxyFeedbackKind.UPSTREAM_5XX
    else:
        kind = ProxyFeedbackKind.FORBIDDEN
    return ProxyFeedback(kind=kind, status_code=status)


__all__ = ["completions", "stream_build_chat"]
