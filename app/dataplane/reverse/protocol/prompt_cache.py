"""Prompt cache identity resolution and injection for Build API requests.

Port of Go's resolvePromptCacheIdentity + injectPromptCacheKey.

Derives a stable, isolated cache key from client_key_id, provider, upstream
model, operation, and session seed.  Uses SHA-256 to prevent cross-tenant
collisions in shared account pools.
"""

import hashlib


_PROMPT_CACHE_VERSION = "v1"


def resolve_prompt_cache_identity(
    *,
    client_key_id: int = 0,
    provider: str = "",
    upstream_model: str = "",
    operation: str = "",
    explicit_key: str | None = None,
    session_seed: str | None = None,
) -> str | None:
    """Resolve a deterministic prompt cache identity for Build API requests.

    Mimics Go's resolvePromptCacheIdentity logic:
      1. Prefer explicit_key over session_seed.
      2. If both seeds are empty, or client_key_id/provder/model is absent,
         return None (no caching).
      3. Build a source string:
         ``grok2api:prompt-cache:v1:{client_key_id}:{provider}:{model}:{operation}:{seed}``
      4. SHA-256 hash the first 16 bytes and format as UUID-like: ``{8}-{4}-{4}-{4}-{12}``

    Parameters
    ----------
    client_key_id : int
        Numeric identifier of the API consumer (0 = unknown / no caching).
    provider : str
        Upstream provider name (e.g. "build", "console").
    upstream_model : str
        The actual model name sent upstream (case-insensitive, trimmed).
    operation : str
        Operation being performed (e.g. "responses"), defaults to "responses".
    explicit_key : str | None
        Explicit prompt cache key from the request.
    session_seed : str | None
        Session seed fallback if explicit_key is empty.

    Returns
    -------
    str | None
        A UUID-like cache identity string, or None if caching is not possible.
    """
    seed = (explicit_key or session_seed or "").strip()
    model = upstream_model.strip().lower()
    if not seed or not client_key_id or not provider or not model:
        return None
    if not operation:
        operation = "responses"

    source = f"grok2api:prompt-cache:{_PROMPT_CACHE_VERSION}:{client_key_id}:{provider}:{model}:{operation}:{seed}"
    digest = hashlib.sha256(source.encode()).digest()[:16]
    hex_id = digest.hex()

    return (
        f"{hex_id[0:8]}-{hex_id[8:12]}-{hex_id[12:16]}-{hex_id[16:20]}-{hex_id[20:32]}"
    )


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


__all__ = [
    "resolve_prompt_cache_identity",
    "inject_prompt_cache_key",
]
