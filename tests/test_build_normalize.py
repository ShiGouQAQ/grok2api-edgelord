"""Tests for Build request normalization — port of Go normalize_test.go (3721babd).

Covers:
- model-aware reasoning effort normalization (xhigh kept only for supported models)
- client_metadata stripping (Codex transport envelope)
- store/include safe defaults
"""

import pytest

from app.dataplane.reverse.protocol.xai_build import (
    build_build_responses_payload,
)

# ---------------------------------------------------------------------------
# Reasoning effort — mirrors Go TestNormalizeBuildReasoningEffort table
# ---------------------------------------------------------------------------

EFFORT_CASES = [
    # (model, effort, expected) — ported from Go normalize_test.go
    ("grok-4.5", "max", "high"),
    ("grok-4.5", "xhigh", "high"),
    ("grok-4.5", "MAX", "high"),
    ("grok-4.5", "high", "high"),
    ("grok-4.5", "medium", "medium"),
    ("grok-4.20-multi-agent-0309", "xhigh", "xhigh"),
    ("grok-4.20-multi-agent-0309", "XHIGH", "xhigh"),
    ("grok-4.20-multi-agent-0309", "max", "high"),
    ("future-model", "xhigh", "high"),
    (None, "xhigh", "high"),
]


@pytest.mark.parametrize("model,effort,want", EFFORT_CASES)
def test_normalize_build_reasoning_effort(model, effort, want):
    payload = build_build_responses_payload(
        model=model,
        messages=[{"role": "user", "content": "hi"}],
        reasoning_effort=effort,
    )
    assert payload["reasoning"]["effort"] == want


def test_build_payload_no_effort_when_none():
    payload = build_build_responses_payload(
        model="grok-4.5", messages=[{"role": "user", "content": "hi"}]
    )
    assert "reasoning" not in payload


# ---------------------------------------------------------------------------
# store/include defaults — mirrors Go applyBuildResponseDefaults
# ---------------------------------------------------------------------------


def test_build_payload_store_false_default():
    payload = build_build_responses_payload(
        model="grok-4.5", messages=[{"role": "user", "content": "hi"}]
    )
    assert payload["store"] is False


def test_build_payload_include_encrypted_content():
    payload = build_build_responses_payload(
        model="grok-4.5", messages=[{"role": "user", "content": "hi"}]
    )
    assert "reasoning.encrypted_content" in payload["include"]


# ---------------------------------------------------------------------------
# client_metadata — mirrors Go normalizeBuildRequestPayload
# ---------------------------------------------------------------------------


def test_build_payload_never_carries_client_metadata():
    """Codex transport envelope must never reach Build (stripped structurally)."""
    payload = build_build_responses_payload(
        model="grok-4.5", messages=[{"role": "user", "content": "hi"}]
    )
    assert "client_metadata" not in payload
