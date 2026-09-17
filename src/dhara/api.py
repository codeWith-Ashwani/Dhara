import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dhara.audit import DecisionAuditRepository, OfficerAction
from dhara.auth import (
    AuthenticationError,
    AuthorizationError,
    OperatorAuthenticator,
    OperatorPrincipal,
    OperatorRole,
)
from dhara.authority import (
    AuthorityEventRecord,
    AuthorityEventRepository,
    HeldOutCalibrationEvaluator,
)
from dhara.community import (
    CommunityEngine,
    DepthOrdinal,
    ReportChannel,
    ReporterTrustEngine,
    ReportSubmission,
)
from dhara.community_persistence import SQLiteReporterTrustStore, SQLiteReportStore
from dhara.connectors import NormalisationError
from dhara.delivery import SandboxDeliveryOrchestrator
from dhara.dossier import SensorEvidence
from dhara.features import build_feature_snapshot
from dhara.fusion import FusionEngine
from dhara.ingestion import IngestionService
from dhara.learning import LearningRepository, LearningService
from dhara.operations import TriageService
from dhara.outcomes import OutcomeLabel, OutcomeRepository
from dhara.pilot import (
    FileAuditAnchorService,
    JobLeaseRepository,
    OperationalMetrics,
    PilotNightlyCoordinator,
)
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


class AuthorityEventRequest(BaseModel):
    authority_event_id: str = Field(min_length=1, max_length=100)
    event_group: str = Field(min_length=1, max_length=100)
    cell_id: str = Field(min_length=15, max_length=15)
    observed_at: datetime
    label: OutcomeLabel
    sensor_confidence: float = Field(ge=0, le=1)
    crowd_confidence: float = Field(ge=0, le=1)
    fused_confidence: float = Field(ge=0, le=1)
    source_reference: str = Field(min_length=1, max_length=500)
    approved_by: str = Field(min_length=1, max_length=100)
    imported_at: datetime
    data_classification: str = Field(default="authority_verified", max_length=100)


class AuthorityImportRequest(BaseModel):
    events: list[AuthorityEventRequest] = Field(min_length=1, max_length=10_000)


class NightlyRunRequest(BaseModel):
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
    authority_repository: AuthorityEventRepository | None = None,
    safety_controls: SafetyControls | None = None,
    operator_authenticator: OperatorAuthenticator | None = None,
    operational_metrics: OperationalMetrics | None = None,
    audit_anchor_service: FileAuditAnchorService | None = None,
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
        version="0.6.0",
        description="Shadow-mode dual-loop decision support for urban flood early warning.",
    )
    application.state.repository = repository
    application.state.ingestion = ingestion
    application.state.sensor_model = loaded_sensor_model
    if community_engine is None:
        trust_store = SQLiteReporterTrustStore(resolved_path)
        report_store = SQLiteReportStore(resolved_path)
        community_engine = CommunityEngine(
            trust=ReporterTrustEngine(trust_store),
            store=report_store,
        )
        application.state.trust_store = trust_store
        application.state.report_store = report_store
    application.state.community_engine = community_engine
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
    application.state.authority_repository = (
        authority_repository or AuthorityEventRepository(resolved_path)
    )
    application.state.heldout_evaluator = HeldOutCalibrationEvaluator(
        application.state.authority_repository
    )
    application.state.operator_authenticator = (
        operator_authenticator or OperatorAuthenticator.from_environment()
    )
    application.state.metrics = operational_metrics or OperationalMetrics()
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
    anchor_path = Path(str(resolved_path) + ".anchors.jsonl")
    application.state.audit_anchor_service = (
        audit_anchor_service
        or FileAuditAnchorService.from_environment(
            path=anchor_path,
            decision_audit=application.state.audit_repository,
            learning=application.state.learning_repository,
        )
    )
    application.state.job_leases = JobLeaseRepository(resolved_path)
    application.state.nightly_coordinator = PilotNightlyCoordinator(
        leases=application.state.job_leases,
        learning=application.state.learning_service,
        heldout=application.state.heldout_evaluator,
        anchors=application.state.audit_anchor_service,
        metrics=application.state.metrics,
    )
    static_root = Path(__file__).with_name("static")
    application.mount("/static", StaticFiles(directory=static_root), name="static")

    def authenticated_operator(
        authorization: str | None = Header(default=None),
    ) -> OperatorPrincipal:
        if authorization is None or not authorization.startswith("Bearer "):
            application.state.metrics.increment("operator_authentication_denied")
            raise HTTPException(status_code=401, detail="operator bearer token is required")
        try:
            return application.state.operator_authenticator.authenticate(
                authorization.removeprefix("Bearer ").strip()
            )
        except AuthenticationError as exc:
            application.state.metrics.increment("operator_authentication_denied")
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    def require_role(minimum: OperatorRole):
        def dependency(
            principal: Annotated[OperatorPrincipal, Depends(authenticated_operator)],
        ) -> OperatorPrincipal:
            try:
                application.state.operator_authenticator.require_role(principal, minimum)
            except AuthorizationError as exc:
                application.state.metrics.increment("operator_authorization_denied")
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            return principal

        return dependency

    viewer = require_role(OperatorRole.VIEWER)
    operator = require_role(OperatorRole.OPERATOR)
    supervisor = require_role(OperatorRole.SUPERVISOR)

    @application.get("/operator", include_in_schema=False)
    def operator_dashboard() -> FileResponse:
        return FileResponse(static_root / "dashboard.html")

    @application.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "mode": "shadow",
            "observations": repository.count(),
            "community_reports": len(application.state.community_engine.store.all()),
            "public_delivery_enabled": False,
            "operator_authentication_enforced": True,
        }

    @application.get("/v1/safety")
    def safety_status() -> dict[str, object]:
        return {
            **application.state.safety_controls.to_dict(),
            "operator_authentication": application.state.operator_authenticator.status(),
        }

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
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
        cell_id: str = Query(min_length=15, max_length=15),
        limit: int = Query(default=500, ge=1, le=10_000),
    ) -> list[dict[str, object]]:
        return [asdict(item) for item in repository.list_for_cell(cell_id, limit=limit)]

    @application.get("/v1/features/{cell_id}")
    def features(
        cell_id: str,
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
        as_of: datetime | None = None,
    ) -> dict[str, object]:
        effective_as_of = as_of or datetime.now(UTC)
        if effective_as_of.tzinfo is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")
        records = repository.list_for_cell(
            cell_id, since=effective_as_of - timedelta(hours=72)
        )
        return asdict(build_feature_snapshot(records, cell_id=cell_id, as_of=effective_as_of))

    @application.post("/v1/replays")
    def replay(
        request: ReplayRequest,
        _principal: Annotated[OperatorPrincipal, Depends(operator)],
    ) -> dict[str, object]:
        path = (resolved_replay_root / request.path).resolve()
        if not path.is_relative_to(resolved_replay_root) or not path.is_file():
            raise HTTPException(status_code=404, detail="replay file not found")
        return replay_file(path, ingestion).to_dict()

    @application.get("/v1/models/loop-a")
    def loop_a_status(
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> dict[str, object]:
        model = application.state.sensor_model
        return {
            "loaded": model is not None,
            "version": model.version if model is not None else None,
            "mode": "shadow",
        }

    @application.post("/v1/risk/sensor")
    def sensor_risk(
        features: SensorFeatures,
        _principal: Annotated[OperatorPrincipal, Depends(operator)],
    ) -> dict[str, object]:
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
        try:
            report = application.state.community_engine.submit(submission)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        application.state.metrics.increment("community_reports_received")
        application.state.metrics.increment(
            "community_reports_accepted"
            if report.quarantine_reason is None
            else "community_reports_quarantined"
        )
        return report.to_dict()

    @application.get("/v1/crowd/{cell_id}")
    def crowd_risk(
        cell_id: str,
        as_of: datetime,
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
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
    def evaluate_fusion(
        request: FusionEvaluationRequest,
        _principal: Annotated[OperatorPrincipal, Depends(operator)],
    ) -> dict[str, object]:
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
    def triage_queue(
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> list[dict[str, object]]:
        return [item.summary() for item in application.state.triage_service.list_cases()]

    @application.get("/v1/alerts/{alert_id}/dossier")
    def alert_dossier(
        alert_id: str,
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> dict[str, object]:
        try:
            return application.state.triage_service.get_case(alert_id).dossier.to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/v1/alerts/{alert_id}/audit")
    def alert_audit(
        alert_id: str,
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> dict[str, object]:
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
        principal: Annotated[OperatorPrincipal, Depends(operator)],
    ) -> dict[str, object]:
        if request.valid_until.utcoffset() is None:
            raise HTTPException(status_code=422, detail="valid_until must include a timezone")
        if request.actor_id != principal.subject:
            raise HTTPException(
                status_code=403,
                detail="action actor_id must match the authenticated operator",
            )
        if request.action is not OfficerAction.REQUEST_GROUND_VERIFICATION:
            try:
                application.state.operator_authenticator.require_role(
                    principal, OperatorRole.SUPERVISOR
                )
            except AuthorizationError as exc:
                application.state.metrics.increment("operator_authorization_denied")
                raise HTTPException(status_code=403, detail=str(exc)) from exc
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
        application.state.metrics.increment("officer_actions_recorded")
        return result.to_dict()

    @application.get("/v1/outcomes")
    def outcomes(
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> list[dict[str, object]]:
        return [
            item.to_dict(include_reporters=False)
            for item in application.state.outcome_repository.all()
        ]

    @application.post("/v1/learning/run")
    def run_learning(
        request: LearningRunRequest,
        _principal: Annotated[OperatorPrincipal, Depends(supervisor)],
    ) -> dict[str, object]:
        if request.as_of.utcoffset() is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")
        try:
            run, reused = application.state.learning_service.run(as_of=request.as_of)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return run.to_dict(reused=reused)

    @application.get("/v1/learning/status")
    def learning_status(
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> dict[str, object]:
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
    def zone_policy(
        cell_id: str,
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> dict[str, object]:
        policy = application.state.learning_repository.latest_zone_policy(cell_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="zone policy not found")
        return policy.to_dict()

    @application.post("/v1/authority/events/import")
    def import_authority_events(
        request: AuthorityImportRequest,
        principal: Annotated[OperatorPrincipal, Depends(supervisor)],
    ) -> dict[str, int]:
        records: list[AuthorityEventRecord] = []
        for event in request.events:
            if event.observed_at.utcoffset() is None or event.imported_at.utcoffset() is None:
                raise HTTPException(
                    status_code=422,
                    detail="authority event timestamps must include a timezone",
                )
            if event.approved_by != principal.subject:
                raise HTTPException(
                    status_code=403,
                    detail="approved_by must match the authenticated supervisor",
                )
            records.append(AuthorityEventRecord(**event.model_dump()))
        try:
            result = application.state.authority_repository.append_many(tuple(records))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        application.state.metrics.increment("authority_events_imported", result["created"])
        return result

    @application.get("/v1/authority/events/summary")
    def authority_event_summary(
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> dict[str, object]:
        records = application.state.authority_repository.all()
        return {
            "records": len(records),
            "event_groups": len({item.event_group for item in records}),
            "confirmed": sum(item.label is OutcomeLabel.CONFIRMED for item in records),
            "refuted": sum(item.label is OutcomeLabel.REFUTED for item in records),
            "data_classifications": sorted(
                {item.data_classification for item in records}
            ),
        }

    @application.post("/v1/heldout/evaluate")
    def run_heldout_evaluation(
        request: LearningRunRequest,
        _principal: Annotated[OperatorPrincipal, Depends(supervisor)],
    ) -> list[dict[str, object]]:
        try:
            artifacts = application.state.heldout_evaluator.evaluate(as_of=request.as_of)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return [item.to_dict() for item in artifacts]

    @application.get("/v1/public/heldout-calibration")
    def public_heldout_calibration() -> dict[str, object]:
        artifacts = {
            stream: application.state.authority_repository.latest_evaluation(stream)
            for stream in ("sensor", "crowd", "fused")
        }
        available = any(item is not None for item in artifacts.values())
        return {
            "mode": "shadow",
            "operational_performance_claim": False,
            "status": "available" if available else "insufficient_data",
            "evaluations": {
                stream: item.to_dict(public=True) if item is not None else None
                for stream, item in artifacts.items()
            },
        }

    @application.post("/v1/jobs/nightly/run")
    def run_nightly_job(
        request: NightlyRunRequest,
        principal: Annotated[OperatorPrincipal, Depends(supervisor)],
    ) -> dict[str, object]:
        if request.as_of.utcoffset() is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")
        try:
            return application.state.nightly_coordinator.run(
                as_of=request.as_of,
                owner_id=principal.subject,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/v1/audit/anchor")
    def create_audit_anchor(
        request: LearningRunRequest,
        _principal: Annotated[OperatorPrincipal, Depends(supervisor)],
    ) -> dict[str, object]:
        if request.as_of.utcoffset() is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")
        try:
            receipt, reused = application.state.audit_anchor_service.create(
                created_at=request.as_of
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        application.state.metrics.increment("audit_anchor_requests")
        return {**receipt.to_dict(), "reused": reused}

    @application.get("/v1/audit/anchor")
    def audit_anchor_status(
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> dict[str, object]:
        return application.state.audit_anchor_service.status()

    @application.get("/v1/operations/status")
    def operations_status(
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> dict[str, object]:
        return {
            "mode": "shadow",
            "metrics": application.state.metrics.snapshot(),
            "decision_audit_chain_valid": application.state.audit_repository.verify_chain(),
            "learning_audit_chain_valid": (
                application.state.learning_repository.verify_run_chain()
            ),
            "anchor": application.state.audit_anchor_service.status(),
            "authentication": application.state.operator_authenticator.status(),
        }

    @application.get("/metrics", response_class=PlainTextResponse)
    def prometheus_metrics(
        _principal: Annotated[OperatorPrincipal, Depends(viewer)],
    ) -> str:
        return application.state.metrics.prometheus()

    return application

app = create_app()
