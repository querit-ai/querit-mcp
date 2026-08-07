"""Public response mapping and explicit output truncation."""

from __future__ import annotations

from typing import Any

from querit_mcp.errors import UpstreamError
from querit_mcp.models import (
    ContentsMetadata,
    ContentsOutput,
    ContentsResult,
    SearchOutput,
    SearchResult,
)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return None


def _required_id(value: Any) -> str:
    normalized = _optional_string(value)
    if normalized is None or not normalized:
        raise UpstreamError("Querit API returned an incompatible contents result without an id.")
    return normalized


def _sentence_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str)]


def _truncate_sentences(sentences: list[str] | None, limit: int) -> tuple[list[str] | None, bool]:
    if sentences is None:
        return None, False
    remaining = limit
    output: list[str] = []
    truncated = False
    for sentence in sentences:
        if remaining <= 0:
            truncated = True
            break
        if len(sentence) <= remaining:
            output.append(sentence)
            remaining -= len(sentence)
            continue
        output.append(sentence[:remaining])
        remaining = 0
        truncated = True
    return output, truncated


def _truncate_text(value: str | None, limit: int) -> tuple[str | None, bool]:
    if value is None or len(value) <= limit:
        return value, False
    return value[:limit], True


def _serialized_size(output: SearchOutput | ContentsOutput) -> int:
    return len(output.model_dump_json(exclude_none=True))


def _fit_search_response(output: SearchOutput, max_chars: int) -> SearchOutput:
    output.response_truncated = any(result.sentences_truncated for result in output.results)
    while output.results and _serialized_size(output) > max_chars:
        output.results.pop()
        output.omitted_result_count += 1
        output.response_truncated = True
    return output


def _fit_contents_response(output: ContentsOutput, max_chars: int) -> ContentsOutput:
    output.response_truncated = any(result.content_truncated for result in output.results)
    while output.results and _serialized_size(output) > max_chars:
        output.results.pop()
        output.omitted_result_count += 1
        output.response_truncated = True
    return output


def map_search_response(
    body: dict[str, Any],
    *,
    request_query: str,
    max_item_content_chars: int,
    max_response_chars: int,
) -> SearchOutput:
    query_context = body.get("query_context")
    query = request_query
    if isinstance(query_context, dict):
        query = _optional_string(query_context.get("query")) or request_query

    raw_results = body.get("results")
    if isinstance(raw_results, dict):
        raw_results = raw_results.get("result", [])
    if raw_results is None:
        raw_results = []
    if not isinstance(raw_results, list):
        raise UpstreamError("Querit API returned an incompatible search results field.")

    results: list[SearchResult] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        sentences, truncated = _truncate_sentences(
            _sentence_list(raw.get("sentence")),
            max_item_content_chars,
        )
        results.append(
            SearchResult(
                url=_optional_string(raw.get("url")),
                page_age=_optional_string(raw.get("page_age")),
                title=_optional_string(raw.get("title")),
                snippet=_optional_string(raw.get("snippet")),
                site_name=_optional_string(raw.get("site_name")),
                site_icon=_optional_string(raw.get("site_icon")),
                sentences=sentences,
                sentences_truncated=truncated,
            )
        )

    output = SearchOutput(
        query=query,
        search_id=_optional_string(body.get("search_id")),
        took=_optional_string(body.get("took")),
        results=results,
    )
    return _fit_search_response(output, max_response_chars)


def map_contents_response(
    body: dict[str, Any],
    *,
    max_content_chars: int,
    max_response_chars: int,
) -> ContentsOutput:
    raw_statuses = body.get("statuses", [])
    statuses: dict[str, str | None] = {}
    if isinstance(raw_statuses, list):
        for raw_status in raw_statuses:
            if not isinstance(raw_status, dict):
                continue
            status_id = _optional_string(raw_status.get("id"))
            if status_id and status_id not in statuses:
                statuses[status_id] = _optional_string(raw_status.get("status"))

    raw_results = body.get("results", [])
    if not isinstance(raw_results, list):
        raise UpstreamError("Querit API returned an incompatible contents results field.")

    results: list[ContentsResult] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        result_id = _required_id(raw.get("id"))
        raw_metadata = raw.get("extrasMeta")
        metadata = None
        if isinstance(raw_metadata, dict):
            metadata = ContentsMetadata(
                title=_optional_string(raw_metadata.get("title")),
                url=_optional_string(raw_metadata.get("url")),
                publish_time=_optional_string(raw_metadata.get("publishTime")),
                site_name=_optional_string(raw_metadata.get("siteName")),
                site_icon=_optional_string(raw_metadata.get("siteIcon")),
            )
        content, content_truncated = _truncate_text(_optional_string(raw.get("content")), max_content_chars)
        results.append(
            ContentsResult(
                id=result_id,
                status=statuses.get(result_id),
                url=(metadata.url if metadata else None) or _optional_string(raw.get("url")),
                content=content,
                metadata=metadata,
                content_truncated=content_truncated,
            )
        )

    search_time_value = body.get("searchTime")
    try:
        search_time = float(search_time_value) if search_time_value is not None else None
    except (TypeError, ValueError):
        search_time = None

    output = ContentsOutput(
        search_id=_optional_string(body.get("search_id")),
        search_time=search_time,
        results=results,
    )
    return _fit_contents_response(output, max_response_chars)
