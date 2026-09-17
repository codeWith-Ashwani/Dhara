from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from dhara.outcomes import OutcomeLabel

_STREAM_FIELDS = {
    "sensor": "sensor_confidence",
    "crowd": "crowd_confidence",
    "fused": "fused_confidence",
}
_BIN_EDGES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


@dataclass(frozen=True, slots=True)
class AuthorityEventRecord:
    authority_event_id: str
    event_group: str
    cell_id: str
    observed_at: datetime
    label: OutcomeLabel
    sensor_confidence: float
    crowd_confidence: float
    fused_confidence: float
    source_reference: str
    approved_by: str
    imported_at: datetime
    data_classification: str = "authority_verified"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["label"] = self.label.value
        result["observed_at"] = self.observed_at.isoformat()
        result["imported_at"] = self.imported_at.isoformat()
        return result


@dataclass(frozen=True, slots=True)
class HeldOutEvaluationArtifact:
    version: str
    stream: str
    created_at: datetime
    input_digest: str
    sample_count: int
    event_group_count: int
    brier_before: float
    brier_after: float
    mean_improvement: float
    improvement_ci_low: float
    improvement_ci_high: float
    promoted: bool
    promotion_reason: str
    method: str

    def to_dict(self, *, public: bool = False) -> dict[str, object]:
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        if public:
            result.pop("input_digest")
            result.pop("version")
        return result


class AuthorityEventRepository:
    """Immutable authority-owned event ledger and held-out evaluation artifacts."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        if path != Path(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = str(database_path)
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS authority_events (
                authority_event_id TEXT PRIMARY KEY,
                event_group TEXT NOT NULL,
                cell_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                label TEXT NOT NULL CHECK (label IN ('confirmed', 'refuted')),
                sensor_confidence REAL NOT NULL,
                crowd_confidence REAL NOT NULL,
                fused_confidence REAL NOT NULL,
                source_reference TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                data_classification TEXT NOT NULL,
                record_digest TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS authority_events_group_time_idx
                ON authority_events (event_group, observed_at);
            CREATE TABLE IF NOT EXISTS heldout_evaluation_artifacts (
                version TEXT PRIMARY KEY,
                stream TEXT NOT NULL,
                created_at TEXT NOT NULL,
                input_digest TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                event_group_count INTEGER NOT NULL,
                brier_before REAL NOT NULL,
                brier_after REAL NOT NULL,
                mean_improvement REAL NOT NULL,
                improvement_ci_low REAL NOT NULL,
                improvement_ci_high REAL NOT NULL,
                promoted INTEGER NOT NULL,
                promotion_reason TEXT NOT NULL,
                method TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS heldout_stream_created_idx
                ON heldout_evaluation_artifacts (stream, created_at);
            CREATE TRIGGER IF NOT EXISTS authority_events_no_update
            BEFORE UPDATE ON authority_events BEGIN
                SELECT RAISE(ABORT, 'authority events are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS authority_events_no_delete
            BEFORE DELETE ON authority_events BEGIN
                SELECT RAISE(ABORT, 'authority events are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS heldout_evaluations_no_update
            BEFORE UPDATE ON heldout_evaluation_artifacts BEGIN
                SELECT RAISE(ABORT, 'held-out evaluations are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS heldout_evaluations_no_delete
            BEFORE DELETE ON heldout_evaluation_artifacts BEGIN
                SELECT RAISE(ABORT, 'held-out evaluations are immutable');
            END;
            """
        )
        self._connection.commit()

    def append(self, record: AuthorityEventRecord) -> tuple[AuthorityEventRecord, bool]:
        _validate_record(record)
        digest = _record_digest(record)
        with self._lock:
            existing = self.get(record.authority_event_id)
            if existing is not None:
                if _record_digest(existing) != digest:
                    raise ValueError("authority event ID has conflicting immutable content")
                return existing, False
            self._connection.execute(
                """
                INSERT INTO authority_events (
                    authority_event_id, event_group, cell_id, observed_at, label,
                    sensor_confidence, crowd_confidence, fused_confidence,
                    source_reference, approved_by, imported_at, data_classification,
                    record_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.authority_event_id,
                    record.event_group,
                    record.cell_id,
                    record.observed_at.isoformat(),
                    record.label.value,
                    record.sensor_confidence,
                    record.crowd_confidence,
                    record.fused_confidence,
                    record.source_reference,
                    record.approved_by,
                    record.imported_at.isoformat(),
                    record.data_classification,
                    digest,
                ),
            )
            self._connection.commit()
        return record, True

    def append_many(
        self,
        records: tuple[AuthorityEventRecord, ...],
    ) -> dict[str, int]:
        created = 0
        duplicates = 0
        for record in records:
            _validate_record(record)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for record in records:
                    digest = _record_digest(record)
                    row = self._connection.execute(
                        "SELECT * FROM authority_events WHERE authority_event_id = ?",
                        (record.authority_event_id,),
                    ).fetchone()
                    if row is not None:
                        if _record_digest(_event_from_row(row)) != digest:
                            raise ValueError(
                                "authority event ID has conflicting immutable content"
                            )
                        duplicates += 1
                        continue
                    self._insert_event(record, digest)
                    created += 1
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return {"total": len(records), "created": created, "duplicates": duplicates}

    def _insert_event(self, record: AuthorityEventRecord, digest: str) -> None:
        self._connection.execute(
            """
            INSERT INTO authority_events (
                authority_event_id, event_group, cell_id, observed_at, label,
                sensor_confidence, crowd_confidence, fused_confidence,
                source_reference, approved_by, imported_at, data_classification,
                record_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.authority_event_id,
                record.event_group,
                record.cell_id,
                record.observed_at.isoformat(),
                record.label.value,
                record.sensor_confidence,
                record.crowd_confidence,
                record.fused_confidence,
                record.source_reference,
                record.approved_by,
                record.imported_at.isoformat(),
                record.data_classification,
                digest,
            ),
        )

    def get(self, authority_event_id: str) -> AuthorityEventRecord | None:
        row = self._connection.execute(
            "SELECT * FROM authority_events WHERE authority_event_id = ?",
            (authority_event_id,),
        ).fetchone()
        return None if row is None else _event_from_row(row)

    def all(self) -> tuple[AuthorityEventRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM authority_events ORDER BY event_group, observed_at, authority_event_id"
        ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def put_evaluation(self, artifact: HeldOutEvaluationArtifact) -> None:
        self.put_evaluations((artifact,))

    def put_evaluations(
        self,
        artifacts: tuple[HeldOutEvaluationArtifact, ...],
    ) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.executemany(
                """
                INSERT OR IGNORE INTO heldout_evaluation_artifacts (
                    version, stream, created_at, input_digest, sample_count,
                    event_group_count, brier_before, brier_after, mean_improvement,
                    improvement_ci_low, improvement_ci_high, promoted,
                    promotion_reason, method
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        artifact.version,
                        artifact.stream,
                        artifact.created_at.isoformat(),
                        artifact.input_digest,
                        artifact.sample_count,
                        artifact.event_group_count,
                        artifact.brier_before,
                        artifact.brier_after,
                        artifact.mean_improvement,
                        artifact.improvement_ci_low,
                        artifact.improvement_ci_high,
                        int(artifact.promoted),
                        artifact.promotion_reason,
                        artifact.method,
                    )
                    for artifact in artifacts
                ],
            )
            self._connection.commit()

    def latest_evaluation(self, stream: str) -> HeldOutEvaluationArtifact | None:
        row = self._connection.execute(
            "SELECT * FROM heldout_evaluation_artifacts WHERE stream = ? "
            "ORDER BY created_at DESC, version DESC LIMIT 1",
            (stream,),
        ).fetchone()
        return None if row is None else _evaluation_from_row(row)


class HeldOutCalibrationEvaluator:
    def __init__(
        self,
        repository: AuthorityEventRepository,
        *,
        minimum_samples: int = 12,
        minimum_event_groups: int = 3,
        bootstrap_iterations: int = 1_000,
    ) -> None:
        self.repository = repository
        self.minimum_samples = minimum_samples
        self.minimum_event_groups = minimum_event_groups
        self.bootstrap_iterations = bootstrap_iterations

    def evaluate(self, *, as_of: datetime) -> tuple[HeldOutEvaluationArtifact, ...]:
        if as_of.tzinfo is None:
            raise ValueError("held-out evaluation timestamp must include a timezone")
        records = self.repository.all()
        if not records:
            raise ValueError("held-out evaluation requires authority events")
        digest = _events_digest(records)
        artifacts = tuple(
            self._evaluate_stream(
                records,
                stream=stream,
                field_name=field_name,
                digest=digest,
                as_of=as_of,
            )
            for stream, field_name in _STREAM_FIELDS.items()
        )
        self.repository.put_evaluations(artifacts)
        return artifacts

    def _evaluate_stream(
        self,
        records: tuple[AuthorityEventRecord, ...],
        *,
        stream: str,
        field_name: str,
        digest: str,
        as_of: datetime,
    ) -> HeldOutEvaluationArtifact:
        groups = sorted({item.event_group for item in records})
        predictions: list[float] = []
        outcomes: list[float] = []
        calibrated: list[float] = []
        for held_out in groups:
            training = tuple(item for item in records if item.event_group != held_out)
            validation = tuple(item for item in records if item.event_group == held_out)
            bins = _fit_bins(training, field_name)
            for item in validation:
                probability = float(getattr(item, field_name))
                predictions.append(probability)
                outcomes.append(1.0 if item.label is OutcomeLabel.CONFIRMED else 0.0)
                calibrated.append(_apply_bins(probability, bins))
        before_errors = [
            (prediction - outcome) ** 2
            for prediction, outcome in zip(predictions, outcomes, strict=True)
        ]
        after_errors = [
            (prediction - outcome) ** 2
            for prediction, outcome in zip(calibrated, outcomes, strict=True)
        ]
        improvements = [
            before - after
            for before, after in zip(before_errors, after_errors, strict=True)
        ]
        brier_before = _mean(before_errors)
        brier_after = _mean(after_errors)
        ci_low, ci_high = _bootstrap_ci(
            improvements,
            iterations=self.bootstrap_iterations,
            seed=int(hashlib.sha256(f"{stream}:{digest}".encode()).hexdigest()[:16], 16),
        )
        enough_data = (
            len(records) >= self.minimum_samples
            and len(groups) >= self.minimum_event_groups
        )
        promoted = enough_data and ci_low > 0 and brier_after < brier_before
        if not enough_data:
            reason = "insufficient held-out samples or event groups"
        elif promoted:
            reason = "95% bootstrap interval supports held-out Brier improvement"
        else:
            reason = "held-out improvement is absent or statistically uncertain"
        return HeldOutEvaluationArtifact(
            version=f"heldout-{stream}-{digest[:12]}",
            stream=stream,
            created_at=as_of,
            input_digest=digest,
            sample_count=len(records),
            event_group_count=len(groups),
            brier_before=round(brier_before, 6),
            brier_after=round(brier_after, 6),
            mean_improvement=round(brier_before - brier_after, 6),
            improvement_ci_low=round(ci_low, 6),
            improvement_ci_high=round(ci_high, 6),
            promoted=promoted,
            promotion_reason=reason,
            method="leave-one-event-group-out-laplace-bins-bootstrap95",
        )


def _fit_bins(
    records: tuple[AuthorityEventRecord, ...],
    field_name: str,
) -> tuple[tuple[float, float, float], ...]:
    bins: list[tuple[float, float, float]] = []
    for lower, upper in zip(_BIN_EDGES[:-1], _BIN_EDGES[1:], strict=True):
        matching = [
            item
            for item in records
            if lower <= float(getattr(item, field_name)) < upper
            or (float(getattr(item, field_name)) == 1.0 and upper == 1.0)
        ]
        if matching:
            positives = sum(item.label is OutcomeLabel.CONFIRMED for item in matching)
            bins.append((lower, upper, (positives + 1) / (len(matching) + 2)))
    return tuple(bins)


def _apply_bins(value: float, bins: tuple[tuple[float, float, float], ...]) -> float:
    for lower, upper, calibrated in bins:
        if lower <= value < upper or (value == 1.0 and upper == 1.0):
            return calibrated
    return value


def _bootstrap_ci(
    improvements: list[float],
    *,
    iterations: int,
    seed: int,
) -> tuple[float, float]:
    generator = random.Random(seed)
    estimates = sorted(
        _mean([generator.choice(improvements) for _ in improvements])
        for _ in range(iterations)
    )
    return (
        estimates[int(0.025 * (iterations - 1))],
        estimates[int(0.975 * (iterations - 1))],
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _validate_record(record: AuthorityEventRecord) -> None:
    if record.observed_at.tzinfo is None or record.imported_at.tzinfo is None:
        raise ValueError("authority event timestamps must include a timezone")
    required = (
        record.authority_event_id,
        record.event_group,
        record.cell_id,
        record.source_reference,
        record.approved_by,
        record.data_classification,
    )
    if any(not value.strip() for value in required):
        raise ValueError("authority event identity and provenance fields are required")
    for value in (
        record.sensor_confidence,
        record.crowd_confidence,
        record.fused_confidence,
    ):
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("authority event confidence values must be between 0 and 1")


def _record_digest(record: AuthorityEventRecord) -> str:
    return hashlib.sha256(
        json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _events_digest(records: tuple[AuthorityEventRecord, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            [item.to_dict() for item in records],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _event_from_row(row: sqlite3.Row) -> AuthorityEventRecord:
    return AuthorityEventRecord(
        authority_event_id=row["authority_event_id"],
        event_group=row["event_group"],
        cell_id=row["cell_id"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        label=OutcomeLabel(row["label"]),
        sensor_confidence=float(row["sensor_confidence"]),
        crowd_confidence=float(row["crowd_confidence"]),
        fused_confidence=float(row["fused_confidence"]),
        source_reference=row["source_reference"],
        approved_by=row["approved_by"],
        imported_at=datetime.fromisoformat(row["imported_at"]),
        data_classification=row["data_classification"],
    )


def _evaluation_from_row(row: sqlite3.Row) -> HeldOutEvaluationArtifact:
    return HeldOutEvaluationArtifact(
        version=row["version"],
        stream=row["stream"],
        created_at=datetime.fromisoformat(row["created_at"]),
        input_digest=row["input_digest"],
        sample_count=int(row["sample_count"]),
        event_group_count=int(row["event_group_count"]),
        brier_before=float(row["brier_before"]),
        brier_after=float(row["brier_after"]),
        mean_improvement=float(row["mean_improvement"]),
        improvement_ci_low=float(row["improvement_ci_low"]),
        improvement_ci_high=float(row["improvement_ci_high"]),
        promoted=bool(row["promoted"]),
        promotion_reason=row["promotion_reason"],
        method=row["method"],
    )
