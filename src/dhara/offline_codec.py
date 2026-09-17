from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass

from dhara.community import DepthOrdinal
from dhara.geo import geohash_for

PROTOCOL_TAG = "DHR"
HAZARD_CODE = "FL"
_DEPTH_TO_CODE = {
    DepthOrdinal.ANKLE: "A",
    DepthOrdinal.KNEE: "K",
    DepthOrdinal.WAIST: "W",
    DepthOrdinal.ABOVE_WAIST: "X",
}
_CODE_TO_DEPTH = {value: key for key, value in _DEPTH_TO_CODE.items()}
_GEOHASH_ALPHABET = frozenset("0123456789bcdefghjkmnpqrstuvwxyz")


class OfflineCodecError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecodedOfflineReport:
    hazard_code: str
    depth: DepthOrdinal
    geohash8: str
    minutes_elapsed: int
    signature: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["depth"] = self.depth.value
        return result


class OfflineReportCodec:
    def __init__(self, device_key: bytes) -> None:
        if len(device_key) < 16:
            raise ValueError("offline codec device key must be at least 16 bytes")
        self.device_key = device_key

    def encode(
        self,
        *,
        depth: DepthOrdinal,
        latitude: float,
        longitude: float,
        minutes_elapsed: int,
    ) -> str:
        if not 0 <= minutes_elapsed <= 9_999:
            raise OfflineCodecError("minutes_elapsed must be between 0 and 9999")
        geohash8 = geohash_for(latitude, longitude, precision=8)
        unsigned = (
            f"{PROTOCOL_TAG}|{HAZARD_CODE}|{_DEPTH_TO_CODE[depth]}|"
            f"{geohash8}|+{minutes_elapsed:04d}"
        )
        signature = self._signature(unsigned)
        payload = f"{unsigned}|{signature}"
        if len(payload) > 140:
            raise OfflineCodecError("offline report exceeds one 140-character SMS segment")
        return payload

    def decode(self, payload: str) -> DecodedOfflineReport:
        if len(payload) > 140 or "\n" in payload or "\r" in payload:
            raise OfflineCodecError("invalid offline SMS payload length or framing")
        parts = payload.split("|")
        if len(parts) != 6:
            raise OfflineCodecError("offline SMS payload must contain six fields")
        protocol, hazard, depth_code, geohash8, elapsed_raw, supplied_signature = parts
        if protocol != PROTOCOL_TAG or hazard != HAZARD_CODE:
            raise OfflineCodecError("unsupported offline report protocol or hazard")
        if depth_code not in _CODE_TO_DEPTH:
            raise OfflineCodecError("unsupported offline depth code")
        if len(geohash8) != 8 or any(char not in _GEOHASH_ALPHABET for char in geohash8):
            raise OfflineCodecError("invalid geohash-8")
        if len(elapsed_raw) != 5 or not elapsed_raw.startswith("+"):
            raise OfflineCodecError("invalid elapsed-minute field")
        try:
            minutes_elapsed = int(elapsed_raw)
        except ValueError as exc:
            raise OfflineCodecError("invalid elapsed-minute field") from exc
        unsigned = "|".join(parts[:-1])
        expected_signature = self._signature(unsigned)
        if not hmac.compare_digest(expected_signature, supplied_signature.upper()):
            raise OfflineCodecError("offline report signature mismatch")
        return DecodedOfflineReport(
            hazard_code=hazard,
            depth=_CODE_TO_DEPTH[depth_code],
            geohash8=geohash8,
            minutes_elapsed=minutes_elapsed,
            signature=supplied_signature.upper(),
        )

    def _signature(self, unsigned: str) -> str:
        digest = hmac.new(
            self.device_key,
            unsigned.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return digest[:10].upper()
