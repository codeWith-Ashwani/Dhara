from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dhara.features import build_feature_snapshot
from dhara.ingestion import IngestionService
from dhara.replay import replay_file
from dhara.repository import ObservationRepository

FIXTURE = Path(__file__).parents[1] / "data" / "replays" / "pune_ward14_48h.jsonl"


def test_replay_is_idempotent_and_materialises_features() -> None:
    repository = ObservationRepository(":memory:")
    service = IngestionService(repository)

    first = replay_file(FIXTURE, service)
    second = replay_file(FIXTURE, service)

    assert first.total == 18
    assert first.created == 18
    assert first.rejected == 0
    assert second.created == 0
    assert second.duplicates == 18
    assert repository.count() == 18

    observations = list(repository.all())
    cell_id = observations[0].cell_id
    snapshot = build_feature_snapshot(
        observations,
        cell_id=cell_id,
        as_of=datetime.fromisoformat("2024-07-26T18:05:00+05:30"),
    )
    assert snapshot.rainfall_mm_1h == 57.0
    assert snapshot.rainfall_mm_72h == 92.5
    assert snapshot.river_stage_m == 3.84
    assert snapshot.river_stage_rate_m_per_h > 0
    assert snapshot.pump_running is False
    assert snapshot.forecast_probability == 0.79
