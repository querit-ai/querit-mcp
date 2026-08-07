from types import SimpleNamespace
from typing import cast

import pytest
from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import Context
from mcp.server.session import ServerSession
from mcp.types import InputRequiredResult
from starlette.testclient import TestClient

from querit_mcp.app import AppState, create_app, create_mcp_server
from querit_mcp.config import Settings
from querit_mcp.models import SearchOutput, SearchResult
from querit_mcp.service import QueritService


class SuccessfulService:
    async def search(self, **kwargs) -> SearchOutput:
        return SearchOutput(query=kwargs["query"], results=[SearchResult(title="Result")])


@pytest.mark.asyncio
async def test_mcp_lists_structured_search_and_contents_tools() -> None:
    server = create_mcp_server(Settings())

    tools = {tool.name: tool for tool in await server.list_tools()}

    assert set(tools) == {"querit_search", "querit_contents"}
    assert tools["querit_search"].output_schema is not None
    assert "results" in tools["querit_search"].output_schema["properties"]
    assert "requires a user-provided Querit API Key" in (tools["querit_search"].description or "")
    include_content = tools["querit_search"].input_schema["properties"]["include_content"]
    assert "requires a user-provided Querit API Key" in include_content["description"]
    assert tools["querit_contents"].output_schema is not None
    assert "max_content_chars" in tools["querit_contents"].input_schema["properties"]


@pytest.mark.asyncio
async def test_mcp_converts_pydantic_result_to_structured_content() -> None:
    server = create_mcp_server(Settings())
    request_context = ServerRequestContext(
        session=cast(ServerSession, object()),
        lifespan_context=AppState(service=cast(QueritService, SuccessfulService())),
        protocol_version="2026-07-28",
        method="tools/call",
        request_id="request-1",
        request=SimpleNamespace(headers={"authorization": "Bearer user-key"}),
    )
    context = Context(request_context=request_context, mcp_server=server)

    result = await server.call_tool("querit_search", {"query": "hello"}, context)

    assert not isinstance(result, InputRequiredResult)
    assert result.structured_content is not None
    assert result.structured_content["query"] == "hello"
    assert result.structured_content["results"][0]["title"] == "Result"


def test_health_and_readiness_routes() -> None:
    settings = Settings(mcp_enable_dns_rebinding_protection=False)

    with TestClient(create_app(settings)) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "anonymous_free": False}
