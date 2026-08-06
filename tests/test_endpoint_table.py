"""Tests for Build provider endpoint constants."""

from app.dataplane.reverse.runtime.endpoint_table import (
    BUILD_BASE,
    BUILD_BILLING,
    BUILD_RESPONSES,
)


def test_build_endpoints_exist():
    assert BUILD_BASE
    assert BUILD_RESPONSES
    assert BUILD_BILLING


def test_build_base_url():
    assert BUILD_BASE == "https://cli-chat-proxy.grok.com/v1"


def test_build_responses_formed_correctly():
    assert BUILD_RESPONSES == "https://cli-chat-proxy.grok.com/v1/responses"
