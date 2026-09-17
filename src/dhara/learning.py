from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from dhara.community import ReporterTrustEngine
from dhara.outcomes import OutcomeLabel, OutcomeRecord, OutcomeRepository

_STREAM_FIELDS = {
    "sensor": "sensor_confidence",
    "crowd": "crowd_confidence",
    "fused": "fused_confidence",
}
_BIN_EDGES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    positives: int
    mean_prediction: float
    calibrated_probability: float


@dataclass(frozen=True, slots=True)
class CalibrationArtifact:
    version: str
    stream: str
    created_at: datetime
    input_digest: str
    sample_count: int
    brier_before: float
    brier_after: float
    promoted: bool
    promotion_reason: str
    bins: tuple[CalibrationBin, ...]

    def calibrate(self, value: float) -> float:
        if not self.promoted:
            return value
        return self.candidate_calibrate(value)

    def candidate_calibrate(self, value: float) -> float:
        for item in self.bins:
            if item.lower <= value < item.upper or (value == 1.0 and item.upper == 1.0):
                return item.calibrated_probability
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "stream": self.stream,
            "created_at": self.created_at.isoformat(),
            "input_digest": self.input_digest,
            "sample_count": self.sample_count,
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
            "promoted": self.promoted,
            "promotion_reason": self.promotion_reason,
            "bins": [asdict(item) for item in self.bins],
        }

    def to_public_dict(self) -> dict[str, object]:
        """Return aggregate calibration data without the private input digest."""
        result = self.to_dict()
        result.pop("input_digest")
        return result


@dataclass(frozen=True, slots=True)
class ZonePolicyArtifact:
    version: str
    cell_id: str
    created_at: datetime
    input_digest: str
    sample_count: int
    false_alarms: int
    misses: int
    miss_cost: float
    false_alarm_cost: float
    sensor_weight: float
    corroboration_bonus: float
    advisory_threshold: float
    watch_threshold: float
    warning_threshold: float

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        return result


@dataclass(frozen=True, slots=True)
class LearningRun:
    sequence: int
    job_id: str
    input_digest: str
    started_at: datetime
    completed_at: datetime
    status: str
    result: dict[str, object]
    previous_hash: str
    event_hash: str

    def to_dict(self, *, reused: bool = False) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "job_id": self.job_id,
            "input_digest": self.input_digest,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "status": self.status,
            "result": self.result,
            "previous_hash": self.previous_hash,
            "event_hash": self.event_hash,
            "reused": reused,
        }


class LearningRepository:
    """Append-only calibration, policy, and learning-job artifacts."""

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
            CREATE TABLE IF NOT EXISTS calibration_artifacts (
                version TEXT PRIMARY KEY,
                stream TEXT NOT NULL,
                created_at TEXT NOT NULL,
                input_digest TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                brier_before REAL NOT NULL,
                brier_after REAL NOT NULL,
                promoted INTEGER NOT NULL DEFAULT 0,
                promotion_reason TEXT NOT NULL DEFAULT 'legacy artifact',
                bins TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_calibration_stream_created
                ON calibration_artifacts (stream, created_at);
            CREATE TABLE IF NOT EXISTS zone_policy_artifacts (
                version TEXT PRIMARY KEY,
                cell_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                input_digest TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                false_alarms INTEGER NOT NULL,
                misses INTEGER NOT NULL,
                miss_cost REAL NOT NULL,
                false_alarm_cost REAL NOT NULL,
                sensor_weight REAL NOT NULL,
                corroboration_bonus REAL NOT NULL,
                advisory_threshold REAL NOT NULL,
                watch_threshold REAL NOT NULL,
                warning_threshold REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_zone_policy_cell_created
                ON zone_policy_artifacts (cell_id, created_at);
            CREATE TABLE IF NOT EXISTS learning_job_runs (
                sequence INTEGER PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE,
                input_digest TEXT NOT NULL UNIQUE,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE
            );
            CREATE TRIGGER IF NOT EXISTS calibration_no_update
            BEFORE UPDATE ON calibration_artifacts BEGIN
                SELECT RAISE(ABORT, 'calibration artifacts are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS calibration_no_delete
            BEFORE DELETE ON calibration_artifacts BEGIN
                SELECT RAISE(ABORT, 'calibration artifacts are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS zone_policy_no_update
            BEFORE UPDATE ON zone_policy_artifacts BEGIN
                SELECT RAISE(ABORT, 'zone policy artifacts are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS zone_policy_no_delete
            BEFORE DELETE ON zone_policy_artifacts BEGIN
                SELECT RAISE(ABORT, 'zone policy artifacts are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS learning_runs_no_update
            BEFORE UPDATE ON learning_job_runs BEGIN
                SELECT RAISE(ABORT, 'learning runs are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS learning_runs_no_delete
            BEFORE DELETE ON learning_job_runs BEGIN
                SELECT RAISE(ABORT, 'learning runs are immutable');
            END;
            """
        )
        self._ensure_column(
            "calibration_artifacts",
            "promoted",
            "INTEGER NOT NULL DEFAULT 0",
        )
        self._ensure_column(
            "calibration_artifacts",
            "promotion_reason",
            "TEXT NOT NULL DEFAULT 'legacy artifact'",
        )
        self._connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def put_calibration(self, artifact: CalibrationArtifact) -> None:
        self._connection.execute(
            """
            INSERT INTO calibration_artifacts (
                version, stream, created_at, input_digest, sample_count,
                brier_before, brier_after, promoted, promotion_reason, bins
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.version,
                artifact.stream,
                artifact.created_at.isoformat(),
                artifact.input_digest,
                artifact.sample_count,
                artifact.brier_before,
                artifact.brier_after,
                int(artifact.promoted),
                artifact.promotion_reason,
                json.dumps([asdict(item) for item in artifact.bins], sort_keys=True),
            ),
        )
        self._connection.commit()

    def latest_calibration(self, stream: str) -> CalibrationArtifact | None:
        row = self._connection.execute(
            "SELECT * FROM calibration_artifacts WHERE stream = ? "
            "ORDER BY created_at DESC, version DESC LIMIT 1",
            (stream,),
        ).fetchone()
        return None if row is None else _calibration_from_row(row)

    def put_zone_policy(self, artifact: ZonePolicyArtifact) -> None:
        self._connection.execute(
            """
            INSERT INTO zone_policy_artifacts (
                version, cell_id, created_at, input_digest, sample_count,
                false_alarms, misses, miss_cost, false_alarm_cost,
                sensor_weight, corroboration_bonus, advisory_threshold,
                watch_threshold, warning_threshold
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.version,
                artifact.cell_id,
                artifact.created_at.isoformat(),
                artifact.input_digest,
                artifact.sample_count,
                artifact.false_alarms,
                artifact.misses,
                artifact.miss_cost,
                artifact.false_alarm_cost,
                artifact.sensor_weight,
                artifact.corroboration_bonus,
                artifact.advisory_threshold,
                artifact.watch_threshold,
                artifact.warning_threshold,
            ),
        )
        self._connection.commit()

    def latest_zone_policy(self, cell_id: str) -> ZonePolicyArtifact | None:
        row = self._connection.execute(
            "SELECT * FROM zone_policy_artifacts WHERE cell_id = ? "
            "ORDER BY created_at DESC, version DESC LIMIT 1",
            (cell_id,),
        ).fetchone()
        return None if row is None else _zone_policy_from_row(row)

    def find_run(self, input_digest: str) -> LearningRun | None:
        row = self._connection.execute(
            "SELECT * FROM learning_job_runs WHERE input_digest = ?",
            (input_digest,),
        ).fetchone()
        return None if row is None else _run_from_row(row)

    def append_run(
        self,
        *,
        input_digest: str,
        started_at: datetime,
        completed_at: datetime,
        status: str,
        result: dict[str, object],
    ) -> LearningRun:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT sequence, event_hash FROM learning_job_runs "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 1 if row is None else int(row["sequence"]) + 1
            previous_hash = "0" * 64 if row is None else str(row["event_hash"])
            canonical = _canonical_run(
                sequence=sequence,
                job_id=job_id,
                input_digest=input_digest,
                started_at=started_at,
                completed_at=completed_at,
                status=status,
                result=result,
                previous_hash=previous_hash,
            )
            event_hash = hashlib.sha256(canonical).hexdigest()
            self._connection.execute(
                """
                INSERT INTO learning_job_runs (
                    sequence, job_id, input_digest, started_at, completed_at,
                    status, result, previous_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence,
                    job_id,
                    input_digest,
                    started_at.isoformat(),
                    completed_at.isoformat(),
                    status,
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                    previous_hash,
                    event_hash,
                ),
            )
            self._connection.commit()
        return LearningRun(
            sequence=sequence,
            job_id=job_id,
            input_digest=input_digest,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            result=result,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )

    def latest_run(self) -> LearningRun | None:
        row = self._connection.execute(
            "SELECT * FROM learning_job_runs ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return None if row is None else _run_from_row(row)

    def verify_run_chain(self) -> bool:
        rows = self._connection.execute(
            "SELECT * FROM learning_job_runs ORDER BY sequence"
        ).fetchall()
        previous_hash = "0" * 64
        for row in rows:
            run = _run_from_row(row)
            if run.previous_hash != previous_hash:
                return False
            expected = hashlib.sha256(
                _canonical_run(
                    sequence=run.sequence,
                    job_id=run.job_id,
                    input_digest=run.input_digest,
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                    status=run.status,
                    result=run.result,
                    previous_hash=run.previous_hash,
                )
            ).hexdigest()
            if not hmac.compare_digest(expected, run.event_hash):
                return False
            previous_hash = run.event_hash
        return True


class LearningService:
    def __init__(
        self,
        *,
        outcomes: OutcomeRepository,
        trust: ReporterTrustEngine,
        repository: LearningRepository,
        miss_cost: float = 5.0,
        false_alarm_cost: float = 1.0,
    ) -> None:
        self.outcomes = outcomes
        self.trust = trust
        self.repository = repository
        self.miss_cost = miss_cost
        self.false_alarm_cost = false_alarm_cost
        self._run_lock = threading.Lock()

    def run(self, *, as_of: datetime) -> tuple[LearningRun, bool]:
        if as_of.tzinfo is None:
            raise ValueError("learning job timestamp must include a timezone")
        with self._run_lock:
            return self._run_locked(as_of=as_of)

    def _run_locked(self, *, as_of: datetime) -> tuple[LearningRun, bool]:
        records = self.outcomes.all()
        if not records:
            raise ValueError("learning job requires at least one labelled outcome")
        input_digest = _outcome_digest(records)
        existing = self.repository.find_run(input_digest)
        if existing is not None:
            return existing, True

        trust_changes = self._update_trust(records, as_of)
        calibrations = tuple(
            _build_calibration(
                records,
                stream=stream,
                field_name=field_name,
                created_at=as_of,
                input_digest=input_digest,
            )
            for stream, field_name in _STREAM_FIELDS.items()
        )
        for artifact in calibrations:
            self.repository.put_calibration(artifact)
        policies = self._build_zone_policies(records, calibrations, as_of, input_digest)
        for policy in policies:
            self.repository.put_zone_policy(policy)
        result: dict[str, object] = {
            "outcomes_processed": len(records),
            "confirmed": sum(item.label is OutcomeLabel.CONFIRMED for item in records),
            "refuted": sum(item.label is OutcomeLabel.REFUTED for item in records),
            "reporter_trust_changes": trust_changes,
            "calibration_versions": {
                item.stream: item.version for item in calibrations
            },
            "calibration_brier": {
                item.stream: {
                    "before": item.brier_before,
                    "after": item.brier_after,
                    "promoted": item.promoted,
                }
                for item in calibrations
            },
            "zone_policy_versions": {
                item.cell_id: item.version for item in policies
            },
            "policy_costs": {
                "miss": self.miss_cost,
                "false_alarm": self.false_alarm_cost,
            },
        }
        run = self.repository.append_run(
            input_digest=input_digest,
            started_at=as_of,
            completed_at=as_of,
            status="completed",
            result=result,
        )
        return run, False

    def public_metrics(self, *, minimum_sample_size: int = 5) -> dict[str, object]:
        records = self.outcomes.all()
        base: dict[str, object] = {
            "mode": "shadow",
            "operational_performance_claim": False,
            "sample_size": len(records),
            "minimum_public_sample_size": minimum_sample_size,
        }
        if len(records) < minimum_sample_size:
            return {**base, "status": "insufficient_data", "suppressed": True}
        labels = [1 if item.label is OutcomeLabel.CONFIRMED else 0 for item in records]
        fused = [item.fused_confidence for item in records]
        predicted = [value >= 0.55 for value in fused]
        pairs = tuple(zip(predicted, labels, strict=True))
        true_positive = sum(flag and label == 1 for flag, label in pairs)
        false_positive = sum(flag and label == 0 for flag, label in pairs)
        false_negative = sum(not flag and label == 1 for flag, label in pairs)
        calibrations = {
            stream: self.repository.latest_calibration(stream) for stream in _STREAM_FIELDS
        }
        data_classification = (
            "synthetic"
            if all(item.data_classification == "synthetic" for item in records)
            else "operational_unverified"
        )
        return {
            **base,
            "status": "available",
            "suppressed": False,
            "data_classification": data_classification,
            "confirmed": sum(labels),
            "refuted": len(labels) - sum(labels),
            "probability_of_detection": _ratio(true_positive, true_positive + false_negative),
            "false_alarm_ratio": _ratio(false_positive, true_positive + false_positive),
            "critical_success_index": _ratio(
                true_positive,
                true_positive + false_positive + false_negative,
            ),
            "calibration": {
                stream: artifact.to_public_dict() if artifact is not None else None
                for stream, artifact in calibrations.items()
            },
        }

    def _update_trust(
        self,
        records: tuple[OutcomeRecord, ...],
        as_of: datetime,
    ) -> dict[str, dict[str, float]]:
        changes: dict[str, dict[str, float]] = {}
        for record in records:
            for reporter_id in set(record.reporter_ids):
                before = self.trust.trust(reporter_id, as_of=as_of)
                after = self.trust.update(
                    reporter_id,
                    confirmed=record.label is OutcomeLabel.CONFIRMED,
                    at=as_of,
                )
                current = changes.setdefault(reporter_id, {"before": before, "after": after})
                current["after"] = after
        return changes

    def _build_zone_policies(
        self,
        records: tuple[OutcomeRecord, ...],
        calibrations: tuple[CalibrationArtifact, ...],
        created_at: datetime,
        input_digest: str,
    ) -> tuple[ZonePolicyArtifact, ...]:
        grouped: dict[str, list[OutcomeRecord]] = {}
        for record in records:
            grouped.setdefault(record.cell_id, []).append(record)
        sensor_artifact = next(item for item in calibrations if item.stream == "sensor")
        crowd_artifact = next(item for item in calibrations if item.stream == "crowd")
        sensor_brier = (
            sensor_artifact.brier_after
            if sensor_artifact.promoted
            else sensor_artifact.brier_before
        )
        crowd_brier = (
            crowd_artifact.brier_after
            if crowd_artifact.promoted
            else crowd_artifact.brier_before
        )
        sensor_weight = round(
            min(0.70, max(0.50, 0.60 + (crowd_brier - sensor_brier) * 0.20)),
            6,
        )
        policies: list[ZonePolicyArtifact] = []
        for cell_id, cell_records in sorted(grouped.items()):
            false_alarms = sum(
                item.label is OutcomeLabel.REFUTED and item.fused_confidence >= 0.55
                for item in cell_records
            )
            misses = sum(
                item.label is OutcomeLabel.CONFIRMED and item.fused_confidence < 0.55
                for item in cell_records
            )
            pressure = (
                false_alarms * self.false_alarm_cost - misses * self.miss_cost
            ) / len(cell_records)
            warning = round(min(0.85, max(0.65, 0.75 + 0.03 * pressure)), 6)
            watch = round(max(0.45, warning - 0.20), 6)
            advisory = round(max(0.25, watch - 0.20), 6)
            policies.append(
                ZonePolicyArtifact(
                    version=f"zone-{hashlib.sha256(f'{cell_id}:{input_digest}'.encode()).hexdigest()[:12]}",
                    cell_id=cell_id,
                    created_at=created_at,
                    input_digest=input_digest,
                    sample_count=len(cell_records),
                    false_alarms=false_alarms,
                    misses=misses,
                    miss_cost=self.miss_cost,
                    false_alarm_cost=self.false_alarm_cost,
                    sensor_weight=sensor_weight,
                    corroboration_bonus=0.15,
                    advisory_threshold=advisory,
                    watch_threshold=watch,
                    warning_threshold=warning,
                )
            )
        return tuple(policies)


def _build_calibration(
    records: tuple[OutcomeRecord, ...],
    *,
    stream: str,
    field_name: str,
    created_at: datetime,
    input_digest: str,
) -> CalibrationArtifact:
    probabilities = [float(getattr(item, field_name)) for item in records]
    outcomes = [1.0 if item.label is OutcomeLabel.CONFIRMED else 0.0 for item in records]
    bins: list[CalibrationBin] = []
    calibrated_values: list[float] = []
    for lower, upper in zip(_BIN_EDGES[:-1], _BIN_EDGES[1:], strict=True):
        indices = [
            index
            for index, value in enumerate(probabilities)
            if lower <= value < upper or (value == 1.0 and upper == 1.0)
        ]
        if not indices:
            continue
        count = len(indices)
        positives = int(sum(outcomes[index] for index in indices))
        mean_prediction = sum(probabilities[index] for index in indices) / count
        calibrated_probability = (positives + 1) / (count + 2)
        item = CalibrationBin(
            lower=lower,
            upper=upper,
            count=count,
            positives=positives,
            mean_prediction=round(mean_prediction, 6),
            calibrated_probability=round(calibrated_probability, 6),
        )
        bins.append(item)
    calibration_bins = tuple(bins)
    calibrated_values = [
        _calibrate_candidate(value, calibration_bins) for value in probabilities
    ]
    brier_before = round(_brier(probabilities, outcomes), 6)
    brier_after = round(_brier(calibrated_values, outcomes), 6)
    promoted = len(records) >= 5 and brier_after < brier_before
    return CalibrationArtifact(
        version=f"cal-{stream}-{input_digest[:12]}",
        stream=stream,
        created_at=created_at,
        input_digest=input_digest,
        sample_count=len(records),
        brier_before=brier_before,
        brier_after=brier_after,
        promoted=promoted,
        promotion_reason=(
            "candidate improved replay Brier score"
            if promoted
            else "candidate retained for audit; promotion gate not met"
        ),
        bins=calibration_bins,
    )


def _outcome_digest(records: tuple[OutcomeRecord, ...]) -> str:
    payload = [item.to_dict() for item in records]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _brier(probabilities: list[float], outcomes: list[float]) -> float:
    squared_errors = (
        (probability - outcome) ** 2
        for probability, outcome in zip(probabilities, outcomes, strict=True)
    )
    return sum(squared_errors) / len(outcomes)


def _calibrate_candidate(value: float, bins: tuple[CalibrationBin, ...]) -> float:
    for item in bins:
        if item.lower <= value < item.upper or (value == 1.0 and item.upper == 1.0):
            return item.calibrated_probability
    return value


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _canonical_run(
    *,
    sequence: int,
    job_id: str,
    input_digest: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
    result: dict[str, object],
    previous_hash: str,
) -> bytes:
    return json.dumps(
        {
            "sequence": sequence,
            "job_id": job_id,
            "input_digest": input_digest,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "status": status,
            "result": result,
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _calibration_from_row(row: sqlite3.Row) -> CalibrationArtifact:
    return CalibrationArtifact(
        version=row["version"],
        stream=row["stream"],
        created_at=datetime.fromisoformat(row["created_at"]),
        input_digest=row["input_digest"],
        sample_count=int(row["sample_count"]),
        brier_before=float(row["brier_before"]),
        brier_after=float(row["brier_after"]),
        promoted=bool(row["promoted"]),
        promotion_reason=row["promotion_reason"],
        bins=tuple(CalibrationBin(**item) for item in json.loads(row["bins"])),
    )


def _zone_policy_from_row(row: sqlite3.Row) -> ZonePolicyArtifact:
    return ZonePolicyArtifact(
        version=row["version"],
        cell_id=row["cell_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        input_digest=row["input_digest"],
        sample_count=int(row["sample_count"]),
        false_alarms=int(row["false_alarms"]),
        misses=int(row["misses"]),
        miss_cost=float(row["miss_cost"]),
        false_alarm_cost=float(row["false_alarm_cost"]),
        sensor_weight=float(row["sensor_weight"]),
        corroboration_bonus=float(row["corroboration_bonus"]),
        advisory_threshold=float(row["advisory_threshold"]),
        watch_threshold=float(row["watch_threshold"]),
        warning_threshold=float(row["warning_threshold"]),
    )


def _run_from_row(row: sqlite3.Row) -> LearningRun:
    return LearningRun(
        sequence=int(row["sequence"]),
        job_id=row["job_id"],
        input_digest=row["input_digest"],
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]),
        status=row["status"],
        result=json.loads(row["result"]),
        previous_hash=row["previous_hash"],
        event_hash=row["event_hash"],
    )
