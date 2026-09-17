from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from dhara.community import CommunityEngine, CommunityReport, CrowdAssessment, ReportStatus
from dhara.fusion import AlertTier, FusionDecision, FusionEngine, FusionPreview


@dataclass(frozen=True, slots=True)
class SensorEvidence:
    model_version: str
    novelty_probability: float | None = None
    precursor_probability: float | None = None
    trend_probability: float | None = None
    attributions: dict[str, float] = field(default_factory=dict)
    explanation_method: str = "signed_feature_importance"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvidenceDossier:
    alert_id: str
    cell_id: str
    generated_at: datetime
    narrative: str
    panels: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "alert_id": self.alert_id,
            "cell_id": self.cell_id,
            "generated_at": self.generated_at.isoformat(),
            "narrative": self.narrative,
            "panels": list(self.panels),
        }


class EvidenceDossierBuilder:
    def __init__(self, community: CommunityEngine, fusion: FusionEngine) -> None:
        self.community = community
        self.fusion = fusion

    def build(
        self,
        *,
        alert_id: str,
        decision: FusionDecision,
        crowd: CrowdAssessment,
        sensor: SensorEvidence,
        generated_at: datetime,
        estimated_population: int = 0,
        historical_analogues: tuple[dict[str, object], ...] = (),
    ) -> EvidenceDossier:
        reports = [
            report
            for report in self.community.store.recent(
                as_of=generated_at,
                window=self.community.policy.cluster_window,
            )
            if report.cell_id == decision.cell_id
        ]
        accepted = [item for item in reports if item.status is ReportStatus.ACCEPTED]
        first_evidence = min((item.captured_at for item in reports), default=generated_at)
        elapsed_minutes = max(
            0,
            round((generated_at - first_evidence).total_seconds() / 60),
        )
        weakest_ids = _weakest_contributors(crowd, accepted, count=3)
        without_weakest = self.community.crowd_confidence(
            decision.cell_id,
            as_of=generated_at,
            exclude_report_ids=frozenset(weakest_ids),
        )
        weakest_preview = self.fusion.preview(
            sensor_confidence=decision.sensor_confidence,
            crowd_confidence=without_weakest.crowd_confidence,
        )
        without_crowd = self.fusion.preview(
            sensor_confidence=decision.sensor_confidence,
            crowd_confidence=0.0,
        )
        without_sensor = self.fusion.preview(
            sensor_confidence=0.0,
            crowd_confidence=decision.crowd_confidence,
        )
        threshold = _threshold_for(decision.candidate_tier, self.fusion)
        quarantined_reasons = _quarantine_summary(reports)
        attested = [item for item in reports if item.attestation_passed is not None]
        attestation_rate = (
            sum(item.attestation_passed is True for item in attested) / len(attested)
            if attested
            else None
        )
        top_attributions = dict(
            sorted(sensor.attributions.items(), key=lambda item: abs(item[1]), reverse=True)[:5]
        )

        panels = (
            {
                "number": 1,
                "name": "verdict",
                "content": {
                    "committed_tier": decision.committed_tier.name.lower(),
                    "candidate_tier": decision.candidate_tier.name.lower(),
                    "fused_confidence": decision.fused_confidence,
                    "affected_cells": [decision.cell_id],
                    "estimated_exposed_population": estimated_population,
                    "minutes_since_first_evidence": elapsed_minutes,
                    "hysteresis_pending_cycles": decision.pending_cycles,
                    "requires_human_review": decision.requires_human_review,
                    "requires_officer_signoff": decision.requires_officer_signoff,
                },
            },
            {
                "number": 2,
                "name": "sensor_evidence",
                "content": {
                    **sensor.to_dict(),
                    "sensor_confidence": decision.sensor_confidence,
                    "top_attributions": top_attributions,
                },
            },
            {
                "number": 3,
                "name": "crowd_evidence",
                "content": {
                    "crowd_confidence": crowd.crowd_confidence,
                    "gate_satisfied": crowd.gate_satisfied,
                    "distinct_devices": crowd.distinct_devices,
                    "distinct_subcells": crowd.distinct_subcells,
                    "attestation_pass_rate": (
                        None if attestation_rate is None else round(attestation_rate, 6)
                    ),
                    "quarantined_by_reason": quarantined_reasons,
                    "reports": [_report_map_item(item) for item in reports],
                },
            },
            {
                "number": 4,
                "name": "fusion_arithmetic",
                "content": {
                    "sensor_confidence": decision.sensor_confidence,
                    "crowd_confidence": decision.crowd_confidence,
                    "sensor_weight_beta": self.fusion.policy.sensor_weight,
                    "corroboration_gamma": self.fusion.policy.corroboration_bonus,
                    "fused_confidence": decision.fused_confidence,
                    "threshold": threshold,
                    "margin": round(decision.fused_confidence - threshold, 6),
                    "governance_gate_applied": decision.governance_gate_applied,
                    "divergence": decision.divergence,
                    "divergence_direction": decision.divergence_direction,
                    "diagnostic_question": decision.diagnostic_question,
                },
            },
            {
                "number": 5,
                "name": "robustness_counterfactual",
                "content": {
                    "removed_weakest_report_ids": list(weakest_ids),
                    "without_weakest_reports": _preview_summary(weakest_preview),
                    "without_crowd_stream": _preview_summary(without_crowd),
                    "without_sensor_stream": _preview_summary(without_sensor),
                },
            },
            {
                "number": 6,
                "name": "precedent_action",
                "content": {
                    "historical_analogues": list(historical_analogues),
                    "analogue_data_status": (
                        "available" if historical_analogues else "not_yet_available"
                    ),
                    "actions": [
                        "confirm",
                        "request_ground_verification",
                        "reject_as_false",
                        "escalate_publish_cap",
                    ],
                },
            },
        )
        narrative = _narrative(decision, crowd)
        return EvidenceDossier(
            alert_id=alert_id,
            cell_id=decision.cell_id,
            generated_at=generated_at,
            narrative=narrative,
            panels=panels,
        )


def _weakest_contributors(
    crowd: CrowdAssessment,
    reports: list[CommunityReport],
    *,
    count: int,
) -> tuple[str, ...]:
    contributing = set(crowd.contributing_report_ids)
    ranked = sorted(
        (item for item in reports if item.report_id in contributing),
        key=lambda item: item.contribution,
    )
    return tuple(item.report_id for item in ranked[:count])


def _report_map_item(report: CommunityReport) -> dict[str, object]:
    return {
        "report_id": report.report_id,
        "latitude": report.latitude,
        "longitude": report.longitude,
        "status": report.status.value,
        "trust": report.trust_at_submit,
        "contribution": report.contribution,
        "classifier_class": report.classifier_class,
        "classifier_confidence": report.classifier_confidence,
        "claimed_depth": report.claimed_depth.value,
        "predicted_depth": (
            report.predicted_depth.value if report.predicted_depth is not None else None
        ),
        "quarantine_reason": (
            report.quarantine_reason.value if report.quarantine_reason is not None else None
        ),
    }


def _quarantine_summary(reports: list[CommunityReport]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for report in reports:
        if report.quarantine_reason is None:
            continue
        reason = report.quarantine_reason.value
        summary[reason] = summary.get(reason, 0) + 1
    return summary


def _preview_summary(preview: FusionPreview) -> dict[str, object]:
    return {
        "fused_confidence": preview.fused_confidence,
        "candidate_tier": preview.candidate_tier.name.lower(),
        "governance_gate_applied": preview.governance_gate_applied,
    }


def _threshold_for(tier: AlertTier, fusion: FusionEngine) -> float:
    if tier is AlertTier.WARNING:
        return fusion.policy.warning_threshold
    if tier is AlertTier.WATCH:
        return fusion.policy.watch_threshold
    if tier is AlertTier.ADVISORY:
        return fusion.policy.advisory_threshold
    return 0.0


def _narrative(decision: FusionDecision, crowd: CrowdAssessment) -> str:
    tier = decision.committed_tier.name.title()
    message = (
        f"{tier} for cell {decision.cell_id}: sensor confidence "
        f"{decision.sensor_confidence:.2f}, crowd confidence {decision.crowd_confidence:.2f}, "
        f"and fused confidence {decision.fused_confidence:.2f}. "
        f"Community evidence spans {crowd.distinct_devices} devices and "
        f"{crowd.distinct_subcells} sub-cells."
    )
    if decision.requires_officer_signoff:
        message += " The Warning is a draft until an authorised officer signs it."
    elif decision.requires_human_review:
        message += " Stream divergence requires human review."
    return message
