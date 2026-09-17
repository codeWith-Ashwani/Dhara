from __future__ import annotations

import numpy as np
import pytest

from dhara.sensor_dataset import FEATURE_NAMES
from dhara.sensor_models import SensorEnsemble


def test_ensemble_produces_calibrated_contract(sensor_dataset, fitted_sensor_model) -> None:
    predictions = fitted_sensor_model.predict(sensor_dataset.matrix)
    assert len(predictions) == len(sensor_dataset.examples)
    for prediction in predictions:
        assert 0 <= prediction.novelty_probability <= 1
        assert 0 <= prediction.precursor_probability <= 1
        assert 0 <= prediction.trend_probability <= 1
        assert 0 <= prediction.sensor_confidence <= 1
        expected = (
            0.25 * prediction.novelty_probability
            + 0.40 * prediction.precursor_probability
            + 0.35 * prediction.trend_probability
        )
        assert prediction.sensor_confidence == pytest.approx(expected, abs=2e-6)
        assert set(prediction.attributions) == set(FEATURE_NAMES)

    confidences = np.asarray([item.sensor_confidence for item in predictions])
    assert confidences[sensor_dataset.outcomes == 1].mean() > confidences[
        sensor_dataset.outcomes == 0
    ].mean()


def test_model_artifact_round_trip(tmp_path, sensor_dataset, fitted_sensor_model) -> None:
    destination = tmp_path / "loop_a.joblib"
    fitted_sensor_model.save(destination)
    restored = SensorEnsemble.load(destination)

    before = fitted_sensor_model.predict(sensor_dataset.matrix[:3])
    after = restored.predict(sensor_dataset.matrix[:3])
    assert before == after


def test_sensor_weights_are_governed() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        SensorEnsemble(version="bad", weights=(0.4, 0.4, 0.4))
