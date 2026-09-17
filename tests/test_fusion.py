from __future__ import annotations

import pytest

from dhara.fusion import (
    AlertTier,
    DivergenceDirection,
    FusionEngine,
    FusionPolicy,
)


def test_hysteresis_requires_two_cycles_up_and_three_down() -> None:
    fusion = FusionEngine()
    first_up = fusion.evaluate("cell", sensor_confidence=0.95, crowd_confidence=0.85)
    second_up = fusion.evaluate("cell", sensor_confidence=0.95, crowd_confidence=0.85)

    assert first_up.committed_tier is AlertTier.MONITOR
    assert second_up.committed_tier is AlertTier.WARNING

    first_down = fusion.evaluate("cell", sensor_confidence=0.0, crowd_confidence=0.0)
    second_down = fusion.evaluate("cell", sensor_confidence=0.0, crowd_confidence=0.0)
    third_down = fusion.evaluate("cell", sensor_confidence=0.0, crowd_confidence=0.0)
    assert first_down.committed_tier is AlertTier.WARNING
    assert second_down.committed_tier is AlertTier.WARNING
    assert third_down.committed_tier is AlertTier.MONITOR


def test_divergence_preserves_direction_and_diagnostic_question() -> None:
    fusion = FusionEngine()
    sensor_led = fusion.evaluate("sensor", sensor_confidence=0.9, crowd_confidence=0.1)
    crowd_led = fusion.evaluate("crowd", sensor_confidence=0.1, crowd_confidence=0.9)

    assert sensor_led.divergence_direction is DivergenceDirection.SENSOR_LED
    assert crowd_led.divergence_direction is DivergenceDirection.CROWD_LED
    assert sensor_led.requires_human_review is True
    assert crowd_led.diagnostic_question is not None


def test_governance_gate_blocks_unilateral_warning() -> None:
    fusion = FusionEngine(FusionPolicy(warning_threshold=0.4))
    decision = fusion.evaluate("cell", sensor_confidence=0.9, crowd_confidence=0.0)

    assert decision.candidate_tier is AlertTier.WATCH
    assert decision.governance_gate_applied is True
    assert decision.requires_human_review is True


def test_fusion_rejects_unbounded_confidence() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        FusionEngine().evaluate("cell", sensor_confidence=1.1, crowd_confidence=0.5)
