from pytest import MonkeyPatch

from querit_mcp.config import Settings


def test_secret_environment_variables(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_FREE_TIER_ENABLED", "true")
    monkeypatch.setenv("QUERIT_MCP_FREE_API_KEY", "api-key")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    monkeypatch.setenv("MCP_FREE_IP_HMAC_SECRET", "hmac-secret")

    settings = Settings()

    assert settings.querit_mcp_free_api_key is not None
    assert settings.querit_mcp_free_api_key.get_secret_value() == "api-key"
    assert settings.redis_url is not None
    assert settings.redis_url.get_secret_value() == "redis://example:6379/0"
    assert settings.mcp_free_ip_hmac_secret is not None
    assert settings.mcp_free_ip_hmac_secret.get_secret_value() == "hmac-secret"
