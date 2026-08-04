"""Alias + reasoning-effort tests — port of Go 1edc9fbe.

Covers:
- registry alias → canonical model resolution (resolve / resolve_alias)
- ModelSpec.supports_reasoning flag
- console alias maps (CONSOLE_MODELS / _MODEL_FIXED_EFFORT) extension
- Build payload thinking semantics: "none" disables, effort enables adaptive
- effort normalization routed through the alias (xhigh guard, max→high)
"""

import pytest

from app.control.model.registry import ALIASES, get, resolve, resolve_alias
from app.dataplane.reverse.protocol.xai_build import (
    _normalize_reasoning_effort,
    build_build_responses_payload,
)
from app.dataplane.reverse.protocol.xai_console_chat import (
    CONSOLE_MODELS,
    _MODEL_FIXED_EFFORT,
    build_console_payload,
)


# ---------------------------------------------------------------------------
# Registry alias resolution
# ---------------------------------------------------------------------------


class TestRegistryAlias:
    def test_alias_resolves_to_canonical_model(self):
        spec = resolve("grok-4.20-0309-reasoning-low")
        assert spec.model_name == "grok-4.20-0309-reasoning-console"

    def test_exact_name_wins_over_alias(self):
        assert resolve("grok-4.3-low").model_name == "grok-4.3-low"

    def test_unknown_alias_raises(self):
        with pytest.raises(ValueError):
            resolve("grok-4.20-0309-reasoning-xhigh")

    def test_resolve_alias_identity_for_plain_model(self):
        assert resolve_alias("grok-4.5") == "grok-4.5"
        assert (
            resolve_alias("grok-4.20-0309-reasoning-low")
            == "grok-4.20-0309-reasoning-console"
        )

    def test_get_remains_exact_name_only(self):
        # routers rely on get() for exact registry lookups; aliases resolve
        # only through resolve()/resolve_alias()
        assert get("grok-4.20-0309-reasoning-low") is None
        assert get("grok-4.3-low") is not None


# ---------------------------------------------------------------------------
# supports_reasoning flag (Go catalog SupportsReasoning)
# ---------------------------------------------------------------------------


class TestSupportsReasoningFlag:
    @pytest.mark.parametrize(
        "name",
        [
            "grok-4.20-0309-reasoning",
            "grok-4.20-0309-reasoning-console",
            "grok-4.3-console",
            "grok-4.3-low",
            "grok-4.20-multi-agent-0309",
            "grok-4.20-multi-agent-xhigh",
        ],
    )
    def test_reasoning_models_flag_true(self, name):
        assert resolve(name).supports_reasoning is True

    @pytest.mark.parametrize(
        "name",
        [
            "grok-4.20-0309",
            "grok-4.20-0309-console",
            "grok-4.20-0309-non-reasoning-console",
        ],
    )
    def test_non_reasoning_models_flag_false(self, name):
        assert resolve(name).supports_reasoning is False


# ---------------------------------------------------------------------------
# Console alias maps — extension only, existing entries locked
# ---------------------------------------------------------------------------


class TestConsoleAliasMaps:
    def test_reasoning_aliases_map_to_upstream(self):
        assert (
            CONSOLE_MODELS["grok-4.20-0309-reasoning-low"] == "grok-4.20-0309-reasoning"
        )
        assert (
            CONSOLE_MODELS["grok-4.20-0309-reasoning-high"]
            == "grok-4.20-0309-reasoning"
        )

    def test_reasoning_aliases_pin_fixed_effort(self):
        assert _MODEL_FIXED_EFFORT["grok-4.20-0309-reasoning-low"] == "low"
        assert _MODEL_FIXED_EFFORT["grok-4.20-0309-reasoning-medium"] == "medium"
        assert _MODEL_FIXED_EFFORT["grok-4.20-0309-reasoning-high"] == "high"

    def test_registered_alias_fixed_effort_in_payload(self):
        payload = build_console_payload(
            model="grok-4.3-low", messages=[{"role": "user", "content": "hi"}]
        )
        assert payload["reasoning"]["effort"] == "low"

    def test_no_effort_defaults_to_medium(self):
        payload = build_console_payload(
            model="grok-4.3-console", messages=[{"role": "user", "content": "hi"}]
        )
        assert payload["reasoning"]["effort"] == "medium"

    def test_unknown_alias_falls_through(self):
        # not a registered alias/model: no reasoning injected, no crash
        payload = build_console_payload(
            model="grok-4.20-0309-reasoning-xhigh",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert "reasoning" not in payload


# ---------------------------------------------------------------------------
# Build payload — thinking semantics (Go rewriteAliasedModel port)
# ---------------------------------------------------------------------------


class TestBuildEffortNormalization:
    def test_none_disables_thinking(self):
        payload = build_build_responses_payload(
            model="grok-4.5",
            messages=[{"role": "user", "content": "hi"}],
            reasoning_effort="none",
        )
        assert payload["thinking"] == {"type": "disabled"}
        assert "reasoning" not in payload

    def test_effort_sets_reasoning_and_adaptive_thinking(self):
        payload = build_build_responses_payload(
            model="grok-4.5",
            messages=[{"role": "user", "content": "hi"}],
            reasoning_effort="low",
        )
        assert payload["reasoning"] == {"effort": "low"}
        assert payload["thinking"] == {"type": "adaptive"}

    def test_no_effort_sets_no_thinking(self):
        payload = build_build_responses_payload(
            model="grok-4.5", messages=[{"role": "user", "content": "hi"}]
        )
        assert "reasoning" not in payload
        assert "thinking" not in payload

    def test_max_maps_to_high_for_alias(self):
        assert (
            _normalize_reasoning_effort("max", "grok-4.20-0309-reasoning-low") == "high"
        )

    def test_xhigh_kept_only_for_supported_models(self):
        assert (
            _normalize_reasoning_effort("xhigh", "grok-4.20-multi-agent-0309")
            == "xhigh"
        )
        assert (
            _normalize_reasoning_effort("xhigh", "grok-4.20-0309-reasoning-low")
            == "high"
        )

    def test_unknown_alias_xhigh_defensive_high(self):
        assert (
            _normalize_reasoning_effort("xhigh", "grok-4.20-0309-reasoning-xhigh")
            == "high"
        )

    def test_non_reasoning_model_with_no_effort_keeps_reasoning_deleted(self):
        payload = build_build_responses_payload(
            model="grok-4.20-0309-non-reasoning",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert "reasoning" not in payload
