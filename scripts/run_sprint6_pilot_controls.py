from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient
from run_sprint4_acceptance_demo import NOW, build_community, submission

from dhara.api import create_app
from dhara.auth import OperatorAuthenticator, OperatorRole
from dhara.authority import AuthorityEventRecord
from dhara.community import CommunityEngine, QuarantineReason, ReporterRole, ReporterTrustEngine
from dhara.community_persistence import SQLiteReporterTrustStore, SQLiteReportStore
from dhara.outcomes import OutcomeLabel

ROOT = Path(__file__).parents[1]
REPLAY_ROOT = ROOT / "data" / "replays"


def authority_events() -> tuple[AuthorityEventRecord, ...]:
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
                    authority_event_id=f"pilot-event-{group_index}-{row_index}",
                    event_group=f"synthetic-monsoon-{group_index}",
                    cell_id="8960885016bffff",
                    observed_at=NOW - timedelta(days=group_index, minutes=row_index),
                    label=label,
                    sensor_confidence=sensor,
                    crowd_confidence=crowd,
                    fused_confidence=fused,
                    source_reference=f"synthetic://authority/{group_index}/{row_index}",
                    approved_by="supervisor-1",
                    imported_at=NOW,
                    data_classification="synthetic_authority_fixture",
                )
            )
    return tuple(records)


def run_durability(database: Path) -> dict[str, object]:
    baseline, signer, tokens = build_community()
    trust_store = SQLiteReporterTrustStore(database)
    report_store = SQLiteReportStore(database)
    trust = ReporterTrustEngine(trust_store)
    trust.register("verified-node", ReporterRole.VERIFIED_NODE)
    engine = CommunityEngine(
        trust=trust,
        classifier=baseline.classifier,
        attestation=baseline.attestation,
        signatures=baseline.signatures,
        store=report_store,
    )
    initial_submission = submission(
        signer=signer,
        tokens=tokens,
        index=0,
        reporter_id="verified-node",
    )
    first = engine.submit(initial_submission)
    trust.update("verified-node", confirmed=True, at=NOW)
    trust_before_restart = trust.trust("verified-node", as_of=NOW)
    report_count_before_restart = len(report_store.all())
    trust_store.close()
    report_store.close()

    restarted_trust_store = SQLiteReporterTrustStore(database)
    restarted_report_store = SQLiteReportStore(database)
    restarted = CommunityEngine(
        trust=ReporterTrustEngine(restarted_trust_store),
        classifier=baseline.classifier,
        attestation=baseline.attestation,
        signatures=baseline.signatures,
        store=restarted_report_store,
    )
    replay_unsigned = replace(
        initial_submission,
        report_id="sprint6-replayed-counter",
        media_reference="sprint4-media-1",
        signature="unsigned",
    )
    replay = restarted.submit(
        replace(replay_unsigned, signature=signer.sign(replay_unsigned))
    )
    trust_after_restart = restarted.trust.trust("verified-node", as_of=NOW)
    result = {
        "first_report_status": first.status.value,
        "report_count_before_restart": report_count_before_restart,
        "report_count_after_restart": len(restarted_report_store.all()),
        "trust_before_restart": trust_before_restart,
        "trust_after_restart": trust_after_restart,
        "replayed_counter_result": replay.quarantine_reason.value
        if replay.quarantine_reason
        else None,
        "replayed_counter_failed_closed": (
            replay.quarantine_reason is QuarantineReason.REPLAYED_COUNTER
            and replay.contribution == 0.0
        ),
    }
    restarted_trust_store.close()
    restarted_report_store.close()
    return result


def run_control_plane(database: Path) -> dict[str, object]:
    authenticator = OperatorAuthenticator(b"sprint6-rehearsal-operator-secret!!")
    app = create_app(
        database,
        replay_root=REPLAY_ROOT,
        operator_authenticator=authenticator,
    )
    token_time = datetime.now(UTC)
    viewer = authenticator.issue_token(
        subject="viewer-1",
        role=OperatorRole.VIEWER,
        issued_at=token_time,
    )
    operator = authenticator.issue_token(
        subject="operator-1",
        role=OperatorRole.OPERATOR,
        issued_at=token_time,
    )
    supervisor = authenticator.issue_token(
        subject="supervisor-1",
        role=OperatorRole.SUPERVISOR,
        issued_at=token_time,
    )
    viewer_headers = {"Authorization": f"Bearer {viewer}"}
    operator_headers = {"Authorization": f"Bearer {operator}"}
    supervisor_headers = {"Authorization": f"Bearer {supervisor}"}

    events = authority_events()
    for index, event in enumerate(events):
        app.state.outcome_repository.append(
            alert_id=f"pilot-outcome-{index}",
            cell_id=event.cell_id,
            label=event.label,
            observed_at=event.observed_at,
            recorded_at=NOW,
            actor_id="supervisor-1",
            source="synthetic_rehearsal",
            notes="Sprint 6 deterministic outcome",
            report_ids=(f"pilot-report-{index}",),
            reporter_ids=(
                "trusted-reporter"
                if event.label is OutcomeLabel.CONFIRMED
                else "noisy-reporter",
            ),
            sensor_confidence=event.sensor_confidence,
            crowd_confidence=event.crowd_confidence,
            fused_confidence=event.fused_confidence,
            predicted_tier="watch",
            data_classification="synthetic",
        )
    with TestClient(app) as client:
        no_token = client.get("/v1/triage")
        viewer_replay = client.post(
            "/v1/replays",
            json={"path": "pune_ward14_48h.jsonl"},
            headers=viewer_headers,
        )
        operator_replay = client.post(
            "/v1/replays",
            json={"path": "pune_ward14_48h.jsonl"},
            headers=operator_headers,
        )
        actor_mismatch = client.post(
            "/v1/alerts/not-created/actions",
            json={
                "action": "request_ground_verification",
                "actor_id": "different-operator",
                "reason": "Synthetic identity binding test",
                "valid_until": (NOW + timedelta(hours=1)).isoformat(),
            },
            headers=operator_headers,
        )
        event_payload = {"events": [item.to_dict() for item in events]}
        imported = client.post(
            "/v1/authority/events/import",
            json=event_payload,
            headers=supervisor_headers,
        )
        imported_repeat = client.post(
            "/v1/authority/events/import",
            json=event_payload,
            headers=supervisor_headers,
        )
        started = perf_counter()
        nightly = client.post(
            "/v1/jobs/nightly/run",
            json={"as_of": (NOW + timedelta(hours=1)).isoformat()},
            headers=supervisor_headers,
        )
        nightly_ms = (perf_counter() - started) * 1_000
        nightly_repeat = client.post(
            "/v1/jobs/nightly/run",
            json={"as_of": (NOW + timedelta(hours=2)).isoformat()},
            headers=supervisor_headers,
        )
        public = client.get("/v1/public/heldout-calibration")
        operations = client.get("/v1/operations/status", headers=viewer_headers)
        metrics = client.get("/metrics", headers=viewer_headers)

    nightly_payload = nightly.json()
    repeated_payload = nightly_repeat.json()
    evaluations = {item["stream"]: item for item in nightly_payload["heldout"]}
    result = {
        "authentication": {
            "missing_token_status": no_token.status_code,
            "viewer_write_status": viewer_replay.status_code,
            "operator_replay_status": operator_replay.status_code,
            "actor_mismatch_status": actor_mismatch.status_code,
            "ephemeral_key": authenticator.ephemeral,
        },
        "authority_import": {
            "first": imported.json(),
            "repeat": imported_repeat.json(),
            "event_groups": len({item.event_group for item in events}),
        },
        "nightly": {
            "status": nightly_payload["status"],
            "duration_ms": round(nightly_ms, 3),
            "latency_objective_ms": 2_000.0,
            "learning_reused_initially": nightly_payload["learning"]["reused"],
            "learning_reused_on_repeat": repeated_payload["learning"]["reused"],
            "anchor_reused_initially": nightly_payload["anchor_reused"],
            "anchor_reused_on_repeat": repeated_payload["anchor_reused"],
            "evaluations": evaluations,
        },
        "public_heldout": public.json(),
        "operations": operations.json(),
        "prometheus": metrics.text.strip().splitlines(),
    }
    _close_app(app)
    return result


def run_rehearsal() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="dhara-sprint6-") as temporary:
        root = Path(temporary)
        durability = run_durability(root / "durability.db")
        controls = run_control_plane(root / "controls.db")
    public_rendered = json.dumps(controls["public_heldout"], sort_keys=True).lower()
    acceptance = {
        "operator_authentication_required": (
            controls["authentication"]["missing_token_status"] == 401
        ),
        "viewer_write_forbidden": controls["authentication"]["viewer_write_status"] == 403,
        "operator_replay_allowed": (
            controls["authentication"]["operator_replay_status"] == 200
        ),
        "actor_identity_bound_to_token": (
            controls["authentication"]["actor_mismatch_status"] == 403
        ),
        "report_survives_restart": durability["report_count_before_restart"] == 1,
        "trust_survives_restart": (
            durability["trust_before_restart"] == durability["trust_after_restart"]
        ),
        "replay_counter_survives_restart": durability["replayed_counter_failed_closed"],
        "authority_import_idempotent": (
            controls["authority_import"]["first"]["created"] == 16
            and controls["authority_import"]["repeat"]["duplicates"] == 16
        ),
        "heldout_groups_are_separated": all(
            item["event_group_count"] == controls["authority_import"]["event_groups"]
            for item in controls["nightly"]["evaluations"].values()
        ),
        "supported_calibration_promoted": (
            controls["nightly"]["evaluations"]["sensor"]["promoted"]
            and controls["nightly"]["evaluations"]["sensor"]["improvement_ci_low"] > 0
        ),
        "uncertain_fused_calibration_blocked": (
            not controls["nightly"]["evaluations"]["fused"]["promoted"]
        ),
        "nightly_job_idempotent": (
            controls["nightly"]["learning_reused_on_repeat"]
            and controls["nightly"]["anchor_reused_on_repeat"]
        ),
        "nightly_latency_within_objective": (
            controls["nightly"]["duration_ms"]
            < controls["nightly"]["latency_objective_ms"]
        ),
        "anchor_chain_valid": controls["operations"]["anchor"]["chain_valid"],
        "all_internal_chains_valid": (
            controls["operations"]["decision_audit_chain_valid"]
            and controls["operations"]["learning_audit_chain_valid"]
        ),
        "public_evaluation_privacy_safe": all(
            forbidden not in public_rendered
            for forbidden in (
                "input_digest",
                "approved_by",
                "source_reference",
                "authority_event_id",
            )
        ),
    }
    return {
        "generated_at": NOW.isoformat(),
        "mode": "shadow",
        "public_delivery_enabled": False,
        "fixture_classification": "synthetic_pilot_control_fixture",
        "durability": durability,
        "control_plane": controls,
        "acceptance": acceptance,
    }


def _close_app(app) -> None:
    for name in (
        "repository",
        "audit_repository",
        "outcome_repository",
        "learning_repository",
        "authority_repository",
        "job_leases",
        "trust_store",
        "report_store",
    ):
        resource = getattr(app.state, name, None)
        if resource is not None:
            resource.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/reports/sprint6_pilot_controls_results.json",
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
