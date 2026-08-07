"""Querit MCP server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("querit-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
