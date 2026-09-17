from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
from numpy.typing import NDArray
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from dhara.sensor_dataset import FEATURE_NAMES, TREND_FEATURE_NAMES

DEFAULT_WEIGHTS = (0.25, 0.40, 0.35)
TREND_INDICES = tuple(FEATURE_NAMES.index(name) for name in TREND_FEATURE_NAMES)


@dataclass(frozen=True, slots=True)
class SensorPrediction:
    model_version: str
    novelty_probability: float
    precursor_probability: float
    trend_probability: float
    sensor_confidence: float
    attributions: dict[str, float]
    explanation_method: str = "signed_feature_importance"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class NoveltyAutoencoder:
    """Small reconstruction network trained exclusively on normal observations."""

    def __init__(self, random_state: int = 42) -> None:
        self.scaler = StandardScaler()
        self.network = MLPRegressor(
            hidden_layer_sizes=(8, 4, 8),
            activation="tanh",
            solver="lbfgs",
            alpha=0.001,
            max_iter=3_000,
            random_state=random_state,
        )
        self.error_threshold = 0.0
        self.error_scale = 1.0

    def fit(self, matrix: NDArray[np.float64], outcomes: NDArray[np.int64]) -> None:
        normal = matrix[outcomes == 0]
        if len(normal) < 10:
            raise ValueError("novelty model needs at least 10 normal observations")
        scaled = self.scaler.fit_transform(normal)
        self.network.fit(scaled, scaled)
        errors = np.mean(np.square(scaled - self.network.predict(scaled)), axis=1)
        self.error_threshold = float(np.quantile(errors, 0.95))
        median = float(np.median(errors))
        mad = float(np.median(np.abs(errors - median)))
        self.error_scale = max(mad * 1.4826, self.error_threshold * 0.1, 1e-6)

    def predict_probability(self, matrix: NDArray[np.float64]) -> NDArray[np.float64]:
        scaled = self.scaler.transform(matrix)
        reconstructed = self.network.predict(scaled)
        errors = np.mean(np.square(scaled - reconstructed), axis=1)
        logits = np.clip((errors - self.error_threshold) / self.error_scale, -30, 30)
        return 1.0 / (1.0 + np.exp(-logits))


class PrecursorClassifier:
    def __init__(self, random_state: int = 42) -> None:
        self.explanation_model = GradientBoostingClassifier(
            n_estimators=100,
            learning_rate=0.05,
            max_depth=2,
            random_state=random_state,
        )
        self.calibrated_model = CalibratedClassifierCV(
            estimator=GradientBoostingClassifier(
                n_estimators=100,
                learning_rate=0.05,
                max_depth=2,
                random_state=random_state,
            ),
            method="isotonic",
            cv=3,
        )
        self.background_mean = np.zeros(len(FEATURE_NAMES))

    def fit(self, matrix: NDArray[np.float64], outcomes: NDArray[np.int64]) -> None:
        _require_both_classes(outcomes)
        self.explanation_model.fit(matrix, outcomes)
        self.calibrated_model.fit(matrix, outcomes)
        self.background_mean = np.mean(matrix, axis=0)

    def predict_probability(self, matrix: NDArray[np.float64]) -> NDArray[np.float64]:
        return self.calibrated_model.predict_proba(matrix)[:, 1]

    def explain(self, row: NDArray[np.float64]) -> dict[str, float]:
        raw = self.explanation_model.feature_importances_ * (row - self.background_mean)
        denominator = float(np.sum(np.abs(raw)))
        normalised = raw if denominator == 0 else raw / denominator
        return {
            name: round(float(value), 6)
            for name, value in sorted(
                zip(FEATURE_NAMES, normalised, strict=True),
                key=lambda item: abs(item[1]),
                reverse=True,
            )
        }


class TrendClassifier:
    """Calibrated Sprint 2 surrogate over sequence-derived trend features."""

    def __init__(self, random_state: int = 42) -> None:
        estimator = make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1_000, random_state=random_state),
        )
        self.calibrated_model = CalibratedClassifierCV(
            estimator=estimator,
            method="sigmoid",
            cv=3,
        )

    def fit(self, matrix: NDArray[np.float64], outcomes: NDArray[np.int64]) -> None:
        _require_both_classes(outcomes)
        self.calibrated_model.fit(matrix[:, TREND_INDICES], outcomes)

    def predict_probability(self, matrix: NDArray[np.float64]) -> NDArray[np.float64]:
        return self.calibrated_model.predict_proba(matrix[:, TREND_INDICES])[:, 1]


class SensorEnsemble:
    def __init__(
        self,
        *,
        version: str,
        weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
        random_state: int = 42,
    ) -> None:
        if len(weights) != 3 or any(weight < 0 for weight in weights):
            raise ValueError("sensor weights must contain three non-negative values")
        if not math.isclose(sum(weights), 1.0, abs_tol=1e-9):
            raise ValueError("sensor weights must sum to 1")
        self.version = version
        self.weights = weights
        self.novelty = NoveltyAutoencoder(random_state=random_state)
        self.precursor = PrecursorClassifier(random_state=random_state)
        self.trend = TrendClassifier(random_state=random_state)
        self.fitted = False

    def fit(self, matrix: NDArray[np.float64], outcomes: NDArray[np.int64]) -> SensorEnsemble:
        if matrix.ndim != 2 or matrix.shape[1] != len(FEATURE_NAMES):
            raise ValueError(f"expected a matrix with {len(FEATURE_NAMES)} features")
        self.novelty.fit(matrix, outcomes)
        self.precursor.fit(matrix, outcomes)
        self.trend.fit(matrix, outcomes)
        self.fitted = True
        return self

    def predict(self, matrix: NDArray[np.float64]) -> list[SensorPrediction]:
        if not self.fitted:
            raise RuntimeError("sensor ensemble has not been fitted")
        values = np.atleast_2d(np.asarray(matrix, dtype=float))
        novelty = self.novelty.predict_probability(values)
        precursor = self.precursor.predict_probability(values)
        trend = self.trend.predict_probability(values)
        sensor = np.clip(
            self.weights[0] * novelty
            + self.weights[1] * precursor
            + self.weights[2] * trend,
            0.0,
            1.0,
        )
        return [
            SensorPrediction(
                model_version=self.version,
                novelty_probability=round(float(novelty[index]), 6),
                precursor_probability=round(float(precursor[index]), 6),
                trend_probability=round(float(trend[index]), 6),
                sensor_confidence=round(float(sensor[index]), 6),
                attributions=self.precursor.explain(values[index]),
            )
            for index in range(len(values))
        ]

    def save(self, path: str | Path) -> None:
        if not self.fitted:
            raise RuntimeError("cannot save an unfitted sensor ensemble")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, destination)

    @classmethod
    def load(cls, path: str | Path) -> SensorEnsemble:
        model = joblib.load(path)
        if not isinstance(model, cls):
            raise TypeError("artifact does not contain a SensorEnsemble")
        return model


def _require_both_classes(outcomes: NDArray[np.int64]) -> None:
    if set(np.unique(outcomes)) != {0, 1}:
        raise ValueError("classifier training requires both outcome classes")
