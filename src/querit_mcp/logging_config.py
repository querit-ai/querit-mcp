"""Single-line structured logging for container runtimes."""

from __future__ import annotations

import json
import logging
import re
import traceback
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

_JSON_FIELDS_ATTRIBUTE = "json_fields"
_PROTECTED_FIELDS = frozenset(
    {"severity", "message", "time", "logger", "exception_type", "exception_message", "stack_trace"}
)
_BEARER_PATTERN = re.compile(r"\bBearer[ \t]+[^\s\"']+", re.IGNORECASE)
_REDIS_URL_PATTERN = re.compile(r"\b(rediss?://)[^\s\"']+", re.IGNORECASE)
_MAX_MESSAGE_CHARS = 4_096
_MAX_EXCEPTION_MESSAGE_CHARS = 2_048
_MAX_STACK_TRACE_CHARS = 16_384
_HEALTH_PATHS = frozenset({"/healthz", "/readyz"})


def _sanitize_text(value: object, *, limit: int) -> str:
    text = str(value)
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _REDIS_URL_PATTERN.sub(r"\1[REDACTED]", text)
    return text[:limit]


def _json_default(value: object) -> str:
    return _sanitize_text(value, limit=_MAX_MESSAGE_CHARS)


def _timestamp(record: logging.LogRecord) -> str:
    timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds")
    return timestamp.replace("+00:00", "Z")


def safe_request_id(value: object | None) -> str | None:
    """Return a bounded request identifier suitable for log correlation."""

    if value is None:
        return None
    return _sanitize_text(value, limit=128)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    **fields: object,
) -> None:
    """Emit an event whose metadata is understood by :class:`JsonFormatter`."""

    json_fields: dict[str, object] = {"event": event}
    json_fields.update({key: value for key, value in fields.items() if value is not None})
    logger.log(level, message, extra={_JSON_FIELDS_ATTRIBUTE: json_fields})


class JsonFormatter(logging.Formatter):
    """Serialize one ``LogRecord`` to one physical JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "severity": record.levelname,
            "message": _sanitize_text(record.getMessage(), limit=_MAX_MESSAGE_CHARS),
            "time": _timestamp(record),
            "logger": record.name,
        }
        fields = getattr(record, _JSON_FIELDS_ATTRIBUTE, None)
        if isinstance(fields, Mapping):
            payload.update(
                {
                    str(key): value
                    for key, value in fields.items()
                    if str(key) not in _PROTECTED_FIELDS and value is not None
                }
            )

        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc_value, exc_traceback = record.exc_info
            payload["exception_type"] = exc_type.__name__
            payload["exception_message"] = _sanitize_text(exc_value, limit=_MAX_EXCEPTION_MESSAGE_CHARS)
            if exc_traceback is not None:
                stack_trace = "".join(traceback.format_tb(exc_traceback))
                payload["stack_trace"] = _sanitize_text(stack_trace, limit=_MAX_STACK_TRACE_CHARS)
        if record.stack_info:
            payload["stack_trace"] = _sanitize_text(record.stack_info, limit=_MAX_STACK_TRACE_CHARS)

        return json.dumps(payload, ensure_ascii=True, default=_json_default, separators=(",", ":"))


def _access_values(record: logging.LogRecord) -> tuple[str, str, str, int] | None:
    args = record.args
    if not isinstance(args, tuple) or len(args) < 5:
        return None
    _, method, raw_path, http_version, status_code = args[:5]
    try:
        normalized_status = int(str(status_code))
    except (TypeError, ValueError):
        return None
    path = urlsplit(str(raw_path)).path or "/"
    return str(method), path, str(http_version), normalized_status


class UvicornAccessJsonFormatter(JsonFormatter):
    """Map Uvicorn access records to Google Cloud's ``httpRequest`` shape."""

    def format(self, record: logging.LogRecord) -> str:
        values = _access_values(record)
        if values is None:
            return super().format(record)
        method, path, http_version, status_code = values
        severity = "ERROR" if status_code >= 500 else "WARNING" if status_code >= 400 else "INFO"
        payload = {
            "severity": severity,
            "message": f"HTTP {method} {path} {status_code}",
            "time": _timestamp(record),
            "logger": record.name,
            "event": "http_request_completed",
            "httpRequest": {
                "requestMethod": method,
                "requestUrl": path,
                "status": status_code,
                "protocol": http_version,
            },
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


class HealthCheckAccessFilter(logging.Filter):
    """Drop high-volume liveness and readiness access records."""

    def filter(self, record: logging.LogRecord) -> bool:
        values = _access_values(record)
        return values is None or values[1] not in _HEALTH_PATHS


def build_uvicorn_log_config(log_level: str) -> dict[str, Any]:
    """Build a Uvicorn config that keeps application logs structured on stdout."""

    level = log_level.upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {"()": "querit_mcp.logging_config.JsonFormatter"},
            "access_json": {"()": "querit_mcp.logging_config.UvicornAccessJsonFormatter"},
        },
        "filters": {
            "exclude_health_checks": {"()": "querit_mcp.logging_config.HealthCheckAccessFilter"},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "access_json",
                "filters": ["exclude_health_checks"],
                "stream": "ext://sys.stdout",
            },
        },
        "root": {"handlers": ["default"], "level": level},
        "loggers": {
            "querit_mcp": {"handlers": [], "level": level, "propagate": True},
            "uvicorn": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": level, "propagate": False},
            "httpx": {"handlers": [], "level": "WARNING", "propagate": True},
            "httpcore": {"handlers": [], "level": "WARNING", "propagate": True},
            "mcp": {"handlers": [], "level": "WARNING", "propagate": True},
        },
    }
