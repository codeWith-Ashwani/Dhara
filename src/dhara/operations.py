from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from dhara.alert_templates import AlertTemplateCatalog
from dhara.audit import AuditEvent, DecisionAuditRepository, OfficerAction
from dhara.community import CommunityEngine, CrowdAssessment, DepthOrdinal
from dhara.delivery import DeliveryBundle, SandboxDeliveryOrchestrator
from dhara.dossier import EvidenceDossier, EvidenceDossierBuilder, SensorEvidence
from dhara.fusion import AlertTier, FusionDecision, FusionEngine


@dataclass(frozen=True, slots=True)
class AlertCase:
    alert_id: str
    cell_id: str
    opened_at: datetime
    updated_at: datetime
    status: str
    center_latitude: float | None
    center_longitude: float | None
    decision: FusionDecision
    crowd: CrowdAssessment
    dossier: EvidenceDossier

    def summary(self) -> dict[str, object]:
        return {
            "alert_id": self.alert_id,
            "cell_id": self.cell_id,
            "opened_at": self.opened_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "status": self.status,
            "center_latitude": self.center_latitude,
            "center_longitude": self.center_longitude,
            "committed_tier": self.decision.committed_tier.name.lower(),
            "candidate_tier": self.decision.candidate_tier.name.lower(),
            "fused_confidence": self.decision.fused_confidence,
            "sensor_confidence": self.decision.sensor_confidence,
            "crowd_confidence": self.decision.crowd_confidence,
            "requires_human_review": self.decision.requires_human_review,
            "requires_officer_signoff": self.decision.requires_officer_signoff,
            "distinct_devices": self.crowd.distinct_devices,
            "distinct_subcells": self.crowd.distinct_subcells,
        }


@dataclass(frozen=True, slots=True)
class OfficerActionResult:
    case_status: str
    audit_event: AuditEvent
    delivery: DeliveryBundle | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "case_status": self.case_status,
            "audit_event": self.audit_event.to_dict(),
            "delivery": self.delivery.to_dict() if self.delivery is not None else None,
        }


class TriageService:
    def __init__(
        self,
        *,
        community: CommunityEngine,
        fusion: FusionEngine,
        audit: DecisionAuditRepository,
        templates: AlertTemplateCatalog | None = None,
        delivery: SandboxDeliveryOrchestrator | None = None,
    ) -> None:
        self.community = community
        self.fusion = fusion
        self.audit_repository = audit
        self.templates = templates or AlertTemplateCatalog()
        self.delivery = delivery or SandboxDeliveryOrchestrator()
        self.dossiers = EvidenceDossierBuilder(community, fusion)
        self._cases: dict[str, AlertCase] = {}

    def record_evaluation(
        self,
        *,
        decision: FusionDecision,
        crowd: CrowdAssessment,
        sensor: SensorEvidence,
        evaluated_at: datetime,
        estimated_population: int = 0,
        historical_analogues: tuple[dict[str, object], ...] = (),
    ) -> AlertCase:
        alert_id = f"dhara-{decision.cell_id}"
        existing = self._cases.get(alert_id)
        dossier = self.dossiers.build(
            alert_id=alert_id,
            decision=decision,
            crowd=crowd,
            sensor=sensor,
            generated_at=evaluated_at,
            estimated_population=estimated_population,
            historical_analogues=historical_analogues,
        )
        center_latitude, center_longitude = self._center(decision.cell_id, evaluated_at)
        case = AlertCase(
            alert_id=alert_id,
            cell_id=decision.cell_id,
            opened_at=existing.opened_at if existing else evaluated_at,
            updated_at=evaluated_at,
            status=existing.status if existing else "open",
            center_latitude=center_latitude,
            center_longitude=center_longitude,
            decision=decision,
            crowd=crowd,
            dossier=dossier,
        )
        self._cases[alert_id] = case
        return case

    def list_cases(self) -> tuple[AlertCase, ...]:
        return tuple(
            sorted(
                self._cases.values(),
                key=lambda item: (
                    item.decision.committed_tier,
                    item.decision.requires_human_review,
                    item.updated_at,
                ),
                reverse=True,
            )
        )

    def get_case(self, alert_id: str) -> AlertCase:
        try:
            return self._cases[alert_id]
        except KeyError as exc:
            raise KeyError("alert case not found") from exc

    def act(
        self,
        *,
        alert_id: str,
        action: OfficerAction,
        actor_id: str,
        reason: str,
        occurred_at: datetime,
        language: str = "en-IN",
        place: str = "affected area",
        road: str = "affected road",
        depth: DepthOrdinal = DepthOrdinal.KNEE,
        valid_until: str = "further notice",
    ) -> OfficerActionResult:
        case = self.get_case(alert_id)
        delivery_bundle: DeliveryBundle | None = None
        status = _status_for_action(action)
        payload: dict[str, object] = {
            "previous_status": case.status,
            "new_status": status,
            "committed_tier": case.decision.committed_tier.name.lower(),
            "fused_confidence": case.decision.fused_confidence,
            "shadow_mode": True,
        }
        if action is OfficerAction.ESCALATE_PUBLISH_CAP:
            if (
                case.decision.committed_tier is not AlertTier.WARNING
                or not case.decision.requires_officer_signoff
            ):
                raise ValueError("CAP escalation requires a committed Warning draft")
            message = self.templates.render(
                template_id="flood.warning.avoid_route.v1",
                language=language,
                place=place,
                road=road,
                depth=depth,
                valid_until=valid_until,
            )
            delivery_bundle = self.delivery.send_all(
                alert_id=alert_id,
                recipient=f"sandbox-polygon:{case.cell_id}",
                sender="dhara-shadow@city.example",
                sent_at=occurred_at,
                cell_id=case.cell_id,
                area_description=place,
                message=message,
            )
            payload.update(
                {
                    "template_id": message.template_id,
                    "language": message.language,
                    "approval_scope": message.approval_scope,
                    "channels": [item.channel.value for item in delivery_bundle.receipts],
                    "message_sha256": delivery_bundle.receipts[0].message_sha256,
                    "cap_status": "Test",
                }
            )
        event = self.audit_repository.append(
            alert_id=alert_id,
            action=action,
            actor_id=actor_id,
            occurred_at=occurred_at,
            reason=reason,
            payload=payload,
        )
        self._cases[alert_id] = replace(case, status=status, updated_at=occurred_at)
        return OfficerActionResult(
            case_status=status,
            audit_event=event,
            delivery=delivery_bundle,
        )

    def _center(self, cell_id: str, as_of: datetime) -> tuple[float | None, float | None]:
        reports = [
            item
            for item in self.community.store.recent(
                as_of=as_of,
                window=self.community.policy.cluster_window,
            )
            if item.cell_id == cell_id
        ]
        if not reports:
            return None, None
        return (
            round(sum(item.latitude for item in reports) / len(reports), 6),
            round(sum(item.longitude for item in reports) / len(reports), 6),
        )


def _status_for_action(action: OfficerAction) -> str:
    return {
        OfficerAction.CONFIRM: "confirmed",
        OfficerAction.REQUEST_GROUND_VERIFICATION: "verification_requested",
        OfficerAction.REJECT_AS_FALSE: "rejected",
        OfficerAction.ESCALATE_PUBLISH_CAP: "sandbox_dispatched",
    }[action]
