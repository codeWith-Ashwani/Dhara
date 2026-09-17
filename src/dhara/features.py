from __future__ import annotations

from datetime import datetime, timedelta

from dhara.domain import FeatureSnapshot, Metric, Observation, QualityStatus

_RAIN_WINDOWS = (1, 3, 6, 24, 72)


def build_feature_snapshot(
    observations: list[Observation], *, cell_id: str, as_of: datetime
) -> FeatureSnapshot:
    eligible = sorted(
        (item for item in observations if item.observed_at <= as_of),
        key=lambda item: (item.observed_at, item.external_id),
    )
    rainfall = [item for item in eligible if item.metric is Metric.RAINFALL_MM_15M]

    totals: dict[int, float] = {}
    for hours in _RAIN_WINDOWS:
        window_start = as_of - timedelta(hours=hours)
        totals[hours] = round(
            sum(item.value for item in rainfall if item.observed_at > window_start), 4
        )

    stage = [item for item in eligible if item.metric is Metric.RIVER_STAGE_M]
    river_stage = stage[-1].value if stage else None
    stage_rate = None
    if len(stage) >= 2:
        previous, latest = stage[-2], stage[-1]
        elapsed_hours = (latest.observed_at - previous.observed_at).total_seconds() / 3600
        if elapsed_hours > 0:
            stage_rate = round((latest.value - previous.value) / elapsed_hours, 4)

    pump = [item for item in eligible if item.metric is Metric.PUMP_RUNNING]
    forecast = [item for item in eligible if item.metric is Metric.FORECAST_PROBABILITY]
    return FeatureSnapshot(
        cell_id=cell_id,
        as_of=as_of,
        rainfall_mm_1h=totals[1],
        rainfall_mm_3h=totals[3],
        rainfall_mm_6h=totals[6],
        rainfall_mm_24h=totals[24],
        rainfall_mm_72h=totals[72],
        river_stage_m=river_stage,
        river_stage_rate_m_per_h=stage_rate,
        pump_running=bool(pump[-1].value) if pump else None,
        forecast_probability=forecast[-1].value if forecast else None,
        flagged_observation_count=sum(
            item.status is QualityStatus.FLAGGED for item in eligible
        ),
        observation_count=len(eligible),
    )
