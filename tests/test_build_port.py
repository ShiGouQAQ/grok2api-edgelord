"""Tests for Build prompt cache port.

Covers:
- Prompt cache tuple return type
- Prompt cache key injection
"""

import pytest

from app.dataplane.reverse.protocol.prompt_cache import (
    inject_prompt_cache_key,
    resolve_prompt_cache_identity,
)


# ---------------------------------------------------------------------------
# Prompt cache tuple return
# ---------------------------------------------------------------------------


class TestPromptCacheTupleReturn:
    """resolve_prompt_cache_identity should return (cache_key, replay_key) tuple."""

    def test_returns_tuple(self):
        """Return value should be a tuple of length 2."""
        result = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="s",
        )
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_returns_none_none_when_empty(self):
        """Empty inputs should return (None, None)."""
        result = resolve_prompt_cache_identity()
        assert result == (None, None)

    def test_replay_key_none_without_seed(self):
        """Without seed, replay_key should be None."""
        result = resolve_prompt_cache_identity(
            client_key_id=0,
            provider="build",
            upstream_model="grok-4.5",
        )
        assert result == (None, None)

    def test_replay_key_set_with_explicit_key(self):
        """With explicit_key, replay_key should be a string."""
        result = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="my-key",
        )
        cache_key, replay_key = result
        assert cache_key is not None
        assert replay_key is not None
        assert isinstance(replay_key, str)
        assert len(replay_key) == 36  # 8-4-4-4-12 format

    def test_replay_key_set_with_session_seed(self):
        """With session_seed, replay_key should be a string."""
        result = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        cache_key, replay_key = result
        assert cache_key is not None
        assert replay_key is not None
        assert isinstance(replay_key, str)

    def test_replay_key_deterministic(self):
        """Same inputs should produce same replay_key."""
        r1 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="key",
        )
        r2 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="key",
        )
        assert r1 == r2

    def test_replay_key_differs_from_cache_key(self):
        """replay_key should be different from cache_key."""
        result = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="key",
        )
        cache_key, replay_key = result
        assert cache_key != replay_key

    def test_different_client_keys_different_replay_keys(self):
        """Different client_key_id should produce different replay_keys."""
        r1 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="key",
        )
        r2 = resolve_prompt_cache_identity(
            client_key_id=8,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="key",
        )
        assert r1[1] != r2[1]

    def test_replay_key_hex_format(self):
        """replay_key should be hex characters in UUID-like format."""
        result = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="key",
        )
        _, replay_key = result
        assert replay_key is not None
        hex_chars = set("0123456789abcdef-")
        assert all(c in hex_chars for c in replay_key)


# ---------------------------------------------------------------------------
# Tuple unpacking in callers (static verification)
# ---------------------------------------------------------------------------


class TestTupleUnpackingCallers:
    """Verify callers correctly unpack (cache_key, replay_key) tuple."""

    def test_console_chat_unpacks_tuple(self):
        """console_chat.completions should unpack tuple correctly."""
        result = resolve_prompt_cache_identity(
            client_key_id=0,
            provider="console",
            upstream_model="grok-4.3",
            operation="chat",
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        _pc_key, _replay_key = result
        assert _pc_key is None
        assert _replay_key is None

    def test_console_responses_unpacks_tuple(self):
        """console_responses.create should unpack tuple correctly."""
        result = resolve_prompt_cache_identity(
            client_key_id=0,
            provider="console",
            upstream_model="grok-4.3",
            operation="responses",
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        _pc_key, _replay_key = result
        assert _pc_key is None
        assert _replay_key is None


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing behavior should remain unchanged."""

    def test_prompt_cache_key_injection_still_works(self):
        """inject_prompt_cache_key should still work."""
        body: dict[str, object] = {"model": "grok-4.3"}
        result = inject_prompt_cache_key(body, "test-key")
        assert result["prompt_cache_key"] == "test-key"

    def test_prompt_cache_key_none_no_injection(self):
        """inject_prompt_cache_key with None should not inject."""
        body: dict[str, object] = {"model": "grok-4.3"}
        result = inject_prompt_cache_key(body, None)
        assert "prompt_cache_key" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
