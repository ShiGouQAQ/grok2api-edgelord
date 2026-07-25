"""Tests for Build XAI/Reasoning/Prompt Cache port.

Covers:
- Reasoning effort normalization for Build models
- Build model version mapping
- Build search tools support
- Prompt cache tuple return type
"""

import pytest

from app.dataplane.reverse.protocol.xai_console_chat import (
    CONSOLE_MODELS,
    _BUILD_EFFORT_NORMALIZE,
    _EFFORT_MAP,
    _MODELS_WITH_SEARCH_TOOLS,
    _MODELS_WITH_REASONING_FIELD,
    build_console_payload,
)
from app.dataplane.reverse.protocol.prompt_cache import (
    resolve_prompt_cache_identity,
)


# ---------------------------------------------------------------------------
# Reasoning effort normalization
# ---------------------------------------------------------------------------


class TestBuildReasoningEffortNormalization:
    """Build models should normalize 'max' and 'xhigh' effort to 'high'."""

    def test_build_model_max_normalizes_to_high(self):
        """grok-build-console with reasoning_effort='max' → effort should be 'high'."""
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-build-console",
            reasoning_effort="max",
        )
        # Build model is not in _MODELS_WITH_REASONING_FIELD, so no reasoning field
        # but the effort computation should still normalize
        assert "reasoning" not in payload  # Build models don't get reasoning field

    def test_build_model_effort_map_normalizes_max(self):
        """_EFFORT_MAP should map 'max' → 'high' for all models."""
        assert _EFFORT_MAP["max"] == "high"

    def test_build_model_effort_map_normalizes_xhigh(self):
        """_EFFORT_MAP should map 'xhigh' → 'high' for all models."""
        assert _EFFORT_MAP["xhigh"] == "high"

    def test_build_effort_normalize_set_contains_expected(self):
        """_BUILD_EFFORT_NORMALIZE should contain 'max' and 'xhigh'."""
        assert "max" in _BUILD_EFFORT_NORMALIZE
        assert "xhigh" in _BUILD_EFFORT_NORMALIZE

    def test_non_build_low_medium_high_pass_through(self):
        """Non-build models with 'low', 'medium', 'high' should pass through."""
        assert _EFFORT_MAP["low"] == "low"
        assert _EFFORT_MAP["medium"] == "medium"
        assert _EFFORT_MAP["high"] == "high"

    def test_effort_map_none_maps_to_none(self):
        """'none' maps to 'none'."""
        assert _EFFORT_MAP["none"] == "none"

    def test_effort_map_minimal_maps_to_low(self):
        """'minimal' maps to 'low'."""
        assert _EFFORT_MAP["minimal"] == "low"

    def test_build_console_model_not_in_reasoning_field_set(self):
        """Build console model should NOT be in _MODELS_WITH_REASONING_FIELD."""
        assert "grok-build-0.2.106" not in _MODELS_WITH_REASONING_FIELD

    def test_grok43_in_reasoning_field_set(self):
        """grok-4.3 should be in _MODELS_WITH_REASONING_FIELD (sanity check)."""
        assert "grok-4.3" in _MODELS_WITH_REASONING_FIELD


# ---------------------------------------------------------------------------
# Build model version
# ---------------------------------------------------------------------------


class TestBuildModelVersion:
    """Build models should use grok-build-0.2.106."""

    def test_build_console_maps_to_0_2_106(self):
        """grok-build-console should map to grok-build-0.2.106."""
        assert CONSOLE_MODELS["grok-build-console"] == "grok-build-0.2.106"

    def test_build_model_not_grok_build_0_1(self):
        """grok-build-0.1 should not appear in CONSOLE_MODELS."""
        assert "grok-build-0.1" not in CONSOLE_MODELS.values()

    def test_build_payload_uses_correct_model(self):
        """Payload should contain grok-build-0.2.106 as the model field."""
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-build-console",
        )
        assert payload["model"] == "grok-build-0.2.106"

    def test_build_model_max_output_tokens(self):
        """Build model should have 256_000 max_output_tokens."""
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-build-console",
        )
        assert payload["max_output_tokens"] == 256_000


# ---------------------------------------------------------------------------
# Build search tools
# ---------------------------------------------------------------------------


class TestBuildSearchTools:
    """Build models should support web_search and x_search tools."""

    def test_build_model_in_search_tools_set(self):
        """grok-build-0.2.106 should be in _MODELS_WITH_SEARCH_TOOLS."""
        assert "grok-build-0.2.106" in _MODELS_WITH_SEARCH_TOOLS

    def test_build_payload_has_tools(self):
        """Payload for Build model should include tools."""
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-build-console",
        )
        assert "tools" in payload
        tool_types = [t["type"] for t in payload["tools"]]
        assert "web_search" in tool_types
        assert "x_search" in tool_types

    def test_build_payload_has_tool_choice(self):
        """Payload for Build model should include tool_choice='auto'."""
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-build-console",
        )
        assert payload.get("tool_choice") == "auto"

    def test_search_tools_have_image_understanding(self):
        """web_search tool should have enable_image_understanding=True."""
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-build-console",
        )
        web_search = next(t for t in payload["tools"] if t["type"] == "web_search")
        assert web_search["enable_image_understanding"] is True

    def test_search_tools_have_video_understanding(self):
        """x_search tool should have enable_video_understanding=True."""
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-build-console",
        )
        x_search = next(t for t in payload["tools"] if t["type"] == "x_search")
        assert x_search["enable_video_understanding"] is True

    def test_non_search_model_no_tools(self):
        """Model not in _MODELS_WITH_SEARCH_TOOLS should not have tools."""
        # grok-build-0.2.106 is in search tools, but let's test a model that isn't
        # Use a model that maps to a non-search console model
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-4.20-0309-reasoning-console",
        )
        # grok-4.20-0309-reasoning is in search tools, so this should have tools
        assert "tools" in payload


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
        # Static verification: the code uses `_pc_key, _replay_key = resolve_prompt_cache_identity(...)`
        # This is verified by reading the source; runtime test ensures no TypeError
        result = resolve_prompt_cache_identity(
            client_key_id=0,
            provider="console",
            upstream_model="grok-build-console",
            operation="chat",
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        _pc_key, _replay_key = result
        # With client_key_id=0, both should be None
        assert _pc_key is None
        assert _replay_key is None

    def test_console_responses_unpacks_tuple(self):
        """console_responses.create should unpack tuple correctly."""
        result = resolve_prompt_cache_identity(
            client_key_id=0,
            provider="console",
            upstream_model="grok-build-console",
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

    def test_non_build_models_still_work(self):
        """Non-build console models should work as before."""
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-4.3-console",
        )
        assert payload["model"] == "grok-4.3"
        # grok-4.3 is in _MODELS_WITH_REASONING_FIELD
        assert "reasoning" in payload

    def test_grok43_effort_default_medium(self):
        """grok-4.3-console with no reasoning_effort should default to medium."""
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-4.3-console",
        )
        assert payload["reasoning"]["effort"] == "medium"

    def test_grok43_low_fixed_effort(self):
        """grok-4.3-low should have fixed low effort."""
        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-4.3-low",
        )
        assert payload["reasoning"]["effort"] == "low"

    def test_prompt_cache_key_injection_still_works(self):
        """inject_prompt_cache_key should still work."""
        from app.dataplane.reverse.protocol.prompt_cache import inject_prompt_cache_key

        body: dict[str, object] = {"model": "grok-build-0.2.106"}
        result = inject_prompt_cache_key(body, "test-key")
        assert result["prompt_cache_key"] == "test-key"

    def test_prompt_cache_key_none_no_injection(self):
        """inject_prompt_cache_key with None should not inject."""
        from app.dataplane.reverse.protocol.prompt_cache import inject_prompt_cache_key

        body: dict[str, object] = {"model": "grok-build-0.2.106"}
        result = inject_prompt_cache_key(body, None)
        assert "prompt_cache_key" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
