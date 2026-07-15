"""Unit tests for resolve_prompt_cache_identity and inject_prompt_cache_key.

Ported from Go resolvePromptCacheIdentity + injectPromptCacheKey.
"""

import hashlib

import pytest

from app.dataplane.reverse.protocol.prompt_cache import (
    inject_prompt_cache_key,
    resolve_prompt_cache_identity,
)


class TestResolvePromptCacheIdentity:
    """resolve_prompt_cache_identity 纯函数测试 — 确定性子串 + 碰撞隔离性"""

    # --- 返回 None 的边界 ---

    def test_returns_none_when_all_empty(self):
        assert resolve_prompt_cache_identity() is None

    def test_returns_none_when_seed_empty(self):
        assert (
            resolve_prompt_cache_identity(
                client_key_id=1, provider="build", upstream_model="grok-4.5"
            )
            is None
        )

    def test_returns_none_when_client_key_zero(self):
        assert (
            resolve_prompt_cache_identity(
                client_key_id=0,
                provider="build",
                upstream_model="grok-4.5",
                explicit_key="k",
            )
            is None
        )

    def test_returns_none_when_provider_empty(self):
        assert (
            resolve_prompt_cache_identity(
                client_key_id=1,
                provider="",
                upstream_model="grok-4.5",
                explicit_key="k",
            )
            is None
        )

    def test_returns_none_when_model_empty(self):
        assert (
            resolve_prompt_cache_identity(
                client_key_id=1, provider="build", upstream_model="", explicit_key="k"
            )
            is None
        )

    def test_returns_none_with_only_session_seed(self):
        """session_seed 填充 seed 但 client_key=0 → None"""
        assert resolve_prompt_cache_identity(session_seed="sess-1") is None

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
        assert r1 is not None

    def test_falls_back_to_session_seed(self):
        r = resolve_prompt_cache_identity(
            client_key_id=1,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        assert r is not None

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
        assert r is not None
        assert len(r) == 36  # 8-4-4-4-12
        parts = r.split("-")
        assert len(parts) == 5
        assert all(len(p) in (4, 8, 12) for p in parts)

    def test_hex_characters_only(self):
        r = resolve_prompt_cache_identity(
            client_key_id=7,
            provider="build",
            upstream_model="grok-4.5",
            session_seed="session-1",
        )
        assert r is not None
        hex_chars = set("0123456789abcdef-")
        assert all(c in hex_chars for c in r)

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
        assert r is not None


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
