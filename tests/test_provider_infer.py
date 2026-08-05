"""Tests for SSO token → provider inference (provider_infer.py).

Console SSO JWTs (verified against production: 5673/5673 active tokens)
carry only a session_id claim and no exp — inference tags them grok_console
so refresh uses console /v1/usage instead of grok.com/rest/rate-limits.
"""

import json
from base64 import urlsafe_b64encode

import pytest

from app.control.account.provider_infer import decode_sso_jwt, infer_provider


def _jwt(payload: dict) -> str:
    def b64(obj: object) -> str:
        return urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64(payload)}.{b64({'sig': True})}"


class TestDecodeSsoJwt:
    def test_valid_jwt_returns_payload(self):
        assert decode_sso_jwt(_jwt({"session_id": "abc"})) == {"session_id": "abc"}

    def test_not_jwt_returns_none(self):
        assert decode_sso_jwt("sso-cookie-string") is None

    def test_short_token_returns_none(self):
        assert decode_sso_jwt("one-part") is None

    def test_garbage_payload_returns_none(self):
        assert decode_sso_jwt("a.!!!.c") is None


class TestInferProvider:
    def test_console_session_jwt(self):
        tok = _jwt({"session_id": "10d0706c-de09-4963-aa31-32fa9ce7593e"})
        assert infer_provider(tok) == "grok_console"

    def test_sid_claim_is_console(self):
        assert infer_provider(_jwt({"sid": "x"})) == "grok_console"

    def test_with_exp_is_web(self):
        tok = _jwt({"session_id": "abc", "exp": 1785935840})
        assert infer_provider(tok) is None

    def test_extra_claims_is_web(self):
        tok = _jwt({"session_id": "abc", "sub": "user-1"})
        assert infer_provider(tok) is None

    def test_opaque_token_is_web(self):
        assert infer_provider("sso=opaque-cookie") is None
