from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dhara.alert_templates import AlertTemplateCatalog
from dhara.audit import DecisionAuditRepository, OfficerAction
from dhara.community import DepthOrdinal
from dhara.delivery import CAP_NAMESPACE, DeliveryChannel
from dhara.dossier import SensorEvidence
from dhara.fusion import AlertTier, FusionEngine
from dhara.offline_codec import OfflineCodecError, OfflineReportCodec
from dhara.operations import TriageService


def _warning_case(community_harness, audit_path: str | Path = ":memory:"):
    reporters = ("verified-1", "citizen-2", "citizen-3", "citizen-4")
    for index, (reporter, location) in enumerate(
        zip(reporters, community_harness.locations, strict=True),
        start=1,
    ):
        community_harness.engine.submit(
            community_harness.submission(
                report_id=f"dossier-{index}",
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
        cell_id,
        as_of=community_harness.now,
    )
    fusion = FusionEngine()
    fusion.evaluate(
        cell_id,
        sensor_confidence=0.95,
        crowd_confidence=crowd.crowd_confidence,
    )
    decision = fusion.evaluate(
        cell_id,
        sensor_confidence=0.95,
        crowd_confidence=crowd.crowd_confidence,
    )
    audit = DecisionAuditRepository(audit_path)
    triage = TriageService(
        community=community_harness.engine,
        fusion=fusion,
        audit=audit,
    )
    case = triage.record_evaluation(
        decision=decision,
        crowd=crowd,
        sensor=SensorEvidence(
            model_version="sprint4-test-model",
            novelty_probability=0.82,
            precursor_probability=0.96,
            trend_probability=0.91,
            attributions={"rainfall_mm_72h": 0.31, "river_stage_rate_m_per_h": 0.22},
        ),
        evaluated_at=community_harness.now,
        estimated_population=3_240,
    )
    return triage, audit, case


def test_six_panel_dossier_includes_stream_and_weak_report_counterfactuals(
    community_harness,
) -> None:
    _, _, case = _warning_case(community_harness)
    dossier = case.dossier.to_dict()

    assert case.decision.committed_tier is AlertTier.WARNING
    assert [panel["name"] for panel in dossier["panels"]] == [
        "verdict",
        "sensor_evidence",
        "crowd_evidence",
        "fusion_arithmetic",
        "robustness_counterfactual",
        "precedent_action",
    ]
    robustness = dossier["panels"][4]["content"]
    assert len(robustness["removed_weakest_report_ids"]) == 3
    assert robustness["without_crowd_stream"]["candidate_tier"] != "warning"
    assert robustness["without_sensor_stream"]["candidate_tier"] != "warning"


def test_officer_signoff_uses_one_message_across_sandbox_channels_and_cap(
    community_harness,
) -> None:
    triage, audit, case = _warning_case(community_harness)
    result = triage.act(
        alert_id=case.alert_id,
        action=OfficerAction.ESCALATE_PUBLISH_CAP,
        actor_id="officer-17",
        reason="Independent evidence reviewed in the control room",
        occurred_at=community_harness.now + timedelta(minutes=1),
        language="mr-IN",
        place="प्रभाग १४",
        road="नदीकाठ रस्ता",
        depth=DepthOrdinal.KNEE,
        valid_until="20:30 IST",
    )

    assert result.delivery is not None
    receipts = result.delivery.receipts
    assert {receipt.channel for receipt in receipts} == set(DeliveryChannel)
    assert len({receipt.body for receipt in receipts}) == 1
    assert len({receipt.message_sha256 for receipt in receipts}) == 1
    root = ET.fromstring(result.delivery.cap_xml)
    assert root.findtext(f"{{{CAP_NAMESPACE}}}status") == "Test"
    assert root.findtext(f".//{{{CAP_NAMESPACE}}}description") == receipts[0].body
    assert audit.verify_chain() is True
    assert audit.list_for_alert(case.alert_id)[0].actor_id == "officer-17"


def test_decision_audit_database_rejects_update_and_delete(
    tmp_path: Path,
    community_harness,
) -> None:
    database = tmp_path / "audit.db"
    _, audit, case = _warning_case(community_harness, database)
    audit.append(
        alert_id=case.alert_id,
        action=OfficerAction.CONFIRM,
        actor_id="officer-1",
        occurred_at=community_harness.now,
        reason="Verified against ward response log",
    )
    external = sqlite3.connect(database)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        external.execute("UPDATE decision_audit_events SET reason = 'changed'")
    external.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        external.execute("DELETE FROM decision_audit_events")
    external.close()
    assert audit.verify_chain() is True


def test_alert_templates_are_versioned_and_explicitly_sandbox_only() -> None:
    catalog = AlertTemplateCatalog()
    assert catalog.languages("flood.warning.avoid_route.v1") == (
        "en-IN",
        "hi-IN",
        "mr-IN",
    )
    for language in catalog.languages("flood.warning.avoid_route.v1"):
        rendered = catalog.render(
            template_id="flood.warning.avoid_route.v1",
            language=language,
            place="Ward 14",
            road="River Road",
            depth=DepthOrdinal.KNEE,
            valid_until="20:30 IST",
        )
        assert "{" not in rendered.body
        assert rendered.approval_scope == "sandbox_only"
        assert rendered.human_reviewed is False
        assert rendered.dlt_id is None


def test_offline_sms_codec_round_trip_and_tamper_rejection() -> None:
    codec = OfflineReportCodec(b"sprint-four-test-device-key")
    payload = codec.encode(
        depth=DepthOrdinal.KNEE,
        latitude=18.5204,
        longitude=73.8567,
        minutes_elapsed=7,
    )

    decoded = codec.decode(payload)
    assert payload.startswith("DHR|FL|K|")
    assert len(payload) < 60
    assert decoded.depth is DepthOrdinal.KNEE
    assert decoded.minutes_elapsed == 7

    tampered = payload.replace("|K|", "|W|")
    with pytest.raises(OfflineCodecError, match="signature mismatch"):
        codec.decode(tampered)


def test_audit_requires_timezone() -> None:
    audit = DecisionAuditRepository(":memory:")
    with pytest.raises(ValueError, match="timezone"):
        audit.append(
            alert_id="alert",
            action=OfficerAction.CONFIRM,
            actor_id="officer",
            occurred_at=datetime(2026, 9, 17),
            reason="Reason is present",
        )

    aware = datetime(2026, 9, 17, tzinfo=UTC)
    audit.append(
        alert_id="alert",
        action=OfficerAction.CONFIRM,
        actor_id="officer",
        occurred_at=aware,
        reason="Reason is present",
    )
    assert audit.verify_chain() is True
