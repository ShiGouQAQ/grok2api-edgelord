import pytest

from app.dataplane.reverse.routing.fallback import (
    BuildRouteMode,
    inference_base_for_operation,
    is_definitive_account_block_body,
    should_probe_xai_fallback,
)


def test_inference_base_auto_super_not_bot():
    url = inference_base_for_operation(BuildRouteMode.AUTO, is_super=True, is_bot=False)
    assert "cli-chat-proxy.grok.com" in url


def test_inference_base_auto_bot():
    url = inference_base_for_operation(BuildRouteMode.AUTO, is_super=True, is_bot=True)
    assert "api.x.ai" in url


def test_inference_base_xai_forced():
    url = inference_base_for_operation(BuildRouteMode.XAI, is_super=False, is_bot=False)
    assert "api.x.ai" in url


def test_inference_base_build_forced():
    url = inference_base_for_operation(
        BuildRouteMode.BUILD, is_super=False, is_bot=False
    )
    assert "cli-chat-proxy.grok.com" in url


def test_should_probe_xai_fallback_403_super():
    assert should_probe_xai_fallback(403, BuildRouteMode.AUTO, True, "responses")


def test_should_probe_xai_fallback_free_skip():
    assert not should_probe_xai_fallback(403, BuildRouteMode.AUTO, False, "responses")


def test_should_probe_xai_fallback_not_403():
    assert not should_probe_xai_fallback(200, BuildRouteMode.AUTO, True, "responses")


def test_should_probe_xai_fallback_build_mode():
    assert not should_probe_xai_fallback(403, BuildRouteMode.BUILD, True, "responses")


def test_is_definitive_account_block_blocked_user():
    assert is_definitive_account_block_body('{"error":{"code":"blocked-user"}}')


def test_is_definitive_account_block_user_blocked():
    assert is_definitive_account_block_body('{"error":{"message":"user is blocked"}}')


def test_is_definitive_account_block_non_block():
    assert not is_definitive_account_block_body('{"error":{"code":"rate_limit"}}')


def test_is_definitive_account_block_empty():
    assert not is_definitive_account_block_body("")


def test_is_definitive_account_block_invalid_json():
    assert not is_definitive_account_block_body("not json")
