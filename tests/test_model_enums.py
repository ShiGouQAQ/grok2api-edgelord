"""Tests for BUILD capability and ModeId additions."""

from app.control.model.enums import (
    Capability,
    ModeId,
    Tier,
)
from app.control.model.spec import ModelSpec


class TestBuildCapability:
    def test_build_capability_value(self):
        assert Capability.BUILD.value == 128

    def test_build_mode_id_value(self):
        assert ModeId.BUILD.value == 6

    def test_capability_bitmask_independence(self):
        """BUILD must not collide with any existing capability bits."""
        existing = (
            Capability.CHAT
            | Capability.IMAGE
            | Capability.IMAGE_EDIT
            | Capability.VIDEO
            | Capability.VOICE
            | Capability.ASSET
            | Capability.CONSOLE_CHAT
        )
        assert Capability.BUILD & existing == 0


class TestModelSpecIsBuild:
    def test_model_spec_is_build(self):
        spec = ModelSpec(
            model_name="grok-build-test",
            mode_id=ModeId.BUILD,
            tier=Tier.BASIC,
            capability=Capability.BUILD,
            enabled=True,
            public_name="Build Test",
        )
        assert spec.is_build() is True
        assert spec.is_console_chat() is False

    def test_model_spec_without_build(self):
        spec = ModelSpec(
            model_name="grok-chat",
            mode_id=ModeId.AUTO,
            tier=Tier.BASIC,
            capability=Capability.CHAT,
            enabled=True,
            public_name="Chat",
        )
        assert spec.is_build() is False


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
