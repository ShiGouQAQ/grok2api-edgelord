"""XAI console.x.ai chat protocol — payload builder and SSE stream adapter.

端点: POST https://console.x.ai/v1/responses
认证: Authorization: DPoP <token> + DPoP: <proof>（RFC 9449） + Cookie: sso=<token>; sso-rw=<token>

请求格式 (OpenAI Responses API):
{
    "model": "grok-4.3",
    "input": [{"role": "user", "content": [{"type": "input_text", "text": "..."}]}],
    "max_output_tokens": 1000000,
    "temperature": 0.7,
    "top_p": 0.95,
    "reasoning": {"effort": "low"},
    "store": false,
    "include": ["reasoning.encrypted_content"],
    "stream": true
}

响应 SSE 事件类型:
- response.created / response.in_progress  — 忽略
- response.output_item.added               — 忽略
- response.output_item.done                — reasoning item，含 encrypted_content（不可读）
- response.content_part.added             — 忽略
- response.output_text.delta              — 文本 token，delta 字段
- response.output_text.done              — 忽略
- response.content_part.done             — 忽略
- response.output_item.done (message)    — 忽略
- response.completed                      — 含 usage 统计
"""

from typing import Any, AsyncGenerator

import orjson
import zlib

from app.dataplane.proxy.adapters import session as _session_adapter
from app.dataplane.reverse.protocol.dpop import (
    DPoPError,
    DPoPSession,
    DPoPSessionManager,
    DPoPTokenEndpointError,
    _X_CLUSTER_HEADER,
    dpop_session_cache_key,
    sign_dpop_proof,
)
from app.dataplane.reverse.protocol.xai_console_usage import _is_definitive_block_body
from app.platform.errors import UpstreamError
from app.platform.logging.logger import logger


# ---------------------------------------------------------------------------
# 支持的模型名 → console.x.ai 实际 model 字段映射
# ---------------------------------------------------------------------------

# console.x.ai 上可用的模型（通过 grok.com SSO 免费访问）
# key = grok2api 对外暴露的模型名，value = console.x.ai 实际 model 字段
CONSOLE_MODELS: dict[str, str] = {
    "grok-4.3-console": "grok-4.3",
    "grok-4.3-low": "grok-4.3",
    "grok-4.3-medium": "grok-4.3",
    "grok-4.3-high": "grok-4.3",
    "grok-4.20-0309-reasoning-console": "grok-4.20-0309-reasoning",
    "grok-4.20-0309-reasoning-low": "grok-4.20-0309-reasoning",
    "grok-4.20-0309-reasoning-medium": "grok-4.20-0309-reasoning",
    "grok-4.20-0309-reasoning-high": "grok-4.20-0309-reasoning",
    "grok-4.20-0309-console": "grok-4.20-0309",
    "grok-4.20-0309-non-reasoning-console": "grok-4.20-0309-non-reasoning",
    "grok-4.20-multi-agent-console": "grok-4.20-multi-agent-0309",
    "grok-4.20-multi-agent-low": "grok-4.20-multi-agent-0309",
    "grok-4.20-multi-agent-medium": "grok-4.20-multi-agent-0309",
    "grok-4.20-multi-agent-high": "grok-4.20-multi-agent-0309",
    "grok-4.20-multi-agent-xhigh": "grok-4.20-multi-agent-0309",
}

# 需要附带 reasoning 字段的模型（grok-4.3 系列需要，grok-4.20 系列不需要）
_MODELS_WITH_REASONING_FIELD: frozenset[str] = frozenset(
    {
        "grok-4.3",
        "grok-4.20-multi-agent-0309",
    }
)

# 模型名后缀 → 固定 effort 值（优先级高于用户传入的 reasoning_effort）
_MODEL_FIXED_EFFORT: dict[str, str] = {
    "grok-4.3-low": "low",
    "grok-4.3-medium": "medium",
    "grok-4.3-high": "high",
    "grok-4.20-0309-reasoning-low": "low",
    "grok-4.20-0309-reasoning-medium": "medium",
    "grok-4.20-0309-reasoning-high": "high",
    "grok-4.20-multi-agent-low": "low",
    "grok-4.20-multi-agent-medium": "medium",
    "grok-4.20-multi-agent-high": "high",
    "grok-4.20-multi-agent-xhigh": "xhigh",
}

# 特殊 max_output_tokens（默认 1_000_000）
_MODEL_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "grok-4.20-multi-agent-0309": 2_000_000,
}

# 支持 web_search / x_search 工具的模型
_MODELS_WITH_SEARCH_TOOLS: frozenset[str] = frozenset(
    {
        "grok-4.20-multi-agent-0309",
        "grok-4.20-0309",
        "grok-4.20-0309-reasoning",
        "grok-4.20-0309-non-reasoning",
        "grok-4.3",
    }
)

# reasoning effort 映射：OpenAI reasoning_effort → console API effort
_EFFORT_MAP: dict[str, str] = {
    "none": "none",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


def build_console_payload(
    *,
    messages: list[dict[str, Any]],
    model: str,
    temperature: float = 0.7,
    top_p: float = 0.95,
    reasoning_effort: str | None = None,
    stream: bool = True,
    prompt_cache_key: str | None = None,
) -> dict[str, Any]:
    """Build the JSON payload for POST console.x.ai/v1/responses.

    将 OpenAI messages 格式转换为 Responses API input 格式。

    Parameters
    ----------
    prompt_cache_key : str | None
        If set, injected as ``prompt_cache_key`` in the request payload.
        Used for deterministic prompt caching.
    """
    from app.dataplane.reverse.protocol.prompt_cache import inject_prompt_cache_key

    # 转换 messages → input 数组
    input_items: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # 映射 role
        if role in ("system", "developer"):
            # system 消息作为 instructions 字段处理，这里先放入 input
            api_role = "system"
        elif role == "assistant":
            api_role = "assistant"
        else:
            api_role = "user"

        # 处理 content
        if isinstance(content, str):
            content_blocks = [{"type": "input_text", "text": content}]
        elif isinstance(content, list):
            content_blocks = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    content_blocks.append(
                        {"type": "input_text", "text": block.get("text", "")}
                    )
                elif btype == "image_url":
                    url = (block.get("image_url") or {}).get("url", "")
                    if url:
                        content_blocks.append({"type": "input_image", "image_url": url})
                else:
                    # 其他类型降级为文本
                    text = block.get("text") or str(block)
                    content_blocks.append({"type": "input_text", "text": text})
        else:
            content_blocks = [{"type": "input_text", "text": str(content)}]

        if content_blocks:
            input_items.append({"role": api_role, "content": content_blocks})

    effort = _MODEL_FIXED_EFFORT.get(model) or _EFFORT_MAP.get(
        reasoning_effort or "medium", "medium"
    )

    console_model = CONSOLE_MODELS.get(model, model)

    payload: dict[str, Any] = {
        "model": console_model,
        "input": input_items,
        "max_output_tokens": _MODEL_MAX_OUTPUT_TOKENS.get(console_model, 1_000_000),
        "temperature": temperature,
        "top_p": top_p,
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "stream": stream,
    }

    if console_model in _MODELS_WITH_REASONING_FIELD:
        payload["reasoning"] = {"effort": effort}

    if console_model in _MODELS_WITH_SEARCH_TOOLS:
        payload["tools"] = [
            {"type": "web_search", "enable_image_understanding": True},
            {"type": "x_search", "enable_video_understanding": True},
        ]
        payload["tool_choice"] = "auto"

    if prompt_cache_key:
        payload = inject_prompt_cache_key(payload, prompt_cache_key)

    logger.debug(
        "console payload built: model={} console_model={} input_items={} has_reasoning={} prompt_cache={}",
        model,
        console_model,
        len(input_items),
        console_model in _MODELS_WITH_REASONING_FIELD,
        prompt_cache_key is not None,
    )
    return payload


# ---------------------------------------------------------------------------
# SSE stream adapter
# ---------------------------------------------------------------------------


class ConsoleStreamAdapter:
    """Parse console.x.ai SSE events and yield text tokens.

    只关心 response.output_text.delta 事件，其余忽略。
    response.completed 事件用于提取 usage 统计。
    """

    __slots__ = ("text_buf", "usage", "_done")

    def __init__(self) -> None:
        self.text_buf: list[str] = []
        self.usage: dict[str, Any] | None = None
        self._done = False

    def feed(self, event_type: str, data: str) -> list[str]:
        """解析一个 SSE 事件，返回文本 token 列表（通常 0 或 1 个）。"""
        if self._done:
            return []

        try:
            obj = orjson.loads(data)
        except (orjson.JSONDecodeError, ValueError):
            return []

        if event_type == "response.output_text.delta":
            delta = obj.get("delta", "")
            if delta:
                self.text_buf.append(delta)
                return [delta]

        elif event_type == "response.completed":
            resp = obj.get("response", {})
            self.usage = resp.get("usage")
            self._done = True

        elif event_type in ("error", "response.failed"):
            # Go conversation stream: "error" and "response.failed" are both
            # stream errors — a clean [DONE] after a mid-stream failure must
            # not masquerade as success.
            msg = obj.get("message") or obj.get("error") or str(obj)
            raise UpstreamError(f"Console API error: {msg}", status=502)

        return []

    @property
    def full_text(self) -> str:
        return "".join(self.text_buf)


def classify_console_line(line: str) -> tuple[str, str]:
    """Parse a raw SSE line into (event_type, data).

    console.x.ai 使用标准 SSE 格式:
        event: response.output_text.delta
        data: {...}
    """
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


async def stream_console_chat(
    token: str,
    payload: dict[str, Any],
    *,
    timeout_s: float = 120.0,
) -> AsyncGenerator[tuple[str, str], None]:
    """POST to console.x.ai/v1/responses and yield (event_type, data) pairs.

    走现有的 proxy lease + curl-cffi 体系，与 grok.com 共用 CF clearance。
    x.ai 要求 RFC 9449 DPoP 证明（否则 403 unauthorized:dpop-required），
    出站请求带 Authorization: DPoP <access_token> + DPoP: <proof>。
    """
    from app.dataplane.proxy import get_proxy_runtime
    from app.dataplane.reverse.runtime.endpoint_table import CONSOLE_BASE

    proxy = await get_proxy_runtime()
    lease = await proxy.acquire(clearance_origin=CONSOLE_BASE)

    payload_bytes = orjson.dumps(payload)
    session_kwargs = _session_adapter.build_session_kwargs(lease=lease)

    manager = _get_dpop_manager(token)

    async with _session_adapter.ResettableSession(**session_kwargs) as session:
        try:
            dpop_session = await manager.get_or_fetch(
                CONSOLE_BASE, 0, _lease_node_id(lease), token, lease
            )
        except DPoPTokenEndpointError as exc:
            if exc.invalidate_clearance:
                # Go fetchDPoPSession: 403 on a non-definitive block →
                # lease.InvalidateClearance() so the next acquire() re-solves
                # instead of reusing stale cf_clearance.
                await proxy.feedback(
                    lease,
                    _forbidden_feedback(403, invalidate_clearance=True),
                )
                raise UpstreamError(
                    "Console DPoP token endpoint rejected request", status=403
                ) from exc
            await proxy.feedback(lease, _transport_error_feedback())
            raise UpstreamError(
                f"Console DPoP token endpoint failed: {exc}", status=502
            ) from exc
        except DPoPError as exc:
            await proxy.feedback(lease, _transport_error_feedback())
            raise UpstreamError(
                f"Console DPoP setup failed: {exc}", status=502
            ) from exc

        try:
            response = await _post_console_with_dpop(
                manager,
                dpop_session,
                token=token,
                lease=lease,
                payload_bytes=payload_bytes,
                timeout_s=timeout_s,
                session=session,
            )
        except Exception as exc:
            await proxy.feedback(lease, _transport_error_feedback())
            raise UpstreamError(f"Console transport failed: {exc}", status=502) from exc

        if response.status_code != 200:
            try:
                body = await response.atext()
            except Exception:
                body = ""
            exc = UpstreamError.from_http_response(
                f"Console API returned {response.status_code}",
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
                # curl-cffi 的 aiter_lines 返回 bytes，先解码为 str
                if isinstance(raw_line, bytes):
                    try:
                        raw_line = raw_line.decode("utf-8")
                    except UnicodeDecodeError:
                        raw_line = raw_line.decode("utf-8", errors="replace")
                kind, value = classify_console_line(raw_line)
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
            raise UpstreamError(
                f"Console stream read failed: {exc}", status=502
            ) from exc


# ---------------------------------------------------------------------------
# DPoP (RFC 9449) wiring — port of chenyme/grok2api PR #853 console/dpop.go
# ---------------------------------------------------------------------------


async def _post_dpop_token(
    url: str, headers: dict[str, str], json_body: dict[str, Any], lease
) -> tuple[int, dict[str, Any]]:
    """Perform the POST {base}/v1/dpop/token exchange through the proxy machinery.

    Runs on the SAME lease as the triggering request (Go fetchDPoPSession) so
    the transport egress node matches the request headers' cf_clearance node.
    Transport failures surface as status 0 so the manager raises
    DPoPTokenEndpointError instead of leaking raw exceptions.
    """
    from app.dataplane.proxy.adapters.session import (
        ResettableSession,
        build_session_kwargs,
    )

    session_kwargs = build_session_kwargs(lease=lease)
    try:
        async with ResettableSession(**session_kwargs) as s:
            resp = await s.post(
                url, headers=headers, data=orjson.dumps(json_body), timeout=30.0
            )
            status = resp.status_code
            try:
                text = await resp.atext()
            except Exception:
                text = ""
            if not text:
                return status, {}
            try:
                return status, orjson.loads(text)
            except (orjson.JSONDecodeError, TypeError, ValueError):
                return status, {}
    except Exception:
        return 0, {}


_dpop_manager = None  # DPoPSessionManager — built lazily, one per SSO token
_dpop_manager_token: str | None = None


def _lease_node_id(lease) -> int:
    """Egress-node identifier for the DPoP cache key (Go ``lease.NodeID``).

    ProxyLease exposes the egress ``proxy_url`` (no numeric node id), so hash
    it to a stable int; direct (no proxy) leases share the default 0.
    """
    url = getattr(lease, "proxy_url", None)
    return zlib.crc32(url.encode()) if isinstance(url, str) and url else 0


def _get_dpop_manager(token: str) -> "DPoPSessionManager":
    """Lazily build the DPoP session manager.

    One manager per SSO token so the cached DPoP session survives across calls.
    Token-exchange browser headers are re-derived from the lease of the request
    that triggers the exchange — Go fetchDPoPSession applies the *current*
    lease's browser headers per exchange, not the first request's.
    """
    global _dpop_manager, _dpop_manager_token
    if _dpop_manager is None or _dpop_manager_token != token:
        from app.dataplane.proxy.adapters.headers import build_console_headers

        _dpop_manager = DPoPSessionManager(
            _post_dpop_token,
            browser_headers=lambda lease: build_console_headers(token, lease=lease),
            is_definitive_block=_is_definitive_block_body,
        )
        _dpop_manager_token = token
    return _dpop_manager


async def _post_console_with_dpop(
    manager: "DPoPSessionManager",
    dpop_session: "DPoPSession",
    *,
    token: str,
    lease,
    payload_bytes: bytes,
    timeout_s: float,
    session,
):
    """POST /v1/responses with a DPoP proof; on 401 invalidate the session,
    refetch and retry once (mirrors Go doDPoPRequest's single retry)."""
    from app.dataplane.proxy.adapters.headers import build_console_headers
    from app.dataplane.reverse.runtime.endpoint_table import (
        CONSOLE_BASE,
        CONSOLE_RESPONSES,
    )

    async def _post(dpop_sess) -> Any:
        headers = build_console_headers(
            token,
            lease=lease,
            access_token=dpop_sess.access_token,
            dpop_proof=sign_dpop_proof(dpop_sess, method="POST", url=CONSOLE_RESPONSES),
        )
        # Go doDPoPRequest sets x-cluster only for paths ending in /responses.
        headers["x-cluster"] = _X_CLUSTER_HEADER
        return await session.post(
            CONSOLE_RESPONSES,
            headers=headers,
            data=payload_bytes,
            timeout=timeout_s,
            stream=True,
        )

    response = await _post(dpop_session)
    if response.status_code != 401:
        return response
    manager.invalidate(
        dpop_session_cache_key(CONSOLE_BASE, 0, _lease_node_id(lease), token),
        dpop_session.access_token,
    )
    refreshed = await manager.get_or_fetch(
        CONSOLE_BASE, 0, _lease_node_id(lease), token, lease
    )
    return await _post(refreshed)


def _success_feedback():
    from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind

    return ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS, status_code=200)


def _forbidden_feedback(status: int = 403, *, invalidate_clearance: bool = False):
    from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind

    return ProxyFeedback(
        kind=ProxyFeedbackKind.FORBIDDEN,
        status_code=status,
        invalidate_clearance=invalidate_clearance,
    )


def _transport_error_feedback():
    from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind

    return ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)


def _parse_body_code(body: str) -> str:
    """Extract the 'code' field from an upstream JSON error body."""
    if not body:
        return ""
    try:
        parsed = orjson.loads(body)
        code = parsed.get("code", "")
        return str(code) if code else ""
    except (orjson.JSONDecodeError, TypeError, AttributeError):
        return ""


def _status_feedback(status: int, body: str = "", exc: UpstreamError | None = None):
    """Map an upstream console response to ProxyFeedback (account-level semantics).

    Account-level 403s (blocked user, invalid credentials, permanent denial)
    must stay FORBIDDEN — they are account problems, not Cloudflare issues, and
    must NOT invalidate the clearance bundle. Only body-marked CF challenge /
    node-banned responses rotate or invalidate.
    """
    from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind
    from app.dataplane.reverse.protocol.xai_usage import (
        is_content_violation_body,
        is_invalid_credentials_body,
    )
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

    reason = _parse_body_code(body) if body else ""
    if status == 403:
        if body and is_invalid_credentials_body(body):
            kind = ProxyFeedbackKind.FORBIDDEN
        elif body and is_content_violation_body(body):
            kind = ProxyFeedbackKind.FORBIDDEN
        elif body and _is_cf_challenge(body):
            kind = ProxyFeedbackKind.CHALLENGE
        else:
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
    return ProxyFeedback(kind=kind, status_code=status, reason=reason)


__all__ = [
    "CONSOLE_MODELS",
    "build_console_payload",
    "ConsoleStreamAdapter",
    "classify_console_line",
    "stream_console_chat",
]
