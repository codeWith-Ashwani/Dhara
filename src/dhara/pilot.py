from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from dhara.audit import DecisionAuditRepository
from dhara.authority import HeldOutCalibrationEvaluator
from dhara.learning import LearningRepository, LearningService


class OperationalMetrics:
    """Low-cardinality process metrics without identity-bearing labels."""

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._lock = threading.Lock()

    def increment(self, name: str, amount: float = 1.0) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + amount

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {
                "counters": dict(sorted(self._counters.items())),
                "gauges": dict(sorted(self._gauges.items())),
            }

    def prometheus(self) -> str:
        snapshot = self.snapshot()
        lines = [
            f"dhara_{name}_total {value:g}"
            for name, value in snapshot["counters"].items()
        ]
        lines.extend(
            f"dhara_{name} {value:g}" for name, value in snapshot["gauges"].items()
        )
        return "\n".join(lines) + "\n"


class JobLeaseRepository:
    """SQLite lease boundary for a future single-active nightly worker."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        if path != Path(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS job_leases (
                job_name TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def acquire(
        self,
        *,
        job_name: str,
        owner_id: str,
        now: datetime,
        ttl: timedelta = timedelta(minutes=15),
    ) -> bool:
        if now.tzinfo is None:
            raise ValueError("lease timestamp must include a timezone")
        if ttl <= timedelta(0):
            raise ValueError("lease TTL must be positive")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT owner_id, expires_at FROM job_leases WHERE job_name = ?",
                (job_name,),
            ).fetchone()
            if row is not None and datetime.fromisoformat(row["expires_at"]) > now:
                self._connection.rollback()
                return False
            self._connection.execute(
                """
                INSERT INTO job_leases (job_name, owner_id, acquired_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(job_name) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at
                """,
                (job_name, owner_id, now.isoformat(), (now + ttl).isoformat()),
            )
            self._connection.commit()
        return True

    def release(self, *, job_name: str, owner_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM job_leases WHERE job_name = ? AND owner_id = ?",
                (job_name, owner_id),
            )
            self._connection.commit()
        return cursor.rowcount == 1


class PilotNightlyCoordinator:
    def __init__(
        self,
        *,
        leases: JobLeaseRepository,
        learning: LearningService,
        heldout: HeldOutCalibrationEvaluator,
        anchors: FileAuditAnchorService,
        metrics: OperationalMetrics,
    ) -> None:
        self.leases = leases
        self.learning = learning
        self.heldout = heldout
        self.anchors = anchors
        self.metrics = metrics

    def run(self, *, as_of: datetime, owner_id: str) -> dict[str, object]:
        acquired = self.leases.acquire(
            job_name="nightly-learning",
            owner_id=owner_id,
            now=as_of,
        )
        if not acquired:
            self.metrics.increment("nightly_job_lease_conflicts")
            return {"status": "lease_conflict", "owner_id": owner_id}
        try:
            learning_run, learning_reused = self.learning.run(as_of=as_of)
            evaluations = self.heldout.evaluate(as_of=as_of)
            anchor, anchor_reused = self.anchors.create(created_at=as_of)
            self.metrics.increment("nightly_jobs_completed")
            self.metrics.gauge("heldout_samples", evaluations[0].sample_count)
            self.metrics.gauge(
                "heldout_promoted_streams",
                sum(item.promoted for item in evaluations),
            )
            return {
                "status": "completed",
                "owner_id": owner_id,
                "learning": learning_run.to_dict(reused=learning_reused),
                "heldout": [item.to_dict() for item in evaluations],
                "anchor": anchor.to_dict(),
                "anchor_reused": anchor_reused,
            }
        except Exception:
            self.metrics.increment("nightly_jobs_failed")
            raise
        finally:
            self.leases.release(job_name="nightly-learning", owner_id=owner_id)


@dataclass(frozen=True, slots=True)
class AuditAnchorReceipt:
    sequence: int
    anchor_id: str
    created_at: datetime
    decision_sequence: int
    decision_hash: str
    learning_sequence: int
    learning_hash: str
    previous_anchor_hash: str
    anchor_hash: str
    signature: str
    key_id: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        return result


class FileAuditAnchorService:
    """Writes signed chain heads to a separate append-only exchange file."""

    def __init__(
        self,
        *,
        path: str | Path,
        secret: bytes,
        decision_audit: DecisionAuditRepository,
        learning: LearningRepository,
        key_id: str = "local-pilot-anchor-v1",
        ephemeral_key: bool = False,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("audit anchor secret must contain at least 32 bytes")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._secret = secret
        self.decision_audit = decision_audit
        self.learning = learning
        self.key_id = key_id
        self.ephemeral_key = ephemeral_key
        self._lock = threading.Lock()

    @classmethod
    def from_environment(
        cls,
        *,
        path: str | Path,
        decision_audit: DecisionAuditRepository,
        learning: LearningRepository,
        environment: Mapping[str, str] | None = None,
    ) -> FileAuditAnchorService:
        values = os.environ if environment is None else environment
        configured = values.get("DHARA_AUDIT_ANCHOR_SECRET")
        return cls(
            path=path,
            secret=configured.encode() if configured else secrets.token_bytes(32),
            decision_audit=decision_audit,
            learning=learning,
            ephemeral_key=configured is None,
        )

    def create(self, *, created_at: datetime) -> tuple[AuditAnchorReceipt, bool]:
        if created_at.tzinfo is None:
            raise ValueError("anchor timestamp must include a timezone")
        decision_sequence, decision_hash = self.decision_audit.head()
        learning_run = self.learning.latest_run()
        learning_sequence = learning_run.sequence if learning_run else 0
        learning_hash = learning_run.event_hash if learning_run else "0" * 64
        with self._lock:
            receipts = self._load()
            if receipts:
                latest = receipts[-1]
                if (
                    latest.decision_sequence == decision_sequence
                    and latest.decision_hash == decision_hash
                    and latest.learning_sequence == learning_sequence
                    and latest.learning_hash == learning_hash
                ):
                    return latest, True
            sequence = len(receipts) + 1
            previous_hash = receipts[-1].anchor_hash if receipts else "0" * 64
            anchor_id = str(uuid.uuid4())
            canonical = _canonical_anchor(
                sequence=sequence,
                anchor_id=anchor_id,
                created_at=created_at,
                decision_sequence=decision_sequence,
                decision_hash=decision_hash,
                learning_sequence=learning_sequence,
                learning_hash=learning_hash,
                previous_anchor_hash=previous_hash,
                key_id=self.key_id,
            )
            anchor_hash = hashlib.sha256(canonical).hexdigest()
            signature = hmac.new(
                self._secret, anchor_hash.encode(), hashlib.sha256
            ).hexdigest()
            receipt = AuditAnchorReceipt(
                sequence=sequence,
                anchor_id=anchor_id,
                created_at=created_at,
                decision_sequence=decision_sequence,
                decision_hash=decision_hash,
                learning_sequence=learning_sequence,
                learning_hash=learning_hash,
                previous_anchor_hash=previous_hash,
                anchor_hash=anchor_hash,
                signature=signature,
                key_id=self.key_id,
            )
            with self.path.open("a", encoding="utf-8", newline="\n") as destination:
                destination.write(
                    json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
                destination.flush()
                os.fsync(destination.fileno())
            return receipt, False

    def verify(self) -> bool:
        receipts = self._load()
        previous_hash = "0" * 64
        for expected_sequence, receipt in enumerate(receipts, start=1):
            if (
                receipt.sequence != expected_sequence
                or receipt.previous_anchor_hash != previous_hash
            ):
                return False
            canonical = _canonical_anchor(
                sequence=receipt.sequence,
                anchor_id=receipt.anchor_id,
                created_at=receipt.created_at,
                decision_sequence=receipt.decision_sequence,
                decision_hash=receipt.decision_hash,
                learning_sequence=receipt.learning_sequence,
                learning_hash=receipt.learning_hash,
                previous_anchor_hash=receipt.previous_anchor_hash,
                key_id=receipt.key_id,
            )
            expected_hash = hashlib.sha256(canonical).hexdigest()
            expected_signature = hmac.new(
                self._secret, expected_hash.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected_hash, receipt.anchor_hash):
                return False
            if not hmac.compare_digest(expected_signature, receipt.signature):
                return False
            previous_hash = receipt.anchor_hash
        return True

    def status(self) -> dict[str, object]:
        receipts = self._load()
        return {
            "sink": "append_only_exchange_file",
            "receipts": len(receipts),
            "chain_valid": self.verify(),
            "latest": receipts[-1].to_dict() if receipts else None,
            "ephemeral_key": self.ephemeral_key,
        }

    def _load(self) -> list[AuditAnchorReceipt]:
        if not self.path.exists():
            return []
        return [
            _anchor_from_dict(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def _canonical_anchor(
    *,
    sequence: int,
    anchor_id: str,
    created_at: datetime,
    decision_sequence: int,
    decision_hash: str,
    learning_sequence: int,
    learning_hash: str,
    previous_anchor_hash: str,
    key_id: str,
) -> bytes:
    return json.dumps(
        {
            "sequence": sequence,
            "anchor_id": anchor_id,
            "created_at": created_at.isoformat(),
            "decision_sequence": decision_sequence,
            "decision_hash": decision_hash,
            "learning_sequence": learning_sequence,
            "learning_hash": learning_hash,
            "previous_anchor_hash": previous_anchor_hash,
            "key_id": key_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _anchor_from_dict(value: dict[str, object]) -> AuditAnchorReceipt:
    return AuditAnchorReceipt(
        sequence=int(value["sequence"]),
        anchor_id=str(value["anchor_id"]),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        decision_sequence=int(value["decision_sequence"]),
        decision_hash=str(value["decision_hash"]),
        learning_sequence=int(value["learning_sequence"]),
        learning_hash=str(value["learning_hash"]),
        previous_anchor_hash=str(value["previous_anchor_hash"]),
        anchor_hash=str(value["anchor_hash"]),
        signature=str(value["signature"]),
        key_id=str(value["key_id"]),
    )
