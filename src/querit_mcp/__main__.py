"""CLI entry point."""

import uvicorn

from querit_mcp.config import get_settings
from querit_mcp.logging_config import build_uvicorn_log_config


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "querit_mcp.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        log_config=build_uvicorn_log_config(settings.log_level),
    )


if __name__ == "__main__":
    main()
