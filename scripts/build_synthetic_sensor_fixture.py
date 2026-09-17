from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dhara.geo import cell_for
from dhara.sensor_dataset import FEATURE_NAMES

FIELD_NAMES = ("event_id", "cell_id", "observed_at", "outcome", *FEATURE_NAMES)
LOCATIONS = (
    (18.52040, 73.85670),
    (18.50890, 73.84600),
    (18.53140, 73.84460),
)


def build_rows(seed: int = 42) -> list[dict[str, object]]:
    rng = random.Random(seed)
    rows: list[dict[str, object]] = []
    base_time = datetime(2024, 6, 15, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    for event_index in range(10):
        flood_event = event_index >= 5
        location = LOCATIONS[event_index % len(LOCATIONS)]
        cell_id = cell_for(*location)
        event_start = base_time + timedelta(days=event_index * 8)
        antecedent = 8.0 + event_index * 1.5
        for step in range(12):
            ramp = max(0, step - 3) if flood_event else max(0, step - 8) * 0.2
            rainfall_1h = max(0.0, rng.uniform(0, 2.5) + ramp * (3.8 if flood_event else 0.5))
            rainfall_3h = rainfall_1h * 2.0 + antecedent * 0.12 + rng.uniform(0, 2)
            rainfall_6h = rainfall_3h * 1.55 + antecedent * 0.2
            rainfall_24h = rainfall_6h + antecedent + ramp * 2.0
            rainfall_72h = rainfall_24h + 12 + event_index * 1.8
            stage_rate = 0.01 + ramp * (0.045 if flood_event else 0.003) + rng.uniform(-0.01, 0.01)
            river_stage = 2.7 + event_index * 0.04 + step * max(stage_rate, 0)
            forecast = 1 / (1 + math.exp(-(ramp - 3.2))) if flood_event else 0.04 + step * 0.005
            pump_running = 0.0 if flood_event and step >= 8 and event_index % 2 == 0 else 1.0
            outcome = int(flood_event and step >= 8)
            rows.append(
                {
                    "event_id": f"event-{event_index + 1:02d}",
                    "cell_id": cell_id,
                    "observed_at": (event_start + timedelta(hours=step)).isoformat(),
                    "outcome": outcome,
                    "rainfall_mm_1h": round(rainfall_1h, 4),
                    "rainfall_mm_3h": round(rainfall_3h, 4),
                    "rainfall_mm_6h": round(rainfall_6h, 4),
                    "rainfall_mm_24h": round(rainfall_24h, 4),
                    "rainfall_mm_72h": round(rainfall_72h, 4),
                    "river_stage_m": round(river_stage, 4),
                    "river_stage_rate_m_per_h": round(stage_rate, 4),
                    "pump_running": pump_running,
                    "forecast_probability": round(min(forecast, 0.99), 4),
                    "flagged_observation_count": int(rng.random() < 0.04),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="data/training/synthetic_sensor_episodes.csv",
    )
    args = parser.parse_args()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows = build_rows()
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELD_NAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
