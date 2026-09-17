from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Provider(StrEnum):
    IMD = "imd"
    CWC = "cwc"
    ULB = "ulb"
    FLOOD_HUB = "flood_hub"


class Metric(StrEnum):
    RAINFALL_MM_15M = "rainfall_mm_15m"
    RIVER_STAGE_M = "river_stage_m"
    PUMP_RUNNING = "pump_running"
    FORECAST_PROBABILITY = "forecast_probability"


class QualityStatus(StrEnum):
    ACCEPTED = "accepted"
    FLAGGED = "flagged"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Observation:
    source: Provider
    external_id: str
    metric: Metric
    value: float
    unit: str
    observed_at: datetime
    received_at: datetime
    latitude: float
    longitude: float
    cell_id: str
    status: QualityStatus = QualityStatus.ACCEPTED
    quality_flags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.received_at.tzinfo is None:
            raise ValueError("observation timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class StoredObservation:
    id: int
    observation: Observation
    created: bool


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    cell_id: str
    as_of: datetime
    rainfall_mm_1h: float
    rainfall_mm_3h: float
    rainfall_mm_6h: float
    rainfall_mm_24h: float
    rainfall_mm_72h: float
    river_stage_m: float | None
    river_stage_rate_m_per_h: float | None
    pump_running: bool | None
    forecast_probability: float | None
    flagged_observation_count: int
    observation_count: int


def utc_now() -> datetime:
    return datetime.now(UTC)
