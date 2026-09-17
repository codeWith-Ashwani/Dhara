from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from dhara.geo import cell_for, geohash_for


class ReporterRole(StrEnum):
    ANONYMOUS = "anonymous"
    CITIZEN = "citizen"
    VERIFIED_NODE = "verified_node"


class ReportChannel(StrEnum):
    APP = "app"
    SMS = "sms"
    IVR = "ivr"
    NODE = "node"


class DepthOrdinal(StrEnum):
    ANKLE = "ankle"
    KNEE = "knee"
    WAIST = "waist"
    ABOVE_WAIST = "above_waist"


class ReportStatus(StrEnum):
    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"


class QuarantineReason(StrEnum):
    SIGNATURE_SERVICE_UNAVAILABLE = "signature_service_unavailable"
    INVALID_SIGNATURE = "invalid_signature"
    ATTESTATION_SERVICE_UNAVAILABLE = "attestation_service_unavailable"
    ATTESTATION_FAILED = "attestation_failed"
    REPLAYED_COUNTER = "replayed_counter"
    INVALID_CAPTURE_TIME = "invalid_capture_time"
    CONTENT_MISMATCH = "content_mismatch"
    CLASSIFIER_UNAVAILABLE = "classifier_unavailable"
    DUPLICATE_MEDIA = "duplicate_media"
    RATE_LIMITED = "rate_limited"


_ROLE_PRIORS = {
    ReporterRole.ANONYMOUS: (1.0, 3.0),
    ReporterRole.CITIZEN: (2.0, 2.0),
    ReporterRole.VERIFIED_NODE: (8.0, 2.0),
}
_DEPTH_ORDER = {
    DepthOrdinal.ANKLE: 0,
    DepthOrdinal.KNEE: 1,
    DepthOrdinal.WAIST: 2,
    DepthOrdinal.ABOVE_WAIST: 3,
}
_FLOOD_CLASSES = {"flooded_road", "submerged_vehicle", "overflowing_drain"}


@dataclass(slots=True)
class ReporterProfile:
    reporter_id: str
    role: ReporterRole
    alpha: float
    beta: float
    last_activity: datetime | None = None


class ReporterTrustStore(Protocol):
    def load(self, reporter_id: str) -> ReporterProfile | None: ...

    def save(self, profile: ReporterProfile) -> None: ...


class ReporterTrustEngine:
    def __init__(self, store: ReporterTrustStore | None = None) -> None:
        self._profiles: dict[str, ReporterProfile] = {}
        self._store = store

    def register(self, reporter_id: str, role: ReporterRole) -> ReporterProfile:
        existing = self._profiles.get(reporter_id)
        if existing is None and self._store is not None:
            existing = self._store.load(reporter_id)
            if existing is not None:
                self._profiles[reporter_id] = existing
        if existing is not None:
            if existing.role is not role:
                raise ValueError("reporter role conflicts with durable profile")
            return existing
        alpha, beta = _ROLE_PRIORS[role]
        profile = ReporterProfile(reporter_id, role, alpha, beta)
        self._profiles[reporter_id] = profile
        if self._store is not None:
            self._store.save(profile)
        return profile

    def profile(self, reporter_id: str) -> ReporterProfile:
        profile = self._profiles.get(reporter_id)
        if profile is None and self._store is not None:
            profile = self._store.load(reporter_id)
            if profile is not None:
                self._profiles[reporter_id] = profile
        return profile or self.register(reporter_id, ReporterRole.ANONYMOUS)

    def trust(self, reporter_id: str, *, as_of: datetime) -> float:
        profile = self.profile(reporter_id)
        conservative = _conservative_beta(profile.alpha, profile.beta)
        if profile.last_activity is None:
            return conservative
        prior_alpha, prior_beta = _ROLE_PRIORS[profile.role]
        prior = _conservative_beta(prior_alpha, prior_beta)
        inactive_days = max(0.0, (as_of - profile.last_activity).total_seconds() / 86_400)
        retained = 0.5 ** (inactive_days / 180.0)
        return round(prior + retained * (conservative - prior), 6)

    def update(self, reporter_id: str, *, confirmed: bool, at: datetime) -> float:
        profile = self.profile(reporter_id)
        if confirmed:
            profile.alpha += 1
        else:
            profile.beta += 1
        profile.last_activity = at
        if self._store is not None:
            self._store.save(profile)
        return self.trust(reporter_id, as_of=at)


@dataclass(frozen=True, slots=True)
class ContentAssessment:
    hazard_class: str
    confidence: float
    predicted_depth: DepthOrdinal | None
    perceptual_hash: int | None


@dataclass(frozen=True, slots=True)
class ReportSubmission:
    report_id: str
    reporter_id: str
    device_id: str
    device_counter: int
    captured_at: datetime
    received_at: datetime
    latitude: float
    longitude: float
    gps_accuracy_m: float
    channel: ReportChannel
    claimed_depth: DepthOrdinal
    media_reference: str | None
    attestation_token: str | None
    signature: str

    def signing_payload(self) -> bytes:
        payload = {
            "report_id": self.report_id,
            "reporter_id": self.reporter_id,
            "device_id": self.device_id,
            "device_counter": self.device_counter,
            "captured_at": self.captured_at.isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "gps_accuracy_m": self.gps_accuracy_m,
            "channel": self.channel.value,
            "claimed_depth": self.claimed_depth.value,
            "media_reference": self.media_reference,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


class ContentClassifier(Protocol):
    def classify(self, media_reference: str | None) -> ContentAssessment: ...


class AttestationVerifier(Protocol):
    def verify(self, device_id: str, token: str | None) -> bool: ...


class SignatureVerifier(Protocol):
    def verify(self, submission: ReportSubmission) -> bool: ...


class HmacDeviceSignatureVerifier:
    def __init__(self, device_keys: dict[str, bytes]) -> None:
        self.device_keys = device_keys

    def verify(self, submission: ReportSubmission) -> bool:
        key = self.device_keys.get(submission.device_id)
        if key is None:
            return False
        expected = hmac.new(key, submission.signing_payload(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, submission.signature)

    def sign(self, submission: ReportSubmission) -> str:
        key = self.device_keys[submission.device_id]
        return hmac.new(key, submission.signing_payload(), hashlib.sha256).hexdigest()


class StaticAttestationVerifier:
    def __init__(self, valid_tokens: dict[str, str]) -> None:
        self.valid_tokens = valid_tokens

    def verify(self, device_id: str, token: str | None) -> bool:
        expected = self.valid_tokens.get(device_id)
        return expected is not None and token is not None and hmac.compare_digest(expected, token)


class StaticContentClassifier:
    def __init__(self, assessments: dict[str, ContentAssessment] | None = None) -> None:
        self.assessments = assessments or {}

    def classify(self, media_reference: str | None) -> ContentAssessment:
        if media_reference is None:
            return ContentAssessment("unverified_remote", 0.35, None, None)
        return self.assessments.get(
            media_reference,
            ContentAssessment("irrelevant", 0.0, None, None),
        )


@dataclass(frozen=True, slots=True)
class CommunityReport:
    report_id: str
    reporter_id: str
    reporter_role: ReporterRole
    device_id: str
    device_counter: int
    captured_at: datetime
    received_at: datetime
    latitude: float
    longitude: float
    cell_id: str
    geohash7: str
    channel: ReportChannel
    claimed_depth: DepthOrdinal
    predicted_depth: DepthOrdinal | None
    classifier_class: str
    classifier_confidence: float
    trust_at_submit: float
    geo_integrity: float
    contribution: float
    status: ReportStatus
    signature_valid: bool
    attestation_passed: bool | None
    quarantine_reason: QuarantineReason | None = None
    perceptual_hash: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ReportStore(Protocol):
    def add(self, report: CommunityReport) -> None: ...

    def all(self) -> tuple[CommunityReport, ...]: ...

    def recent(self, *, as_of: datetime, window: timedelta) -> list[CommunityReport]: ...


class InMemoryReportStore:
    def __init__(self) -> None:
        self._reports: list[CommunityReport] = []

    def add(self, report: CommunityReport) -> None:
        self._reports.append(report)

    def all(self) -> tuple[CommunityReport, ...]:
        return tuple(self._reports)

    def recent(self, *, as_of: datetime, window: timedelta) -> list[CommunityReport]:
        earliest = as_of - window
        return [item for item in self._reports if earliest <= item.captured_at <= as_of]


@dataclass(frozen=True, slots=True)
class CommunityPolicy:
    cluster_window: timedelta = timedelta(minutes=30)
    duplicate_window: timedelta = timedelta(hours=24)
    recency_half_life_minutes: float = 45.0
    minimum_devices: int = 4
    minimum_subcells: int = 3
    spread_target: int = 4
    sparse_minimum_devices: int = 2
    sparse_minimum_subcells: int = 2
    max_device_reports: int = 5
    max_reporter_reports: int = 4
    minimum_classifier_confidence: float = 0.55
    maximum_phash_distance: int = 6


@dataclass(frozen=True, slots=True)
class CrowdAssessment:
    cell_id: str
    as_of: datetime
    crowd_confidence: float
    raw_confidence: float
    spread_factor: float
    distinct_devices: int
    distinct_subcells: int
    accepted_reports: int
    quarantined_reports: int
    gate_satisfied: bool
    sparse_zone_relaxation: bool
    contributing_report_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class CommunityEngine:
    def __init__(
        self,
        *,
        trust: ReporterTrustEngine | None = None,
        classifier: ContentClassifier | None = None,
        attestation: AttestationVerifier | None = None,
        signatures: SignatureVerifier | None = None,
        store: ReportStore | None = None,
        policy: CommunityPolicy | None = None,
    ) -> None:
        self.trust = trust or ReporterTrustEngine()
        self.classifier = classifier or StaticContentClassifier()
        self.attestation = attestation or StaticAttestationVerifier({})
        self.signatures = signatures or HmacDeviceSignatureVerifier({})
        self.store = store or InMemoryReportStore()
        self.policy = policy or CommunityPolicy()
        self._device_counters: dict[str, int] = {}
        self._device_arrivals: defaultdict[str, deque[datetime]] = defaultdict(deque)
        self._reporter_arrivals: defaultdict[str, deque[datetime]] = defaultdict(deque)
        for report in self.store.all():
            self._device_counters[report.device_id] = max(
                report.device_counter,
                self._device_counters.get(report.device_id, -1),
            )
            self._device_arrivals[report.device_id].append(report.received_at)
            self._reporter_arrivals[report.reporter_id].append(report.received_at)

    def submit(self, submission: ReportSubmission) -> CommunityReport:
        cell_id = cell_for(submission.latitude, submission.longitude)
        subcell = geohash_for(submission.latitude, submission.longitude, 7)
        profile = self.trust.profile(submission.reporter_id)
        classifier_available = True
        try:
            assessment = self.classifier.classify(submission.media_reference)
        except Exception:
            classifier_available = False
            assessment = ContentAssessment("unavailable", 0.0, None, None)
        trust_value = self.trust.trust(submission.reporter_id, as_of=submission.received_at)
        geo_integrity = _geo_integrity(submission)

        reason: QuarantineReason | None = None
        signature_available = True
        try:
            signature_valid = self.signatures.verify(submission)
        except Exception:
            signature_available = False
            signature_valid = False
        attestation_required = submission.channel in {ReportChannel.APP, ReportChannel.NODE}
        attestation_available = True
        try:
            attestation_passed = (
                self.attestation.verify(submission.device_id, submission.attestation_token)
                if attestation_required
                else None
            )
        except Exception:
            attestation_available = False
            attestation_passed = False
        if not signature_available:
            reason = QuarantineReason.SIGNATURE_SERVICE_UNAVAILABLE
        elif not signature_valid:
            reason = QuarantineReason.INVALID_SIGNATURE
        elif attestation_required and not attestation_available:
            reason = QuarantineReason.ATTESTATION_SERVICE_UNAVAILABLE
        elif attestation_required and not attestation_passed:
            reason = QuarantineReason.ATTESTATION_FAILED
        elif submission.device_counter <= self._device_counters.get(submission.device_id, -1):
            reason = QuarantineReason.REPLAYED_COUNTER
        elif (
            submission.captured_at > submission.received_at + timedelta(minutes=5)
            or submission.captured_at < submission.received_at - timedelta(hours=6)
        ):
            reason = QuarantineReason.INVALID_CAPTURE_TIME
        elif not classifier_available:
            reason = QuarantineReason.CLASSIFIER_UNAVAILABLE
        elif submission.channel in {ReportChannel.APP, ReportChannel.NODE} and (
            assessment.hazard_class not in _FLOOD_CLASSES
            or assessment.confidence < self.policy.minimum_classifier_confidence
        ):
            reason = QuarantineReason.CONTENT_MISMATCH

        if signature_valid:
            self._device_counters[submission.device_id] = max(
                submission.device_counter,
                self._device_counters.get(submission.device_id, -1),
            )
        if reason is None and self._is_duplicate(assessment, submission.received_at):
            reason = QuarantineReason.DUPLICATE_MEDIA
        if reason is None and self._rate_limited(submission):
            reason = QuarantineReason.RATE_LIMITED

        contribution = round(
            trust_value * _visual_confidence(submission, assessment) * geo_integrity,
            6,
        )
        if reason is not None:
            contribution = 0.0
        report = CommunityReport(
            report_id=submission.report_id,
            reporter_id=submission.reporter_id,
            reporter_role=profile.role,
            device_id=submission.device_id,
            device_counter=submission.device_counter,
            captured_at=submission.captured_at,
            received_at=submission.received_at,
            latitude=submission.latitude,
            longitude=submission.longitude,
            cell_id=cell_id,
            geohash7=subcell,
            channel=submission.channel,
            claimed_depth=submission.claimed_depth,
            predicted_depth=assessment.predicted_depth,
            classifier_class=assessment.hazard_class,
            classifier_confidence=assessment.confidence,
            trust_at_submit=trust_value,
            geo_integrity=geo_integrity,
            contribution=contribution,
            status=ReportStatus.QUARANTINED if reason else ReportStatus.ACCEPTED,
            signature_valid=signature_valid,
            attestation_passed=attestation_passed,
            quarantine_reason=reason,
            perceptual_hash=assessment.perceptual_hash,
        )
        self.store.add(report)
        if reason is None:
            self._remember_arrival(submission)
        return report

    def crowd_confidence(
        self,
        cell_id: str,
        *,
        as_of: datetime,
        sparse_zone: bool = False,
        exclude_report_ids: frozenset[str] = frozenset(),
    ) -> CrowdAssessment:
        recent = [
            item
            for item in self.store.recent(as_of=as_of, window=self.policy.cluster_window)
            if item.cell_id == cell_id and item.report_id not in exclude_report_ids
        ]
        accepted = [item for item in recent if item.status is ReportStatus.ACCEPTED]
        devices = {item.device_id for item in accepted}
        subcells = {item.geohash7 for item in accepted}
        has_verified = any(item.reporter_role is ReporterRole.VERIFIED_NODE for item in accepted)
        use_sparse = sparse_zone and has_verified
        minimum_devices = (
            self.policy.sparse_minimum_devices if use_sparse else self.policy.minimum_devices
        )
        minimum_subcells = (
            self.policy.sparse_minimum_subcells if use_sparse else self.policy.minimum_subcells
        )
        gate_satisfied = len(devices) >= minimum_devices and len(subcells) >= minimum_subcells

        selected: list[CommunityReport] = []
        used_devices: set[str] = set()
        used_subcells: set[str] = set()
        for report in sorted(accepted, key=lambda item: item.contribution, reverse=True):
            if report.device_id in used_devices or report.geohash7 in used_subcells:
                continue
            selected.append(report)
            used_devices.add(report.device_id)
            used_subcells.add(report.geohash7)

        product = 1.0
        for report in selected:
            age_minutes = max(0.0, (as_of - report.captured_at).total_seconds() / 60)
            recency = 0.5 ** (age_minutes / self.policy.recency_half_life_minutes)
            product *= 1.0 - min(0.95, report.contribution * recency)
        raw_confidence = 1.0 - product
        spread_factor = min(1.0, len(subcells) / self.policy.spread_target)
        confidence = raw_confidence * spread_factor
        if not gate_satisfied:
            confidence = min(confidence, 0.49)
        return CrowdAssessment(
            cell_id=cell_id,
            as_of=as_of,
            crowd_confidence=round(confidence, 6),
            raw_confidence=round(raw_confidence, 6),
            spread_factor=round(spread_factor, 6),
            distinct_devices=len(devices),
            distinct_subcells=len(subcells),
            accepted_reports=len(accepted),
            quarantined_reports=len(recent) - len(accepted),
            gate_satisfied=gate_satisfied,
            sparse_zone_relaxation=use_sparse,
            contributing_report_ids=tuple(item.report_id for item in selected),
        )

    def _is_duplicate(self, assessment: ContentAssessment, as_of: datetime) -> bool:
        if assessment.perceptual_hash is None:
            return False
        for report in self.store.recent(as_of=as_of, window=self.policy.duplicate_window):
            if report.perceptual_hash is None or report.status is not ReportStatus.ACCEPTED:
                continue
            distance = (assessment.perceptual_hash ^ report.perceptual_hash).bit_count()
            if distance <= self.policy.maximum_phash_distance:
                return True
        return False

    def _rate_limited(self, submission: ReportSubmission) -> bool:
        earliest = submission.received_at - self.policy.cluster_window
        device_arrivals = self._device_arrivals[submission.device_id]
        reporter_arrivals = self._reporter_arrivals[submission.reporter_id]
        while device_arrivals and device_arrivals[0] < earliest:
            device_arrivals.popleft()
        while reporter_arrivals and reporter_arrivals[0] < earliest:
            reporter_arrivals.popleft()
        return (
            len(device_arrivals) >= self.policy.max_device_reports
            or len(reporter_arrivals) >= self.policy.max_reporter_reports
        )

    def _remember_arrival(self, submission: ReportSubmission) -> None:
        self._device_arrivals[submission.device_id].append(submission.received_at)
        self._reporter_arrivals[submission.reporter_id].append(submission.received_at)


def _conservative_beta(alpha: float, beta: float) -> float:
    total = alpha + beta
    mean = alpha / total
    variance = alpha * beta / (total * total * (total + 1))
    return round(min(0.95, max(0.05, mean - math.sqrt(variance))), 6)


def _visual_confidence(
    submission: ReportSubmission, assessment: ContentAssessment
) -> float:
    if submission.channel in {ReportChannel.SMS, ReportChannel.IVR}:
        return 0.35
    if assessment.predicted_depth is None:
        return assessment.confidence * 0.6
    difference = abs(
        _DEPTH_ORDER[submission.claimed_depth] - _DEPTH_ORDER[assessment.predicted_depth]
    )
    agreement = 1.0 if difference == 0 else 0.75 if difference == 1 else 0.4
    return assessment.confidence * agreement


def _geo_integrity(submission: ReportSubmission) -> float:
    accuracy = max(0.2, 1.0 - min(max(submission.gps_accuracy_m, 0.0), 160.0) / 200.0)
    if submission.channel is ReportChannel.SMS:
        return round(accuracy * 0.6, 6)
    if submission.channel is ReportChannel.IVR:
        return round(accuracy * 0.5, 6)
    return round(accuracy, 6)
