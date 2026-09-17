from __future__ import annotations

from dataclasses import replace

from dhara.connectors import normalize
from dhara.domain import QualityStatus
from dhara.qc import assess


def _rainfall():
    return normalize(
        {
            "provider": "imd",
            "received_at": "2024-07-26T18:01:00+05:30",
            "payload": {
                "observation_id": "imd-1",
                "station_id": "station-a",
                "observed_at": "2024-07-26T18:00:00+05:30",
                "latitude": 18.5204,
                "longitude": 73.8567,
                "rainfall_mm_15m": 12.5,
            },
        }
    )


def test_valid_observation_is_accepted() -> None:
    assert assess(_rainfall()).status is QualityStatus.ACCEPTED


def test_out_of_range_observation_is_rejected() -> None:
    assessed = assess(replace(_rainfall(), value=999.0))
    assert assessed.status is QualityStatus.REJECTED
    assert "value_out_of_range" in assessed.quality_flags
