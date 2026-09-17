from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum


class OperatingMode(StrEnum):
    SHADOW = "shadow"


@dataclass(frozen=True, slots=True)
class SafetyControls:
    mode: OperatingMode = OperatingMode.SHADOW
    public_delivery_enabled: bool = False
    sandbox_delivery_enabled: bool = True
    cap_status: str = "Test"
    runtime_translation_enabled: bool = False

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> SafetyControls:
        values = os.environ if environment is None else environment
        requested_mode = values.get("DHARA_OPERATING_MODE", "shadow").strip().lower()
        if requested_mode != OperatingMode.SHADOW.value:
            raise RuntimeError(
                "this prototype is hard-locked to shadow mode; live operation is unavailable"
            )
        return cls()

    def assert_sandbox_delivery(self) -> None:
        if (
            self.mode is not OperatingMode.SHADOW
            or self.public_delivery_enabled
            or not self.sandbox_delivery_enabled
            or self.cap_status != "Test"
        ):
            raise RuntimeError("sandbox delivery safety controls are not satisfied")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
