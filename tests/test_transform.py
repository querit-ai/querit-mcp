from querit_mcp.transform import map_contents_response, map_search_response


def test_contents_marks_per_item_truncation() -> None:
    result = map_contents_response(
        {
            "results": [{"id": "1", "content": "abcdefgh", "extrasMeta": {"url": "https://example.com"}}],
            "statuses": [{"id": "1", "status": "success"}],
        },
        max_content_chars=4,
        max_response_chars=2_000,
    )

    assert result.results[0].content == "abcd"
    assert result.results[0].content_truncated is True
    assert result.response_truncated is True
    assert result.omitted_result_count == 0


def test_search_omits_complete_results_to_fit_total_budget() -> None:
    result = map_search_response(
        {
            "results": {
                "result": [
                    {"title": "a" * 800, "url": "https://a.example"},
                    {"title": "b" * 800, "url": "https://b.example"},
                ]
            }
        },
        request_query="query",
        max_item_content_chars=8_000,
        max_response_chars=1_024,
    )

    assert len(result.results) == 1
    assert result.omitted_result_count == 1
    assert result.response_truncated is True
