import json
import logging

from querit_mcp.logging_config import (
    HealthCheckAccessFilter,
    JsonFormatter,
    UvicornAccessJsonFormatter,
    build_uvicorn_log_config,
)


def access_record(path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("203.0.113.10:1234", "GET", path, "1.1", status),
        None,
    )


def test_json_formatter_serializes_exception_as_one_redacted_line() -> None:
    try:
        raise RuntimeError("Bearer secret-token at redis://user:password@redis.internal:6379/0")
    except RuntimeError as exc:
        record = logging.LogRecord(
            "querit_mcp.test",
            logging.ERROR,
            __file__,
            1,
            "request failed: %s",
            (exc,),
            (type(exc), exc, exc.__traceback__),
        )

    rendered = JsonFormatter().format(record)
    payload = json.loads(rendered)

    assert "\n" not in rendered
    assert payload["severity"] == "ERROR"
    assert payload["exception_type"] == "RuntimeError"
    assert "secret-token" not in rendered
    assert "user:password" not in rendered
    assert "Bearer [REDACTED]" in rendered
    assert "redis://[REDACTED]" in rendered
    assert "stack_trace" in payload


def test_access_formatter_uses_google_http_request_shape_without_query_or_ip() -> None:
    rendered = UvicornAccessJsonFormatter().format(access_record("/mcp?token=secret", 503))
    payload = json.loads(rendered)

    assert "\n" not in rendered
    assert payload["severity"] == "ERROR"
    assert payload["event"] == "http_request_completed"
    assert payload["httpRequest"] == {
        "requestMethod": "GET",
        "requestUrl": "/mcp",
        "status": 503,
        "protocol": "1.1",
    }
    assert "secret" not in rendered
    assert "203.0.113.10" not in rendered


def test_access_formatter_maps_client_errors_to_warning() -> None:
    payload = json.loads(UvicornAccessJsonFormatter().format(access_record("/mcp", 429)))

    assert payload["severity"] == "WARNING"


def test_health_check_filter_drops_probe_access_logs() -> None:
    access_filter = HealthCheckAccessFilter()

    assert access_filter.filter(access_record("/healthz", 200)) is False
    assert access_filter.filter(access_record("/readyz?full=true", 200)) is False
    assert access_filter.filter(access_record("/mcp", 200)) is True


def test_uvicorn_config_writes_json_to_stdout_and_quiets_dependencies() -> None:
    config = build_uvicorn_log_config("INFO")

    assert config["handlers"]["default"]["stream"] == "ext://sys.stdout"
    assert config["handlers"]["access"]["stream"] == "ext://sys.stdout"
    assert config["root"] == {"handlers": ["default"], "level": "INFO"}
    assert config["loggers"]["httpx"]["level"] == "WARNING"
    assert config["loggers"]["httpcore"]["level"] == "WARNING"
    assert config["loggers"]["mcp"]["level"] == "WARNING"
