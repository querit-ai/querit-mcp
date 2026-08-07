from typing import Any

import pytest
from pydantic import SecretStr

from querit_mcp.auth import AuthMode, Credential
from querit_mcp.config import Settings
from querit_mcp.errors import FreeTierLimitError, QueritMCPError
from querit_mcp.limiter import LimitDecision
from querit_mcp.service import QueritService


class RecordingAPIClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any], Credential, str | None]] = []

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        credential: Credential,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((path, payload, credential, request_id))
        return self.response


class RecordingLimiter:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.credits: list[int] = []

    async def consume(self, identity: str, credits: int) -> LimitDecision:
        assert len(identity) == 64
        self.credits.append(credits)
        return LimitDecision(allowed=self.allowed)


def anonymous_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "mcp_free_tier_enabled": True,
        "querit_mcp_free_api_key": SecretStr("server-key"),
        "redis_url": SecretStr("redis://localhost:6379/0"),
        "mcp_free_ip_hmac_secret": SecretStr("identity-secret"),
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_search_maps_payload_and_public_response_for_byok(caplog: pytest.LogCaptureFixture) -> None:
    api = RecordingAPIClient(
        {
            "error_code": 0,
            "search_id": 9007199254740993,
            "took": "12",
            "query_context": {"query": "trimmed query"},
            "results": {
                "result": [
                    {
                        "url": "https://example.com",
                        "title": "Example",
                        "snippet": "Snippet",
                        "page_age": "2d",
                        "site_name": "Example",
                        "site_icon": "https://example.com/icon.png",
                        "sentence": ["x" * 300],
                        "internal_score": 0.9,
                    }
                ]
            },
            "internal_field": "must not escape",
        }
    )
    service = QueritService(
        settings=Settings(max_item_content_chars=256),
        api_client=api,
        free_limiter=None,
    )
    caplog.set_level("INFO", logger="querit_mcp.service")

    result = await service.search(
        headers={"authorization": "Bearer user-key"},
        request_id="request-1",
        query="  trimmed query  ",
        count=5,
        chunks_per_doc=1,
        include_content=True,
        languages=["english"],
        include_domains=["example.com"],
        exclude_domains=None,
        date_range="d7",
        countries=["united states"],
    )

    path, payload, credential, request_id = api.calls[0]
    assert path == "/v1/search"
    assert payload["needContent"] is True
    assert payload["filters"]["sites"]["include"] == ["example.com"]
    assert credential.mode is AuthMode.BYOK
    assert request_id == "request-1"
    assert result.search_id == "9007199254740993"
    assert result.results[0].sentences == ["x" * 256]
    assert result.results[0].sentences_truncated is True
    assert result.response_truncated is True
    assert "internal" not in result.model_dump_json()
    event = caplog.records[-1].__dict__["json_fields"]
    assert event["event"] == "tool_call_completed"
    assert event["tool"] == "querit_search"
    assert event["auth_mode"] == "byok"
    assert event["requested_count"] == 5
    assert event["result_count"] == 1
    assert event["response_truncated"] is True
    assert "trimmed query" not in caplog.text
    assert "user-key" not in caplog.text


@pytest.mark.asyncio
async def test_anonymous_search_consumes_one_credit_and_server_key() -> None:
    api = RecordingAPIClient({"error_code": 0, "results": {"result": []}})
    limiter = RecordingLimiter()
    service = QueritService(settings=anonymous_settings(), api_client=api, free_limiter=limiter)

    await service.search(
        headers={"x-querit-client-ip": "203.0.113.5"},
        request_id=None,
        query="query",
        count=5,
        chunks_per_doc=1,
        include_content=False,
        languages=None,
        include_domains=None,
        exclude_domains=None,
        date_range=None,
        countries=None,
    )

    assert limiter.credits == [1]
    assert api.calls[0][2] == Credential(AuthMode.ANONYMOUS_FREE, "Bearer server-key")


@pytest.mark.asyncio
async def test_anonymous_include_content_requires_byok_before_charging(caplog: pytest.LogCaptureFixture) -> None:
    api = RecordingAPIClient({"error_code": 0, "results": {"result": []}})
    limiter = RecordingLimiter()
    service = QueritService(settings=anonymous_settings(), api_client=api, free_limiter=limiter)
    caplog.set_level("WARNING", logger="querit_mcp.service")

    with pytest.raises(FreeTierLimitError, match="include_content=false"):
        await service.search(
            headers={"x-querit-client-ip": "203.0.113.5"},
            request_id=None,
            query="query",
            count=5,
            chunks_per_doc=1,
            include_content=True,
            languages=None,
            include_domains=None,
            exclude_domains=None,
            date_range=None,
            countries=None,
        )

    assert limiter.credits == []
    assert api.calls == []
    event = caplog.records[-1].__dict__["json_fields"]
    assert event["event"] == "tool_call_rejected"
    assert event["tool"] == "querit_search"
    assert event["auth_mode"] == "anonymous_free"
    assert event["error_type"] == "FreeTierLimitError"


@pytest.mark.asyncio
async def test_anonymous_parameter_limit_fails_before_charging() -> None:
    limiter = RecordingLimiter()
    service = QueritService(
        settings=anonymous_settings(),
        api_client=RecordingAPIClient({}),
        free_limiter=limiter,
    )

    with pytest.raises(FreeTierLimitError):
        await service.search(
            headers={"x-querit-client-ip": "203.0.113.5"},
            request_id=None,
            query="query",
            count=6,
            chunks_per_doc=1,
            include_content=False,
            languages=None,
            include_domains=None,
            exclude_domains=None,
            date_range=None,
            countries=None,
        )

    assert limiter.credits == []


@pytest.mark.asyncio
async def test_contents_merges_status_by_id_and_uses_metadata_url() -> None:
    api = RecordingAPIClient(
        {
            "error_code": 0,
            "search_id": "search-1",
            "searchTime": 1.25,
            "statuses": [{"id": "b", "status": "failed"}, {"id": "a", "status": "success"}],
            "results": [
                {
                    "id": "a",
                    "content": "content",
                    "url": "https://fallback.example",
                    "extrasMeta": {
                        "title": "Page",
                        "url": "https://canonical.example",
                        "publishTime": "2026-08-01",
                        "siteName": "Canonical",
                        "siteIcon": "https://canonical.example/icon.png",
                    },
                }
            ],
        }
    )
    service = QueritService(settings=Settings(), api_client=api, free_limiter=None)

    result = await service.contents(
        headers={"authorization": "Bearer user-key"},
        request_id="request-2",
        urls=["https://canonical.example"],
        format="markdown",
        crawl_timeout=10,
        max_content_chars=8_000,
    )

    assert result.results[0].status == "success"
    assert result.results[0].url == "https://canonical.example"
    assert result.results[0].metadata is not None
    assert result.results[0].metadata.publish_time == "2026-08-01"
    assert api.calls[0][1]["extrasMeta"] is True


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "https://user:password@example.com", "not-a-url"],
)
@pytest.mark.asyncio
async def test_contents_rejects_unsafe_urls(url: str) -> None:
    service = QueritService(settings=Settings(), api_client=RecordingAPIClient({}), free_limiter=None)

    with pytest.raises(QueritMCPError):
        await service.contents(
            headers={"authorization": "Bearer user-key"},
            request_id=None,
            urls=[url],
            format="markdown",
            crawl_timeout=10,
            max_content_chars=8_000,
        )
