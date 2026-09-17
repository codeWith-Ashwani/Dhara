from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from dhara.domain import Metric, Observation, Provider, QualityStatus

_EXPECTED_UNITS = {
    Metric.RAINFALL_MM_15M: "mm/15min",
    Metric.RIVER_STAGE_M: "m",
    Metric.PUMP_RUNNING: "boolean",
    Metric.FORECAST_PROBABILITY: "probability",
}

_VALID_RANGES = {
    Metric.RAINFALL_MM_15M: (0.0, 300.0),
    Metric.RIVER_STAGE_M: (-10.0, 100.0),
    Metric.PUMP_RUNNING: (0.0, 1.0),
    Metric.FORECAST_PROBABILITY: (0.0, 1.0),
}

_MAX_SOURCE_DELAY = {
    Provider.IMD: timedelta(minutes=45),
    Provider.CWC: timedelta(hours=3),
    Provider.ULB: timedelta(minutes=20),
    Provider.FLOOD_HUB: timedelta(hours=12),
}


def assess(observation: Observation) -> Observation:
    flags: list[str] = []
    rejected = False

    low, high = _VALID_RANGES[observation.metric]
    if not low <= observation.value <= high:
        flags.append("value_out_of_range")
        rejected = True
    if observation.unit != _EXPECTED_UNITS[observation.metric]:
        flags.append("unit_mismatch")
        rejected = True
    if observation.observed_at > observation.received_at + timedelta(minutes=5):
        flags.append("future_timestamp")
        rejected = True
    delay = observation.received_at - observation.observed_at
    if delay > _MAX_SOURCE_DELAY[observation.source]:
        flags.append("stale_source")

    if rejected:
        status = QualityStatus.REJECTED
    elif flags:
        status = QualityStatus.FLAGGED
    else:
        status = QualityStatus.ACCEPTED
    return replace(observation, status=status, quality_flags=tuple(flags))
