from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum, StrEnum


class AlertTier(IntEnum):
    MONITOR = 0
    ADVISORY = 1
    WATCH = 2
    WARNING = 3


class DivergenceDirection(StrEnum):
    SENSOR_LED = "sensor_led"
    CROWD_LED = "crowd_led"


@dataclass(frozen=True, slots=True)
class FusionPolicy:
    sensor_weight: float = 0.60
    corroboration_bonus: float = 0.15
    advisory_threshold: float = 0.35
    watch_threshold: float = 0.55
    warning_threshold: float = 0.75
    stream_warning_floor: float = 0.50
    divergence_threshold: float = 0.50
    escalation_cycles: int = 2
    deescalation_cycles: int = 3


@dataclass(frozen=True, slots=True)
class FusionDecision:
    cell_id: str
    sensor_confidence: float
    crowd_confidence: float
    fused_confidence: float
    candidate_tier: AlertTier
    committed_tier: AlertTier
    pending_cycles: int
    divergence: bool
    divergence_direction: DivergenceDirection | None
    governance_gate_applied: bool
    requires_human_review: bool
    requires_officer_signoff: bool
    diagnostic_question: str | None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["candidate_tier"] = self.candidate_tier.name.lower()
        result["committed_tier"] = self.committed_tier.name.lower()
        return result


@dataclass(frozen=True, slots=True)
class FusionPreview:
    sensor_confidence: float
    crowd_confidence: float
    fused_confidence: float
    candidate_tier: AlertTier
    divergence: bool
    divergence_direction: DivergenceDirection | None
    governance_gate_applied: bool
    requires_human_review: bool
    diagnostic_question: str | None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["candidate_tier"] = self.candidate_tier.name.lower()
        return result


@dataclass(slots=True)
class _CellFusionState:
    committed_tier: AlertTier = AlertTier.MONITOR
    pending_tier: AlertTier | None = None
    pending_cycles: int = 0


class FusionEngine:
    def __init__(self, policy: FusionPolicy | None = None) -> None:
        self.policy = policy or FusionPolicy()
        self._states: dict[str, _CellFusionState] = {}

    def evaluate(
        self,
        cell_id: str,
        *,
        sensor_confidence: float,
        crowd_confidence: float,
    ) -> FusionDecision:
        preview = self.preview(
            sensor_confidence=sensor_confidence,
            crowd_confidence=crowd_confidence,
        )
        candidate = preview.candidate_tier

        state = self._states.setdefault(cell_id, _CellFusionState())
        if candidate is state.committed_tier:
            state.pending_tier = None
            state.pending_cycles = 0
        else:
            if state.pending_tier is candidate:
                state.pending_cycles += 1
            else:
                state.pending_tier = candidate
                state.pending_cycles = 1
            required = (
                self.policy.escalation_cycles
                if candidate > state.committed_tier
                else self.policy.deescalation_cycles
            )
            if state.pending_cycles >= required:
                state.committed_tier = candidate
                state.pending_tier = None
                state.pending_cycles = 0

        return FusionDecision(
            cell_id=cell_id,
            sensor_confidence=preview.sensor_confidence,
            crowd_confidence=preview.crowd_confidence,
            fused_confidence=preview.fused_confidence,
            candidate_tier=candidate,
            committed_tier=state.committed_tier,
            pending_cycles=state.pending_cycles,
            divergence=preview.divergence,
            divergence_direction=preview.divergence_direction,
            governance_gate_applied=preview.governance_gate_applied,
            requires_human_review=preview.requires_human_review,
            requires_officer_signoff=state.committed_tier is AlertTier.WARNING,
            diagnostic_question=preview.diagnostic_question,
        )

    def preview(
        self,
        *,
        sensor_confidence: float,
        crowd_confidence: float,
    ) -> FusionPreview:
        """Compute policy output without advancing the cell hysteresis state."""
        sensor = _bounded(sensor_confidence)
        crowd = _bounded(crowd_confidence)
        beta = self.policy.sensor_weight
        gamma = self.policy.corroboration_bonus
        fused = (beta * sensor + (1 - beta) * crowd + gamma * sensor * crowd) / (
            1 + gamma
        )
        candidate = self._tier_for(fused)
        gate_applied = False
        human_review = False
        if candidate is AlertTier.WARNING and not (
            sensor >= self.policy.stream_warning_floor
            and crowd >= self.policy.stream_warning_floor
        ):
            candidate = AlertTier.WATCH
            gate_applied = True
            human_review = True

        difference = sensor - crowd
        divergence = abs(difference) > self.policy.divergence_threshold
        direction = None
        question = None
        if divergence:
            human_review = True
            if difference > 0:
                direction = DivergenceDirection.SENSOR_LED
                question = "Is low community evidence caused by time, coverage, or no local impact?"
            else:
                direction = DivergenceDirection.CROWD_LED
                question = (
                    "Do imagery, physical cause, and device independence support a local failure?"
                )

        return FusionPreview(
            sensor_confidence=round(sensor, 6),
            crowd_confidence=round(crowd, 6),
            fused_confidence=round(fused, 6),
            candidate_tier=candidate,
            divergence=divergence,
            divergence_direction=direction,
            governance_gate_applied=gate_applied,
            requires_human_review=human_review,
            diagnostic_question=question,
        )

    def _tier_for(self, fused: float) -> AlertTier:
        if fused >= self.policy.warning_threshold:
            return AlertTier.WARNING
        if fused >= self.policy.watch_threshold:
            return AlertTier.WATCH
        if fused >= self.policy.advisory_threshold:
            return AlertTier.ADVISORY
        return AlertTier.MONITOR


def _bounded(value: float) -> float:
    if not 0 <= value <= 1:
        raise ValueError("confidence values must be between 0 and 1")
    return float(value)
