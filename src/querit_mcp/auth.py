"""Per-request credential selection and anonymous identity derivation."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from querit_mcp.config import Settings
from querit_mcp.errors import AuthenticationError, FreeTierUnavailableError

_BEARER_PATTERN = re.compile(r"^Bearer[ \t]+([^\s]+)$", re.IGNORECASE)
_INTERNAL_CLIENT_IP_HEADER = "x-querit-client-ip"


class AuthMode(StrEnum):
    ANONYMOUS_FREE = "anonymous_free"
    BYOK = "byok"


@dataclass(frozen=True, slots=True)
class Credential:
    mode: AuthMode
    authorization: str = field(repr=False)


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def select_credential(headers: Mapping[str, str] | None, settings: Settings) -> Credential:
    authorization = _header(headers, "authorization")
    if authorization is not None:
        match = _BEARER_PATTERN.fullmatch(authorization.strip())
        if match is None or len(match.group(1)) > 8_192:
            raise AuthenticationError("Authorization must use the format: Bearer <Querit API Key>")
        return Credential(mode=AuthMode.BYOK, authorization=f"Bearer {match.group(1)}")

    if not settings.mcp_free_tier_enabled or settings.querit_mcp_free_api_key is None:
        raise AuthenticationError(
            "Querit anonymous access is disabled. Configure Authorization: Bearer <Querit API Key>."
        )

    return Credential(
        mode=AuthMode.ANONYMOUS_FREE,
        authorization=f"Bearer {settings.querit_mcp_free_api_key.get_secret_value()}",
    )


def anonymous_identity(headers: Mapping[str, str] | None, settings: Settings) -> str:
    raw_ip = _header(headers, _INTERNAL_CLIENT_IP_HEADER)
    if raw_ip is None or settings.mcp_free_ip_hmac_secret is None:
        raise FreeTierUnavailableError("Querit anonymous access is temporarily unavailable.")
    try:
        normalized_ip = ipaddress.ip_address(raw_ip).compressed
    except ValueError as exc:
        raise FreeTierUnavailableError("Querit anonymous access is temporarily unavailable.") from exc

    digest = hmac.new(
        settings.mcp_free_ip_hmac_secret.get_secret_value().encode(),
        normalized_ip.encode(),
        hashlib.sha256,
    )
    return digest.hexdigest()
