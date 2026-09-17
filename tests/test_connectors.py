from __future__ import annotations

import pytest

from dhara.connectors import NormalisationError, normalize
from dhara.domain import Metric, Provider


def test_imd_payload_normalises_to_h3_observation() -> None:
    observation = normalize(
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

    assert observation.source is Provider.IMD
    assert observation.metric is Metric.RAINFALL_MM_15M
    assert observation.cell_id.startswith("89")
    assert len(observation.cell_id) == 15


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(NormalisationError, match="unsupported provider"):
        normalize({"provider": "unknown", "payload": {}})


def test_invalid_timestamp_is_a_normalisation_error() -> None:
    payload = {
        "provider": "imd",
        "received_at": "not-a-timestamp",
        "payload": {
            "observation_id": "imd-1",
            "station_id": "station-a",
            "observed_at": "2024-07-26T18:00:00+05:30",
            "latitude": 18.5204,
            "longitude": 73.8567,
            "rainfall_mm_15m": 12.5,
        },
    }
    with pytest.raises(NormalisationError, match="valid ISO-8601"):
        normalize(payload)
