from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from dhara.community import (
    CommunityReport,
    DepthOrdinal,
    QuarantineReason,
    ReportChannel,
    ReporterProfile,
    ReporterRole,
    ReportStatus,
)


class _SQLiteStore:
    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        if path != Path(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = str(database_path)
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()

    def close(self) -> None:
        self._connection.close()


class SQLiteReporterTrustStore(_SQLiteStore):
    def __init__(self, database_path: str | Path) -> None:
        super().__init__(database_path)
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS reporter_trust_profiles (
                reporter_id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                alpha REAL NOT NULL CHECK (alpha > 0),
                beta REAL NOT NULL CHECK (beta > 0),
                last_activity TEXT
            );
            """
        )
        self._connection.commit()

    def load(self, reporter_id: str) -> ReporterProfile | None:
        row = self._connection.execute(
            "SELECT * FROM reporter_trust_profiles WHERE reporter_id = ?",
            (reporter_id,),
        ).fetchone()
        if row is None:
            return None
        return ReporterProfile(
            reporter_id=row["reporter_id"],
            role=ReporterRole(row["role"]),
            alpha=float(row["alpha"]),
            beta=float(row["beta"]),
            last_activity=(
                datetime.fromisoformat(row["last_activity"])
                if row["last_activity"] is not None
                else None
            ),
        )

    def save(self, profile: ReporterProfile) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO reporter_trust_profiles (
                    reporter_id, role, alpha, beta, last_activity
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(reporter_id) DO UPDATE SET
                    role = excluded.role,
                    alpha = excluded.alpha,
                    beta = excluded.beta,
                    last_activity = excluded.last_activity
                """,
                (
                    profile.reporter_id,
                    profile.role.value,
                    profile.alpha,
                    profile.beta,
                    profile.last_activity.isoformat()
                    if profile.last_activity is not None
                    else None,
                ),
            )
            self._connection.commit()


class SQLiteReportStore(_SQLiteStore):
    def __init__(self, database_path: str | Path) -> None:
        super().__init__(database_path)
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS community_reports (
                report_id TEXT PRIMARY KEY,
                reporter_id TEXT NOT NULL,
                reporter_role TEXT NOT NULL,
                device_id TEXT NOT NULL,
                device_counter INTEGER NOT NULL,
                captured_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                cell_id TEXT NOT NULL,
                geohash7 TEXT NOT NULL,
                channel TEXT NOT NULL,
                claimed_depth TEXT NOT NULL,
                predicted_depth TEXT,
                classifier_class TEXT NOT NULL,
                classifier_confidence REAL NOT NULL,
                trust_at_submit REAL NOT NULL,
                geo_integrity REAL NOT NULL,
                contribution REAL NOT NULL,
                status TEXT NOT NULL,
                signature_valid INTEGER NOT NULL,
                attestation_passed INTEGER,
                quarantine_reason TEXT,
                perceptual_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS community_reports_cell_captured_idx
                ON community_reports (cell_id, captured_at DESC);
            CREATE INDEX IF NOT EXISTS community_reports_device_received_idx
                ON community_reports (device_id, received_at DESC);
            CREATE TRIGGER IF NOT EXISTS community_reports_no_update
            BEFORE UPDATE ON community_reports BEGIN
                SELECT RAISE(ABORT, 'community reports are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS community_reports_no_delete
            BEFORE DELETE ON community_reports BEGIN
                SELECT RAISE(ABORT, 'community reports are immutable');
            END;
            """
        )
        self._connection.commit()

    def add(self, report: CommunityReport) -> None:
        with self._lock:
            existing = self._connection.execute(
                "SELECT * FROM community_reports WHERE report_id = ?",
                (report.report_id,),
            ).fetchone()
            if existing is not None:
                if _report_from_row(existing) != report:
                    raise ValueError("report ID has conflicting immutable content")
                return
            self._connection.execute(
                """
                INSERT INTO community_reports (
                    report_id, reporter_id, reporter_role, device_id, device_counter,
                    captured_at, received_at, latitude, longitude, cell_id, geohash7,
                    channel, claimed_depth, predicted_depth, classifier_class,
                    classifier_confidence, trust_at_submit, geo_integrity, contribution,
                    status, signature_valid, attestation_passed, quarantine_reason,
                    perceptual_hash
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    report.report_id,
                    report.reporter_id,
                    report.reporter_role.value,
                    report.device_id,
                    report.device_counter,
                    report.captured_at.isoformat(),
                    report.received_at.isoformat(),
                    report.latitude,
                    report.longitude,
                    report.cell_id,
                    report.geohash7,
                    report.channel.value,
                    report.claimed_depth.value,
                    report.predicted_depth.value if report.predicted_depth else None,
                    report.classifier_class,
                    report.classifier_confidence,
                    report.trust_at_submit,
                    report.geo_integrity,
                    report.contribution,
                    report.status.value,
                    int(report.signature_valid),
                    int(report.attestation_passed)
                    if report.attestation_passed is not None
                    else None,
                    report.quarantine_reason.value if report.quarantine_reason else None,
                    str(report.perceptual_hash)
                    if report.perceptual_hash is not None
                    else None,
                ),
            )
            self._connection.commit()

    def all(self) -> tuple[CommunityReport, ...]:
        rows = self._connection.execute(
            "SELECT * FROM community_reports ORDER BY received_at, report_id"
        ).fetchall()
        return tuple(_report_from_row(row) for row in rows)

    def recent(self, *, as_of: datetime, window: timedelta) -> list[CommunityReport]:
        earliest = as_of - window
        rows = self._connection.execute(
            """
            SELECT * FROM community_reports
            WHERE captured_at >= ? AND captured_at <= ?
            ORDER BY captured_at, report_id
            """,
            (earliest.isoformat(), as_of.isoformat()),
        ).fetchall()
        return [_report_from_row(row) for row in rows]


def _report_from_row(row: sqlite3.Row) -> CommunityReport:
    return CommunityReport(
        report_id=row["report_id"],
        reporter_id=row["reporter_id"],
        reporter_role=ReporterRole(row["reporter_role"]),
        device_id=row["device_id"],
        device_counter=int(row["device_counter"]),
        captured_at=datetime.fromisoformat(row["captured_at"]),
        received_at=datetime.fromisoformat(row["received_at"]),
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
        cell_id=row["cell_id"],
        geohash7=row["geohash7"],
        channel=ReportChannel(row["channel"]),
        claimed_depth=DepthOrdinal(row["claimed_depth"]),
        predicted_depth=(
            DepthOrdinal(row["predicted_depth"])
            if row["predicted_depth"] is not None
            else None
        ),
        classifier_class=row["classifier_class"],
        classifier_confidence=float(row["classifier_confidence"]),
        trust_at_submit=float(row["trust_at_submit"]),
        geo_integrity=float(row["geo_integrity"]),
        contribution=float(row["contribution"]),
        status=ReportStatus(row["status"]),
        signature_valid=bool(row["signature_valid"]),
        attestation_passed=(
            bool(row["attestation_passed"])
            if row["attestation_passed"] is not None
            else None
        ),
        quarantine_reason=(
            QuarantineReason(row["quarantine_reason"])
            if row["quarantine_reason"] is not None
            else None
        ),
        perceptual_hash=(
            int(row["perceptual_hash"])
            if row["perceptual_hash"] is not None
            else None
        ),
    )
