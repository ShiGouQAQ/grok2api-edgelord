"""XAI Build protocol — payload builder and SSE stream adapter.

Endpoints: POST https://cli-chat-proxy.grok.com/v1/responses
Auth: Bearer token (x.ai account)

Request format (OpenAI Responses API):
{
    "model": "grok-4",
    "input": [{"role": "user", "content": [{"type": "input_text", "text": "..."}]}],
    "stream": true,
    "max_output_tokens": 1000000,
    "temperature": 0.7,
    "top_p": 0.95,
    "store": false,
    "include": ["reasoning.encrypted_content"],
}

SSE event types:
- response.output_text.delta          — text token, delta field
- response.output_item.done (reasoning) — reasoning item with encrypted_content
- response.completed                  — usage stats
- Other events                        — ignored
"""

from typing import Any

import orjson

from app.control.model.registry import resolve_alias
from app.dataplane.reverse.protocol.tool_parser import (
    StreamFunctionArgumentsBuffer,
    normalize_function_arguments,
)
from app.dataplane.reverse.protocol.tool_parser import (
    StreamFunctionArgumentsBuffer,
    normalize_function_arguments,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Models that actually accept "xhigh" effort (Go modeldomain.ReasoningEffortXHigh).
# Unknown models default to defensive xhigh→high; only these keep the value.
_XHIGH_SUPPORTED_MODELS: frozenset[str] = frozenset({"grok-4.20-multi-agent-0309"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_reasoning_effort(
    effort: str | None, model: str | None = None
) -> str | None:
    """Normalize reasoning effort for Build API (Go normalizeBuildReasoningEffortPayload).

    "max" is never accepted — map to "high". "xhigh" is kept only for models
    that support it (multi-agent), otherwise mapped to "high". The model name
    is resolved through the registry alias map first so aliases inherit their
    canonical model's capabilities (Go 1edc9fbe).
    """
    if effort is None:
        return None
    normalized = effort.lower()
    if normalized == "max":
        return "high"
    if normalized == "xhigh":
        canonical = resolve_alias(model) if model else None
        if canonical not in _XHIGH_SUPPORTED_MODELS:
            return "high"
    return normalized


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


def build_build_responses_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    reasoning_effort: str | None = None,
    stream: bool = True,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
    max_output_tokens: int = 1_000_000,
    temperature: float = 0.7,
    top_p: float = 0.95,
    prompt_cache_key: str | None = None,
    previous_response_id: str | None = None,
) -> dict[str, Any]:
    """Build the JSON payload for Build API POST /v1/responses.

    Converts OpenAI messages format to Responses API input format.
    """
    from app.dataplane.reverse.protocol.prompt_cache import inject_prompt_cache_key

    # Convert messages → input array
    input_items: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            content_blocks: list[dict[str, Any]] = [
                {"type": "input_text", "text": content}
            ]
        elif isinstance(content, list):
            content_blocks = content
        else:
            content_blocks = [{"type": "input_text", "text": str(content)}]

        input_items.append({"role": role, "content": content_blocks})

    payload: dict[str, Any] = {
        "model": model,
        "input": input_items,
        "stream": stream,
        "max_output_tokens": max_output_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }

    effort = _normalize_reasoning_effort(reasoning_effort, model)
    if effort == "none":
        # Go rewriteAliasedModel: "none" disables reasoning entirely.
        payload["thinking"] = {"type": "disabled"}
    elif effort is not None:
        payload["reasoning"] = {"effort": effort}
        payload["thinking"] = {"type": "adaptive"}

    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    if prompt_cache_key:
        payload = inject_prompt_cache_key(payload, prompt_cache_key)

    if previous_response_id:
        payload["previous_response_id"] = previous_response_id

    return payload


# ---------------------------------------------------------------------------
# SSE stream adapter
# ---------------------------------------------------------------------------


class BuildStreamAdapter:
    """Parse Build API SSE events into structured output dicts.

    Feed it raw (event_type, data) pairs from SSE parsing.
    Returns 0-N output dicts per event.

    When *schemas* (tool name → JSON schema, pre-filtered to schemas with a
    reachable integer constraint) is provided, streamed
    ``response.function_call_arguments.delta`` / ``.done`` events are
    buffered and integer-normalized like Go rewriteStreamData (8b5c1ed6):
    on .done the full arguments are normalized and a corrected delta + done
    are re-emitted; over the 1MB/4MB caps the call falls back to passthrough
    with the buffered text flushed verbatim as one delta.
    """

    __slots__ = ("_buffer", "text_buf", "usage", "_done", "schemas", "_stream_calls")

    def __init__(self, schemas: dict[str, Any] | None = None) -> None:
        self._buffer: list[dict[str, Any]] = []
        self.text_buf: list[str] = []
        self.usage: dict[str, Any] | None = None
        self._done = False
        self.schemas = schemas or {}
        self._stream_calls: dict[
            str, tuple[str, Any, StreamFunctionArgumentsBuffer]
        ] = {}

    def _stream_call(
        self, obj: dict[str, Any]
    ) -> tuple[str, Any, StreamFunctionArgumentsBuffer] | None:
        """Look up the per-call state by item_id then call_id."""
        for key in (obj.get("item_id"), obj.get("call_id")):
            if key:
                call = self._stream_calls.get(key)
                if call is not None:
                    return call
        return None

    def _normalize_item_arguments(self, item: dict[str, Any]) -> None:
        """Normalize a function_call item's arguments against its schema
        (Go rewriteFunctionCall for output_item.done payloads)."""
        schema = self.schemas.get(item.get("name", ""))
        if not isinstance(schema, dict):
            return
        arguments = item.get("arguments")
        if not isinstance(arguments, str):
            return
        normalized, _ = normalize_function_arguments(arguments, schema)
        if normalized != arguments:
            item["arguments"] = normalized

    def feed(self, event_type: str, data: str) -> list[dict[str, Any]]:
        """Parse one SSE event. Returns 0-N output dicts."""
        try:
            obj = orjson.loads(data)
        except (orjson.JSONDecodeError, ValueError):
            return []

        if self._done:
            return []

        if event_type == "response.output_item.added":
            item = obj.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                name = item.get("name", "")
                call = (name, self.schemas.get(name), StreamFunctionArgumentsBuffer())
                for key in (item.get("id"), item.get("call_id")):
                    if key:
                        self._stream_calls[key] = call
                return [{"type": "sse", "event": event_type, "payload": obj}]
            return []

        if event_type == "response.function_call_arguments.delta":
            call = self._stream_call(obj)
            if call is None:
                # Un-remembered call (internal tool) — drop, as before.
                return []
            _, schema, stream = call
            if schema is None or stream.is_passthrough():
                # No integer constraint or overflow fallback — verbatim.
                return [{"type": "sse", "event": event_type, "payload": obj}]
            flushed = stream.feed_delta(obj.get("delta", ""), obj)
            if flushed is not None:
                outputs: list[dict[str, Any]] = []
                if flushed:
                    flushed_payload = dict(obj)
                    flushed_payload["delta"] = flushed
                    outputs.append(
                        {"type": "sse", "event": event_type, "payload": flushed_payload}
                    )
                outputs.append({"type": "sse", "event": event_type, "payload": obj})
                return outputs
            return []

        if event_type == "response.function_call_arguments.done":
            call = self._stream_call(obj)
            if call is None:
                return []
            _, schema, stream = call
            if schema is None:
                return [{"type": "sse", "event": event_type, "payload": obj}]
            normalized, last_delta = stream.done(obj.get("arguments", ""), schema)
            outputs = []
            if last_delta is not None:
                outputs.append(
                    {
                        "type": "sse",
                        "event": "response.function_call_arguments.delta",
                        "payload": last_delta,
                    }
                )
            done_payload = dict(obj)
            done_payload["arguments"] = normalized
            outputs.append(
                {
                    "type": "sse",
                    "event": "response.function_call_arguments.done",
                    "payload": done_payload,
                }
            )
            return outputs

        if event_type == "response.output_text.delta":
            delta = obj.get("delta", "")
            index = obj.get("index", 0)
            if delta:
                self.text_buf.append(delta)
            return [{"type": "text", "delta": delta, "index": index}]

        if event_type == "response.output_item.done":
            item = obj.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                self._normalize_item_arguments(item)
                return [{"type": "sse", "event": event_type, "payload": obj}]
            if obj.get("type") == "reasoning":
                encrypted = obj.get("encrypted_content", "")
                return [{"type": "reasoning", "encrypted_content": encrypted}]
            return []

        if event_type == "response.completed":
            resp = obj.get("response", {})
            usage = resp.get("usage", {})
            self.usage = usage
            self._done = True
            return [{"type": "done", "usage": usage}]

        return []

    @property
    def full_text(self) -> str:
        return "".join(self.text_buf)


__all__ = [
    "build_build_responses_payload",
    "BuildStreamAdapter",
]
