from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dhara.connectors import normalize
from dhara.domain import Observation, QualityStatus, StoredObservation
from dhara.qc import assess
from dhara.repository import ObservationRepository


class IngestionService:
    def __init__(self, repository: ObservationRepository) -> None:
        self.repository = repository

    def ingest(self, envelope: Mapping[str, Any]) -> StoredObservation:
        return self.ingest_observation(normalize(envelope))

    def ingest_observation(self, observation: Observation) -> StoredObservation:
        assessed = assess(observation)
        if assessed.status is QualityStatus.REJECTED:
            return StoredObservation(id=-1, observation=assessed, created=False)
        return self.repository.put(assessed)
