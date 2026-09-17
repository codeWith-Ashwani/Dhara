from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from numpy.typing import NDArray

from dhara.sensor_dataset import SensorDataset
from dhara.sensor_models import SensorEnsemble


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_confidence: float
    observed_frequency: float


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    sample_count: int
    event_count: int
    threshold: float
    brier_score: float
    probability_of_detection: float
    false_alarm_ratio: float
    critical_success_index: float
    reliability: tuple[ReliabilityBin, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def metrics_from_probabilities(
    outcomes: NDArray[np.int64],
    probabilities: NDArray[np.float64],
    *,
    event_count: int,
    threshold: float = 0.5,
    bin_count: int = 5,
) -> EvaluationMetrics:
    truth = np.asarray(outcomes, dtype=int)
    confidence = np.clip(np.asarray(probabilities, dtype=float), 0.0, 1.0)
    predicted = confidence >= threshold
    positive = truth == 1
    true_positive = int(np.sum(predicted & positive))
    false_positive = int(np.sum(predicted & ~positive))
    false_negative = int(np.sum(~predicted & positive))
    pod = _ratio(true_positive, true_positive + false_negative)
    far = _ratio(false_positive, true_positive + false_positive)
    csi = _ratio(true_positive, true_positive + false_positive + false_negative)

    bins: list[ReliabilityBin] = []
    edges = np.linspace(0.0, 1.0, bin_count + 1)
    for index in range(bin_count):
        lower = float(edges[index])
        upper = float(edges[index + 1])
        if index == bin_count - 1:
            mask = (confidence >= lower) & (confidence <= upper)
        else:
            mask = (confidence >= lower) & (confidence < upper)
        if not np.any(mask):
            continue
        bins.append(
            ReliabilityBin(
                lower=round(lower, 4),
                upper=round(upper, 4),
                count=int(np.sum(mask)),
                mean_confidence=round(float(np.mean(confidence[mask])), 6),
                observed_frequency=round(float(np.mean(truth[mask])), 6),
            )
        )
    return EvaluationMetrics(
        sample_count=len(truth),
        event_count=event_count,
        threshold=threshold,
        brier_score=round(float(np.mean(np.square(confidence - truth))), 6),
        probability_of_detection=round(pod, 6),
        false_alarm_ratio=round(far, 6),
        critical_success_index=round(csi, 6),
        reliability=tuple(bins),
    )


def leave_one_event_out(
    dataset: SensorDataset,
    *,
    threshold: float = 0.5,
    random_state: int = 42,
) -> EvaluationMetrics:
    all_probabilities = np.zeros(len(dataset.examples), dtype=float)
    for event_id in dataset.event_ids:
        held_out = dataset.event_mask(event_id)
        training = ~held_out
        model = SensorEnsemble(
            version=f"loeo-{event_id}",
            random_state=random_state,
        ).fit(dataset.matrix[training], dataset.outcomes[training])
        predictions = model.predict(dataset.matrix[held_out])
        all_probabilities[held_out] = [item.sensor_confidence for item in predictions]
    return metrics_from_probabilities(
        dataset.outcomes,
        all_probabilities,
        event_count=len(dataset.event_ids),
        threshold=threshold,
    )


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else 0.0
