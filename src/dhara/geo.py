from __future__ import annotations

import h3

H3_RESOLUTION = 9


def cell_for(latitude: float, longitude: float, resolution: int = H3_RESOLUTION) -> str:
    if not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90")
    if not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180")
    return h3.latlng_to_cell(latitude, longitude, resolution)
