from pydantic import SecretStr, ValidationError

from querit_mcp.auth import AuthMode, anonymous_identity, select_credential
from querit_mcp.config import Settings
from querit_mcp.errors import AuthenticationError


def free_settings() -> Settings:
    return Settings(
        mcp_free_tier_enabled=True,
        querit_mcp_free_api_key=SecretStr("server-secret"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        mcp_free_ip_hmac_secret=SecretStr("identity-secret"),
    )


def test_anonymous_uses_server_side_credential() -> None:
    credential = select_credential({}, free_settings())

    assert credential.mode is AuthMode.ANONYMOUS_FREE
    assert credential.authorization == "Bearer server-secret"


def test_byok_overrides_server_side_credential() -> None:
    credential = select_credential({"Authorization": "Bearer user-secret"}, free_settings())

    assert credential.mode is AuthMode.BYOK
    assert credential.authorization == "Bearer user-secret"
    assert "user-secret" not in repr(credential)


def test_malformed_byok_never_falls_back_to_anonymous() -> None:
    try:
        select_credential({"authorization": "Basic invalid"}, free_settings())
    except AuthenticationError as exc:
        assert "Bearer" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected AuthenticationError")


def test_anonymous_identity_is_hmac_not_raw_ip() -> None:
    identity = anonymous_identity({"x-querit-client-ip": "2001:db8::1"}, free_settings())

    assert len(identity) == 64
    assert "2001:db8" not in identity
    assert identity == anonymous_identity({"x-querit-client-ip": "2001:0db8:0:0::1"}, free_settings())


def test_free_tier_requires_all_server_secrets() -> None:
    try:
        Settings(mcp_free_tier_enabled=True)
    except ValidationError as exc:
        message = str(exc)
        assert "QUERIT_MCP_FREE_API_KEY" in message
        assert "REDIS_URL" in message
        assert "MCP_FREE_IP_HMAC_SECRET" in message
    else:  # pragma: no cover
        raise AssertionError("expected ValidationError")
