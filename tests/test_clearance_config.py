"""resolve_clearance_config must derive cf_clearance from cf_cookies.

Schema only defines proxy.clearance.cf_cookies (and legacy proxy.cf_cookies),
so the flat proxy.cf_clearance / proxy.clearance.cf_clearance keys never
exist — the cf_clearance field used to be always empty.
"""

from app.control.proxy.config import resolve_clearance_config


class _StubCfg:
    def __init__(self, data: dict):
        self.data = data

    def get_str(self, key: str, default: str = "") -> str:
        value = self.data.get(key, default)
        return str(value) if value is not None else default


def test_extracts_cf_clearance_from_cf_cookies():
    cfg = _StubCfg({"proxy.clearance.cf_cookies": "cf_clearance=abc123; foo=bar"})

    result = resolve_clearance_config(cfg)

    assert result.cf_clearance == "abc123"


def test_extracts_short_cf_clearance_from_cf_cookies():
    cfg = _StubCfg({"proxy.clearance.cf_cookies": "cf_clearance=xyz"})

    result = resolve_clearance_config(cfg)

    assert result.cf_clearance == "xyz"


def test_legacy_flat_key_still_works_without_cf_cookies():
    cfg = _StubCfg({"proxy.cf_clearance": "legacy-val"})

    result = resolve_clearance_config(cfg)

    assert result.cf_clearance == "legacy-val"


def test_empty_when_no_config():
    result = resolve_clearance_config(_StubCfg({}))

    assert result.cf_clearance == ""


def test_cf_cookies_passthrough_and_extraction_together():
    cfg = _StubCfg({"proxy.clearance.cf_cookies": "other=1; cf_clearance=456"})

    result = resolve_clearance_config(cfg)

    assert result.cf_cookies == "other=1; cf_clearance=456"
    assert result.cf_clearance == "456"
