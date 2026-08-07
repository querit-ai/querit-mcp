"""Structured MCP output models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchResult(OutputModel):
    url: str | None = None
    page_age: str | None = None
    title: str | None = None
    snippet: str | None = None
    site_name: str | None = None
    site_icon: str | None = None
    sentences: list[str] | None = None
    sentences_truncated: bool = False


class SearchOutput(OutputModel):
    query: str
    search_id: str | None = None
    took: str | None = None
    results: list[SearchResult] = Field(default_factory=list)
    omitted_result_count: int = 0
    response_truncated: bool = False


class ContentsMetadata(OutputModel):
    title: str | None = None
    url: str | None = None
    publish_time: str | None = None
    site_name: str | None = None
    site_icon: str | None = None


class ContentsResult(OutputModel):
    id: str
    status: str | None = None
    url: str | None = None
    content: str | None = None
    metadata: ContentsMetadata | None = None
    content_truncated: bool = False


class ContentsOutput(OutputModel):
    search_id: str | None = None
    search_time: float | None = None
    results: list[ContentsResult] = Field(default_factory=list)
    omitted_result_count: int = 0
    response_truncated: bool = False
