from app.control.model.enums import Capability, ModeId, Tier
from app.control.model.registry import get


def test_grok45_model_registered():
    spec = get("grok-4.5")
    assert spec is not None
    assert spec.is_build()


def test_build_model_tier_super():
    spec = get("grok-4.5")
    assert spec is not None
    assert spec.tier == Tier.SUPER


def test_build_model_capability():
    spec = get("grok-4.5")
    assert spec is not None
    assert bool(spec.capability & Capability.BUILD)


def test_build_model_mode_id():
    spec = get("grok-4.5")
    assert spec is not None
    assert spec.mode_id == ModeId.BUILD


def test_build_models_enabled():
    for name in ("grok-4.5", "grok-4.5-mini", "grok-4.5-build-free"):
        spec = get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.enabled, f"{name} not enabled"
