from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from dhara.sensor_dataset import FEATURE_NAMES, load_sensor_dataset
from dhara.sensor_evaluation import leave_one_event_out
from dhara.sensor_models import DEFAULT_WEIGHTS, SensorEnsemble


def train_and_evaluate(
    dataset_path: str | Path,
    model_path: str | Path,
    *,
    version: str,
    report_path: str | Path | None = None,
    data_classification: str = "synthetic",
) -> dict[str, object]:
    if data_classification not in {"synthetic", "verified"}:
        raise ValueError("data_classification must be synthetic or verified")
    source = Path(dataset_path)
    dataset = load_sensor_dataset(source)
    metrics = leave_one_event_out(dataset)
    model = SensorEnsemble(version=version).fit(dataset.matrix, dataset.outcomes)
    model.save(model_path)
    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "safety_mode": "shadow",
        "data_classification": data_classification,
        "operational_performance_claim": False,
        "dataset": {
            "path": source.as_posix(),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "rows": len(dataset.examples),
            "events": len(dataset.event_ids),
            "positive_rows": int(dataset.outcomes.sum()),
        },
        "model": {
            "version": version,
            "artifact_path": Path(model_path).as_posix(),
            "feature_names": FEATURE_NAMES,
            "weights": {
                "novelty": DEFAULT_WEIGHTS[0],
                "precursor": DEFAULT_WEIGHTS[1],
                "trend": DEFAULT_WEIGHTS[2],
            },
            "components": {
                "novelty": "normal-only MLP reconstruction model",
                "precursor": "isotonic-calibrated gradient boosting",
                "trend": "sigmoid-calibrated trend-feature logistic surrogate",
            },
        },
        "evaluation": metrics.to_dict(),
    }
    if report_path is not None:
        destination = Path(report_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
