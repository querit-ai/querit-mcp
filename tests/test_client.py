import httpx
import pytest

from querit_mcp.auth import AuthMode, Credential
from querit_mcp.client import QueritAPIClient
from querit_mcp.errors import AuthenticationError, FreeTierUnavailableError, UpstreamError


@pytest.mark.asyncio
async def test_client_sends_selected_authorization_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer selected-key"
        assert "selected-key" not in str(request.url)
        return httpx.Response(200, json={"error_code": 0, "results": {"result": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.querit.ai") as http:
        body = await QueritAPIClient(http).post(
            "/v1/search",
            {"query": "test"},
            Credential(AuthMode.BYOK, "Bearer selected-key"),
        )

    assert body["error_code"] == 0


@pytest.mark.asyncio
async def test_client_accepts_querit_success_code_200() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error_code": 200, "results": {"result": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.querit.ai") as http:
        body = await QueritAPIClient(http).post(
            "/v1/search",
            {"query": "test"},
            Credential(AuthMode.BYOK, "Bearer selected-key"),
        )

    assert body["error_code"] == 200


@pytest.mark.asyncio
async def test_invalid_byok_returns_auth_error_without_retry(caplog: pytest.LogCaptureFixture) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error_code": "401", "error_msg": "invalid"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.querit.ai") as http:
        caplog.set_level("WARNING", logger="querit_mcp.client")
        with pytest.raises(AuthenticationError, match="invalid"):
            await QueritAPIClient(http).post(
                "/v1/search",
                {},
                Credential(AuthMode.BYOK, "Bearer bad"),
            )

    assert calls == 1
    event = caplog.records[-1].__dict__["json_fields"]
    assert event["event"] == "upstream_request_failed"
    assert event["path"] == "/v1/search"
    assert event["auth_mode"] == "byok"
    assert event["failure_kind"] == "upstream_response"
    assert event["status"] == 401
    assert event["error_type"] == "AuthenticationError"
    assert "bad" not in caplog.text


@pytest.mark.asyncio
async def test_anonymous_upstream_auth_failure_is_server_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error_code": 403})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.querit.ai") as http:
        with pytest.raises(FreeTierUnavailableError):
            await QueritAPIClient(http).post(
                "/v1/contents",
                {},
                Credential(AuthMode.ANONYMOUS_FREE, "Bearer server"),
            )


@pytest.mark.asyncio
async def test_non_json_upstream_error_does_not_echo_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="secret upstream dump")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.querit.ai") as http:
        with pytest.raises(UpstreamError) as exc_info:
            await QueritAPIClient(http).post(
                "/v1/search",
                {},
                Credential(AuthMode.BYOK, "Bearer user"),
            )

    assert "secret upstream dump" not in str(exc_info.value)
    assert "temporarily unavailable" in str(exc_info.value)
