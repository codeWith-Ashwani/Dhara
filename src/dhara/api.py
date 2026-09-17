from __future__ import annotations

import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from dhara.connectors import NormalisationError
from dhara.features import build_feature_snapshot
from dhara.ingestion import IngestionService
from dhara.replay import replay_file
from dhara.repository import ObservationRepository
from dhara.sensor_dataset import SensorDatasetError, feature_vector
from dhara.sensor_models import SensorEnsemble


class ProviderEnvelope(BaseModel):
    provider: str
    received_at: datetime | None = None
    payload: dict[str, Any]


class ReplayRequest(BaseModel):
    path: str = Field(description="Local JSONL path; operator-only prototype endpoint")


class SensorFeatures(BaseModel):
    rainfall_mm_1h: float = Field(ge=0)
    rainfall_mm_3h: float = Field(ge=0)
    rainfall_mm_6h: float = Field(ge=0)
    rainfall_mm_24h: float = Field(ge=0)
    rainfall_mm_72h: float = Field(ge=0)
    river_stage_m: float
    river_stage_rate_m_per_h: float
    pump_running: float = Field(ge=0, le=1)
    forecast_probability: float = Field(ge=0, le=1)
    flagged_observation_count: float = Field(ge=0)


def create_app(
    database_path: str | Path | None = None,
    replay_root: str | Path | None = None,
    sensor_model: SensorEnsemble | None = None,
    sensor_model_path: str | Path | None = None,
) -> FastAPI:
    resolved_path = database_path or os.getenv("DHARA_DATABASE_PATH", "var/dhara.db")
    resolved_replay_root = Path(
        replay_root or os.getenv("DHARA_REPLAY_ROOT", "data/replays")
    ).resolve()
    repository = ObservationRepository(resolved_path)
    ingestion = IngestionService(repository)
    configured_model_path = sensor_model_path or os.getenv("DHARA_LOOP_A_MODEL")
    loaded_sensor_model = sensor_model
    if loaded_sensor_model is None and configured_model_path:
        loaded_sensor_model = SensorEnsemble.load(configured_model_path)
    application = FastAPI(
        title="D.H.A.R.A. API",
        version="0.2.0",
        description="Shadow-mode ingestion and replay spine for urban flood early warning.",
    )
    application.state.repository = repository
    application.state.ingestion = ingestion
    application.state.sensor_model = loaded_sensor_model

    @application.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "mode": "shadow", "observations": repository.count()}

    @application.post("/v1/observations", status_code=202)
    def ingest(envelope: ProviderEnvelope) -> dict[str, object]:
        try:
            result = ingestion.ingest(envelope.model_dump(mode="json", exclude_none=True))
        except NormalisationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        observation = result.observation
        return {
            "id": result.id if result.id >= 0 else None,
            "created": result.created,
            "status": observation.status,
            "quality_flags": observation.quality_flags,
            "cell_id": observation.cell_id,
        }

    @application.get("/v1/observations")
    def observations(
        cell_id: str = Query(min_length=15, max_length=15),
        limit: int = Query(default=500, ge=1, le=10_000),
    ) -> list[dict[str, object]]:
        return [asdict(item) for item in repository.list_for_cell(cell_id, limit=limit)]

    @application.get("/v1/features/{cell_id}")
    def features(cell_id: str, as_of: datetime | None = None) -> dict[str, object]:
        effective_as_of = as_of or datetime.now(UTC)
        if effective_as_of.tzinfo is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")
        records = repository.list_for_cell(
            cell_id, since=effective_as_of - timedelta(hours=72)
        )
        return asdict(build_feature_snapshot(records, cell_id=cell_id, as_of=effective_as_of))

    @application.post("/v1/replays")
    def replay(request: ReplayRequest) -> dict[str, object]:
        path = (resolved_replay_root / request.path).resolve()
        if not path.is_relative_to(resolved_replay_root) or not path.is_file():
            raise HTTPException(status_code=404, detail="replay file not found")
        return replay_file(path, ingestion).to_dict()

    @application.get("/v1/models/loop-a")
    def loop_a_status() -> dict[str, object]:
        model = application.state.sensor_model
        return {
            "loaded": model is not None,
            "version": model.version if model is not None else None,
            "mode": "shadow",
        }

    @application.post("/v1/risk/sensor")
    def sensor_risk(features: SensorFeatures) -> dict[str, object]:
        model = application.state.sensor_model
        if model is None:
            raise HTTPException(status_code=503, detail="Loop A model is not loaded")
        try:
            vector = feature_vector(features.model_dump())
        except SensorDatasetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return model.predict(vector)[0].to_dict()

    return application

app = create_app()
