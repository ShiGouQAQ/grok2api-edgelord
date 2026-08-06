"""Structured settings API — Go→Python port of ``transport/http/settings/handler.go``.

Go exposes a structured settings surface (GET/PUT ``/admin/api/settings``) with
optimistic-concurrency revision (409 on stale). Python's storage stays the
TOML config (``config.defaults.toml`` + user overrides in ``data/config.toml``);
this module is a read/write projection between the Go settingsResponse shape
and TOML sections. The raw ``/admin/api/config`` endpoints keep working
unchanged — this API is additive.

Mapping table (Go field → TOML dotted key):

    providerBuild.clientVersion            → build.client_version
    providerBuild.clientIdentifier         → build.client_identifier
    providerBuild.tokenAuth                → build.token_auth
    providerBuild.userAgent                → build.user_agent
    providerWeb.chatTimeout                → chat.timeout            (dur_s: "60s" ↔ 60)
    providerWeb.imageTimeout               → image.timeout           (dur_s)
    providerWeb.videoTimeout               → video.timeout           (dur_s)
    providerWeb.clearanceMode              → proxy.clearance.mode
    providerWeb.flareSolverrURL            → proxy.clearance.flaresolverr_url
    providerWeb.clearanceTimeout           → proxy.clearance.timeout_sec   (dur_s)
    providerWeb.clearanceRefresh           → proxy.clearance.refresh_interval (dur_s)
    providerWeb.allowNSFW                  → features.enable_nsfw
    providerWeb.statsigMode                → features.dynamic_statsig ("auto"→true, else false)
    batch.refreshConcurrency               → batch.refresh_concurrency
    media.maxImageBytes                    → cache.local.image_max_mb (bytes ↔ MB, ×1048576)
    frontend.publicApiBaseURL              → app.app_url
    routing.maxAttempts                    → routing.max_routing_attempts (None → unset/omit)
    routing.preferFreeBuild                → build.prefer_free_build
    routing.markBuildChatDeniedAsReauth    → account.build_detect.mark_build_chat_denied_as_reauth
    accounts.buildForbiddenReauthCodes     → features.build_403_invalidation_codes (list ↔ comma string)

Fields with no Python runtime consumer are persisted under the inert
``[settings]`` user-override section (deep-merged like any other key, versioned
by the same revision, untouched by Python runtime code) so GET→PUT round-trips
without restructuring the established schema:

    server.maxConcurrentRequests           → settings.server_max_concurrent_requests
    providerBuild.{baseURL,fallbackBaseURL,responseHeaderTimeout}
    providerWeb.{baseURL,statsigManualValue,statsigSignerURL,quotaTimeout,
                 mediaConcurrency,recoveryBackoffBase,recoveryBackoffMax}
    providerConsole.{baseURL,chatTimeout}
    batch.{importConcurrency,conversionConcurrency,syncConcurrency,randomDelay}
    media.{maxTotalBytes,cleanupThresholdPercent,cleanupInterval}
    routing.{stickyTTL,cooldownBase,cooldownMax,capacityWait,
             accountIsolatedConnections,segmentedSelector.*}
    audit.*  clientKeyDefaults.*
    accounts.{markBuildForbiddenReauth,autoCleanReauthEnabled,
              autoCleanReauthInterval,autoCleanReauthMinAge,autoCleanIncludeDisabled}

Computed (read-only) fields — ``tokenAuthConfigured`` / ``statsigManualConfigured``
are derived, accepted-but-ignored on PUT (mirrors Go, which ignores unknown
struct fields); any other unknown/unmappable field is rejected with 422.

Revision = sha256 of the canonical JSON of the backend's stored overrides —
content-based and backend-agnostic (works for toml/redis/sql backends).
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

import orjson
from fastapi import APIRouter, Body

from app.platform.config.snapshot import config
from app.platform.errors import AppError, ErrorKind

router = APIRouter(prefix="/settings", tags=["Admin - System"])

# Field kinds:
#   str      — verbatim string
#   int      — integer (bool rejected)
#   bool     — boolean
#   dur_s    — Go duration string ("60s") ↔ Python int seconds
#   bytes_mb — bytes (Go) ↔ MB (cache.local.*_max_mb)
#   codes    — list[str] (Go) ↔ comma-separated string (Python)
_SECTIONS: dict[str, dict[str, tuple[str, str, Any]]] = {
    "server": {
        "maxConcurrentRequests": ("settings.server_max_concurrent_requests", "int", 0),
    },
    "providerBuild": {
        "baseURL": (
            "settings.provider_build_base_url",
            "str",
            "https://cli-chat-proxy.grok.com/v1",
        ),
        "fallbackBaseURL": (
            "settings.provider_build_fallback_base_url",
            "str",
            "https://api.x.ai/v1",
        ),
        "clientVersion": ("build.client_version", "str", "0.2.119"),
        "clientIdentifier": ("build.client_identifier", "str", "grok-shell"),
        "tokenAuth": ("build.token_auth", "str", "xai-grok-cli"),
        "userAgent": ("build.user_agent", "str", ""),
        "responseHeaderTimeout": (
            "settings.provider_build_response_header_timeout",
            "str",
            "5m",
        ),
    },
    "providerWeb": {
        "baseURL": ("settings.provider_web_base_url", "str", "https://grok.com"),
        "statsigMode": ("features.dynamic_statsig", "statsig", True),
        "statsigManualValue": ("settings.provider_web_statsig_manual_value", "str", ""),
        "statsigSignerURL": ("settings.provider_web_statsig_signer_url", "str", ""),
        "clearanceMode": ("proxy.clearance.mode", "str", "none"),
        "flareSolverrURL": ("proxy.clearance.flaresolverr_url", "str", ""),
        "clearanceTimeout": ("proxy.clearance.timeout_sec", "dur_s", 60),
        "clearanceRefresh": ("proxy.clearance.refresh_interval", "dur_s", 3600),
        "quotaTimeout": ("settings.provider_web_quota_timeout", "str", "30s"),
        "chatTimeout": ("chat.timeout", "dur_s", 60),
        "imageTimeout": ("image.timeout", "dur_s", 60),
        "videoTimeout": ("video.timeout", "dur_s", 60),
        "mediaConcurrency": ("settings.provider_web_media_concurrency", "int", 4),
        "allowNSFW": ("features.enable_nsfw", "bool", True),
        "recoveryBackoffBase": (
            "settings.provider_web_recovery_backoff_base",
            "str",
            "1s",
        ),
        "recoveryBackoffMax": (
            "settings.provider_web_recovery_backoff_max",
            "str",
            "30s",
        ),
    },
    "providerConsole": {
        "baseURL": (
            "settings.provider_console_base_url",
            "str",
            "https://console.x.ai",
        ),
        "chatTimeout": ("settings.provider_console_chat_timeout", "str", "60s"),
    },
    "batch": {
        "importConcurrency": ("settings.batch_import_concurrency", "int", 5),
        "conversionConcurrency": ("settings.batch_conversion_concurrency", "int", 5),
        "syncConcurrency": ("settings.batch_sync_concurrency", "int", 5),
        "refreshConcurrency": ("batch.refresh_concurrency", "int", 50),
        "randomDelay": ("settings.batch_random_delay", "str", "0s"),
    },
    "media": {
        "maxImageBytes": ("cache.local.image_max_mb", "bytes_mb", 0),
        "maxTotalBytes": ("settings.media_max_total_bytes", "int", 0),
        "cleanupThresholdPercent": (
            "settings.media_cleanup_threshold_percent",
            "int",
            80,
        ),
        "cleanupInterval": ("settings.media_cleanup_interval", "str", "1h"),
    },
    "frontend": {
        "publicApiBaseURL": ("app.app_url", "str", ""),
    },
    "routing": {
        "stickyTTL": ("settings.routing_sticky_ttl", "str", "5m"),
        "cooldownBase": ("settings.routing_cooldown_base", "str", "5s"),
        "cooldownMax": ("settings.routing_cooldown_max", "str", "30s"),
        "capacityWait": ("settings.routing_capacity_wait", "str", "2s"),
        "maxAttempts": (
            "routing.max_routing_attempts",
            "int",
            None,
        ),  # None → unset (legacy budget)
        "preferFreeBuild": ("build.prefer_free_build", "bool", False),
        "markBuildChatDeniedAsReauth": (
            "account.build_detect.mark_build_chat_denied_as_reauth",
            "bool",
            False,
        ),
        "accountIsolatedConnections": (
            "settings.routing_account_isolated_connections",
            "bool",
            False,
        ),
    },
    "audit": {
        "bufferSize": ("settings.audit_buffer_size", "int", 1024),
        "batchSize": ("settings.audit_batch_size", "int", 64),
        "flushInterval": ("settings.audit_flush_interval", "str", "1s"),
        "commitDelayMS": ("settings.audit_commit_delay_ms", "int", 100),
    },
    "clientKeyDefaults": {
        "rpmLimit": ("settings.client_key_rpm_limit", "int", 120),
        "maxConcurrent": ("settings.client_key_max_concurrent", "int", 4),
    },
    "accounts": {
        "markBuildForbiddenReauth": (
            "settings.accounts_mark_build_forbidden_reauth",
            "bool",
            False,
        ),
        "buildForbiddenReauthCodes": (
            "features.build_403_invalidation_codes",
            "codes",
            [],
        ),
        "autoCleanReauthEnabled": (
            "settings.accounts_auto_clean_reauth_enabled",
            "bool",
            False,
        ),
        "autoCleanReauthInterval": (
            "settings.accounts_auto_clean_reauth_interval",
            "str",
            "15m",
        ),
        "autoCleanReauthMinAge": (
            "settings.accounts_auto_clean_reauth_min_age",
            "str",
            "24h",
        ),
        "autoCleanIncludeDisabled": (
            "settings.accounts_auto_clean_reauth_include_disabled",
            "bool",
            False,
        ),
    },
}

# Derived fields present in the GET payload; accepted-but-ignored on PUT
# (Go also ignores them when unmarshalling into its structs).
_COMPUTED_FIELDS = {
    "providerBuild": {"tokenAuthConfigured"},
    "providerWeb": {"statsigManualConfigured"},
}

# Nested sub-objects handled outside the flat field loop.
_SUB_OBJECT_FIELDS = {"routing": {"segmentedSelector"}}

# Nested sub-object (not expressible as a flat field).
_SEGMENTED_SELECTOR_FIELDS = (
    ("enabled", "settings.routing_segmented_selector_enabled", "bool", False),
    ("minCandidates", "settings.routing_segmented_selector_min_candidates", "int", 2),
    ("windowSize", "settings.routing_segmented_selector_window_size", "int", 5),
)

_DUR_RE = re.compile(r"(\d+)([smh])")
_DUR_MULT = {"s": 1, "m": 60, "h": 3600}


# ---------------------------------------------------------------------------
# Value conversions (Go shape ↔ TOML)
# ---------------------------------------------------------------------------


def _fmt_duration(seconds: int) -> str:
    return f"{int(seconds)}s"


def _parse_duration(value: Any) -> int:
    """Parse a Go duration string (``60s``/``5m``/``1h30m``) or raw int → seconds."""
    if isinstance(value, bool):
        raise ValueError(f"not a duration: {value!r}")
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise ValueError(f"not a duration: {value!r}")
    text = value.strip().lower()
    if text.isdigit():
        return int(text)
    total = 0
    for num, unit in _DUR_RE.findall(text):
        total += int(num) * _DUR_MULT[unit]
    if not total:
        raise ValueError(f"not a duration: {value!r}")
    return total


def _to_go_value(raw: Any, kind: str) -> Any:
    if kind == "dur_s":
        return _fmt_duration(raw if raw is not None else 0)
    if kind == "bytes_mb":
        return int(raw or 0) * 1048576
    if kind == "codes":
        if not raw:
            return []
        return [c.strip() for c in str(raw).split(",") if c.strip()]
    if kind == "statsig":
        return "auto" if raw else "off"
    return raw


def _from_go_value(value: Any, kind: str, default: Any) -> Any:
    """Convert a PUT value to its TOML representation. Raises on invalid input."""
    if value is None:
        if default is None:  # nullable field → omit (leave unset)
            return _OMIT
        raise ValueError(f"expected a value, got null")
    if kind == "str":
        if not isinstance(value, str):
            raise ValueError(f"expected string, got {type(value).__name__}")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"expected integer, got {type(value).__name__}")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"expected boolean, got {type(value).__name__}")
        return value
    if kind == "dur_s":
        return _parse_duration(value)
    if kind == "bytes_mb":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"expected non-negative byte count, got {value!r}")
        return value // 1048576
    if kind == "codes":
        if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
            raise ValueError(f"expected list of strings, got {value!r}")
        return ",".join(value)
    if kind == "statsig":
        if not isinstance(value, str) or value not in {"auto", "manual", "off"}:
            raise ValueError(f"expected 'auto'|'manual'|'off', got {value!r}")
        return value == "auto"
    raise AssertionError(f"unknown field kind: {kind}")


class _Omit:
    pass


_OMIT = _Omit()


def _set_nested(target: dict[str, Any], dotted: str, value: Any) -> None:
    node = target
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


# ---------------------------------------------------------------------------
# GET projection
# ---------------------------------------------------------------------------


def _to_go_config(cfg: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for section, fields in _SECTIONS.items():
        sec = {}
        for field, (toml, kind, default) in fields.items():
            raw = cfg.get(toml, default)
            if raw is None and default is None:
                sec[field] = None
                continue
            sec[field] = _to_go_value(raw, kind)
        out[section] = sec
    # Nested sub-objects.
    seg = {
        name: _to_go_value(cfg.get(toml, default), kind)
        for name, toml, kind, default in _SEGMENTED_SELECTOR_FIELDS
    }
    out["routing"]["segmentedSelector"] = seg
    # Derived flags.
    out["providerBuild"]["tokenAuthConfigured"] = bool(
        out["providerBuild"]["tokenAuth"]
    )
    out["providerWeb"]["statsigManualConfigured"] = bool(
        out["providerWeb"]["statsigManualValue"]
    )
    return out


async def _compute_revision() -> str:
    backend = config._get_backend()
    overrides = await backend.load()
    payload = orjson.dumps(overrides, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(payload).hexdigest()


def _updated_at(ver: object) -> str:
    if isinstance(ver, float) and ver > 0:
        return datetime.fromtimestamp(ver, timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


async def get_settings_response() -> dict[str, Any]:
    """Build the Go ``settingsResponse`` from the current effective config."""
    await config.load()
    cfg = config
    return {
        "config": _to_go_config(cfg),
        "recommendedProviderBuild": {
            "clientVersion": cfg.get_str("build.client_version", "0.2.119"),
            "userAgent": cfg.get_str("build.user_agent", ""),
        },
        "updatedAt": _updated_at(await config._get_backend().version()),
        "revision": await _compute_revision(),
        "restartRequired": [],
    }


# ---------------------------------------------------------------------------
# PUT inverse mapping
# ---------------------------------------------------------------------------


def _from_go_config(body_config: Any) -> dict[str, Any]:
    """Inverse-map the Go config shape onto a TOML patch. Raises on unmappable input."""
    if not isinstance(body_config, dict):
        raise AppError(
            "config must be an object",
            kind=ErrorKind.VALIDATION,
            code="invalidRequest",
            status=400,
        )
    patch: dict[str, Any] = {}
    bad: list[str] = []
    for section, values in body_config.items():
        if section not in _SECTIONS:
            bad.append(section)
            continue
        if not isinstance(values, dict):
            bad.append(f"{section}: expected object, got {type(values).__name__}")
            continue
        computed = _COMPUTED_FIELDS.get(section, set())
        sub_objects = _SUB_OBJECT_FIELDS.get(section, set())
        for field, value in values.items():
            if field in computed or field in sub_objects:
                continue
            if field not in _SECTIONS[section]:
                bad.append(f"{section}.{field}")
                continue
            toml, kind, default = _SECTIONS[section][field]
            try:
                converted = _from_go_value(value, kind, default)
            except ValueError as exc:
                bad.append(f"{section}.{field}: {exc}")
                continue
            if converted is not _OMIT:
                _set_nested(patch, toml, converted)

    # Nested sub-objects.
    seg = body_config.get("routing", {}).get("segmentedSelector")
    if seg is not None:
        if isinstance(seg, dict):
            for name, toml, kind, default in _SEGMENTED_SELECTOR_FIELDS:
                if name in seg:
                    try:
                        converted = _from_go_value(seg[name], kind, default)
                    except ValueError as exc:
                        bad.append(f"routing.segmentedSelector.{name}: {exc}")
                        continue
                    if converted is not _OMIT:
                        _set_nested(patch, toml, converted)
        else:
            bad.append("routing.segmentedSelector: expected object")

    if bad:
        raise AppError(
            "settings fields cannot be mapped to config: " + "; ".join(bad),
            kind=ErrorKind.VALIDATION,
            code="unmappable_settings_fields",
            status=422,
            details={"param": ", ".join(bad)},
        )
    return patch


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def get_settings() -> dict[str, Any]:
    return await get_settings_response()


@router.put("")
async def put_settings(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    revision = body.get("revision")
    if revision is None:
        raise AppError(
            "missing revision",
            kind=ErrorKind.VALIDATION,
            code="invalidRequest",
            status=400,
        )
    patch = _from_go_config(body.get("config"))

    current = await _compute_revision()
    if str(revision) != current:
        raise AppError(
            "设置已被其他会话更新，请刷新后重试",
            kind=ErrorKind.VALIDATION,
            code="settingsConflict",
            status=409,
        )

    await config.update(patch)
    await config.load()
    return await get_settings_response()
