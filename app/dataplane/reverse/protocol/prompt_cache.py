"""Prompt cache identity resolution and injection for Build API requests.

Ports of Go's resolvePromptCacheIdentity, injectPromptCacheKey,
extractPromptCacheSeed, extractMessageAnchors.

Derives a stable, isolated cache key from client_key_id, provider, upstream
model, operation, and session seed.  Uses SHA-256 to prevent cross-tenant
collisions in shared account pools.

Version history:
  v1 — initial port (dc9b157, 3c30472)
  v2 — Sub2API/Codex/Claude Code header seed extraction (48ec7dc, f65a07e)
  v3 — soft session from instructions/system anchors (539a6ae)
"""

import hashlib
from typing import Any


_PROMPT_CACHE_VERSION = "v3"


def resolve_prompt_cache_identity(
    *,
    client_key_id: int = 0,
    provider: str = "",
    upstream_model: str = "",
    operation: str = "",
    explicit_key: str | None = None,
    session_seed: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve deterministic prompt cache identity and reasoning replay key.

    Returns (cache_key, replay_key). replay_key is only set when explicit_key
    or session_seed is provided (not soft message-derived).
    """
    seed = (explicit_key or session_seed or "").strip()
    model = upstream_model.strip().lower()
    if not seed or not client_key_id or not provider or not model:
        return None, None
    if not operation:
        operation = "responses"

    source = f"grok2api:prompt-cache:{_PROMPT_CACHE_VERSION}:{client_key_id}:{provider}:{model}:{operation}:{seed}"
    digest = hashlib.sha256(source.encode()).digest()[:16]
    hex_id = digest.hex()
    cache_key = (
        f"{hex_id[0:8]}-{hex_id[8:12]}-{hex_id[12:16]}-{hex_id[16:20]}-{hex_id[20:32]}"
    )

    replay_source = f"grok2api:build-replay:{_PROMPT_CACHE_VERSION}:{client_key_id}:{provider}:{seed}"
    replay_digest = hashlib.sha256(replay_source.encode()).digest()[:16]
    replay_hex = replay_digest.hex()
    replay_key = f"{replay_hex[0:8]}-{replay_hex[8:12]}-{replay_hex[12:16]}-{replay_hex[16:20]}-{replay_hex[20:32]}"

    return cache_key, replay_key


def inject_prompt_cache_key(
    body: dict[str, object], cache_key: str | None
) -> dict[str, object]:
    """Inject prompt_cache_key into a JSON request body dict.

    If *cache_key* is empty or None the body is returned unchanged.
    If the body already has a ``prompt_cache_key`` field it is left as-is.
    Otherwise the key is added to the dict.

    Matching Go's injectPromptCacheKey in cli/adapter.go.
    """
    key = (cache_key or "").strip()
    if not key:
        return body
    if "prompt_cache_key" in body:
        return body
    body["prompt_cache_key"] = key
    return body


# ---------------------------------------------------------------------------
# Seed extraction from HTTP headers + body (port of 48ec7dc, f65a07e)
# ---------------------------------------------------------------------------

# Headers that may carry a prompt cache seed (tried in priority order).
_CACHE_SEED_HEADERS: tuple[str, ...] = (
    "x-prompt-cache-key",
    "x-client-session-id",
    "x-grok-session-id",
    "x-grok-conv-id",
    "session_id",
    "conversation_id",
    "x-codex-window-id",
    "x-claude-session-id",
)

# Body fields that may carry a prompt cache seed (tried in priority order).
_CACHE_SEED_BODY_FIELDS: tuple[str, ...] = (
    "prompt_cache_key",
    "session_id",
    "sessionId",
)


def extract_prompt_cache_seed(
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> str | None:
    """Extract the prompt cache seed from HTTP headers and/or request body.

    Port of Go ``extractPromptCacheSeed`` in transport/http/inference/prompt_cache.go.

    Searches headers first (tried in priority order), then body fields.
    Returns the first non-empty, non-whitespace value found, or ``None``.
    """
    if headers:
        for hdr in _CACHE_SEED_HEADERS:
            val = headers.get(hdr, "").strip()
            if val:
                return val
    if body:
        for field in _CACHE_SEED_BODY_FIELDS:
            raw = body.get(field)
            if isinstance(raw, str):
                val = raw.strip()
                if val:
                    return val
            elif raw is not None:
                val = str(raw).strip()
                if val:
                    return val
    return None


# ---------------------------------------------------------------------------
# Soft session from message anchors (port of 539a6ae)
# ---------------------------------------------------------------------------

_ANCHOR_MAX_LEN = 256


def _truncate_anchor(text: str) -> str:
    """Truncate a text anchor to a deterministic, stable length."""
    text = text.strip()
    if len(text) <= _ANCHOR_MAX_LEN:
        return text
    return text[:_ANCHOR_MAX_LEN]


def extract_soft_session(
    messages: list[dict[str, Any]] | None = None,
    body: dict[str, Any] | None = None,
) -> str | None:
    """Derive a soft session seed from system/instructions and first user message.

    Port of Go ``extractMessageAnchors`` in gateway/prompt_cache.go (539a6ae).
    Uses top-level ``instructions``/``system`` fields plus first user message
    as a deterministic cache affinity anchor when no explicit session key exists.

    Returns a joined anchor string or ``None`` if no meaningful anchor is found.
    """
    parts: list[str] = []

    # Top-level instructions/system (539a6ae: read from body)
    if body:
        for key in ("instructions", "system"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(_truncate_anchor(val))
                break
            elif isinstance(val, list):
                # Anthropic-style system: list of text blocks
                text = " ".join(
                    b.get("text", "")
                    for b in val
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
                if text:
                    parts.append(_truncate_anchor(text))
                    break

    # Messages anchors: system messages + first user message
    if messages:
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                text = _extract_text_from_content(msg.get("content", ""))
                if text.strip():
                    parts.append(_truncate_anchor(text))
            elif role == "user" and not any(
                p
                for p in parts
                if p  # only if no system anchor yet
            ):
                text = _extract_text_from_content(msg.get("content", ""))
                if text.strip():
                    parts.append(_truncate_anchor(text))
                break  # only first user message

    if not parts:
        return None
    return "|".join(parts)


def _extract_text_from_content(content: Any) -> str:
    """Extract plain text from a message content value."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return " ".join(texts)
    return str(content) if content else ""


# ---------------------------------------------------------------------------
# Usage merging (port of 539a6ae mergeGatewayUsage)
# ---------------------------------------------------------------------------


def merge_usage(
    base: dict[str, Any], override: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge two usage dicts, overwriting only non-zero/non-empty fields.

    Port of Go ``mergeGatewayUsage`` in transport/http/inference/handler.go (539a6ae).
    Non-zero fields in *override* overwrite corresponding fields in *base*.
    Zero/empty fields in *override* are ignored (preserving *base* values).
    """
    if not override:
        return base
    result = dict(base)
    for key, val in override.items():
        if val is None:
            continue
        if isinstance(val, (int, float)) and val == 0:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        if isinstance(val, dict):
            existing = result.get(key)
            if isinstance(existing, dict):
                result[key] = merge_usage(existing, val)
            else:
                result[key] = val
        else:
            result[key] = val
    return result


__all__ = [
    "resolve_prompt_cache_identity",
    "inject_prompt_cache_key",
    "extract_prompt_cache_seed",
    "extract_soft_session",
    "merge_usage",
]
