from __future__ import annotations

import h3

H3_RESOLUTION = 9
_GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"


def cell_for(latitude: float, longitude: float, resolution: int = H3_RESOLUTION) -> str:
    if not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90")
    if not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180")
    return h3.latlng_to_cell(latitude, longitude, resolution)


def geohash_for(latitude: float, longitude: float, precision: int = 7) -> str:
    if not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90")
    if not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180")
    if precision < 1:
        raise ValueError("geohash precision must be positive")
    latitude_range = [-90.0, 90.0]
    longitude_range = [-180.0, 180.0]
    bits = (16, 8, 4, 2, 1)
    result: list[str] = []
    bit_index = character = 0
    use_longitude = True
    while len(result) < precision:
        interval = longitude_range if use_longitude else latitude_range
        value = longitude if use_longitude else latitude
        midpoint = (interval[0] + interval[1]) / 2
        if value >= midpoint:
            character |= bits[bit_index]
            interval[0] = midpoint
        else:
            interval[1] = midpoint
        use_longitude = not use_longitude
        if bit_index < 4:
            bit_index += 1
        else:
            result.append(_GEOHASH_ALPHABET[character])
            bit_index = character = 0
    return "".join(result)
