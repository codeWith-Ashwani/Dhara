from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from dhara.community import (
    CommunityEngine,
    ContentAssessment,
    DepthOrdinal,
    HmacDeviceSignatureVerifier,
    ReportChannel,
    ReporterRole,
    ReportStatus,
    ReportSubmission,
    StaticAttestationVerifier,
    StaticContentClassifier,
)
from dhara.fusion import AlertTier, FusionEngine

NOW = datetime(2026, 9, 17, 18, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
LOCATIONS = (
    (18.5202, 73.85695),
    (18.5202, 73.85745),
    (18.5214, 73.85695),
    (18.5214, 73.85725),
)


def json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def build_engine(media_names: list[str], devices: list[str]):
    keys = {device: f"key-for-{device}".encode() for device in devices}
    tokens = {device: f"attested-{device}" for device in devices}
    classifier = StaticContentClassifier(
        {
            name: ContentAssessment(
                "flooded_road",
                0.95,
                DepthOrdinal.KNEE,
                int.from_bytes(sha256(name.encode()).digest()[:8]),
            )
            for name in media_names
        }
    )
    signer = HmacDeviceSignatureVerifier(keys)
    engine = CommunityEngine(
        classifier=classifier,
        attestation=StaticAttestationVerifier(tokens),
        signatures=signer,
    )
    return engine, signer, tokens


def signed_submission(
    *,
    signer: HmacDeviceSignatureVerifier,
    tokens: dict[str, str],
    report_id: str,
    reporter_id: str,
    device_id: str,
    counter: int,
    location: tuple[float, float],
    media_reference: str,
    minutes_ago: int,
) -> ReportSubmission:
    captured_at = NOW - timedelta(minutes=minutes_ago)
    unsigned = ReportSubmission(
        report_id=report_id,
        reporter_id=reporter_id,
        device_id=device_id,
        device_counter=counter,
        captured_at=captured_at,
        received_at=captured_at + timedelta(seconds=20),
        latitude=location[0],
        longitude=location[1],
        gps_accuracy_m=10.0,
        channel=ReportChannel.APP,
        claimed_depth=DepthOrdinal.KNEE,
        media_reference=media_reference,
        attestation_token=tokens[device_id],
        signature="unsigned",
    )
    return replace(unsigned, signature=signer.sign(unsigned))


def run_scenarios() -> dict[str, object]:
    attack_media = [f"attack-{index}" for index in range(15)]
    attack_engine, attack_signer, attack_tokens = build_engine(
        attack_media, ["shared-device"]
    )
    for index, media_reference in enumerate(attack_media):
        attack_engine.submit(
            signed_submission(
                signer=attack_signer,
                tokens=attack_tokens,
                report_id=media_reference,
                reporter_id=f"fresh-account-{index}",
                device_id="shared-device",
                counter=index + 1,
                location=LOCATIONS[0],
                media_reference=media_reference,
                minutes_ago=1,
            )
        )
    attack_reports = attack_engine.store.all()
    attack_cell = attack_reports[0].cell_id
    attack_crowd = attack_engine.crowd_confidence(attack_cell, as_of=NOW)
    attack_fusion = FusionEngine()
    attack_fusion.evaluate(
        attack_cell,
        sensor_confidence=0.95,
        crowd_confidence=attack_crowd.crowd_confidence,
    )
    attack_decision = attack_fusion.evaluate(
        attack_cell,
        sensor_confidence=0.95,
        crowd_confidence=attack_crowd.crowd_confidence,
    )

    legitimate_media = [f"legit-{index}" for index in range(4)]
    legitimate_devices = [f"device-{index}" for index in range(4)]
    legitimate_engine, legitimate_signer, legitimate_tokens = build_engine(
        legitimate_media, legitimate_devices
    )
    legitimate_engine.trust.register("verified-node", ReporterRole.VERIFIED_NODE)
    for index in range(1, 4):
        legitimate_engine.trust.register(f"citizen-{index}", ReporterRole.CITIZEN)
    reporters = ("verified-node", "citizen-1", "citizen-2", "citizen-3")
    for index, (reporter, device, location, media_reference) in enumerate(
        zip(
            reporters,
            legitimate_devices,
            LOCATIONS,
            legitimate_media,
            strict=True,
        )
    ):
        legitimate_engine.submit(
            signed_submission(
                signer=legitimate_signer,
                tokens=legitimate_tokens,
                report_id=media_reference,
                reporter_id=reporter,
                device_id=device,
                counter=1,
                location=location,
                media_reference=media_reference,
                minutes_ago=index + 1,
            )
        )
    legitimate_reports = legitimate_engine.store.all()
    legitimate_cell = legitimate_reports[0].cell_id
    legitimate_crowd = legitimate_engine.crowd_confidence(legitimate_cell, as_of=NOW)
    legitimate_fusion = FusionEngine()
    first_cycle = legitimate_fusion.evaluate(
        legitimate_cell,
        sensor_confidence=0.95,
        crowd_confidence=legitimate_crowd.crowd_confidence,
    )
    second_cycle = legitimate_fusion.evaluate(
        legitimate_cell,
        sensor_confidence=0.95,
        crowd_confidence=legitimate_crowd.crowd_confidence,
    )

    sybil_blocked = (
        not attack_crowd.gate_satisfied
        and attack_decision.committed_tier is not AlertTier.WARNING
    )
    warning_drafted = second_cycle.committed_tier is AlertTier.WARNING
    return {
        "generated_at": NOW.isoformat(),
        "mode": "shadow",
        "public_delivery_enabled": False,
        "same_device_attack": {
            "submitted": len(attack_reports),
            "accepted": sum(item.status is ReportStatus.ACCEPTED for item in attack_reports),
            "quarantined": sum(
                item.status is ReportStatus.QUARANTINED for item in attack_reports
            ),
            "crowd": attack_crowd.to_dict(),
            "fusion": attack_decision.to_dict(),
        },
        "independent_corroboration": {
            "submitted": len(legitimate_reports),
            "crowd": legitimate_crowd.to_dict(),
            "first_cycle": first_cycle.to_dict(),
            "second_cycle": second_cycle.to_dict(),
        },
        "acceptance": {
            "same_device_sybil_blocked": sybil_blocked,
            "independent_cluster_warning_drafted": warning_drafted,
            "officer_signoff_required": second_cycle.requires_officer_signoff,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/reports/sprint3_adversarial_results.json",
    )
    args = parser.parse_args()
    result = run_scenarios()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result, indent=2, default=json_default)
    destination.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if all(result["acceptance"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
