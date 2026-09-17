from __future__ import annotations

from dhara.community import (
    QuarantineReason,
    ReporterRole,
    ReporterTrustEngine,
    ReportStatus,
)
from dhara.fusion import AlertTier, FusionEngine


def test_beta_trust_uses_conservative_priors_and_outcomes(community_harness) -> None:
    trust = ReporterTrustEngine()
    trust.register("node", ReporterRole.VERIFIED_NODE)
    before = trust.trust("node", as_of=community_harness.now)
    after_confirmation = trust.update("node", confirmed=True, at=community_harness.now)
    after_refutation = trust.update("node", confirmed=False, at=community_harness.now)

    assert before > 0.6
    assert after_confirmation > before
    assert after_refutation < after_confirmation


def test_invalid_replay_and_duplicate_reports_are_retained_in_quarantine(
    community_harness,
) -> None:
    first = community_harness.submission(
        report_id="first",
        reporter_id="citizen-2",
        device_id="device-1",
        counter=1,
        location=community_harness.locations[0],
        media_reference="duplicate",
    )
    accepted = community_harness.engine.submit(first)
    replayed = community_harness.engine.submit(
        community_harness.submission(
            report_id="replayed",
            reporter_id="citizen-2",
            device_id="device-1",
            counter=1,
            location=community_harness.locations[0],
            media_reference="legit-1",
        )
    )
    duplicate = community_harness.engine.submit(
        community_harness.submission(
            report_id="duplicate",
            reporter_id="citizen-3",
            device_id="device-2",
            counter=1,
            location=community_harness.locations[1],
            media_reference="duplicate",
        )
    )

    assert accepted.status is ReportStatus.ACCEPTED
    assert replayed.quarantine_reason is QuarantineReason.REPLAYED_COUNTER
    assert duplicate.quarantine_reason is QuarantineReason.DUPLICATE_MEDIA
    assert len(community_harness.engine.store.all()) == 3


def test_same_device_sybil_attack_cannot_create_warning(community_harness) -> None:
    for index in range(15):
        community_harness.engine.submit(
            community_harness.submission(
                report_id=f"attack-{index}",
                reporter_id=f"attacker-account-{index}",
                device_id="shared-device",
                counter=index + 1,
                location=community_harness.locations[0],
                media_reference=f"attack-{index}",
            )
        )

    cell_id = community_harness.engine.store.all()[0].cell_id
    crowd = community_harness.engine.crowd_confidence(
        cell_id, as_of=community_harness.now
    )
    fusion = FusionEngine()
    fusion.evaluate(cell_id, sensor_confidence=0.95, crowd_confidence=crowd.crowd_confidence)
    decision = fusion.evaluate(
        cell_id,
        sensor_confidence=0.95,
        crowd_confidence=crowd.crowd_confidence,
    )

    assert crowd.distinct_devices == 1
    assert crowd.distinct_subcells == 1
    assert crowd.gate_satisfied is False
    assert crowd.crowd_confidence < 0.5
    assert crowd.quarantined_reports == 10
    assert decision.committed_tier is not AlertTier.WARNING


def test_spatially_independent_reports_corroborate_warning(community_harness) -> None:
    reporters = ("verified-1", "citizen-2", "citizen-3", "citizen-4")
    for index, (reporter, location) in enumerate(
        zip(reporters, community_harness.locations, strict=True),
        start=1,
    ):
        community_harness.engine.submit(
            community_harness.submission(
                report_id=f"legit-{index}",
                reporter_id=reporter,
                device_id=f"device-{index}",
                counter=1,
                location=location,
                media_reference=f"legit-{index}",
                minutes_ago=index,
            )
        )

    cell_id = community_harness.engine.store.all()[0].cell_id
    crowd = community_harness.engine.crowd_confidence(
        cell_id, as_of=community_harness.now
    )
    fusion = FusionEngine()
    first = fusion.evaluate(
        cell_id, sensor_confidence=0.95, crowd_confidence=crowd.crowd_confidence
    )
    second = fusion.evaluate(
        cell_id, sensor_confidence=0.95, crowd_confidence=crowd.crowd_confidence
    )

    assert crowd.distinct_devices == 4
    assert crowd.distinct_subcells >= 3
    assert crowd.gate_satisfied is True
    assert crowd.crowd_confidence >= 0.5
    assert first.committed_tier is AlertTier.MONITOR
    assert second.committed_tier is AlertTier.WARNING
    assert second.requires_officer_signoff is True
