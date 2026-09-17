from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class OfficerAction(StrEnum):
    CONFIRM = "confirm"
    REQUEST_GROUND_VERIFICATION = "request_ground_verification"
    REJECT_AS_FALSE = "reject_as_false"
    ESCALATE_PUBLISH_CAP = "escalate_publish_cap"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    sequence: int
    event_id: str
    alert_id: str
    action: OfficerAction
    actor_id: str
    occurred_at: datetime
    reason: str
    payload: dict[str, object]
    previous_hash: str
    event_hash: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["action"] = self.action.value
        result["occurred_at"] = self.occurred_at.isoformat()
        return result


class DecisionAuditRepository:
    """Append-only, hash-chained local audit ledger for officer decisions."""

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
            CREATE TABLE IF NOT EXISTS decision_audit_events (
                sequence INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                alert_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                payload TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_decision_audit_alert_sequence
                ON decision_audit_events (alert_id, sequence);
            CREATE TRIGGER IF NOT EXISTS decision_audit_no_update
            BEFORE UPDATE ON decision_audit_events
            BEGIN
                SELECT RAISE(ABORT, 'decision audit events are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS decision_audit_no_delete
            BEFORE DELETE ON decision_audit_events
            BEGIN
                SELECT RAISE(ABORT, 'decision audit events are immutable');
            END;
            """
        )
        self._connection.commit()

    def append(
        self,
        *,
        alert_id: str,
        action: OfficerAction,
        actor_id: str,
        occurred_at: datetime,
        reason: str,
        payload: dict[str, object] | None = None,
    ) -> AuditEvent:
        if occurred_at.tzinfo is None:
            raise ValueError("audit event timestamp must include a timezone")
        if not actor_id.strip() or not reason.strip():
            raise ValueError("audit actor and reason are required")
        event_id = str(uuid.uuid4())
        event_payload = payload or {}
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT sequence, event_hash FROM decision_audit_events "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 1 if row is None else int(row["sequence"]) + 1
            previous_hash = "0" * 64 if row is None else str(row["event_hash"])
            canonical = _canonical_event(
                sequence=sequence,
                event_id=event_id,
                alert_id=alert_id,
                action=action,
                actor_id=actor_id,
                occurred_at=occurred_at,
                reason=reason,
                payload=event_payload,
                previous_hash=previous_hash,
            )
            event_hash = hashlib.sha256(canonical).hexdigest()
            self._connection.execute(
                """
                INSERT INTO decision_audit_events (
                    sequence, event_id, alert_id, action, actor_id, occurred_at,
                    reason, payload, previous_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence,
                    event_id,
                    alert_id,
                    action.value,
                    actor_id,
                    occurred_at.isoformat(),
                    reason,
                    json.dumps(event_payload, sort_keys=True, separators=(",", ":")),
                    previous_hash,
                    event_hash,
                ),
            )
            self._connection.commit()
        return AuditEvent(
            sequence=sequence,
            event_id=event_id,
            alert_id=alert_id,
            action=action,
            actor_id=actor_id,
            occurred_at=occurred_at,
            reason=reason,
            payload=event_payload,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )

    def list_for_alert(self, alert_id: str) -> tuple[AuditEvent, ...]:
        rows = self._connection.execute(
            "SELECT * FROM decision_audit_events WHERE alert_id = ? ORDER BY sequence",
            (alert_id,),
        ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def head(self) -> tuple[int, str]:
        row = self._connection.execute(
            "SELECT sequence, event_hash FROM decision_audit_events "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return (0, "0" * 64) if row is None else (int(row["sequence"]), row["event_hash"])

    def verify_chain(self) -> bool:
        rows = self._connection.execute(
            "SELECT * FROM decision_audit_events ORDER BY sequence"
        ).fetchall()
        previous_hash = "0" * 64
        for row in rows:
            event = _from_row(row)
            if event.previous_hash != previous_hash:
                return False
            expected = hashlib.sha256(
                _canonical_event(
                    sequence=event.sequence,
                    event_id=event.event_id,
                    alert_id=event.alert_id,
                    action=event.action,
                    actor_id=event.actor_id,
                    occurred_at=event.occurred_at,
                    reason=event.reason,
                    payload=event.payload,
                    previous_hash=event.previous_hash,
                )
            ).hexdigest()
            if not hmac.compare_digest(expected, event.event_hash):
                return False
            previous_hash = event.event_hash
        return True


def _canonical_event(
    *,
    sequence: int,
    event_id: str,
    alert_id: str,
    action: OfficerAction,
    actor_id: str,
    occurred_at: datetime,
    reason: str,
    payload: dict[str, object],
    previous_hash: str,
) -> bytes:
    return json.dumps(
        {
            "sequence": sequence,
            "event_id": event_id,
            "alert_id": alert_id,
            "action": action.value,
            "actor_id": actor_id,
            "occurred_at": occurred_at.isoformat(),
            "reason": reason,
            "payload": payload,
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _from_row(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        sequence=int(row["sequence"]),
        event_id=row["event_id"],
        alert_id=row["alert_id"],
        action=OfficerAction(row["action"]),
        actor_id=row["actor_id"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        reason=row["reason"],
        payload=json.loads(row["payload"]),
        previous_hash=row["previous_hash"],
        event_hash=row["event_hash"],
    )
