from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from dhara.connectors import normalize
from dhara.domain import QualityStatus
from dhara.ingestion import IngestionService


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    source_file: str
    total: int
    created: int
    duplicates: int
    flagged: int
    rejected: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        for key in ("first_observed_at", "last_observed_at"):
            value = result[key]
            result[key] = value.isoformat() if value is not None else None
        return result


def load_jsonl(path: str | Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}: {exc.msg}") from exc
    return records


def replay_file(path: str | Path, service: IngestionService) -> ReplaySummary:
    source = Path(path)
    normalized = [normalize(record) for record in load_jsonl(source)]
    normalized.sort(key=lambda item: (item.observed_at, item.source.value, item.external_id))

    created = duplicates = flagged = rejected = 0
    for observation in normalized:
        result = service.ingest_observation(observation)
        if result.observation.status is QualityStatus.REJECTED:
            rejected += 1
        elif result.created:
            created += 1
        else:
            duplicates += 1
        if result.observation.status is QualityStatus.FLAGGED:
            flagged += 1

    times = [item.observed_at for item in normalized]
    return ReplaySummary(
        source_file=str(source),
        total=len(normalized),
        created=created,
        duplicates=duplicates,
        flagged=flagged,
        rejected=rejected,
        first_observed_at=min(times) if times else None,
        last_observed_at=max(times) if times else None,
    )
