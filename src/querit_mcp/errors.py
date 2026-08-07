"""Public, credential-safe errors returned by MCP tools."""


class QueritMCPError(RuntimeError):
    """Base class for expected tool execution failures."""


class ConfigurationError(QueritMCPError):
    """The server is missing required deployment configuration."""


class AuthenticationError(QueritMCPError):
    """The caller supplied an invalid authentication header or API key."""


class FreeTierLimitError(QueritMCPError):
    """The anonymous free allowance has been exhausted."""


class FreeTierUnavailableError(QueritMCPError):
    """The anonymous path is unavailable without exposing an internal cause."""


class UpstreamError(QueritMCPError):
    """The Querit public API returned an error or incompatible response."""
