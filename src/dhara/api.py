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


class ProviderEnvelope(BaseModel):
    provider: str
    received_at: datetime | None = None
    payload: dict[str, Any]


class ReplayRequest(BaseModel):
    path: str = Field(description="Local JSONL path; operator-only prototype endpoint")


def create_app(
    database_path: str | Path | None = None,
    replay_root: str | Path | None = None,
) -> FastAPI:
    resolved_path = database_path or os.getenv("DHARA_DATABASE_PATH", "var/dhara.db")
    resolved_replay_root = Path(
        replay_root or os.getenv("DHARA_REPLAY_ROOT", "data/replays")
    ).resolve()
    repository = ObservationRepository(resolved_path)
    ingestion = IngestionService(repository)
    application = FastAPI(
        title="D.H.A.R.A. API",
        version="0.1.0",
        description="Shadow-mode ingestion and replay spine for urban flood early warning.",
    )
    application.state.repository = repository
    application.state.ingestion = ingestion

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

    return application

app = create_app()
