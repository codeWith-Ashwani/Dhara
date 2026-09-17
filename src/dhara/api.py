from __future__ import annotations

import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dhara.audit import DecisionAuditRepository, OfficerAction
from dhara.community import (
    CommunityEngine,
    DepthOrdinal,
    ReportChannel,
    ReportSubmission,
)
from dhara.connectors import NormalisationError
from dhara.delivery import SandboxDeliveryOrchestrator
from dhara.dossier import SensorEvidence
from dhara.features import build_feature_snapshot
from dhara.fusion import FusionEngine
from dhara.ingestion import IngestionService
from dhara.learning import LearningRepository, LearningService
from dhara.operations import TriageService
from dhara.outcomes import OutcomeRepository
from dhara.replay import replay_file
from dhara.repository import ObservationRepository
from dhara.safety import SafetyControls
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


class CommunityReportRequest(BaseModel):
    report_id: str = Field(min_length=1, max_length=100)
    reporter_id: str = Field(min_length=1, max_length=100)
    device_id: str = Field(min_length=1, max_length=100)
    device_counter: int = Field(ge=0)
    captured_at: datetime
    received_at: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    gps_accuracy_m: float = Field(ge=0)
    channel: ReportChannel
    claimed_depth: DepthOrdinal
    media_reference: str | None = Field(default=None, max_length=500)
    attestation_token: str | None = Field(default=None, max_length=500)
    signature: str = Field(min_length=1, max_length=500)


class FusionEvaluationRequest(BaseModel):
    cell_id: str = Field(min_length=15, max_length=15)
    sensor_confidence: float = Field(ge=0, le=1)
    as_of: datetime
    sparse_zone: bool = False
    sensor_model_version: str = Field(default="external-shadow-input", max_length=100)
    novelty_probability: float | None = Field(default=None, ge=0, le=1)
    precursor_probability: float | None = Field(default=None, ge=0, le=1)
    trend_probability: float | None = Field(default=None, ge=0, le=1)
    sensor_attributions: dict[str, float] = Field(default_factory=dict)
    estimated_population: int = Field(default=0, ge=0)
    historical_analogues: list[dict[str, Any]] = Field(default_factory=list, max_length=10)


class OfficerActionRequest(BaseModel):
    action: OfficerAction
    actor_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=500)
    language: str = Field(default="en-IN", pattern="^(en|hi|mr)-IN$")
    place: str = Field(default="affected area", min_length=1, max_length=120)
    road: str = Field(default="affected road", min_length=1, max_length=120)
    depth: DepthOrdinal = DepthOrdinal.KNEE
    valid_until: datetime


class LearningRunRequest(BaseModel):
    as_of: datetime


def create_app(
    database_path: str | Path | None = None,
    replay_root: str | Path | None = None,
    sensor_model: SensorEnsemble | None = None,
    sensor_model_path: str | Path | None = None,
    community_engine: CommunityEngine | None = None,
    fusion_engine: FusionEngine | None = None,
    audit_repository: DecisionAuditRepository | None = None,
    outcome_repository: OutcomeRepository | None = None,
    learning_repository: LearningRepository | None = None,
    safety_controls: SafetyControls | None = None,
    triage_service: TriageService | None = None,
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
        version="0.5.0",
        description="Shadow-mode dual-loop decision support for urban flood early warning.",
    )
    application.state.repository = repository
    application.state.ingestion = ingestion
    application.state.sensor_model = loaded_sensor_model
    application.state.community_engine = community_engine or CommunityEngine()
    application.state.fusion_engine = fusion_engine or FusionEngine()
    application.state.safety_controls = safety_controls or SafetyControls.from_environment()
    application.state.audit_repository = audit_repository or DecisionAuditRepository(
        resolved_path
    )
    application.state.outcome_repository = outcome_repository or OutcomeRepository(
        resolved_path
    )
    application.state.learning_repository = learning_repository or LearningRepository(
        resolved_path
    )
    application.state.learning_service = LearningService(
        outcomes=application.state.outcome_repository,
        trust=application.state.community_engine.trust,
        repository=application.state.learning_repository,
    )
    delivery = SandboxDeliveryOrchestrator(safety=application.state.safety_controls)
    application.state.triage_service = triage_service or TriageService(
        community=application.state.community_engine,
        fusion=application.state.fusion_engine,
        audit=application.state.audit_repository,
        outcomes=application.state.outcome_repository,
        delivery=delivery,
    )
    static_root = Path(__file__).with_name("static")
    application.mount("/static", StaticFiles(directory=static_root), name="static")

    @application.get("/operator", include_in_schema=False)
    def operator_dashboard() -> FileResponse:
        return FileResponse(static_root / "dashboard.html")

    @application.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "mode": "shadow",
            "observations": repository.count(),
            "public_delivery_enabled": False,
        }

    @application.get("/v1/safety")
    def safety_status() -> dict[str, object]:
        return application.state.safety_controls.to_dict()

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

    @application.post("/v1/reports/community", status_code=202)
    def submit_community_report(request: CommunityReportRequest) -> dict[str, object]:
        if request.captured_at.utcoffset() is None or request.received_at.utcoffset() is None:
            raise HTTPException(status_code=422, detail="report timestamps must include a timezone")
        submission = ReportSubmission(**request.model_dump())
        return application.state.community_engine.submit(submission).to_dict()

    @application.get("/v1/crowd/{cell_id}")
    def crowd_risk(
        cell_id: str,
        as_of: datetime,
        sparse_zone: bool = False,
    ) -> dict[str, object]:
        if as_of.utcoffset() is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")
        return application.state.community_engine.crowd_confidence(
            cell_id,
            as_of=as_of,
            sparse_zone=sparse_zone,
        ).to_dict()

    @application.post("/v1/fusion/evaluate")
    def evaluate_fusion(request: FusionEvaluationRequest) -> dict[str, object]:
        if request.as_of.utcoffset() is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")
        crowd = application.state.community_engine.crowd_confidence(
            request.cell_id,
            as_of=request.as_of,
            sparse_zone=request.sparse_zone,
        )
        decision = application.state.fusion_engine.evaluate(
            request.cell_id,
            sensor_confidence=request.sensor_confidence,
            crowd_confidence=crowd.crowd_confidence,
        )
        sensor = SensorEvidence(
            model_version=request.sensor_model_version,
            novelty_probability=request.novelty_probability,
            precursor_probability=request.precursor_probability,
            trend_probability=request.trend_probability,
            attributions=request.sensor_attributions,
        )
        case = application.state.triage_service.record_evaluation(
            decision=decision,
            crowd=crowd,
            sensor=sensor,
            evaluated_at=request.as_of,
            estimated_population=request.estimated_population,
            historical_analogues=tuple(request.historical_analogues),
        )
        return {
            "crowd": crowd.to_dict(),
            "fusion": decision.to_dict(),
            "alert": case.summary(),
            "dossier": case.dossier.to_dict(),
        }

    @application.get("/v1/triage")
    def triage_queue() -> list[dict[str, object]]:
        return [item.summary() for item in application.state.triage_service.list_cases()]

    @application.get("/v1/alerts/{alert_id}/dossier")
    def alert_dossier(alert_id: str) -> dict[str, object]:
        try:
            return application.state.triage_service.get_case(alert_id).dossier.to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/v1/alerts/{alert_id}/audit")
    def alert_audit(alert_id: str) -> dict[str, object]:
        try:
            application.state.triage_service.get_case(alert_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        repository = application.state.audit_repository
        return {
            "events": [item.to_dict() for item in repository.list_for_alert(alert_id)],
            "chain_valid": repository.verify_chain(),
        }

    @application.post("/v1/alerts/{alert_id}/actions")
    def officer_action(
        alert_id: str,
        request: OfficerActionRequest,
    ) -> dict[str, object]:
        if request.valid_until.utcoffset() is None:
            raise HTTPException(status_code=422, detail="valid_until must include a timezone")
        try:
            result = application.state.triage_service.act(
                alert_id=alert_id,
                action=request.action,
                actor_id=request.actor_id,
                reason=request.reason,
                occurred_at=datetime.now(UTC),
                language=request.language,
                place=request.place,
                road=request.road,
                depth=request.depth,
                valid_until=request.valid_until.isoformat(timespec="minutes"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result.to_dict()

    @application.get("/v1/outcomes")
    def outcomes() -> list[dict[str, object]]:
        return [
            item.to_dict(include_reporters=False)
            for item in application.state.outcome_repository.all()
        ]

    @application.post("/v1/learning/run")
    def run_learning(request: LearningRunRequest) -> dict[str, object]:
        if request.as_of.utcoffset() is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")
        try:
            run, reused = application.state.learning_service.run(as_of=request.as_of)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return run.to_dict(reused=reused)

    @application.get("/v1/learning/status")
    def learning_status() -> dict[str, object]:
        learning_repository = application.state.learning_repository
        run = learning_repository.latest_run()
        return {
            "latest_run": run.to_dict() if run is not None else None,
            "audit_chain_valid": learning_repository.verify_run_chain(),
            "mode": "shadow",
        }

    @application.get("/v1/public/calibration")
    def public_calibration() -> dict[str, object]:
        return application.state.learning_service.public_metrics()

    @application.get("/v1/zone-policy/{cell_id}")
    def zone_policy(cell_id: str) -> dict[str, object]:
        policy = application.state.learning_repository.latest_zone_policy(cell_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="zone policy not found")
        return policy.to_dict()

    return application

app = create_app()
