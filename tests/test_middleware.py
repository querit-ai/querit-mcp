import httpx
import pytest
from starlette.responses import JSONResponse

from querit_mcp.middleware import ClientIPMiddleware


async def echo_client_ip(scope, receive, send) -> None:
    headers = {key.decode(): value.decode() for key, value in scope["headers"]}
    response = JSONResponse({"client_ip": headers.get("x-querit-client-ip")})
    await response(scope, receive, send)


@pytest.mark.asyncio
async def test_trusted_proxy_uses_forwarded_client_ip() -> None:
    app = ClientIPMiddleware(
        echo_client_ip,
        trusted_proxy_cidrs=["10.0.0.0/8"],
        forwarded_for_header="x-forwarded-for",
    )
    transport = httpx.ASGITransport(app=app, client=("10.0.0.2", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers={"x-forwarded-for": "203.0.113.9, 10.0.0.2"})

    assert response.json() == {"client_ip": "203.0.113.9"}


@pytest.mark.asyncio
async def test_untrusted_peer_cannot_spoof_forwarded_client_ip() -> None:
    app = ClientIPMiddleware(
        echo_client_ip,
        trusted_proxy_cidrs=["10.0.0.0/8"],
        forwarded_for_header="x-forwarded-for",
    )
    transport = httpx.ASGITransport(app=app, client=("198.51.100.8", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers={"x-forwarded-for": "203.0.113.9"})

    assert response.json() == {"client_ip": "198.51.100.8"}
