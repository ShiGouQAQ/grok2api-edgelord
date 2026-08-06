"""Static URL table for all upstream XAI / Grok endpoints.

Canonical source of truth for every URL used by the reverse layer.
Protocol modules re-export the subset they need; transport modules
import from protocol — this file is the single shared reference.

NOTE: gRPC-Web endpoints (accept_tos, nsfw_mgmt) live on different
hosts (accounts.x.ai, grok.com with gRPC path), listed separately.
"""

BASE = "https://grok.com"
ASSETS_CDN = "https://assets.grok.com"
CONSOLE_BASE = "https://console.x.ai"

# ── App-chat (SSE streaming, new conversation) ──────────────────────────
CHAT = f"{BASE}/rest/app-chat/conversations/new"

# ── Asset management ─────────────────────────────────────────────────────
ASSETS_UPLOAD = f"{BASE}/rest/app-chat/upload-file"  # POST (base64 upload)
ASSETS_LIST = f"{BASE}/rest/assets"  # GET
ASSETS_DELETE = f"{BASE}/rest/assets-metadata"  # DELETE /{asset_id}
ASSETS_DOWNLOAD = ASSETS_CDN  # GET /{path}

# ── Rate limits (usage / quota sync) ─────────────────────────────────────
RATE_LIMITS = f"{BASE}/rest/rate-limits"  # POST

# ── gRPC-Web endpoints ──────────────────────────────────────────────────
ACCEPT_TOS = "https://accounts.x.ai/auth_mgmt.AuthManagement/SetTosAcceptedVersion"
NSFW_MGMT = f"{BASE}/auth_mgmt.AuthManagement/UpdateUserFeatureControls"

# ── Auth REST ────────────────────────────────────────────────────────────
SET_BIRTH = f"{BASE}/rest/auth/set-birth-date"  # POST

# ── Media (video) ────────────────────────────────────────────────────────
MEDIA_POST = f"{BASE}/rest/media/post/create"  # POST
VIDEO_UPSCALE = f"{BASE}/rest/media/video/upscale"  # POST

# ── Console API (console.x.ai) ───────────────────────────────────────────
# 使用 Bearer token 认证，与 grok.com SSO token 共享同一套凭证
CONSOLE_RESPONSES = f"{CONSOLE_BASE}/v1/responses"  # POST (OpenAI Responses API)
CONSOLE_USAGE = f"{CONSOLE_BASE}/v1/usage"  # GET (DPoP-authenticated quota fetch)

# ── Build provider (cli-chat-proxy.grok.com) ─────────────────────────────
BUILD_BASE = "https://cli-chat-proxy.grok.com/v1"
BUILD_RESPONSES = f"{BUILD_BASE}/responses"  # POST
BUILD_MODELS = f"{BUILD_BASE}/models"  # GET
BUILD_BILLING = f"{BUILD_BASE}/billing"  # GET


__all__ = [
    "ACCEPT_TOS",
    "ASSETS_CDN",
    "ASSETS_DELETE",
    "ASSETS_DOWNLOAD",
    "ASSETS_LIST",
    "ASSETS_UPLOAD",
    "BASE",
    "BUILD_BASE",
    "BUILD_BILLING",
    "BUILD_MODELS",
    "BUILD_RESPONSES",
    "CHAT",
    "CONSOLE_BASE",
    "CONSOLE_RESPONSES",
    "CONSOLE_USAGE",
    "MEDIA_POST",
    "NSFW_MGMT",
    "RATE_LIMITS",
    "SET_BIRTH",
    "VIDEO_UPSCALE",
]
