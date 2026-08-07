"""Querit MCP business orchestration."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlsplit

from querit_mcp.auth import AuthMode, Credential, anonymous_identity, select_credential
from querit_mcp.config import Settings
from querit_mcp.errors import FreeTierLimitError, FreeTierUnavailableError, QueritMCPError, UpstreamError
from querit_mcp.limiter import FreeTierLimiter
from querit_mcp.logging_config import log_event, safe_request_id
from querit_mcp.models import ContentsOutput, SearchOutput
from querit_mcp.transform import map_contents_response, map_search_response

_DOMAIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_RELATIVE_DATE_PATTERN = re.compile(r"^[dwmy]([1-9][0-9]*)$")
_ABSOLUTE_DATE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})to(\d{4}-\d{2}-\d{2})$")

logger = logging.getLogger(__name__)


class QueritClient(Protocol):
    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        credential: Credential,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]: ...


class QueritService:
    def __init__(
        self,
        *,
        settings: Settings,
        api_client: QueritClient,
        free_limiter: FreeTierLimiter | None,
    ) -> None:
        self.settings = settings
        self.api_client = api_client
        self.free_limiter = free_limiter

    async def search(
        self,
        *,
        headers: Mapping[str, str] | None,
        request_id: str | None,
        query: str,
        count: int,
        chunks_per_doc: int,
        include_content: bool,
        languages: Sequence[str] | None,
        include_domains: Sequence[str] | None,
        exclude_domains: Sequence[str] | None,
        date_range: str | None,
        countries: Sequence[str] | None,
    ) -> SearchOutput:
        started_at = time.perf_counter()
        credential: Credential | None = None
        try:
            query = query.strip()
            if not query:
                raise QueritMCPError("query must not be blank")
            credential = select_credential(headers, self.settings)
            if credential.mode is AuthMode.ANONYMOUS_FREE:
                if include_content:
                    raise FreeTierLimitError(
                        "Anonymous search requires include_content=false. "
                        "Add a Querit API Key to return content sentences."
                    )
                if count > self.settings.mcp_free_search_max_count or chunks_per_doc != 1:
                    raise FreeTierLimitError(
                        "Anonymous search supports count up to "
                        f"{self.settings.mcp_free_search_max_count} and chunks_per_doc=1. "
                        "Add a Querit API Key for more."
                    )
                await self._consume_free_credits(headers, 1)

            payload: dict[str, Any] = {
                "query": query,
                "count": count,
                "chunksPerDoc": chunks_per_doc,
                "needContent": include_content,
            }
            filters = self._build_filters(
                languages=languages,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                date_range=date_range,
                countries=countries,
            )
            if filters:
                payload["filters"] = filters

            body = await self.api_client.post("/v1/search", payload, credential, request_id=request_id)
            output = map_search_response(
                body,
                request_query=query,
                max_item_content_chars=self.settings.max_item_content_chars,
                max_response_chars=self.settings.max_response_chars,
            )
        except Exception as exc:
            self._log_tool_failure(
                tool="querit_search",
                request_id=request_id,
                started_at=started_at,
                credential=credential,
                exc=exc,
            )
            raise

        log_event(
            logger,
            logging.INFO,
            "tool_call_completed",
            "Querit tool call completed",
            tool="querit_search",
            outcome="success",
            auth_mode=credential.mode.value,
            request_id=safe_request_id(request_id),
            duration_ms=self._duration_ms(started_at),
            requested_count=count,
            result_count=len(output.results),
            include_content=include_content,
            omitted_result_count=output.omitted_result_count,
            response_truncated=output.response_truncated,
        )
        return output

    async def contents(
        self,
        *,
        headers: Mapping[str, str] | None,
        request_id: str | None,
        urls: Sequence[str],
        format: str,
        crawl_timeout: int,
        max_content_chars: int,
    ) -> ContentsOutput:
        started_at = time.perf_counter()
        credential: Credential | None = None
        try:
            normalized_urls = [self._validate_url(url) for url in urls]
            credential = select_credential(headers, self.settings)
            if max_content_chars > self.settings.max_item_content_chars:
                raise QueritMCPError(
                    f"max_content_chars cannot exceed the server limit of {self.settings.max_item_content_chars}"
                )
            if credential.mode is AuthMode.ANONYMOUS_FREE:
                if (
                    len(normalized_urls) > self.settings.mcp_free_contents_max_urls
                    or max_content_chars > self.settings.mcp_free_contents_max_chars
                    or crawl_timeout > self.settings.mcp_free_crawl_timeout_seconds
                ):
                    raise FreeTierLimitError(
                        "Anonymous contents limits were exceeded. "
                        "Reduce URLs/content size/crawl timeout or add a Querit API Key."
                    )
                await self._consume_free_credits(headers, 2 * len(normalized_urls))

            payload = {
                "urls": normalized_urls,
                "format": format,
                "crawlTimeout": crawl_timeout,
                "extrasMeta": True,
            }
            body = await self.api_client.post("/v1/contents", payload, credential, request_id=request_id)
            output = map_contents_response(
                body,
                max_content_chars=max_content_chars,
                max_response_chars=self.settings.max_response_chars,
            )
        except Exception as exc:
            self._log_tool_failure(
                tool="querit_contents",
                request_id=request_id,
                started_at=started_at,
                credential=credential,
                exc=exc,
            )
            raise

        log_event(
            logger,
            logging.INFO,
            "tool_call_completed",
            "Querit tool call completed",
            tool="querit_contents",
            outcome="success",
            auth_mode=credential.mode.value,
            request_id=safe_request_id(request_id),
            duration_ms=self._duration_ms(started_at),
            url_count=len(normalized_urls),
            content_format=format,
            result_count=len(output.results),
            omitted_result_count=output.omitted_result_count,
            response_truncated=output.response_truncated,
        )
        return output

    @staticmethod
    def _duration_ms(started_at: float) -> float:
        return round((time.perf_counter() - started_at) * 1_000, 2)

    @classmethod
    def _log_tool_failure(
        cls,
        *,
        tool: str,
        request_id: str | None,
        started_at: float,
        credential: Credential | None,
        exc: Exception,
    ) -> None:
        operational_failure = isinstance(exc, UpstreamError | FreeTierUnavailableError) or not isinstance(
            exc, QueritMCPError
        )
        log_event(
            logger,
            logging.ERROR if operational_failure else logging.WARNING,
            "tool_call_failed" if operational_failure else "tool_call_rejected",
            "Querit tool call failed" if operational_failure else "Querit tool call rejected",
            tool=tool,
            outcome="failed" if operational_failure else "rejected",
            auth_mode=credential.mode.value if credential is not None else "unknown",
            request_id=safe_request_id(request_id),
            duration_ms=cls._duration_ms(started_at),
            error_type=type(exc).__name__,
        )

    async def _consume_free_credits(self, headers: Mapping[str, str] | None, credits: int) -> None:
        """Reserve shared credits before an upstream call can consume deployment budget."""

        if self.free_limiter is None:
            raise FreeTierUnavailableError("Querit anonymous access is temporarily unavailable.")
        identity = anonymous_identity(headers, self.settings)
        decision = await self.free_limiter.consume(identity, credits)
        if not decision.allowed:
            raise FreeTierLimitError(
                "Querit anonymous free limit reached. Wait for the limit window to reset or add your own API Key."
            )

    @classmethod
    def _build_filters(
        cls,
        *,
        languages: Sequence[str] | None,
        include_domains: Sequence[str] | None,
        exclude_domains: Sequence[str] | None,
        date_range: str | None,
        countries: Sequence[str] | None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if languages:
            filters["languages"] = {"include": cls._clean_strings(languages)}
        include = cls._validate_domains(include_domains)
        exclude = cls._validate_domains(exclude_domains)
        if include or exclude:
            filters["sites"] = {}
            if include:
                filters["sites"]["include"] = include
            if exclude:
                filters["sites"]["exclude"] = exclude
        if date_range:
            filters["timeRange"] = {"date": cls._validate_date_range(date_range.strip())}
        if countries:
            filters["geo"] = {"countries": {"include": cls._clean_strings(countries)}}
        return filters

    @staticmethod
    def _clean_strings(values: Sequence[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]

    @classmethod
    def _validate_domains(cls, values: Sequence[str] | None) -> list[str]:
        if not values:
            return []
        domains = cls._clean_strings(values)
        invalid = [domain for domain in domains if not _DOMAIN_PATTERN.fullmatch(domain)]
        if invalid:
            raise QueritMCPError("domains must be hostnames without a scheme, port, or path")
        return domains

    @staticmethod
    def _validate_date_range(value: str) -> str:
        if _RELATIVE_DATE_PATTERN.fullmatch(value):
            return value
        match = _ABSOLUTE_DATE_PATTERN.fullmatch(value)
        if match is None:
            raise QueritMCPError("date_range must look like d7, w2, m3, y1, or YYYY-MM-DDtoYYYY-MM-DD")
        try:
            start = date.fromisoformat(match.group(1))
            end = date.fromisoformat(match.group(2))
        except ValueError as exc:
            raise QueritMCPError("date_range contains an invalid calendar date") from exc
        if start > end:
            raise QueritMCPError("date_range start must not be after its end")
        return value

    @staticmethod
    def _validate_url(value: str) -> str:
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise QueritMCPError("urls must contain valid http/https URLs without userinfo")
        return normalized
