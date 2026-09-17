from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from time import perf_counter

from run_sprint4_acceptance_demo import NOW, build_community, submission

from dhara.alert_templates import AlertTemplateCatalog
from dhara.audit import DecisionAuditRepository, OfficerAction
from dhara.community import (
    ContentAssessment,
    DepthOrdinal,
    QuarantineReason,
    ReporterRole,
)
from dhara.delivery import (
    DeliveryChannel,
    SandboxDeliveryAdapter,
    SandboxDeliveryOrchestrator,
)
from dhara.dossier import SensorEvidence
from dhara.features import build_feature_snapshot
from dhara.fusion import FusionEngine
from dhara.ingestion import IngestionService
from dhara.learning import LearningRepository, LearningService
from dhara.operations import TriageService
from dhara.outcomes import OutcomeLabel, OutcomeRepository
from dhara.replay import replay_file
from dhara.repository import ObservationRepository
from dhara.safety import SafetyControls

ROOT = Path(__file__).parents[1]
REPLAY_FIXTURE = ROOT / "data" / "replays" / "pune_ward14_48h.jsonl"


def run_replay_performance(database_path: Path) -> dict[str, object]:
    repository = ObservationRepository(database_path)
    ingestion = IngestionService(repository)
    elapsed_ms: list[float] = []
    summaries = []
    for _ in range(25):
        started = perf_counter()
        summaries.append(replay_file(REPLAY_FIXTURE, ingestion))
        elapsed_ms.append((perf_counter() - started) * 1_000)
    observations = list(repository.all())
    cell_id = observations[0].cell_id
    feature = build_feature_snapshot(
        observations,
        cell_id=cell_id,
        as_of=summaries[0].last_observed_at,
    )
    result = {
        "fixture_classification": "synthetic_contract_fixture",
        "records": summaries[0].total,
        "first_created": summaries[0].created,
        "subsequent_duplicates": sum(item.duplicates for item in summaries[1:]),
        "repository_records": repository.count(),
        "runs": len(summaries),
        "p50_ms": round(_percentile(elapsed_ms, 0.50), 3),
        "p95_ms": round(_percentile(elapsed_ms, 0.95), 3),
        "max_ms": round(max(elapsed_ms), 3),
        "latency_objective_ms": 500.0,
        "feature_snapshot_sha256": _sha256_json(asdict(feature)),
        "all_rejected": sum(item.rejected for item in summaries),
    }
    repository.close()
    return result


def run_learning_rehearsal(database_path: Path) -> dict[str, object]:
    community, signer, tokens = build_community()
    reporters = ("verified-node", "citizen-1", "citizen-2", "citizen-3")
    for index, reporter in enumerate(reporters):
        community.submit(
            submission(signer=signer, tokens=tokens, index=index, reporter_id=reporter)
        )
    cell_id = community.store.all()[0].cell_id
    crowd = community.crowd_confidence(cell_id, as_of=NOW)
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
    audit = DecisionAuditRepository(database_path)
    outcomes = OutcomeRepository(database_path)
    triage = TriageService(
        community=community,
        fusion=fusion,
        audit=audit,
        outcomes=outcomes,
        outcome_data_classification="synthetic",
    )
    case = triage.record_evaluation(
        decision=decision,
        crowd=crowd,
        sensor=SensorEvidence(
            model_version="dhara-loop-a-synthetic-v1",
            novelty_probability=0.84,
            precursor_probability=0.97,
            trend_probability=0.91,
            attributions={"rainfall_mm_72h": 0.31},
        ),
        evaluated_at=NOW,
        estimated_population=3_240,
    )
    outcome_action = triage.act(
        alert_id=case.alert_id,
        action=OfficerAction.CONFIRM,
        actor_id="rehearsal-officer",
        reason="Synthetic rehearsal outcome confirmed",
        occurred_at=NOW + timedelta(minutes=30),
    )
    if outcome_action.outcome is None:
        raise RuntimeError("officer confirmation did not create an outcome")

    _add_learning_fixtures(outcomes, cell_id)
    community.trust.register("trusted-reporter", ReporterRole.CITIZEN)
    community.trust.register("noisy-reporter", ReporterRole.CITIZEN)
    learning_repository = LearningRepository(database_path)
    learning = LearningService(
        outcomes=outcomes,
        trust=community.trust,
        repository=learning_repository,
    )
    before_trusted = community.trust.trust("trusted-reporter", as_of=NOW)
    before_noisy = community.trust.trust("noisy-reporter", as_of=NOW)
    started = perf_counter()
    run, reused = learning.run(as_of=NOW + timedelta(hours=1))
    learning_ms = (perf_counter() - started) * 1_000
    after_trusted = community.trust.trust("trusted-reporter", as_of=NOW)
    after_noisy = community.trust.trust("noisy-reporter", as_of=NOW)
    trust_after_run = (after_trusted, after_noisy)
    repeated, repeated_reused = learning.run(as_of=NOW + timedelta(hours=2))
    trust_after_repeat = (
        community.trust.trust("trusted-reporter", as_of=NOW),
        community.trust.trust("noisy-reporter", as_of=NOW),
    )
    fused_calibration = learning_repository.latest_calibration("fused")
    sensor_calibration = learning_repository.latest_calibration("sensor")
    crowd_calibration = learning_repository.latest_calibration("crowd")
    zone_policy = learning_repository.latest_zone_policy(cell_id)
    if (
        fused_calibration is None
        or sensor_calibration is None
        or crowd_calibration is None
        or zone_policy is None
    ):
        raise RuntimeError("learning artifacts were not created")
    public_metrics = learning.public_metrics()
    result = {
        "outcomes": len(outcomes.all()),
        "job_id": run.job_id,
        "job_reused_initially": reused,
        "repeat_job_id": repeated.job_id,
        "repeat_was_reused": repeated_reused,
        "learning_duration_ms": round(learning_ms, 3),
        "learning_latency_objective_ms": 2_000.0,
        "trust": {
            "trusted_before": before_trusted,
            "trusted_after": after_trusted,
            "noisy_before": before_noisy,
            "noisy_after": after_noisy,
            "unchanged_on_repeat": trust_after_run == trust_after_repeat,
        },
        "calibration": {
            item.stream: {
                "version": item.version,
                "brier_before": item.brier_before,
                "brier_after": item.brier_after,
                "promoted": item.promoted,
                "promotion_reason": item.promotion_reason,
                "bins": len(item.bins),
            }
            for item in (sensor_calibration, crowd_calibration, fused_calibration)
        },
        "zone_policy": zone_policy.to_dict(),
        "public_metrics": public_metrics,
        "decision_audit_chain_valid": audit.verify_chain(),
        "learning_audit_chain_valid": learning_repository.verify_run_chain(),
    }
    audit.close()
    outcomes.close()
    learning_repository.close()
    return result


def run_chaos_and_security() -> dict[str, object]:
    community, signer, tokens = build_community()

    class BrokenClassifier:
        def classify(self, media_reference: str | None) -> ContentAssessment:
            raise TimeoutError("simulated classifier outage")

    community.classifier = BrokenClassifier()
    quarantined = community.submit(
        submission(
            signer=signer,
            tokens=tokens,
            index=0,
            reporter_id="verified-node",
        )
    )

    class BrokenSmsAdapter(SandboxDeliveryAdapter):
        def send(self, alert_id: str, recipient: str, body: str):
            raise TimeoutError("simulated SMS outage")

    adapters = (
        SandboxDeliveryAdapter(DeliveryChannel.PUSH),
        BrokenSmsAdapter(DeliveryChannel.SMS),
        SandboxDeliveryAdapter(DeliveryChannel.IVR),
    )
    delivery = SandboxDeliveryOrchestrator(adapters=adapters)
    message = AlertTemplateCatalog().render(
        template_id="flood.warning.avoid_route.v1",
        language="en-IN",
        place="Ward 14",
        road="Riverside access road",
        depth=DepthOrdinal.KNEE,
        valid_until="21:30 IST",
    )
    bundle = delivery.send_all(
        alert_id="chaos-alert",
        recipient="sandbox",
        sender="shadow@example.test",
        sent_at=NOW,
        cell_id="8960885016bffff",
        area_description="Ward 14",
        message=message,
    )
    live_mode_rejected = False
    try:
        SafetyControls.from_environment({"DHARA_OPERATING_MODE": "live"})
    except RuntimeError:
        live_mode_rejected = True
    return {
        "classifier_outage_result": quarantined.quarantine_reason.value,
        "classifier_failed_closed": (
            quarantined.quarantine_reason is QuarantineReason.CLASSIFIER_UNAVAILABLE
            and quarantined.contribution == 0.0
        ),
        "delivery_statuses": [item.status for item in bundle.receipts],
        "remaining_channels_attempted": bundle.receipts[2].status == "sandbox_recorded",
        "message_hashes_match": len(
            {item.message_sha256 for item in bundle.receipts}
        )
        == 1,
        "live_mode_rejected": live_mode_rejected,
    }


def run_rehearsal() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="dhara-sprint5-") as temporary:
        root = Path(temporary)
        replay = run_replay_performance(root / "replay.db")
        learning = run_learning_rehearsal(root / "learning.db")
    chaos = run_chaos_and_security()
    public_rendered = json.dumps(learning["public_metrics"], sort_keys=True).lower()
    acceptance = {
        "complete_replay_idempotent": (
            replay["first_created"] == replay["records"]
            and replay["repository_records"] == replay["records"]
            and replay["all_rejected"] == 0
        ),
        "replay_p95_within_objective": replay["p95_ms"] < replay["latency_objective_ms"],
        "trust_changed_from_labels": (
            learning["trust"]["trusted_after"] > learning["trust"]["trusted_before"]
            and learning["trust"]["noisy_after"] < learning["trust"]["noisy_before"]
        ),
        "calibration_versions_created": all(
            value["version"].startswith("cal-")
            for value in learning["calibration"].values()
        ),
        "improving_calibration_promoted": any(
            value["promoted"]
            and value["brier_after"] < value["brier_before"]
            for value in learning["calibration"].values()
        ),
        "unsafe_fused_calibration_not_promoted": (
            not learning["calibration"]["fused"]["promoted"]
            and learning["calibration"]["fused"]["brier_after"]
            >= learning["calibration"]["fused"]["brier_before"]
        ),
        "zone_policy_version_created": learning["zone_policy"]["version"].startswith(
            "zone-"
        ),
        "learning_job_idempotent": learning["repeat_was_reused"]
        and learning["trust"]["unchanged_on_repeat"],
        "learning_job_audited": learning["learning_audit_chain_valid"],
        "public_metrics_privacy_safe": all(
            forbidden not in public_rendered
            for forbidden in ("reporter_id", "report_id", "actor_id", "notes")
        ),
        "classifier_outage_failed_closed": chaos["classifier_failed_closed"],
        "channel_outage_degraded_gracefully": chaos["remaining_channels_attempted"],
        "shadow_mode_hard_lock": chaos["live_mode_rejected"],
    }
    return {
        "generated_at": NOW.isoformat(),
        "mode": "shadow",
        "public_delivery_enabled": False,
        "replay_performance": replay,
        "learning": learning,
        "chaos_and_security": chaos,
        "acceptance": acceptance,
    }


def _add_learning_fixtures(outcomes: OutcomeRepository, cell_id: str) -> None:
    fixtures = (
        ("learn-1", OutcomeLabel.CONFIRMED, 0.90, 0.80, 0.88, "trusted-reporter"),
        ("learn-2", OutcomeLabel.CONFIRMED, 0.80, 0.70, 0.78, "trusted-reporter"),
        ("learn-3", OutcomeLabel.CONFIRMED, 0.40, 0.30, 0.35, "trusted-reporter"),
        ("learn-4", OutcomeLabel.REFUTED, 0.80, 0.75, 0.80, "noisy-reporter"),
        ("learn-5", OutcomeLabel.REFUTED, 0.70, 0.65, 0.70, "noisy-reporter"),
        ("learn-6", OutcomeLabel.REFUTED, 0.20, 0.10, 0.15, "noisy-reporter"),
        ("learn-7", OutcomeLabel.REFUTED, 0.30, 0.20, 0.25, "noisy-reporter"),
    )
    for index, (alert_id, label, sensor, crowd, fused, reporter) in enumerate(fixtures):
        outcomes.append(
            alert_id=alert_id,
            cell_id=cell_id,
            label=label,
            observed_at=NOW - timedelta(hours=7 - index),
            recorded_at=NOW - timedelta(hours=7 - index),
            actor_id="synthetic-adjudicator",
            source="synthetic_rehearsal",
            notes="Deterministic Sprint 5 rehearsal label",
            report_ids=(f"synthetic-report-{index}",),
            reporter_ids=(reporter,),
            sensor_confidence=sensor,
            crowd_confidence=crowd,
            fused_confidence=fused,
            predicted_tier="warning" if fused >= 0.75 else "watch",
            data_classification="synthetic",
        )


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/reports/sprint5_rehearsal_results.json",
    )
    args = parser.parse_args()
    result = run_rehearsal()
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if all(result["acceptance"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
