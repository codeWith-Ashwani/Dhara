from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from dhara.api import create_app
from dhara.auth import OperatorRole

REPLAY_ROOT = Path(__file__).parents[1] / "data" / "replays"


def _auth_headers(
    app,
    *,
    role: OperatorRole = OperatorRole.SUPERVISOR,
    subject: str = "api-officer",
) -> dict[str, str]:
    token = app.state.operator_authenticator.issue_token(
        subject=subject,
        role=role,
        issued_at=datetime.now(UTC),
    )
    return {"Authorization": f"Bearer {token}"}


def _payload() -> dict[str, object]:
    return {
        "provider": "imd",
        "received_at": "2024-07-26T18:01:00+05:30",
        "payload": {
            "observation_id": "api-imd-1",
            "station_id": "station-a",
            "observed_at": "2024-07-26T18:00:00+05:30",
            "latitude": 18.5204,
            "longitude": 73.8567,
            "rainfall_mm_15m": 12.5,
        },
    }


def _community_payload(submission) -> dict[str, object]:
    payload = asdict(submission)
    payload["captured_at"] = submission.captured_at.isoformat()
    payload["received_at"] = submission.received_at.isoformat()
    payload["channel"] = submission.channel.value
    payload["claimed_depth"] = submission.claimed_depth.value
    return payload


def test_observation_api_is_idempotent_and_exposes_features(tmp_path: Path) -> None:
    app = create_app(tmp_path / "api.db", replay_root=REPLAY_ROOT)
    headers = _auth_headers(app)
    with TestClient(app) as client:
        assert client.get("/health").json()["mode"] == "shadow"
        first = client.post("/v1/observations", json=_payload())
        second = client.post("/v1/observations", json=_payload())

        assert first.status_code == 202
        assert first.json()["created"] is True
        assert second.json()["created"] is False

        cell_id = first.json()["cell_id"]
        listed = client.get(
            "/v1/observations", params={"cell_id": cell_id}, headers=headers
        )
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        features = client.get(
            f"/v1/features/{cell_id}",
            params={"as_of": "2024-07-26T18:05:00+05:30"},
            headers=headers,
        )
        assert features.status_code == 200
        assert features.json()["rainfall_mm_1h"] == 12.5


def test_replay_endpoint_confines_paths_to_replay_root(tmp_path: Path) -> None:
    app = create_app(tmp_path / "api.db", replay_root=REPLAY_ROOT)
    headers = _auth_headers(app)
    with TestClient(app) as client:
        replay = client.post(
            "/v1/replays", json={"path": "pune_ward14_48h.jsonl"}, headers=headers
        )
        traversal = client.post(
            "/v1/replays", json={"path": "../../pyproject.toml"}, headers=headers
        )

    assert replay.status_code == 200
    assert replay.json()["created"] == 18
    assert traversal.status_code == 404


def test_invalid_provider_payload_returns_422(tmp_path: Path) -> None:
    app = create_app(tmp_path / "api.db", replay_root=REPLAY_ROOT)
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations", json={"provider": "unknown", "payload": {}}
        )
    assert response.status_code == 422
    assert response.json()["detail"].startswith("unsupported provider")


def test_loop_a_inference_is_versioned_and_shadow_only(
    tmp_path: Path, fitted_sensor_model
) -> None:
    app = create_app(
        tmp_path / "api.db",
        replay_root=REPLAY_ROOT,
        sensor_model=fitted_sensor_model,
    )
    headers = _auth_headers(app)
    payload = {
        "rainfall_mm_1h": 48.0,
        "rainfall_mm_3h": 96.0,
        "rainfall_mm_6h": 150.0,
        "rainfall_mm_24h": 210.0,
        "rainfall_mm_72h": 260.0,
        "river_stage_m": 4.2,
        "river_stage_rate_m_per_h": 0.28,
        "pump_running": 0.0,
        "forecast_probability": 0.92,
        "flagged_observation_count": 0.0,
    }
    with TestClient(app) as client:
        status = client.get("/v1/models/loop-a", headers=headers)
        prediction = client.post("/v1/risk/sensor", json=payload, headers=headers)

    assert status.json() == {"loaded": True, "version": "test-loop-a", "mode": "shadow"}
    assert prediction.status_code == 200
    assert prediction.json()["model_version"] == "test-loop-a"
    assert 0 <= prediction.json()["sensor_confidence"] <= 1


def test_loop_a_inference_fails_closed_without_a_model(tmp_path: Path) -> None:
    app = create_app(tmp_path / "api.db", replay_root=REPLAY_ROOT)
    headers = _auth_headers(app)
    payload = {
        "rainfall_mm_1h": 1.0,
        "rainfall_mm_3h": 2.0,
        "rainfall_mm_6h": 3.0,
        "rainfall_mm_24h": 4.0,
        "rainfall_mm_72h": 5.0,
        "river_stage_m": 2.0,
        "river_stage_rate_m_per_h": 0.0,
        "pump_running": 1.0,
        "forecast_probability": 0.1,
        "flagged_observation_count": 0.0,
    }
    with TestClient(app) as client:
        response = client.post("/v1/risk/sensor", json=payload, headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"] == "Loop A model is not loaded"


def test_community_reports_feed_governed_fusion(tmp_path: Path, community_harness) -> None:
    app = create_app(
        tmp_path / "api.db",
        replay_root=REPLAY_ROOT,
        community_engine=community_harness.engine,
    )
    headers = _auth_headers(app)
    reporters = ("verified-1", "citizen-2", "citizen-3", "citizen-4")
    with TestClient(app) as client:
        responses = []
        for index, (reporter, location) in enumerate(
            zip(reporters, community_harness.locations, strict=True),
            start=1,
        ):
            submission = community_harness.submission(
                report_id=f"api-legit-{index}",
                reporter_id=reporter,
                device_id=f"device-{index}",
                counter=1,
                location=location,
                media_reference=f"legit-{index}",
                minutes_ago=index,
            )
            responses.append(
                client.post("/v1/reports/community", json=_community_payload(submission))
            )

        assert all(response.status_code == 202 for response in responses)
        cell_id = responses[0].json()["cell_id"]
        request = {
            "cell_id": cell_id,
            "sensor_confidence": 0.95,
            "as_of": community_harness.now.isoformat(),
        }
        first = client.post("/v1/fusion/evaluate", json=request, headers=headers)
        second = client.post("/v1/fusion/evaluate", json=request, headers=headers)

        alert_id = second.json()["alert"]["alert_id"]
        dashboard = client.get("/operator")
        dashboard_css = client.get("/static/dashboard.css")
        dashboard_js = client.get("/static/dashboard.js")
        triage = client.get("/v1/triage", headers=headers)
        dossier = client.get(f"/v1/alerts/{alert_id}/dossier", headers=headers)
        action = client.post(
            f"/v1/alerts/{alert_id}/actions",
            json={
                "action": "escalate_publish_cap",
                "actor_id": "api-officer",
                "reason": "Reviewed both evidence streams",
                "language": "en-IN",
                "place": "Ward 14",
                "road": "River Road",
                "depth": "knee",
                "valid_until": "2026-09-17T20:30:00+05:30",
            },
            headers=headers,
        )
        confirm = client.post(
            f"/v1/alerts/{alert_id}/actions",
            json={
                "action": "confirm",
                "actor_id": "api-officer",
                "reason": "Ward team confirmed waterlogging",
                "language": "en-IN",
                "place": "Ward 14",
                "road": "River Road",
                "depth": "knee",
                "valid_until": "2026-09-17T20:30:00+05:30",
            },
            headers=headers,
        )
        outcomes = client.get("/v1/outcomes", headers=headers)
        learning = client.post(
            "/v1/learning/run",
            json={"as_of": "2026-09-17T21:00:00+05:30"},
            headers=headers,
        )
        learning_repeat = client.post(
            "/v1/learning/run",
            json={"as_of": "2026-09-17T21:05:00+05:30"},
            headers=headers,
        )
        learning_status = client.get("/v1/learning/status", headers=headers)
        public_metrics = client.get("/v1/public/calibration")
        safety = client.get("/v1/safety")
        audit = client.get(f"/v1/alerts/{alert_id}/audit", headers=headers)

    assert first.status_code == 200
    assert first.json()["crowd"]["gate_satisfied"] is True
    assert first.json()["fusion"]["committed_tier"] == "monitor"
    assert second.json()["fusion"]["committed_tier"] == "warning"
    assert second.json()["fusion"]["requires_officer_signoff"] is True
    assert dashboard.status_code == 200
    assert "D.H.A.R.A. Operator Console" in dashboard.text
    assert dashboard_css.status_code == 200
    assert dashboard_js.status_code == 200
    assert triage.json()[0]["alert_id"] == alert_id
    assert len(dossier.json()["panels"]) == 6
    assert action.status_code == 200
    assert len(action.json()["delivery"]["receipts"]) == 3
    assert confirm.status_code == 200
    assert confirm.json()["outcome"]["label"] == "confirmed"
    assert len(outcomes.json()) == 1
    assert "reporter_ids" not in outcomes.json()[0]
    assert "actor_id" not in outcomes.json()[0]
    assert learning.status_code == 200
    assert learning.json()["reused"] is False
    assert learning_repeat.json()["reused"] is True
    assert learning_status.json()["audit_chain_valid"] is True
    assert public_metrics.json()["suppressed"] is True
    assert safety.json()["public_delivery_enabled"] is False
    assert audit.json()["chain_valid"] is True
