from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from dhara.audit import DecisionAuditRepository, OfficerAction
from dhara.community import (
    CommunityEngine,
    ContentAssessment,
    DepthOrdinal,
    HmacDeviceSignatureVerifier,
    ReportChannel,
    ReporterRole,
    ReportSubmission,
    StaticAttestationVerifier,
    StaticContentClassifier,
)
from dhara.delivery import CAP_NAMESPACE
from dhara.dossier import SensorEvidence
from dhara.fusion import FusionEngine
from dhara.offline_codec import OfflineReportCodec
from dhara.operations import TriageService

NOW = datetime(2026, 9, 17, 19, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
LOCATIONS = (
    (18.5202, 73.85695),
    (18.5202, 73.85745),
    (18.5214, 73.85695),
    (18.5214, 73.85725),
)


def build_community() -> tuple[
    CommunityEngine,
    HmacDeviceSignatureVerifier,
    dict[str, str],
]:
    devices = [f"sprint4-device-{index}" for index in range(4)]
    keys = {device: f"key-for-{device}".encode() for device in devices}
    tokens = {device: f"attested-{device}" for device in devices}
    assessments = {
        f"sprint4-media-{index}": ContentAssessment(
            hazard_class="flooded_road",
            confidence=0.95,
            predicted_depth=DepthOrdinal.KNEE,
            perceptual_hash=int.from_bytes(
                sha256(f"sprint4-media-{index}".encode()).digest()[:8]
            ),
        )
        for index in range(4)
    }
    signer = HmacDeviceSignatureVerifier(keys)
    engine = CommunityEngine(
        classifier=StaticContentClassifier(assessments),
        attestation=StaticAttestationVerifier(tokens),
        signatures=signer,
    )
    engine.trust.register("verified-node", ReporterRole.VERIFIED_NODE)
    for index in range(1, 4):
        engine.trust.register(f"citizen-{index}", ReporterRole.CITIZEN)
    return engine, signer, tokens


def submission(
    *,
    signer: HmacDeviceSignatureVerifier,
    tokens: dict[str, str],
    index: int,
    reporter_id: str,
) -> ReportSubmission:
    device_id = f"sprint4-device-{index}"
    captured_at = NOW - timedelta(minutes=index + 1)
    unsigned = ReportSubmission(
        report_id=f"sprint4-report-{index}",
        reporter_id=reporter_id,
        device_id=device_id,
        device_counter=1,
        captured_at=captured_at,
        received_at=captured_at + timedelta(seconds=20),
        latitude=LOCATIONS[index][0],
        longitude=LOCATIONS[index][1],
        gps_accuracy_m=10.0,
        channel=ReportChannel.APP,
        claimed_depth=DepthOrdinal.KNEE,
        media_reference=f"sprint4-media-{index}",
        attestation_token=tokens[device_id],
        signature="unsigned",
    )
    return replace(unsigned, signature=signer.sign(unsigned))


def run_demo() -> dict[str, object]:
    community, signer, tokens = build_community()
    reporters = ("verified-node", "citizen-1", "citizen-2", "citizen-3")
    for index, reporter in enumerate(reporters):
        community.submit(
            submission(signer=signer, tokens=tokens, index=index, reporter_id=reporter)
        )
    cell_id = community.store.all()[0].cell_id
    crowd = community.crowd_confidence(cell_id, as_of=NOW)
    fusion = FusionEngine()
    first_cycle = fusion.evaluate(
        cell_id,
        sensor_confidence=0.95,
        crowd_confidence=crowd.crowd_confidence,
    )
    second_cycle = fusion.evaluate(
        cell_id,
        sensor_confidence=0.95,
        crowd_confidence=crowd.crowd_confidence,
    )
    audit = DecisionAuditRepository(":memory:")
    triage = TriageService(community=community, fusion=fusion, audit=audit)
    case = triage.record_evaluation(
        decision=second_cycle,
        crowd=crowd,
        sensor=SensorEvidence(
            model_version="dhara-loop-a-synthetic-v1",
            novelty_probability=0.84,
            precursor_probability=0.97,
            trend_probability=0.91,
            attributions={
                "rainfall_mm_72h": 0.31,
                "river_stage_rate_m_per_h": 0.22,
                "forecast_probability": 0.17,
            },
        ),
        evaluated_at=NOW,
        estimated_population=3_240,
    )
    action = triage.act(
        alert_id=case.alert_id,
        action=OfficerAction.ESCALATE_PUBLISH_CAP,
        actor_id="demo-control-room-officer",
        reason="Independent streams and counterfactuals reviewed",
        occurred_at=NOW + timedelta(minutes=1),
        language="en-IN",
        place="Ward 14",
        road="Riverside access road",
        depth=DepthOrdinal.KNEE,
        valid_until="21:30 IST",
    )
    if action.delivery is None:
        raise RuntimeError("sandbox delivery was not created")
    cap_root = ET.fromstring(action.delivery.cap_xml)
    cap_status = cap_root.findtext(f"{{{CAP_NAMESPACE}}}status")
    message_hashes = {item.message_sha256 for item in action.delivery.receipts}
    messages = {item.body for item in action.delivery.receipts}

    codec = OfflineReportCodec(b"sprint-four-demo-device-key")
    offline_payload = codec.encode(
        depth=DepthOrdinal.KNEE,
        latitude=LOCATIONS[0][0],
        longitude=LOCATIONS[0][1],
        minutes_elapsed=7,
    )
    decoded = codec.decode(offline_payload)
    dossier = case.dossier.to_dict()
    robustness = dossier["panels"][4]["content"]
    acceptance = {
        "six_panel_dossier": len(dossier["panels"]) == 6,
        "stream_counterfactuals_present": all(
            key in robustness
            for key in (
                "without_weakest_reports",
                "without_crowd_stream",
                "without_sensor_stream",
            )
        ),
        "officer_action_audited": len(audit.list_for_alert(case.alert_id)) == 1,
        "audit_chain_valid": audit.verify_chain(),
        "same_message_all_channels": len(messages) == 1 and len(message_hashes) == 1,
        "push_sms_ivr_sandboxed": {
            item.channel.value for item in action.delivery.receipts
        }
        == {"push", "sms", "ivr"},
        "cap_is_test_only": cap_status == "Test",
        "offline_codec_round_trip": decoded.depth is DepthOrdinal.KNEE,
        "no_public_delivery": all(
            item.status == "sandbox_recorded" for item in action.delivery.receipts
        ),
    }
    return {
        "generated_at": NOW.isoformat(),
        "mode": "shadow",
        "public_delivery_enabled": False,
        "alert": {
            "alert_id": case.alert_id,
            "cell_id": cell_id,
            "first_cycle_tier": first_cycle.committed_tier.name.lower(),
            "second_cycle_tier": second_cycle.committed_tier.name.lower(),
            "sensor_confidence": second_cycle.sensor_confidence,
            "crowd_confidence": second_cycle.crowd_confidence,
            "fused_confidence": second_cycle.fused_confidence,
        },
        "dossier": {
            "panel_names": [panel["name"] for panel in dossier["panels"]],
            "removed_weakest_reports": robustness["removed_weakest_report_ids"],
            "without_weakest_reports": robustness["without_weakest_reports"],
            "without_crowd_stream": robustness["without_crowd_stream"],
            "without_sensor_stream": robustness["without_sensor_stream"],
        },
        "delivery": {
            "channels": [item.channel.value for item in action.delivery.receipts],
            "statuses": [item.status for item in action.delivery.receipts],
            "message_sha256": next(iter(message_hashes)),
            "template_id": action.delivery.message.template_id,
            "template_language": action.delivery.message.language,
            "template_approval_scope": action.delivery.message.approval_scope,
            "template_human_reviewed": action.delivery.message.human_reviewed,
            "cap_status": cap_status,
        },
        "offline_report": {
            "payload": offline_payload,
            "length": len(offline_payload),
            "decoded": decoded.to_dict(),
        },
        "audit": {
            "events": len(audit.list_for_alert(case.alert_id)),
            "chain_valid": audit.verify_chain(),
        },
        "acceptance": acceptance,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/reports/sprint4_acceptance_results.json",
    )
    args = parser.parse_args()
    result = run_demo()
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if all(result["acceptance"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
