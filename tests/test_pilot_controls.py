from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dhara.api import create_app
from dhara.audit import DecisionAuditRepository, OfficerAction
from dhara.auth import (
    AuthenticationError,
    AuthorizationError,
    OperatorAuthenticator,
    OperatorRole,
)
from dhara.authority import (
    AuthorityEventRecord,
    AuthorityEventRepository,
    HeldOutCalibrationEvaluator,
)
from dhara.community import CommunityEngine, QuarantineReason, ReporterRole, ReporterTrustEngine
from dhara.community_persistence import SQLiteReporterTrustStore, SQLiteReportStore
from dhara.learning import LearningRepository
from dhara.outcomes import OutcomeLabel
from dhara.pilot import FileAuditAnchorService, JobLeaseRepository


def _authority_events(now) -> tuple[AuthorityEventRecord, ...]:
    records: list[AuthorityEventRecord] = []
    for group_index in range(4):
        fixtures = (
            (OutcomeLabel.CONFIRMED, 0.60, 0.80, 0.80),
            (OutcomeLabel.CONFIRMED, 0.60, 0.80, 0.20),
            (OutcomeLabel.REFUTED, 0.40, 0.20, 0.80),
            (OutcomeLabel.REFUTED, 0.40, 0.20, 0.20),
        )
        for row_index, (label, sensor, crowd, fused) in enumerate(fixtures):
            records.append(
                AuthorityEventRecord(
                    authority_event_id=f"event-{group_index}-{row_index}",
                    event_group=f"monsoon-event-{group_index}",
                    cell_id="8960885016bffff",
                    observed_at=now - timedelta(days=group_index, minutes=row_index),
                    label=label,
                    sensor_confidence=sensor,
                    crowd_confidence=crowd,
                    fused_confidence=fused,
                    source_reference=f"authority://event/{group_index}/{row_index}",
                    approved_by="supervisor-1",
                    imported_at=now,
                    data_classification="synthetic_authority_fixture",
                )
            )
    return tuple(records)


def test_operator_tokens_are_short_lived_signed_and_role_gated(community_harness) -> None:
    authenticator = OperatorAuthenticator(b"operator-secret-for-tests-32-bytes!!")
    token = authenticator.issue_token(
        subject="operator-1",
        role=OperatorRole.OPERATOR,
        issued_at=community_harness.now,
        lifetime=timedelta(minutes=20),
    )
    principal = authenticator.authenticate(
        token,
        now=community_harness.now + timedelta(minutes=1),
    )
    assert principal.subject == "operator-1"
    authenticator.require_role(principal, OperatorRole.VIEWER)
    with pytest.raises(AuthorizationError, match="supervisor"):
        authenticator.require_role(principal, OperatorRole.SUPERVISOR)
    with pytest.raises(AuthenticationError, match="signature"):
        authenticator.authenticate(token[:-1] + "x", now=community_harness.now)
    with pytest.raises(AuthenticationError, match="expired"):
        authenticator.authenticate(
            token,
            now=community_harness.now + timedelta(minutes=21),
        )


def test_operator_api_denies_missing_and_underprivileged_tokens(tmp_path: Path) -> None:
    app = create_app(tmp_path / "rbac.db")
    now = datetime.now(UTC)
    viewer_token = app.state.operator_authenticator.issue_token(
        subject="viewer-1",
        role=OperatorRole.VIEWER,
        issued_at=now,
    )
    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}
    with TestClient(app) as client:
        missing = client.get("/v1/triage")
        forbidden = client.post(
            "/v1/replays",
            json={"path": "pune_ward14_48h.jsonl"},
            headers=viewer_headers,
        )
        visible = client.get("/v1/triage", headers=viewer_headers)

    assert missing.status_code == 401
    assert forbidden.status_code == 403
    assert visible.status_code == 200
    assert app.state.metrics.snapshot()["counters"] == {
        "operator_authentication_denied": 1.0,
        "operator_authorization_denied": 1.0,
    }


def test_reports_trust_and_replay_counters_survive_restart(
    tmp_path: Path,
    community_harness,
) -> None:
    database = tmp_path / "community.db"
    trust_store = SQLiteReporterTrustStore(database)
    report_store = SQLiteReportStore(database)
    trust = ReporterTrustEngine(trust_store)
    trust.register("citizen-2", ReporterRole.CITIZEN)
    engine = CommunityEngine(
        trust=trust,
        store=report_store,
        classifier=community_harness.engine.classifier,
        attestation=community_harness.engine.attestation,
        signatures=community_harness.engine.signatures,
    )
    first = engine.submit(
        community_harness.submission(
            report_id="durable-1",
            reporter_id="citizen-2",
            device_id="device-1",
            counter=1,
            location=community_harness.locations[0],
            media_reference="legit-1",
        )
    )
    trust.update("citizen-2", confirmed=True, at=community_harness.now)
    expected_trust = trust.trust("citizen-2", as_of=community_harness.now)
    trust_store.close()
    report_store.close()

    restarted_trust_store = SQLiteReporterTrustStore(database)
    restarted_report_store = SQLiteReportStore(database)
    restarted = CommunityEngine(
        trust=ReporterTrustEngine(restarted_trust_store),
        store=restarted_report_store,
        classifier=community_harness.engine.classifier,
        attestation=community_harness.engine.attestation,
        signatures=community_harness.engine.signatures,
    )
    restarted.trust.register("citizen-2", ReporterRole.CITIZEN)
    replayed = restarted.submit(
        community_harness.submission(
            report_id="durable-replay",
            reporter_id="citizen-2",
            device_id="device-1",
            counter=1,
            location=community_harness.locations[1],
            media_reference="legit-2",
        )
    )

    assert restarted.store.all()[0].report_id == first.report_id
    assert restarted.trust.trust("citizen-2", as_of=community_harness.now) == expected_trust
    assert replayed.quarantine_reason is QuarantineReason.REPLAYED_COUNTER
    external = sqlite3.connect(database)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        external.execute("DELETE FROM community_reports")
    external.close()


def test_authority_ledger_and_grouped_heldout_gate(tmp_path: Path, community_harness) -> None:
    database = tmp_path / "authority.db"
    repository = AuthorityEventRepository(database)
    records = _authority_events(community_harness.now)
    result = repository.append_many(records)
    repeated = repository.append_many(records)
    artifacts = HeldOutCalibrationEvaluator(repository).evaluate(
        as_of=community_harness.now
    )
    by_stream = {item.stream: item for item in artifacts}

    assert result == {"total": 16, "created": 16, "duplicates": 0}
    assert repeated == {"total": 16, "created": 0, "duplicates": 16}
    assert by_stream["sensor"].event_group_count == 4
    assert by_stream["sensor"].promoted is True
    assert by_stream["sensor"].improvement_ci_low > 0
    assert by_stream["fused"].promoted is False
    assert by_stream["fused"].improvement_ci_low <= 0

    new_record = replace(records[0], authority_event_id="event-new")
    conflicting = replace(records[0], sensor_confidence=0.1)
    with pytest.raises(ValueError, match="conflicting"):
        repository.append_many((new_record, conflicting))
    assert repository.get("event-new") is None

    external = sqlite3.connect(database)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        external.execute("DELETE FROM authority_events")
    external.close()


def test_job_lease_allows_only_one_owner_until_expiry(tmp_path: Path, community_harness) -> None:
    leases = JobLeaseRepository(tmp_path / "leases.db")
    assert leases.acquire(
        job_name="nightly-learning",
        owner_id="worker-1",
        now=community_harness.now,
    )
    assert not leases.acquire(
        job_name="nightly-learning",
        owner_id="worker-2",
        now=community_harness.now + timedelta(minutes=1),
    )
    assert leases.acquire(
        job_name="nightly-learning",
        owner_id="worker-2",
        now=community_harness.now + timedelta(minutes=16),
    )


def test_signed_audit_anchor_is_idempotent_and_detects_tampering(
    tmp_path: Path,
    community_harness,
) -> None:
    database = tmp_path / "anchor.db"
    anchors_path = tmp_path / "anchors.jsonl"
    audit = DecisionAuditRepository(database)
    learning = LearningRepository(database)
    audit.append(
        alert_id="alert-1",
        action=OfficerAction.CONFIRM,
        actor_id="supervisor-1",
        occurred_at=community_harness.now,
        reason="Synthetic anchor test",
    )
    service = FileAuditAnchorService(
        path=anchors_path,
        secret=b"audit-anchor-secret-for-tests-32b!!",
        decision_audit=audit,
        learning=learning,
    )
    first, reused = service.create(created_at=community_harness.now)
    repeated, repeated_reused = service.create(
        created_at=community_harness.now + timedelta(minutes=1)
    )

    assert reused is False
    assert repeated_reused is True
    assert repeated.anchor_id == first.anchor_id
    assert service.verify() is True

    payload = json.loads(anchors_path.read_text(encoding="utf-8"))
    payload["decision_hash"] = "f" * 64
    anchors_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert service.verify() is False


def test_supervisor_can_run_complete_nightly_pilot_job(
    tmp_path: Path,
    community_harness,
) -> None:
    app = create_app(tmp_path / "nightly.db")
    token = app.state.operator_authenticator.issue_token(
        subject="supervisor-1",
        role=OperatorRole.SUPERVISOR,
        issued_at=datetime.now(UTC),
    )
    headers = {"Authorization": f"Bearer {token}"}
    app.state.outcome_repository.append(
        alert_id="nightly-outcome-1",
        cell_id="8960885016bffff",
        label=OutcomeLabel.CONFIRMED,
        observed_at=community_harness.now,
        recorded_at=community_harness.now,
        actor_id="supervisor-1",
        source="synthetic_rehearsal",
        notes="Nightly integration fixture",
        report_ids=("nightly-report-1",),
        reporter_ids=("nightly-reporter-1",),
        sensor_confidence=0.6,
        crowd_confidence=0.8,
        fused_confidence=0.7,
        predicted_tier="watch",
        data_classification="synthetic",
    )
    events = [item.to_dict() for item in _authority_events(community_harness.now)]

    with TestClient(app) as client:
        imported = client.post(
            "/v1/authority/events/import",
            json={"events": events},
            headers=headers,
        )
        nightly = client.post(
            "/v1/jobs/nightly/run",
            json={"as_of": community_harness.now.isoformat()},
            headers=headers,
        )
        repeated = client.post(
            "/v1/jobs/nightly/run",
            json={"as_of": (community_harness.now + timedelta(hours=1)).isoformat()},
            headers=headers,
        )
        public = client.get("/v1/public/heldout-calibration")
        operations = client.get("/v1/operations/status", headers=headers)
        metrics = client.get("/metrics", headers=headers)

    assert imported.json()["created"] == 16
    assert nightly.status_code == 200
    assert nightly.json()["status"] == "completed"
    assert nightly.json()["learning"]["reused"] is False
    assert len(nightly.json()["heldout"]) == 3
    assert nightly.json()["anchor_reused"] is False
    assert repeated.json()["learning"]["reused"] is True
    assert repeated.json()["anchor_reused"] is True
    assert public.json()["operational_performance_claim"] is False
    assert "input_digest" not in str(public.json())
    assert "version" not in str(public.json())
    assert operations.json()["anchor"]["chain_valid"] is True
    assert "dhara_nightly_jobs_completed_total 2" in metrics.text
