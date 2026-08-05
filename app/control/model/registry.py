"""Model registry — all supported model variants defined in one place."""

from .enums import Capability, ModeId, Tier
from .spec import ModelSpec

# ---------------------------------------------------------------------------
# Master model list.
# Add new models here; no other files need to change.
# ---------------------------------------------------------------------------

# fmt: off
MODELS: tuple[ModelSpec, ...] = (
    # === Chat ==============================================================

    # Basic fast; auto/expert require Super+
    ModelSpec("grok-4.20-0309-non-reasoning",           ModeId.FAST,     Tier.BASIC, Capability.CHAT,       True, "Grok 4.20 0309 Non-Reasoning"),
    ModelSpec("grok-4.20-0309",                         ModeId.AUTO,     Tier.SUPER, Capability.CHAT,       True, "Grok 4.20 0309"),
    ModelSpec("grok-4.20-0309-reasoning",               ModeId.EXPERT,   Tier.SUPER, Capability.CHAT,       True, "Grok 4.20 0309 Reasoning", supports_reasoning=True),
    # Super+
    ModelSpec("grok-4.20-0309-non-reasoning-super",     ModeId.FAST,     Tier.SUPER, Capability.CHAT,       True, "Grok 4.20 0309 Non-Reasoning Super"),
    ModelSpec("grok-4.20-0309-super",                   ModeId.AUTO,     Tier.SUPER, Capability.CHAT,       True, "Grok 4.20 0309 Super"),
    ModelSpec("grok-4.20-0309-reasoning-super",         ModeId.EXPERT,   Tier.SUPER, Capability.CHAT,       True, "Grok 4.20 0309 Reasoning Super", supports_reasoning=True),
    # Heavy+
    ModelSpec("grok-4.20-0309-non-reasoning-heavy",     ModeId.FAST,     Tier.HEAVY, Capability.CHAT,       True, "Grok 4.20 0309 Non-Reasoning Heavy"),
    ModelSpec("grok-4.20-0309-heavy",                   ModeId.AUTO,     Tier.HEAVY, Capability.CHAT,       True, "Grok 4.20 0309 Heavy"),
    ModelSpec("grok-4.20-0309-reasoning-heavy",         ModeId.EXPERT,   Tier.HEAVY, Capability.CHAT,       True, "Grok 4.20 0309 Reasoning Heavy", supports_reasoning=True),
    ModelSpec("grok-4.20-multi-agent-0309",             ModeId.HEAVY,    Tier.HEAVY, Capability.CHAT,       True, "Grok 4.20 Multi-Agent 0309", supports_reasoning=True),

    # --- 硬优先级反向选池 (heavy → super → basic) ---
    ModelSpec("grok-4.20-fast",                         ModeId.FAST,     Tier.BASIC, Capability.CHAT,       True, "Grok 4.20 Fast",          prefer_best=True),
    ModelSpec("grok-4.3-fast",                          ModeId.FAST,     Tier.BASIC, Capability.CHAT,       True, "Grok 4.3 Fast",           prefer_best=True),
    ModelSpec("grok-4.20-auto",                         ModeId.AUTO,     Tier.SUPER, Capability.CHAT,       True, "Grok 4.20 Auto",          prefer_best=True),
    ModelSpec("grok-4.20-expert",                       ModeId.EXPERT,   Tier.SUPER, Capability.CHAT,       True, "Grok 4.20 Expert",        prefer_best=True),
    ModelSpec("grok-4.20-heavy",                        ModeId.HEAVY,    Tier.HEAVY, Capability.CHAT,       True, "Grok 4.20 Heavy",         prefer_best=True),

    # === Image ==============================================================

    # Basic fast
    ModelSpec("grok-imagine-image-lite",                ModeId.FAST,     Tier.BASIC, Capability.IMAGE,      True, "Grok Imagine Image Lite"),
    # Super+
    ModelSpec("grok-imagine-image",                     ModeId.AUTO,     Tier.SUPER, Capability.IMAGE,      True, "Grok Imagine Image"),
    ModelSpec("grok-imagine-image-pro",                 ModeId.AUTO,     Tier.SUPER, Capability.IMAGE,      True, "Grok Imagine Image Pro"),

    # === Image Edit =========================================================

    # Super+
    ModelSpec("grok-imagine-image-edit",                ModeId.AUTO,     Tier.SUPER, Capability.IMAGE_EDIT, True, "Grok Imagine Image Edit"),

    # === Video ==============================================================

    # Super+
    ModelSpec("grok-imagine-video",                     ModeId.AUTO,     Tier.SUPER, Capability.VIDEO,      True, "Grok Imagine Video"),

    # === Console Chat (console.x.ai/v1/responses) ===========================
    # 通过 console.x.ai 路由，使用 grok.com SSO token，免费账号可用
    # basic pool 即可（不消耗 grok.com 配额，走 console API 独立配额）
    ModelSpec("grok-4.3-console",                       ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.3 (Console)", supports_reasoning=True),
    ModelSpec("grok-4.3-low",                           ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.3 Low Thinking", supports_reasoning=True),
    ModelSpec("grok-4.3-medium",                        ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.3 Medium Thinking", supports_reasoning=True),
    ModelSpec("grok-4.3-high",                          ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.3 High Thinking", supports_reasoning=True),
    ModelSpec("grok-4.20-0309-reasoning-console",       ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.20 0309 Reasoning (Console)", supports_reasoning=True),
    ModelSpec("grok-4.20-0309-console",                 ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.20 0309 (Console)"),
    ModelSpec("grok-4.20-multi-agent-console",          ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.20 Multi-Agent (Console)", supports_reasoning=True),
    ModelSpec("grok-4.20-multi-agent-low",              ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.20 Multi-Agent Low", supports_reasoning=True),
    ModelSpec("grok-4.20-multi-agent-medium",           ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.20 Multi-Agent Medium", supports_reasoning=True),
    ModelSpec("grok-4.20-multi-agent-high",             ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.20 Multi-Agent High", supports_reasoning=True),
    ModelSpec("grok-4.20-multi-agent-xhigh",            ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.20 Multi-Agent XHigh", supports_reasoning=True),
    ModelSpec("grok-4.20-0309-non-reasoning-console",   ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.20 0309 Non-Reasoning (Console)"),
    ModelSpec("grok-4.5-console",                       ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok 4.5 (Console)", supports_reasoning=True),
    ModelSpec("grok-build-console",                     ModeId.CONSOLE,  Tier.BASIC, Capability.CONSOLE_CHAT, True, "Grok Build (Console)"),

    # === Console Media (console.x.ai /images + /videos, Go a05e06a2) =========
    # 上游 model 字段仍为原名（grok-imagine-image-quality 等），见
    # xai_console_media.py CONSOLE_MEDIA_MODELS；grok.com 路由零改动
    ModelSpec("grok-imagine-image-quality-console",     ModeId.CONSOLE,  Tier.BASIC, Capability.IMAGE | Capability.IMAGE_EDIT, True, "Grok Imagine Image Quality (Console)"),
    ModelSpec("grok-imagine-image-console",             ModeId.CONSOLE,  Tier.BASIC, Capability.IMAGE | Capability.IMAGE_EDIT, True, "Grok Imagine Image (Console)"),
    ModelSpec("grok-imagine-video-console",             ModeId.CONSOLE,  Tier.BASIC, Capability.VIDEO,      True, "Grok Imagine Video (Console)"),

    # === Build (grok.com build 端点) ==========================================
    # 上游真实名 + 历史自创名（超集兼容）；账号级目录由 build_models 远程发现补充
    ModelSpec("grok-4.5",              ModeId.BUILD, Tier.SUPER, Capability.BUILD, True, "Grok 4.5 (Build)"),
    ModelSpec("grok-4.5-mini",         ModeId.BUILD, Tier.SUPER, Capability.BUILD, True, "Grok 4.5 Mini (Build)"),
    ModelSpec("grok-4.5-build-free",   ModeId.BUILD, Tier.BASIC, Capability.BUILD, True, "Grok 4.5 Free (Build)"),
    ModelSpec("grok-4.5-latest",       ModeId.BUILD, Tier.SUPER, Capability.BUILD, True, "Grok 4.5 Latest (Build)"),
    ModelSpec("grok-build-latest",     ModeId.BUILD, Tier.SUPER, Capability.BUILD, True, "Grok Build Latest"),
)
# fmt: on

# ---------------------------------------------------------------------------
# Internal lookup structures — built once at import time.
# ---------------------------------------------------------------------------

_BY_NAME: dict[str, ModelSpec] = {m.model_name: m for m in MODELS}

# Reasoning-effort aliases → canonical registered model (Go 1edc9fbe).
# Exact-name matches in MODELS win; only non-registered aliases live here
# (e.g. "grok-4.20-0309-reasoning-low" → "grok-4.20-0309-reasoning-console").
ALIASES: dict[str, str] = {
    "grok-4.20-0309-reasoning-low": "grok-4.20-0309-reasoning-console",
    "grok-4.20-0309-reasoning-medium": "grok-4.20-0309-reasoning-console",
    "grok-4.20-0309-reasoning-high": "grok-4.20-0309-reasoning-console",
}

_BY_CAP: dict[int, list[ModelSpec]] = {}
for _m in MODELS:
    _BY_CAP.setdefault(int(_m.capability), []).append(_m)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get(model_name: str) -> ModelSpec | None:
    """Return the spec for *model_name*, or ``None`` if not registered."""
    return _BY_NAME.get(model_name)


def resolve_alias(model_name: str) -> str:
    """Return the canonical registered name for *model_name*; as-is if not an alias."""
    return ALIASES.get(model_name, model_name)


def resolve(model_name: str) -> ModelSpec:
    """Return the spec for *model_name* (exact match, then alias map); raise ``ValueError`` if unknown."""
    spec = _BY_NAME.get(model_name) or _BY_NAME.get(resolve_alias(model_name))
    if spec is None:
        raise ValueError(f"Unknown model: {model_name!r}")
    return spec


def list_enabled() -> list[ModelSpec]:
    """Return all enabled models in registration order."""
    return [m for m in MODELS if m.enabled]


def list_by_capability(cap: Capability) -> list[ModelSpec]:
    """Return enabled models that include *cap* in their capability mask."""
    return [m for m in MODELS if m.enabled and bool(m.capability & cap)]


__all__ = [
    "MODELS",
    "ALIASES",
    "get",
    "resolve",
    "resolve_alias",
    "list_enabled",
    "list_by_capability",
]
