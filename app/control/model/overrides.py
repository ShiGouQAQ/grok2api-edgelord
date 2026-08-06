"""Model override persistence — `data/model_overrides.json` layered over the static registry.

The static registry (``MODELS``) stays authoritative for defaults; overrides only
carry per-model deltas: ``{"<model_name>": {"enabled": bool, "tier": "basic|super|heavy"}}``.
Reads are cached by file mtime; the file is tiny, so any writer simply saves
the whole dict.
"""

from __future__ import annotations

import orjson
from typing import Any

from app.platform.paths import data_path

_PATH = data_path("model_overrides.json")

# ponytail: single-entry mtime cache — a stat per read, no re-read when unchanged.
_cache: tuple[float, dict[str, dict[str, Any]]] | None = None


def load() -> dict[str, dict[str, Any]]:
    """Return the merged override map (model name → delta dict). Never raises."""
    global _cache
    try:
        mtime = _PATH.stat().st_mtime_ns
    except OSError:
        _cache = None
        return {}
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    try:
        raw = orjson.loads(_PATH.read_bytes())
    except (orjson.JSONDecodeError, ValueError, OSError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    out = {k: v for k, v in raw.items() if isinstance(v, dict)}
    _cache = (mtime, out)
    return out


def save(overrides: dict[str, dict[str, Any]]) -> None:
    """Atomically persist the full override map and invalidate the cache."""
    global _cache
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_bytes(orjson.dumps(overrides, option=orjson.OPT_INDENT_2))
    _cache = None


def enabled(model_name: str) -> bool | None:
    """Override enabled state for *model_name*, or ``None`` when no override."""
    value = load().get(model_name, {}).get("enabled")
    return value if isinstance(value, bool) else None


def tier(model_name: str) -> str | None:
    """Override tier string for *model_name*, or ``None`` when no override."""
    value = load().get(model_name, {}).get("tier")
    return value if isinstance(value, str) and value else None


__all__ = ["load", "save", "enabled", "tier", "_PATH"]
