"""Tests for Build provider endpoint constants."""

from app.dataplane.reverse.runtime.endpoint_table import (
    BUILD_BASE,
    BUILD_BILLING,
    BUILD_RESPONSES,
    XAI_FALLBACK_BASE,
)


def test_build_endpoints_exist():
    assert BUILD_BASE
    assert BUILD_RESPONSES
    assert BUILD_BILLING
    assert XAI_FALLBACK_BASE


def test_build_base_url():
    assert BUILD_BASE == "https://cli-chat-proxy.grok.com/v1"


def test_xai_fallback_base_url():
    assert XAI_FALLBACK_BASE == "https://api.x.ai/v1"


def test_build_responses_formed_correctly():
    assert BUILD_RESPONSES == "https://cli-chat-proxy.grok.com/v1/responses"
