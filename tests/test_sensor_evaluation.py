from __future__ import annotations

import numpy as np
import pytest

from dhara.sensor_evaluation import leave_one_event_out, metrics_from_probabilities


def test_forecast_metrics_match_known_confusion_matrix() -> None:
    metrics = metrics_from_probabilities(
        np.asarray([1, 1, 1, 0, 0, 0]),
        np.asarray([0.9, 0.8, 0.2, 0.7, 0.3, 0.1]),
        event_count=2,
    )
    assert metrics.probability_of_detection == pytest.approx(2 / 3, abs=1e-6)
    assert metrics.false_alarm_ratio == pytest.approx(1 / 3, abs=1e-6)
    assert metrics.critical_success_index == 0.5
    assert metrics.brier_score == 0.213333


def test_leave_one_event_out_never_trains_on_held_out_event(sensor_dataset) -> None:
    metrics = leave_one_event_out(sensor_dataset)
    assert metrics.sample_count == 120
    assert metrics.event_count == 10
    assert 0 <= metrics.brier_score <= 1
    assert 0 <= metrics.probability_of_detection <= 1
    assert 0 <= metrics.false_alarm_ratio <= 1
    assert 0 <= metrics.critical_success_index <= 1
    assert sum(item.count for item in metrics.reliability) == 120
