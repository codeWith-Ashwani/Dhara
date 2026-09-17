from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from dhara.domain import Metric, Observation, Provider, QualityStatus, StoredObservation


class ObservationRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        path = Path(database_path)
        if path != Path(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                cell_id TEXT NOT NULL,
                status TEXT NOT NULL,
                quality_flags TEXT NOT NULL,
                metadata TEXT NOT NULL,
                UNIQUE (source, external_id)
            );
            CREATE INDEX IF NOT EXISTS idx_observations_cell_time
                ON observations (cell_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_observations_metric_time
                ON observations (metric, observed_at);
            """
        )
        self._connection.commit()

    def put(self, observation: Observation) -> StoredObservation:
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO observations (
                source, external_id, metric, value, unit, observed_at, received_at,
                latitude, longitude, cell_id, status, quality_flags, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.source.value,
                observation.external_id,
                observation.metric.value,
                observation.value,
                observation.unit,
                observation.observed_at.isoformat(),
                observation.received_at.isoformat(),
                observation.latitude,
                observation.longitude,
                observation.cell_id,
                observation.status.value,
                json.dumps(observation.quality_flags),
                json.dumps(observation.metadata, sort_keys=True),
            ),
        )
        created = cursor.rowcount == 1
        row = self._connection.execute(
            "SELECT * FROM observations WHERE source = ? AND external_id = ?",
            (observation.source.value, observation.external_id),
        ).fetchone()
        self._connection.commit()
        if row is None:
            raise RuntimeError("observation insert did not produce a row")
        return StoredObservation(id=row["id"], observation=_from_row(row), created=created)

    def list_for_cell(
        self, cell_id: str, *, since: datetime | None = None, limit: int = 10_000
    ) -> list[Observation]:
        parameters: list[object] = [cell_id]
        predicate = "cell_id = ?"
        if since is not None:
            predicate += " AND observed_at >= ?"
            parameters.append(since.isoformat())
        parameters.append(limit)
        rows = self._connection.execute(
            f"SELECT * FROM observations WHERE {predicate} ORDER BY observed_at ASC LIMIT ?",  # noqa: S608
            parameters,
        ).fetchall()
        return [_from_row(row) for row in rows]

    def all(self) -> Iterable[Observation]:
        rows = self._connection.execute(
            "SELECT * FROM observations ORDER BY observed_at ASC"
        ).fetchall()
        return [_from_row(row) for row in rows]

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS count FROM observations").fetchone()
        return int(row["count"])


def _from_row(row: sqlite3.Row) -> Observation:
    return Observation(
        source=Provider(row["source"]),
        external_id=row["external_id"],
        metric=Metric(row["metric"]),
        value=row["value"],
        unit=row["unit"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        received_at=datetime.fromisoformat(row["received_at"]),
        latitude=row["latitude"],
        longitude=row["longitude"],
        cell_id=row["cell_id"],
        status=QualityStatus(row["status"]),
        quality_flags=tuple(json.loads(row["quality_flags"])),
        metadata=json.loads(row["metadata"]),
    )
