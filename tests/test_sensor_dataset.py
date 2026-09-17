from __future__ import annotations

from pathlib import Path

import pytest

from dhara.sensor_dataset import FEATURE_NAMES, SensorDatasetError, feature_vector


def test_fixture_respects_grouped_feature_contract(sensor_dataset) -> None:
    assert len(sensor_dataset.examples) == 120
    assert len(sensor_dataset.event_ids) == 10
    assert sensor_dataset.matrix.shape == (120, len(FEATURE_NAMES))
    assert sensor_dataset.outcomes.sum() == 20
    for event_id in sensor_dataset.event_ids:
        assert sensor_dataset.event_mask(event_id).sum() == 12


def test_feature_vector_rejects_missing_fields() -> None:
    with pytest.raises(SensorDatasetError, match="missing"):
        feature_vector({"rainfall_mm_1h": 1.0})


def test_dataset_loader_rejects_missing_columns(tmp_path: Path) -> None:
    source = tmp_path / "invalid.csv"
    source.write_text("event_id,outcome\nevent-1,0\n", encoding="utf-8")
    from dhara.sensor_dataset import load_sensor_dataset

    with pytest.raises(SensorDatasetError, match="missing columns"):
        load_sensor_dataset(source)
