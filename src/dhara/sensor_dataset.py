from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

FEATURE_NAMES = (
    "rainfall_mm_1h",
    "rainfall_mm_3h",
    "rainfall_mm_6h",
    "rainfall_mm_24h",
    "rainfall_mm_72h",
    "river_stage_m",
    "river_stage_rate_m_per_h",
    "pump_running",
    "forecast_probability",
    "flagged_observation_count",
)

TREND_FEATURE_NAMES = (
    "rainfall_mm_6h",
    "rainfall_mm_24h",
    "river_stage_rate_m_per_h",
    "forecast_probability",
)


class SensorDatasetError(ValueError):
    """Raised when a Loop A training row violates the stable feature contract."""


@dataclass(frozen=True, slots=True)
class SensorExample:
    event_id: str
    cell_id: str
    observed_at: datetime
    outcome: int
    features: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SensorDataset:
    examples: tuple[SensorExample, ...]

    @property
    def matrix(self) -> NDArray[np.float64]:
        return np.asarray([example.features for example in self.examples], dtype=float)

    @property
    def outcomes(self) -> NDArray[np.int64]:
        return np.asarray([example.outcome for example in self.examples], dtype=int)

    @property
    def event_ids(self) -> tuple[str, ...]:
        return tuple(sorted({example.event_id for example in self.examples}))

    def event_mask(self, event_id: str) -> NDArray[np.bool_]:
        return np.asarray([example.event_id == event_id for example in self.examples])


def load_sensor_dataset(path: str | Path) -> SensorDataset:
    source = Path(path)
    examples: list[SensorExample] = []
    required = {"event_id", "cell_id", "observed_at", "outcome", *FEATURE_NAMES}
    with source.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise SensorDatasetError(f"dataset is missing columns: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            try:
                observed_at = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                outcome = int(row["outcome"])
                features = tuple(float(row[name]) for name in FEATURE_NAMES)
            except (TypeError, ValueError) as exc:
                raise SensorDatasetError(f"invalid value on row {row_number}") from exc
            if observed_at.tzinfo is None:
                raise SensorDatasetError(f"timestamp on row {row_number} must include a timezone")
            if outcome not in (0, 1):
                raise SensorDatasetError(f"outcome on row {row_number} must be 0 or 1")
            if not all(math.isfinite(value) for value in features):
                raise SensorDatasetError(f"features on row {row_number} must be finite")
            event_id = row["event_id"].strip()
            cell_id = row["cell_id"].strip()
            if not event_id or not cell_id:
                raise SensorDatasetError(
                    f"event and cell identifiers are required on row {row_number}"
                )
            examples.append(
                SensorExample(
                    event_id=event_id,
                    cell_id=cell_id,
                    observed_at=observed_at,
                    outcome=outcome,
                    features=features,
                )
            )
    if not examples:
        raise SensorDatasetError("dataset must contain at least one row")
    return SensorDataset(tuple(examples))


def feature_vector(values: dict[str, float]) -> NDArray[np.float64]:
    missing = set(FEATURE_NAMES) - set(values)
    if missing:
        raise SensorDatasetError(f"feature vector is missing: {sorted(missing)}")
    vector = np.asarray([float(values[name]) for name in FEATURE_NAMES], dtype=float)
    if not np.all(np.isfinite(vector)):
        raise SensorDatasetError("feature vector must contain only finite values")
    return vector
