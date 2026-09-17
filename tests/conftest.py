from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dhara.community import (  # noqa: E402
    CommunityEngine,
    ContentAssessment,
    DepthOrdinal,
    HmacDeviceSignatureVerifier,
    ReportChannel,
    ReporterRole,
    ReportSubmission,
    StaticAttestationVerifier,
    StaticContentClassifier,
)
from dhara.sensor_dataset import load_sensor_dataset  # noqa: E402
from dhara.sensor_models import SensorEnsemble  # noqa: E402

SENSOR_FIXTURE = (
    Path(__file__).parents[1] / "data" / "training" / "synthetic_sensor_episodes.csv"
)


@pytest.fixture(scope="session")
def sensor_dataset():
    return load_sensor_dataset(SENSOR_FIXTURE)


@pytest.fixture(scope="session")
def fitted_sensor_model(sensor_dataset):
    return SensorEnsemble(version="test-loop-a").fit(
        sensor_dataset.matrix, sensor_dataset.outcomes
    )


@dataclass(slots=True)
class CommunityHarness:
    engine: CommunityEngine
    signer: HmacDeviceSignatureVerifier
    tokens: dict[str, str]
    now: datetime
    locations: tuple[tuple[float, float], ...]

    def submission(
        self,
        *,
        report_id: str,
        reporter_id: str,
        device_id: str,
        counter: int,
        location: tuple[float, float],
        media_reference: str,
        minutes_ago: int = 1,
    ) -> ReportSubmission:
        captured_at = self.now - timedelta(minutes=minutes_ago)
        unsigned = ReportSubmission(
            report_id=report_id,
            reporter_id=reporter_id,
            device_id=device_id,
            device_counter=counter,
            captured_at=captured_at,
            received_at=captured_at + timedelta(seconds=20),
            latitude=location[0],
            longitude=location[1],
            gps_accuracy_m=10.0,
            channel=ReportChannel.APP,
            claimed_depth=DepthOrdinal.KNEE,
            media_reference=media_reference,
            attestation_token=self.tokens[device_id],
            signature="unsigned",
        )
        return replace(unsigned, signature=self.signer.sign(unsigned))


@pytest.fixture
def community_harness() -> CommunityHarness:
    devices = ["shared-device", *(f"device-{index}" for index in range(1, 8))]
    keys = {device: f"key-for-{device}".encode() for device in devices}
    tokens = {device: f"attested-{device}" for device in devices}
    media_names = [
        *(f"attack-{index}" for index in range(15)),
        *(f"legit-{index}" for index in range(8)),
        "duplicate",
        "irrelevant",
    ]
    assessments = {
        name: ContentAssessment(
            hazard_class="irrelevant" if name == "irrelevant" else "flooded_road",
            confidence=0.0 if name == "irrelevant" else 0.95,
            predicted_depth=DepthOrdinal.KNEE,
            perceptual_hash=int.from_bytes(sha256(name.encode()).digest()[:8]),
        )
        for name in media_names
    }
    signer = HmacDeviceSignatureVerifier(keys)
    engine = CommunityEngine(
        classifier=StaticContentClassifier(assessments),
        attestation=StaticAttestationVerifier(tokens),
        signatures=signer,
    )
    engine.trust.register("verified-1", ReporterRole.VERIFIED_NODE)
    for index in range(2, 5):
        engine.trust.register(f"citizen-{index}", ReporterRole.CITIZEN)
    return CommunityHarness(
        engine=engine,
        signer=signer,
        tokens=tokens,
        now=datetime(2026, 9, 17, 18, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        locations=(
            (18.5202, 73.85695),
            (18.5202, 73.85745),
            (18.5214, 73.85695),
            (18.5214, 73.85725),
        ),
    )
