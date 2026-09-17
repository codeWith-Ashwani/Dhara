from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dhara.api import create_app

REPLAY_ROOT = Path(__file__).parents[1] / "data" / "replays"


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


def test_observation_api_is_idempotent_and_exposes_features(tmp_path: Path) -> None:
    app = create_app(tmp_path / "api.db", replay_root=REPLAY_ROOT)
    with TestClient(app) as client:
        assert client.get("/health").json()["mode"] == "shadow"
        first = client.post("/v1/observations", json=_payload())
        second = client.post("/v1/observations", json=_payload())

        assert first.status_code == 202
        assert first.json()["created"] is True
        assert second.json()["created"] is False

        cell_id = first.json()["cell_id"]
        listed = client.get("/v1/observations", params={"cell_id": cell_id})
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        features = client.get(
            f"/v1/features/{cell_id}",
            params={"as_of": "2024-07-26T18:05:00+05:30"},
        )
        assert features.status_code == 200
        assert features.json()["rainfall_mm_1h"] == 12.5


def test_replay_endpoint_confines_paths_to_replay_root(tmp_path: Path) -> None:
    app = create_app(tmp_path / "api.db", replay_root=REPLAY_ROOT)
    with TestClient(app) as client:
        replay = client.post("/v1/replays", json={"path": "pune_ward14_48h.jsonl"})
        traversal = client.post("/v1/replays", json={"path": "../../pyproject.toml"})

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
        status = client.get("/v1/models/loop-a")
        prediction = client.post("/v1/risk/sensor", json=payload)

    assert status.json() == {"loaded": True, "version": "test-loop-a", "mode": "shadow"}
    assert prediction.status_code == 200
    assert prediction.json()["model_version"] == "test-loop-a"
    assert 0 <= prediction.json()["sensor_confidence"] <= 1


def test_loop_a_inference_fails_closed_without_a_model(tmp_path: Path) -> None:
    app = create_app(tmp_path / "api.db", replay_root=REPLAY_ROOT)
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
        response = client.post("/v1/risk/sensor", json=payload)
    assert response.status_code == 503
    assert response.json()["detail"] == "Loop A model is not loaded"
