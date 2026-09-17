from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dhara.sensor_dataset import load_sensor_dataset  # noqa: E402
from dhara.sensor_models import SensorEnsemble  # noqa: E402

SENSOR_FIXTURE = (
    Path(__file__).parents[1] / "data" / "training" / "synthetic_sensor_episodes.csv"
)


@pytest.fixture(scope="session")
def sensor_dataset():
    return load_sensor_dataset(SENSOR_FIXTURE)


@pytest.fixture(scope="session")
def fitted_sensor_model(sensor_dataset):
    return SensorEnsemble(version="test-loop-a").fit(
        sensor_dataset.matrix, sensor_dataset.outcomes
    )
