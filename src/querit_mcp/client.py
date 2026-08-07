"""Credential-safe client for the public Querit API."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from querit_mcp import __version__
from querit_mcp.auth import AuthMode, Credential
from querit_mcp.errors import AuthenticationError, FreeTierUnavailableError, UpstreamError
from querit_mcp.logging_config import log_event, safe_request_id

logger = logging.getLogger(__name__)


class QueritAPIClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        credential: Credential,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        failure_kind = "unknown"
        status: int | None = None
        headers = {
            "Authorization": credential.authorization,
            "User-Agent": f"querit-mcp/{__version__}",
        }
        if request_id:
            headers["X-Request-ID"] = request_id

        try:
            try:
                response = await self.client.post(path, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                failure_kind = "timeout"
                raise UpstreamError("Querit API timed out. Retry the tool call later.") from exc
            except httpx.HTTPError as exc:
                failure_kind = "network_error"
                raise UpstreamError("Querit API is temporarily unavailable. Retry the tool call later.") from exc

            status = response.status_code
            try:
                body = self._parse_body(response)
            except UpstreamError:
                failure_kind = "invalid_response"
                raise
            business_status = self._business_status(body)
            if response.is_success and business_status is None:
                return body

            status = business_status or response.status_code
            failure_kind = "upstream_response"
            self._raise_public_error(status, body, credential.mode, path)
            raise AssertionError("unreachable")
        except (AuthenticationError, FreeTierUnavailableError, UpstreamError) as exc:
            self._log_failure(
                path=path,
                credential=credential,
                request_id=request_id,
                started_at=started_at,
                failure_kind=failure_kind,
                status=status,
                exc=exc,
            )
            raise

    @staticmethod
    def _log_failure(
        *,
        path: str,
        credential: Credential,
        request_id: str | None,
        started_at: float,
        failure_kind: str,
        status: int | None,
        exc: Exception,
    ) -> None:
        level = (
            logging.WARNING
            if status is not None and status < 500 and failure_kind == "upstream_response"
            else logging.ERROR
        )
        log_event(
            logger,
            level,
            "upstream_request_failed",
            "Querit upstream request failed",
            path=urlsplit(path).path,
            auth_mode=credential.mode.value,
            request_id=safe_request_id(request_id),
            duration_ms=round((time.perf_counter() - started_at) * 1_000, 2),
            failure_kind=failure_kind,
            status=status,
            error_type=type(exc).__name__,
        )

    @staticmethod
    def _parse_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            if response.status_code >= 500:
                raise UpstreamError("Querit API is temporarily unavailable. Retry the tool call later.") from exc
            raise UpstreamError("Querit API returned an incompatible response.") from exc
        if not isinstance(body, dict):
            raise UpstreamError("Querit API returned an incompatible response.")
        return body

    @staticmethod
    def _business_status(body: dict[str, Any]) -> int | None:
        code = body.get("error_code")
        if code is None or str(code).strip() == "":
            return None
        try:
            numeric = int(str(code))
        except ValueError:
            return 500
        if numeric == 0 or 200 <= numeric < 300:
            return None
        return numeric if 400 <= numeric <= 599 else 500

    @staticmethod
    def _safe_detail(body: dict[str, Any]) -> str | None:
        message = body.get("error_msg")
        if not isinstance(message, str):
            return None
        cleaned = " ".join(message.split())[:300]
        return cleaned or None

    def _raise_public_error(
        self,
        status: int,
        body: dict[str, Any],
        mode: AuthMode,
        path: str,
    ) -> None:
        detail = self._safe_detail(body)
        suffix = f" Detail: {detail}" if detail else ""

        if mode is AuthMode.ANONYMOUS_FREE and status in {401, 403, 429}:
            raise FreeTierUnavailableError("Querit anonymous access is temporarily unavailable.")
        if mode is AuthMode.BYOK and status == 401:
            raise AuthenticationError("The provided Querit API Key is invalid.")
        if status == 400:
            raise UpstreamError(f"Querit rejected the tool parameters.{suffix}")
        if status == 403 and path == "/v1/contents":
            raise AuthenticationError("This Querit API Key cannot use contents with its current plan.")
        if status == 403:
            raise AuthenticationError("This Querit API Key cannot access the requested capability.")
        if status == 429:
            raise UpstreamError("The Querit API Key quota or upstream rate limit was reached. Retry later.")
        if status >= 500:
            raise UpstreamError("Querit API is temporarily unavailable. Retry the tool call later.")
        raise UpstreamError(f"Querit API request failed.{suffix}")
