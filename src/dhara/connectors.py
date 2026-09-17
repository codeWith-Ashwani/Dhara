from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dhara.domain import Metric, Observation, Provider, utc_now
from dhara.geo import cell_for


class NormalisationError(ValueError):
    """Raised when a provider payload cannot be mapped to the canonical contract."""


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise NormalisationError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NormalisationError("timestamp must be valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise NormalisationError("timestamp must include a timezone")
    return parsed


def _received(envelope: Mapping[str, Any]) -> datetime:
    value = envelope.get("received_at")
    return _timestamp(value) if value is not None else utc_now()


def _observation(
    *,
    provider: Provider,
    external_id: Any,
    metric: Metric,
    value: Any,
    unit: str,
    observed_at: Any,
    received_at: datetime,
    latitude: Any,
    longitude: Any,
    metadata: Mapping[str, Any] | None = None,
) -> Observation:
    try:
        lat = float(latitude)
        lon = float(longitude)
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise NormalisationError("value and coordinates must be numeric") from exc
    external = str(external_id).strip()
    if not external:
        raise NormalisationError("external_id is required")
    try:
        h3_cell = cell_for(lat, lon)
    except ValueError as exc:
        raise NormalisationError(str(exc)) from exc
    return Observation(
        source=provider,
        external_id=external,
        metric=metric,
        value=numeric_value,
        unit=unit,
        observed_at=_timestamp(observed_at),
        received_at=received_at,
        latitude=lat,
        longitude=lon,
        cell_id=h3_cell,
        metadata=dict(metadata or {}),
    )


def _imd(envelope: Mapping[str, Any]) -> Observation:
    payload = envelope["payload"]
    return _observation(
        provider=Provider.IMD,
        external_id=payload["observation_id"],
        metric=Metric.RAINFALL_MM_15M,
        value=payload["rainfall_mm_15m"],
        unit="mm/15min",
        observed_at=payload["observed_at"],
        received_at=_received(envelope),
        latitude=payload["latitude"],
        longitude=payload["longitude"],
        metadata={"station_id": payload["station_id"]},
    )


def _cwc(envelope: Mapping[str, Any]) -> Observation:
    payload = envelope["payload"]
    return _observation(
        provider=Provider.CWC,
        external_id=payload["observation_id"],
        metric=Metric.RIVER_STAGE_M,
        value=payload["water_level_m"],
        unit="m",
        observed_at=payload["observed_at"],
        received_at=_received(envelope),
        latitude=payload["latitude"],
        longitude=payload["longitude"],
        metadata={"gauge_id": payload["gauge_id"]},
    )


def _ulb(envelope: Mapping[str, Any]) -> Observation:
    payload = envelope["payload"]
    if not isinstance(payload["pump_running"], bool):
        raise NormalisationError("pump_running must be a boolean")
    return _observation(
        provider=Provider.ULB,
        external_id=payload["observation_id"],
        metric=Metric.PUMP_RUNNING,
        value=1.0 if payload["pump_running"] else 0.0,
        unit="boolean",
        observed_at=payload["observed_at"],
        received_at=_received(envelope),
        latitude=payload["latitude"],
        longitude=payload["longitude"],
        metadata={"asset_id": payload["asset_id"]},
    )


def _flood_hub(envelope: Mapping[str, Any]) -> Observation:
    payload = envelope["payload"]
    return _observation(
        provider=Provider.FLOOD_HUB,
        external_id=payload["forecast_id"],
        metric=Metric.FORECAST_PROBABILITY,
        value=payload["flood_probability"],
        unit="probability",
        observed_at=payload["issued_at"],
        received_at=_received(envelope),
        latitude=payload["latitude"],
        longitude=payload["longitude"],
        metadata={"site_id": payload["site_id"], "horizon_hours": payload["horizon_hours"]},
    )


_ADAPTERS: dict[str, Callable[[Mapping[str, Any]], Observation]] = {
    Provider.IMD.value: _imd,
    Provider.CWC.value: _cwc,
    Provider.ULB.value: _ulb,
    Provider.FLOOD_HUB.value: _flood_hub,
}


def normalize(envelope: Mapping[str, Any]) -> Observation:
    provider = str(envelope.get("provider", ""))
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        raise NormalisationError(f"unsupported provider: {provider or '<missing>'}")
    try:
        return adapter(envelope)
    except KeyError as exc:
        raise NormalisationError(f"missing provider field: {exc.args[0]}") from exc
