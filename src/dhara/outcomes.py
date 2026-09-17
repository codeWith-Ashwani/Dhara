from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class OutcomeLabel(StrEnum):
    CONFIRMED = "confirmed"
    REFUTED = "refuted"


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    outcome_id: str
    alert_id: str
    cell_id: str
    label: OutcomeLabel
    observed_at: datetime
    recorded_at: datetime
    actor_id: str
    source: str
    notes: str
    report_ids: tuple[str, ...]
    reporter_ids: tuple[str, ...]
    sensor_confidence: float
    crowd_confidence: float
    fused_confidence: float
    predicted_tier: str
    data_classification: str = "operational_unverified"

    def to_dict(self, *, include_reporters: bool = True) -> dict[str, object]:
        result = asdict(self)
        result["label"] = self.label.value
        result["observed_at"] = self.observed_at.isoformat()
        result["recorded_at"] = self.recorded_at.isoformat()
        if not include_reporters:
            result.pop("report_ids", None)
            result.pop("reporter_ids", None)
            result.pop("actor_id", None)
            result.pop("notes", None)
        return result


class OutcomeRepository:
    """Immutable alert outcomes; one final label per alert in the prototype."""

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
            CREATE TABLE IF NOT EXISTS alert_outcomes (
                outcome_id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL UNIQUE,
                cell_id TEXT NOT NULL,
                label TEXT NOT NULL CHECK (label IN ('confirmed', 'refuted')),
                observed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                source TEXT NOT NULL,
                notes TEXT NOT NULL,
                report_ids TEXT NOT NULL,
                reporter_ids TEXT NOT NULL,
                sensor_confidence REAL NOT NULL,
                crowd_confidence REAL NOT NULL,
                fused_confidence REAL NOT NULL,
                predicted_tier TEXT NOT NULL,
                data_classification TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_alert_outcomes_recorded
                ON alert_outcomes (recorded_at);
            CREATE INDEX IF NOT EXISTS idx_alert_outcomes_cell
                ON alert_outcomes (cell_id, recorded_at);
            CREATE TRIGGER IF NOT EXISTS alert_outcomes_no_update
            BEFORE UPDATE ON alert_outcomes
            BEGIN
                SELECT RAISE(ABORT, 'alert outcomes are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS alert_outcomes_no_delete
            BEFORE DELETE ON alert_outcomes
            BEGIN
                SELECT RAISE(ABORT, 'alert outcomes are immutable');
            END;
            """
        )
        self._connection.commit()

    def append(
        self,
        *,
        alert_id: str,
        cell_id: str,
        label: OutcomeLabel,
        observed_at: datetime,
        recorded_at: datetime,
        actor_id: str,
        source: str,
        notes: str,
        report_ids: tuple[str, ...],
        reporter_ids: tuple[str, ...],
        sensor_confidence: float,
        crowd_confidence: float,
        fused_confidence: float,
        predicted_tier: str,
        data_classification: str = "operational_unverified",
    ) -> OutcomeRecord:
        if observed_at.tzinfo is None or recorded_at.tzinfo is None:
            raise ValueError("outcome timestamps must include a timezone")
        if not actor_id.strip() or not source.strip() or not notes.strip():
            raise ValueError("outcome actor, source, and notes are required")
        for value in (sensor_confidence, crowd_confidence, fused_confidence):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("outcome confidence values must be between 0 and 1")
        with self._lock:
            existing = self.get_for_alert(alert_id)
            if existing is not None:
                if existing.label is not label:
                    raise ValueError("alert already has a conflicting immutable outcome")
                return existing
            record = OutcomeRecord(
                outcome_id=str(uuid.uuid4()),
                alert_id=alert_id,
                cell_id=cell_id,
                label=label,
                observed_at=observed_at,
                recorded_at=recorded_at,
                actor_id=actor_id,
                source=source,
                notes=notes,
                report_ids=report_ids,
                reporter_ids=reporter_ids,
                sensor_confidence=float(sensor_confidence),
                crowd_confidence=float(crowd_confidence),
                fused_confidence=float(fused_confidence),
                predicted_tier=predicted_tier,
                data_classification=data_classification,
            )
            self._connection.execute(
                """
                INSERT INTO alert_outcomes (
                    outcome_id, alert_id, cell_id, label, observed_at, recorded_at,
                    actor_id, source, notes, report_ids, reporter_ids,
                    sensor_confidence, crowd_confidence, fused_confidence,
                    predicted_tier, data_classification
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.outcome_id,
                    record.alert_id,
                    record.cell_id,
                    record.label.value,
                    record.observed_at.isoformat(),
                    record.recorded_at.isoformat(),
                    record.actor_id,
                    record.source,
                    record.notes,
                    json.dumps(record.report_ids),
                    json.dumps(record.reporter_ids),
                    record.sensor_confidence,
                    record.crowd_confidence,
                    record.fused_confidence,
                    record.predicted_tier,
                    record.data_classification,
                ),
            )
            self._connection.commit()
        return record

    def get_for_alert(self, alert_id: str) -> OutcomeRecord | None:
        row = self._connection.execute(
            "SELECT * FROM alert_outcomes WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()
        return None if row is None else _from_row(row)

    def all(self) -> tuple[OutcomeRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM alert_outcomes ORDER BY recorded_at, outcome_id"
        ).fetchall()
        return tuple(_from_row(row) for row in rows)


def _from_row(row: sqlite3.Row) -> OutcomeRecord:
    return OutcomeRecord(
        outcome_id=row["outcome_id"],
        alert_id=row["alert_id"],
        cell_id=row["cell_id"],
        label=OutcomeLabel(row["label"]),
        observed_at=datetime.fromisoformat(row["observed_at"]),
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
        actor_id=row["actor_id"],
        source=row["source"],
        notes=row["notes"],
        report_ids=tuple(json.loads(row["report_ids"])),
        reporter_ids=tuple(json.loads(row["reporter_ids"])),
        sensor_confidence=float(row["sensor_confidence"]),
        crowd_confidence=float(row["crowd_confidence"]),
        fused_confidence=float(row["fused_confidence"]),
        predicted_tier=row["predicted_tier"],
        data_classification=row["data_classification"],
    )
