"""Unit tests for resolve_prompt_cache_identity and inject_prompt_cache_key.

Ported from Go resolvePromptCacheIdentity + injectPromptCacheKey.
"""

import hashlib

import pytest

from app.dataplane.reverse.protocol.prompt_cache import (
    extract_prompt_cache_seed,
    extract_soft_session,
    inject_prompt_cache_key,
    merge_usage,
    resolve_prompt_cache_identity,
)


class TestResolvePromptCacheIdentity:
    """resolve_prompt_cache_identity 纯函数测试 — 确定性子串 + 碰撞隔离性"""

    # --- 返回 None 的边界 ---

    def test_returns_none_when_all_empty(self):
        assert resolve_prompt_cache_identity() == (None, None)

    def test_returns_none_when_seed_empty(self):
        assert resolve_prompt_cache_identity(
            client_key_id=1, provider="build", upstream_model="grok-4.5"
        ) == (None, None)

    def test_returns_none_when_client_key_zero(self):
        assert resolve_prompt_cache_identity(
            client_key_id=0,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="k",
        ) == (None, None)

    def test_returns_none_when_provider_empty(self):
        assert resolve_prompt_cache_identity(
            client_key_id=1,
            provider="",
            upstream_model="grok-4.5",
            explicit_key="k",
        ) == (None, None)

    def test_returns_none_when_model_empty(self):
        assert resolve_prompt_cache_identity(
            client_key_id=1, provider="build", upstream_model="", explicit_key="k"
        ) == (None, None)

    def test_returns_none_with_only_session_seed(self):
        """session_seed 填充 seed 但 client_key=0 → None"""
        assert resolve_prompt_cache_identity(session_seed="sess-1") == (None, None)

    # --- 种子选取优先级 ---

    def test_prefers_explicit_key_over_session_seed(self):
        r1 = resolve_prompt_cache_identity(
            client_key_id=1,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="client-key",
        )
        r2 = resolve_prompt_cache_identity(
            client_key_id=1,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="client-key",
            session_seed="session-1",
        )
        assert r1 == r2
        assert r1 is not None and r1[0] is not None

    def test_falls_back_to_session_seed(self):
        r = resolve_prompt_cache_identity(
            client_key_id=1,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        assert r is not None and r[0] is not None

    def test_explicit_key_overrides_different_session(self):
        """显式 key 相同即使 session 不同也返回相同 identity"""
        r1 = resolve_prompt_cache_identity(
            client_key_id=1,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="same-key",
            session_seed="sess-a",
        )
        r2 = resolve_prompt_cache_identity(
            client_key_id=1,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="same-key",
            session_seed="sess-b",
        )
        assert r1 == r2

    # --- 确定性 ---

    def test_deterministic_same_input_same_output(self):
        r1 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        r2 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        assert r1 == r2

    # --- 跨租户碰撞隔离性 ---

    def test_different_client_key_produces_different_identity(self):
        r1 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        r2 = resolve_prompt_cache_identity(
            client_key_id=8,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        assert r1 != r2

    def test_different_provider_produces_different_identity(self):
        r1 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        r2 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="console",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        assert r1 != r2

    def test_different_model_produces_different_identity(self):
        r1 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        r2 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.3",
            session_seed="session-1",
        )
        assert r1 != r2

    def test_different_operation_produces_different_identity(self):
        r1 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            operation="messages",
            session_seed="session-1",
        )
        r2 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            operation="responses",
            session_seed="session-1",
        )
        assert r1 != r2

    def test_different_session_seed_produces_different_identity(self):
        r1 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        r2 = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-2",
        )
        assert r1 != r2

    # --- 输出格式 ---

    def test_output_format_is_uuid_like(self):
        r = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        assert r is not None and r[0] is not None
        assert len(r[0]) == 36  # 8-4-4-4-12
        parts = r[0].split("-")
        assert len(parts) == 5
        assert all(len(p) in (4, 8, 12) for p in parts)

    def test_hex_characters_only(self):
        r = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        assert r is not None and r[0] is not None
        hex_chars = set("0123456789abcdef-")
        assert all(c in hex_chars for c in r[0])

    # --- 输入归一化 ---

    def test_model_case_insensitive(self):
        upper = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="GROK-4.5",
            session_seed="s",
        )
        lower = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="s",
        )
        assert upper == lower

    def test_model_whitespace_trimmed(self):
        spaced = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="  grok-4.5  ",
            session_seed="s",
        )
        normal = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="s",
        )
        assert spaced == normal

    def test_seed_whitespace_trimmed(self):
        spaced = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="  key  ",
        )
        trimmed = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            explicit_key="key",
        )
        assert spaced == trimmed

    # --- operation 默认值 ---

    def test_operation_defaults_to_responses(self):
        default = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="s",
        )
        explicit = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            operation="responses",
            session_seed="s",
        )
        assert default == explicit

    def test_empty_operation_uses_default(self):
        r = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            operation="",
            session_seed="s",
        )
        assert r is not None and r[0] is not None


class TestInjectPromptCacheKey:
    """inject_prompt_cache_key 纯函数测试"""

    def test_returns_body_unchanged_when_key_none(self):
        body: dict[str, object] = {"model": "grok-4.5"}
        assert inject_prompt_cache_key(body, None) is body

    def test_returns_body_unchanged_when_key_empty(self):
        body: dict[str, object] = {"model": "grok-4.5"}
        assert inject_prompt_cache_key(body, "") is body

    def test_returns_body_unchanged_when_key_whitespace(self):
        body: dict[str, object] = {"model": "grok-4.5"}
        assert inject_prompt_cache_key(body, "  ") is body

    def test_injects_key_when_not_present(self):
        body: dict[str, object] = {"model": "grok-4.5"}
        result = inject_prompt_cache_key(body, "my-cache-key")
        assert result["prompt_cache_key"] == "my-cache-key"
        assert result["model"] == "grok-4.5"

    def test_does_not_overwrite_existing_key(self):
        body: dict[str, object] = {"model": "grok-4.5", "prompt_cache_key": "existing"}
        result = inject_prompt_cache_key(body, "new-key")
        assert result["prompt_cache_key"] == "existing"  # unchanged

    def test_mutates_original_dict_and_returns_it(self):
        """修改原始 dict 并返回同一个对象引用（与 Go 的 map 就地修改一致）"""
        body: dict[str, object] = {"model": "grok-4.5"}
        result = inject_prompt_cache_key(body, "k")
        assert result is body  # 同一个引用
        assert body["prompt_cache_key"] == "k"  # 原地修改

    def test_returned_dict_includes_all_original_fields(self):
        body: dict[str, object] = {"a": 1, "b": "two"}
        result = inject_prompt_cache_key(body, "cache-key")
        assert result["a"] == 1
        assert result["b"] == "two"

    def test_key_stripped_before_injection(self):
        body: dict[str, object] = {"model": "grok-4.5"}
        result = inject_prompt_cache_key(body, "  spaced-key  ")
        assert result["prompt_cache_key"] == "spaced-key"


class TestExtractPromptCacheSeed:
    def test_returns_none_when_nothing(self):
        assert extract_prompt_cache_seed() is None

    def test_returns_none_when_empty_headers(self):
        assert extract_prompt_cache_seed(headers={}) is None

    def test_extracts_from_header_priority_order(self):
        headers = {
            "x-client-session-id": "first",
            "x-prompt-cache-key": "second",
        }
        assert extract_prompt_cache_seed(headers=headers) == "second"

    def test_extracts_from_x_grok_session_id(self):
        assert (
            extract_prompt_cache_seed(headers={"x-grok-session-id": "sess-123"})
            == "sess-123"
        )

    def test_extracts_from_session_id_header(self):
        assert extract_prompt_cache_seed(headers={"session_id": "sid-abc"}) == "sid-abc"

    def test_extracts_from_conversation_id_header(self):
        assert (
            extract_prompt_cache_seed(headers={"conversation_id": "conv-1"}) == "conv-1"
        )

    def test_extracts_from_codex_window_header(self):
        assert (
            extract_prompt_cache_seed(headers={"x-codex-window-id": "win-42"})
            == "win-42"
        )

    def test_extracts_from_claude_session_header(self):
        assert (
            extract_prompt_cache_seed(headers={"x-claude-session-id": "claude-sess"})
            == "claude-sess"
        )

    def test_body_fallback_when_no_headers(self):
        body = {"prompt_cache_key": "body-key"}
        assert extract_prompt_cache_seed(body=body) == "body-key"

    def test_body_session_id_field(self):
        body = {"session_id": "body-sid"}
        assert extract_prompt_cache_seed(body=body) == "body-sid"

    def test_body_sessionId_field(self):
        body = {"sessionId": "body-sid-camel"}
        assert extract_prompt_cache_seed(body=body) == "body-sid-camel"

    def test_header_takes_precedence_over_body(self):
        headers = {"x-prompt-cache-key": "hdr-key"}
        body = {"prompt_cache_key": "body-key"}
        assert extract_prompt_cache_seed(headers=headers, body=body) == "hdr-key"

    def test_skips_empty_whitespace_headers(self):
        headers = {"x-prompt-cache-key": "   "}
        assert extract_prompt_cache_seed(headers=headers) is None

    def test_skips_empty_body_fields(self):
        body = {"prompt_cache_key": "", "session_id": "  "}
        assert extract_prompt_cache_seed(body=body) is None

    def test_non_string_body_value_coerced(self):
        body = {"prompt_cache_key": 12345}
        assert extract_prompt_cache_seed(body=body) == "12345"


class TestExtractSoftSession:
    def test_returns_none_when_empty(self):
        assert extract_soft_session() is None

    def test_returns_none_when_no_messages(self):
        assert extract_soft_session(messages=[]) is None

    def test_system_message_as_anchor(self):
        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]
        result = extract_soft_session(messages=msgs)
        assert result is not None
        assert "You are helpful" in result

    def test_top_level_instructions_from_body(self):
        body = {"instructions": "Be concise"}
        result = extract_soft_session(body=body)
        assert result == "Be concise"

    def test_top_level_system_from_body(self):
        body = {"system": "System prompt text"}
        result = extract_soft_session(body=body)
        assert result == "System prompt text"

    def test_anthropic_style_system_list(self):
        body = {"system": [{"type": "text", "text": "Block text"}]}
        result = extract_soft_session(body=body)
        assert result == "Block text"

    def test_first_user_message_when_no_system(self):
        msgs = [
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
        ]
        result = extract_soft_session(messages=msgs)
        assert result == "What is 2+2?"

    def test_long_text_truncated(self):
        long_text = "x" * 500
        msgs = [{"role": "system", "content": long_text}]
        result = extract_soft_session(messages=msgs)
        assert result is not None
        assert len(result) <= 256

    def test_message_content_as_list(self):
        msgs = [
            {
                "role": "system",
                "content": [{"type": "text", "text": "System block"}],
            },
        ]
        result = extract_soft_session(messages=msgs)
        assert result == "System block"

    def test_deterministic(self):
        msgs = [{"role": "user", "content": "Hello"}]
        r1 = extract_soft_session(messages=msgs)
        r2 = extract_soft_session(messages=msgs)
        assert r1 == r2

    def test_different_messages_different_result(self):
        r1 = extract_soft_session(messages=[{"role": "user", "content": "A"}])
        r2 = extract_soft_session(messages=[{"role": "user", "content": "B"}])
        assert r1 != r2


class TestMergeUsage:
    def test_returns_base_when_override_empty(self):
        base = {"input_tokens": 10}
        assert merge_usage(base, {}) == base

    def test_returns_base_when_override_none(self):
        base = {"input_tokens": 10}
        assert merge_usage(base, None) == base

    def test_overwrites_nonzero_fields(self):
        base = {"input_tokens": 10, "output_tokens": 5}
        override = {"input_tokens": 20}
        result = merge_usage(base, override)
        assert result["input_tokens"] == 20
        assert result["output_tokens"] == 5

    def test_skips_zero_fields(self):
        base = {"input_tokens": 10}
        override = {"input_tokens": 0}
        result = merge_usage(base, override)
        assert result["input_tokens"] == 10

    def test_skips_none_fields(self):
        base = {"input_tokens": 10}
        override = {"input_tokens": None}
        result = merge_usage(base, override)
        assert result["input_tokens"] == 10

    def test_nested_dict_merge(self):
        base = {"cache": {"read": 5, "creation": 3}}
        override = {"cache": {"read": 10}}
        result = merge_usage(base, override)
        assert result["cache"]["read"] == 10
        assert result["cache"]["creation"] == 3

    def test_adds_new_fields(self):
        base = {"input_tokens": 10}
        override = {"output_tokens": 20}
        result = merge_usage(base, override)
        assert result["input_tokens"] == 10
        assert result["output_tokens"] == 20
