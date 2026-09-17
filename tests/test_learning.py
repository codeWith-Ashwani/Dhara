from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path

import pytest

from dhara.alert_templates import AlertTemplateCatalog
from dhara.audit import OfficerAction
from dhara.community import (
    ContentAssessment,
    DepthOrdinal,
    QuarantineReason,
    ReporterRole,
    ReporterTrustEngine,
)
from dhara.delivery import (
    CAP_NAMESPACE,
    DeliveryChannel,
    SandboxDeliveryAdapter,
    SandboxDeliveryOrchestrator,
)
from dhara.learning import LearningRepository, LearningService
from dhara.outcomes import OutcomeLabel, OutcomeRepository
from dhara.safety import SafetyControls


def _populate_outcomes(repository: OutcomeRepository, now) -> None:
    fixtures = (
        ("a1", OutcomeLabel.CONFIRMED, 0.90, 0.80, 0.88, "trusted-reporter"),
        ("a2", OutcomeLabel.CONFIRMED, 0.80, 0.70, 0.78, "trusted-reporter"),
        ("a3", OutcomeLabel.CONFIRMED, 0.40, 0.30, 0.35, "trusted-reporter"),
        ("a4", OutcomeLabel.CONFIRMED, 0.65, 0.60, 0.62, "trusted-reporter"),
        ("a5", OutcomeLabel.REFUTED, 0.80, 0.75, 0.80, "noisy-reporter"),
        ("a6", OutcomeLabel.REFUTED, 0.70, 0.65, 0.70, "noisy-reporter"),
        ("a7", OutcomeLabel.REFUTED, 0.20, 0.10, 0.15, "noisy-reporter"),
        ("a8", OutcomeLabel.REFUTED, 0.30, 0.20, 0.25, "noisy-reporter"),
    )
    for index, (alert_id, label, sensor, crowd, fused, reporter) in enumerate(fixtures):
        repository.append(
            alert_id=alert_id,
            cell_id="8960885016bffff",
            label=label,
            observed_at=now - timedelta(hours=8 - index),
            recorded_at=now - timedelta(hours=8 - index),
            actor_id="outcome-adjudicator",
            source="synthetic_rehearsal",
            notes="Deterministic Sprint 5 labelled outcome",
            report_ids=(f"report-{index}",),
            reporter_ids=(reporter,),
            sensor_confidence=sensor,
            crowd_confidence=crowd,
            fused_confidence=fused,
            predicted_tier="warning" if fused >= 0.75 else "watch",
            data_classification="synthetic",
        )


def test_nightly_learning_updates_trust_calibration_and_zone_policy_idempotently(
    tmp_path: Path,
    community_harness,
) -> None:
    database = tmp_path / "learning.db"
    outcomes = OutcomeRepository(database)
    _populate_outcomes(outcomes, community_harness.now)
    trust = ReporterTrustEngine()
    trust.register("trusted-reporter", ReporterRole.CITIZEN)
    trust.register("noisy-reporter", ReporterRole.CITIZEN)
    repository = LearningRepository(database)
    service = LearningService(outcomes=outcomes, trust=trust, repository=repository)

    before_trusted = trust.trust("trusted-reporter", as_of=community_harness.now)
    before_noisy = trust.trust("noisy-reporter", as_of=community_harness.now)
    run, reused = service.run(as_of=community_harness.now)
    after_trusted = trust.trust("trusted-reporter", as_of=community_harness.now)
    after_noisy = trust.trust("noisy-reporter", as_of=community_harness.now)

    assert reused is False
    assert run.status == "completed"
    assert after_trusted > before_trusted
    assert after_noisy < before_noisy
    fused_calibration = repository.latest_calibration("fused")
    sensor_calibration = repository.latest_calibration("sensor")
    assert fused_calibration is not None
    assert sensor_calibration is not None
    assert fused_calibration.brier_after != fused_calibration.brier_before
    for artifact in (sensor_calibration, fused_calibration):
        assert artifact.promoted is (
            artifact.sample_count >= 5
            and artifact.brier_after < artifact.brier_before
        )
    zone_policy = repository.latest_zone_policy("8960885016bffff")
    assert zone_policy is not None
    assert zone_policy.warning_threshold != 0.75
    assert repository.verify_run_chain() is True

    trust_after_first_run = trust.trust("trusted-reporter", as_of=community_harness.now)
    repeated, reused = service.run(as_of=community_harness.now + timedelta(minutes=5))
    assert reused is True
    assert repeated.job_id == run.job_id
    assert trust.trust("trusted-reporter", as_of=community_harness.now) == trust_after_first_run


def test_public_metrics_are_aggregate_and_suppress_small_samples(
    tmp_path: Path,
    community_harness,
) -> None:
    database = tmp_path / "metrics.db"
    outcomes = OutcomeRepository(database)
    learning = LearningRepository(database)
    service = LearningService(
        outcomes=outcomes,
        trust=ReporterTrustEngine(),
        repository=learning,
    )
    assert service.public_metrics()["suppressed"] is True

    _populate_outcomes(outcomes, community_harness.now)
    service.run(as_of=community_harness.now)
    metrics = service.public_metrics()
    rendered = str(metrics)

    assert metrics["status"] == "available"
    assert metrics["sample_size"] == 8
    assert metrics["data_classification"] == "synthetic"
    assert metrics["operational_performance_claim"] is False
    assert "reporter" not in rendered
    assert "actor" not in rendered
    assert "input_digest" not in rendered


def test_small_calibration_candidate_is_audited_but_never_promoted(
    tmp_path: Path,
    community_harness,
) -> None:
    database = tmp_path / "small-calibration.db"
    outcomes = OutcomeRepository(database)
    outcomes.append(
        alert_id="small-1",
        cell_id="8960885016bffff",
        label=OutcomeLabel.CONFIRMED,
        observed_at=community_harness.now,
        recorded_at=community_harness.now,
        actor_id="synthetic-adjudicator",
        source="synthetic_rehearsal",
        notes="Single-label promotion guard",
        report_ids=("report-1",),
        reporter_ids=("reporter-1",),
        sensor_confidence=0.8,
        crowd_confidence=0.7,
        fused_confidence=0.75,
        predicted_tier="warning",
        data_classification="synthetic",
    )
    repository = LearningRepository(database)
    service = LearningService(
        outcomes=outcomes,
        trust=ReporterTrustEngine(),
        repository=repository,
    )
    service.run(as_of=community_harness.now)

    artifact = repository.latest_calibration("fused")
    assert artifact is not None
    assert artifact.sample_count == 1
    assert artifact.promoted is False
    assert artifact.calibrate(0.75) == 0.75


def test_outcomes_are_idempotent_but_conflicting_labels_fail(
    tmp_path: Path,
    community_harness,
) -> None:
    repository = OutcomeRepository(tmp_path / "outcome.db")
    fields = {
        "alert_id": "alert-1",
        "cell_id": "8960885016bffff",
        "observed_at": community_harness.now,
        "recorded_at": community_harness.now,
        "actor_id": "officer",
        "source": "test",
        "notes": "Outcome reviewed",
        "report_ids": ("report-1",),
        "reporter_ids": ("reporter-1",),
        "sensor_confidence": 0.8,
        "crowd_confidence": 0.7,
        "fused_confidence": 0.75,
        "predicted_tier": "warning",
    }
    first = repository.append(label=OutcomeLabel.CONFIRMED, **fields)
    repeated = repository.append(label=OutcomeLabel.CONFIRMED, **fields)
    assert repeated.outcome_id == first.outcome_id
    with pytest.raises(ValueError, match="conflicting"):
        repository.append(label=OutcomeLabel.REFUTED, **fields)


def test_outcome_and_learning_tables_reject_mutation(
    tmp_path: Path,
    community_harness,
) -> None:
    database = tmp_path / "immutable-learning.db"
    outcomes = OutcomeRepository(database)
    _populate_outcomes(outcomes, community_harness.now)
    learning = LearningRepository(database)
    LearningService(
        outcomes=outcomes,
        trust=ReporterTrustEngine(),
        repository=learning,
    ).run(as_of=community_harness.now)

    external = sqlite3.connect(database)
    statements = (
        "UPDATE alert_outcomes SET notes = 'changed'",
        "UPDATE calibration_artifacts SET promoted = 0",
        "DELETE FROM zone_policy_artifacts",
        "UPDATE learning_job_runs SET status = 'failed'",
    )
    for statement in statements:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            external.execute(statement)
        external.rollback()
    external.close()
    assert learning.verify_run_chain() is True


def test_classifier_outage_quarantines_report_instead_of_failing_open(
    community_harness,
) -> None:
    class BrokenClassifier:
        def classify(self, media_reference: str | None) -> ContentAssessment:
            raise TimeoutError("simulated classifier outage")

    community_harness.engine.classifier = BrokenClassifier()
    report = community_harness.engine.submit(
        community_harness.submission(
            report_id="chaos-classifier",
            reporter_id="citizen-2",
            device_id="device-1",
            counter=1,
            location=community_harness.locations[0],
            media_reference="legit-1",
        )
    )

    assert report.quarantine_reason is QuarantineReason.CLASSIFIER_UNAVAILABLE
    assert report.contribution == 0.0


def test_signature_service_outage_quarantines_report(community_harness) -> None:
    class BrokenSignatures:
        def verify(self, submission) -> bool:
            raise TimeoutError("simulated signature service outage")

    community_harness.engine.signatures = BrokenSignatures()
    report = community_harness.engine.submit(
        community_harness.submission(
            report_id="chaos-signature",
            reporter_id="citizen-2",
            device_id="device-1",
            counter=1,
            location=community_harness.locations[0],
            media_reference="legit-1",
        )
    )

    assert report.quarantine_reason is QuarantineReason.SIGNATURE_SERVICE_UNAVAILABLE
    assert report.contribution == 0.0


def test_attestation_service_outage_quarantines_report(community_harness) -> None:
    class BrokenAttestation:
        def verify(self, device_id: str, token: str | None) -> bool:
            raise TimeoutError("simulated attestation service outage")

    community_harness.engine.attestation = BrokenAttestation()
    report = community_harness.engine.submit(
        community_harness.submission(
            report_id="chaos-attestation",
            reporter_id="citizen-2",
            device_id="device-1",
            counter=1,
            location=community_harness.locations[0],
            media_reference="legit-1",
        )
    )

    assert report.quarantine_reason is QuarantineReason.ATTESTATION_SERVICE_UNAVAILABLE
    assert report.contribution == 0.0


def test_delivery_attempts_all_channels_when_one_adapter_fails(community_harness) -> None:
    class BrokenSmsAdapter(SandboxDeliveryAdapter):
        def send(self, alert_id: str, recipient: str, body: str):
            raise TimeoutError("simulated provider outage")

    adapters = (
        SandboxDeliveryAdapter(DeliveryChannel.PUSH),
        BrokenSmsAdapter(DeliveryChannel.SMS),
        SandboxDeliveryAdapter(DeliveryChannel.IVR),
    )
    orchestrator = SandboxDeliveryOrchestrator(adapters=adapters)
    message = AlertTemplateCatalog().render(
        template_id="flood.warning.avoid_route.v1",
        language="en-IN",
        place="Ward 14",
        road="River Road",
        depth=DepthOrdinal.KNEE,
        valid_until="20:30 IST",
    )
    bundle = orchestrator.send_all(
        alert_id="alert",
        recipient="sandbox",
        sender="shadow@example.test",
        sent_at=community_harness.now,
        cell_id="8960885016bffff",
        area_description="Ward 14",
        message=message,
    )

    assert [item.status for item in bundle.receipts] == [
        "sandbox_recorded",
        "sandbox_failed",
        "sandbox_recorded",
    ]
    assert len({item.message_sha256 for item in bundle.receipts}) == 1
    cap = ET.fromstring(bundle.cap_xml)
    assert cap.findtext(f"{{{CAP_NAMESPACE}}}status") == "Test"


def test_prototype_rejects_any_non_shadow_operating_mode() -> None:
    controls = SafetyControls.from_environment({})
    assert controls.public_delivery_enabled is False
    controls.assert_sandbox_delivery()
    with pytest.raises(RuntimeError, match="hard-locked"):
        SafetyControls.from_environment({"DHARA_OPERATING_MODE": "live"})


def test_officer_action_enum_remains_stable_for_outcome_mapping() -> None:
    assert OfficerAction.CONFIRM.value == "confirm"
    assert OfficerAction.REJECT_AS_FALSE.value == "reject_as_false"
