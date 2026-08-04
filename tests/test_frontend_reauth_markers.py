"""Frontend assertion tests: reauth_required status markers in admin UI.

Locks the reauth_required surface (badge, filter chip, counts, restore action,
isInvalidStatus exclusion, i18n keys) with pure string/JSON assertions — no
browser required. Covers app/statics/admin/account.html + app/statics/i18n/*.json.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACCOUNT_HTML = ROOT / "app" / "statics" / "admin" / "account.html"
I18N_DIR = ROOT / "app" / "statics" / "i18n"
LOCALES = ["zh", "en", "ja", "de", "fr", "es"]


def _account_source() -> str:
    return ACCOUNT_HTML.read_text(encoding="utf-8")


def test_account_html_has_reauth_badge():
    """account.html renders a reauth_required badge, amber CSS, filter chip, counts, restore action."""
    src = _account_source()
    # badge mapping entry + its CSS class
    assert (
        "reauth_required:['badge-reauth', tr('account.status.reauthRequired', null, '需重新认证')]"
        in src
    )
    assert ".badge-reauth" in src
    # filter chip + count element + renderStatusFilters wiring + statusCounts init
    assert 'data-status="reauth_required"' in src
    assert 'id="fc-status-reauth"' in src
    assert (
        "setFilterCount('fc-status-reauth', view.statusCounts.reauth_required);" in src
    )
    assert "reauth_required: 0" in src
    # restore action on reauth rows reuses the existing disabled toggle (disabled=false)
    assert "isReauth" in src


def test_is_invalid_status_excludes_reauth():
    """reauth_required must NOT count as an invalid status (avoids delete-invalid sweeping it)."""
    src = _account_source()
    assert (
        "return !['active', 'cooling', 'disabled', 'reauth_required'].includes(status);"
        in src
    )


def test_i18n_reauth_key_all_locales():
    """Every locale ships account.status.reauthRequired."""
    for locale in LOCALES:
        data = json.loads((I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        assert data["account"]["status"]["reauthRequired"], locale
