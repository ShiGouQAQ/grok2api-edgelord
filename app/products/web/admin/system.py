"""System endpoints — Go→Python port of ``transport/http/system/handler.go``.

- GET  ``/admin/api/system``            → ``{publicApiBaseURL}``
- GET  ``/admin/api/system/version``    → ``{currentVersion, latestVersion, changelog}``
- POST ``/admin/api/system/update/check`` → ``{currentVersion, updateAvailable, latestVersion, changelog}``

Version info comes from ``pyproject.toml`` (``app.platform.meta``); an optional
``data/version_check.json`` cache (written by an external updater) may supply
``latestVersion``/``changelog``.
"""

import json
from typing import Any

from fastapi import APIRouter

from app.platform.config.snapshot import config
from app.platform.meta import get_project_version
from app.platform.paths import data_path

router = APIRouter(prefix="/system", tags=["Admin - System"])

_VERSION_CHECK_PATH = data_path("version_check.json")


def _version_check_cache() -> dict[str, Any]:
    """Read the optional version_check.json cache (missing/corrupt → empty)."""
    try:
        with open(_VERSION_CHECK_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _version_payload() -> dict[str, Any]:
    current = get_project_version()
    cache = _version_check_cache()
    return {
        "currentVersion": current,
        "latestVersion": str(cache.get("latestVersion") or current),
        "changelog": str(cache.get("changelog") or ""),
    }


@router.get("")
async def get_system() -> dict[str, Any]:
    public_api_base_url = config.get_str("app.app_url", "").strip().rstrip("/")
    return {"publicApiBaseURL": public_api_base_url}


@router.get("/version")
async def get_version() -> dict[str, Any]:
    return _version_payload()


@router.post("/update/check")
async def check_update() -> dict[str, Any]:
    # TODO(port): real update check is deliberately a stub — the production
    # server has no network dependency on upstream release metadata. When an
    # auto-update flow is wanted, wire `app.platform.update_check.get_latest_release_info()`
    # here and write data/version_check.json from its payload.
    payload = _version_payload()
    payload["updateAvailable"] = False
    return payload
