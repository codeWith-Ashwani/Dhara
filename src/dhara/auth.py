from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class OperatorRole(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    SUPERVISOR = "supervisor"


_ROLE_RANK = {
    OperatorRole.VIEWER: 1,
    OperatorRole.OPERATOR: 2,
    OperatorRole.SUPERVISOR: 3,
}


class AuthenticationError(ValueError):
    pass


class AuthorizationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OperatorPrincipal:
    subject: str
    role: OperatorRole
    issued_at: datetime
    expires_at: datetime
    token_id: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["role"] = self.role.value
        result["issued_at"] = self.issued_at.isoformat()
        result["expires_at"] = self.expires_at.isoformat()
        return result


class OperatorAuthenticator:
    """Verifies short-lived gateway-style HMAC operator claims."""

    def __init__(
        self,
        secret: bytes,
        *,
        issuer: str = "dhara-authority-gateway",
        audience: str = "dhara-shadow-api",
        ephemeral: bool = False,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("operator token secret must contain at least 32 bytes")
        self._secret = secret
        self.issuer = issuer
        self.audience = audience
        self.ephemeral = ephemeral

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> OperatorAuthenticator:
        values = os.environ if environment is None else environment
        configured = values.get("DHARA_OPERATOR_HMAC_SECRET")
        if configured is None:
            return cls(secrets.token_bytes(32), ephemeral=True)
        return cls(configured.encode())

    def issue_token(
        self,
        *,
        subject: str,
        role: OperatorRole,
        issued_at: datetime,
        lifetime: timedelta = timedelta(minutes=30),
    ) -> str:
        if issued_at.tzinfo is None:
            raise ValueError("operator token issued_at must include a timezone")
        if not subject.strip():
            raise ValueError("operator token subject is required")
        if lifetime <= timedelta(0) or lifetime > timedelta(hours=8):
            raise ValueError("operator token lifetime must be between 0 and 8 hours")
        issued_utc = issued_at.astimezone(UTC)
        payload = {
            "aud": self.audience,
            "exp": int((issued_utc + lifetime).timestamp()),
            "iat": int(issued_utc.timestamp()),
            "iss": self.issuer,
            "jti": str(uuid.uuid4()),
            "role": role.value,
            "sub": subject,
        }
        encoded = _base64url(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        signature = _base64url(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def authenticate(self, token: str, *, now: datetime | None = None) -> OperatorPrincipal:
        try:
            encoded, supplied_signature = token.split(".", maxsplit=1)
        except ValueError as exc:
            raise AuthenticationError("malformed operator token") from exc
        expected_signature = _base64url(
            hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(expected_signature, supplied_signature):
            raise AuthenticationError("invalid operator token signature")
        try:
            payload = json.loads(_base64url_decode(encoded))
            role = OperatorRole(payload["role"])
            issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
            expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
            subject = str(payload["sub"])
            token_id = str(payload["jti"])
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            binascii.Error,
            json.JSONDecodeError,
        ) as exc:
            raise AuthenticationError("invalid operator token claims") from exc
        effective_now = (now or datetime.now(UTC)).astimezone(UTC)
        if payload.get("iss") != self.issuer or payload.get("aud") != self.audience:
            raise AuthenticationError("operator token issuer or audience is invalid")
        if issued_at > effective_now + timedelta(minutes=1):
            raise AuthenticationError("operator token is not yet valid")
        if expires_at <= effective_now:
            raise AuthenticationError("operator token has expired")
        if not subject.strip() or not token_id.strip():
            raise AuthenticationError("operator token identity is invalid")
        return OperatorPrincipal(
            subject=subject,
            role=role,
            issued_at=issued_at,
            expires_at=expires_at,
            token_id=token_id,
        )

    def require_role(
        self,
        principal: OperatorPrincipal,
        minimum: OperatorRole,
    ) -> None:
        if _ROLE_RANK[principal.role] < _ROLE_RANK[minimum]:
            raise AuthorizationError(
                f"{minimum.value} role is required; received {principal.role.value}"
            )

    def status(self) -> dict[str, object]:
        return {
            "enforced": True,
            "scheme": "Bearer HMAC-SHA256 gateway claim",
            "ephemeral_key": self.ephemeral,
            "issuer": self.issuer,
            "audience": self.audience,
        }


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
