import pytest
from redis.exceptions import ConnectionError

from querit_mcp.limiter import RedisFreeTierLimiter


class FakeRedis:
    def __init__(self, result: list[int] | None = None, error: Exception | None = None) -> None:
        self.result = result or [1, 1, 2]
        self.error = error
        self.args: tuple[object, ...] | None = None

    async def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> list[int]:
        self.args = (script, numkeys, *keys_and_args)
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_redis_limiter_passes_weighted_daily_credits() -> None:
    redis = FakeRedis()
    limiter = RedisFreeTierLimiter(redis, qps_limit=2, daily_credit_limit=50)

    decision = await limiter.consume("identity", 6)

    assert decision.allowed is True
    assert redis.args is not None
    assert 6 in redis.args
    assert all("identity" in str(arg) for arg in redis.args[2:4])
    assert redis.args[2] == "querit-mcp:free:{identity}:qps"
    assert "ZREMRANGEBYSCORE" in str(redis.args[0])
    assert "ZCARD" in str(redis.args[0])
    assert "TIME" in str(redis.args[0])


@pytest.mark.asyncio
async def test_redis_limiter_denies_without_exposing_dimension() -> None:
    limiter = RedisFreeTierLimiter(FakeRedis(result=[0, 2, 50]), qps_limit=2, daily_credit_limit=50)

    decision = await limiter.consume("identity", 1)

    assert decision.allowed is False


@pytest.mark.asyncio
async def test_redis_failure_opens_anonymous_path(caplog: pytest.LogCaptureFixture) -> None:
    redis = FakeRedis(error=ConnectionError("redis unavailable"))
    limiter = RedisFreeTierLimiter(
        redis,
        qps_limit=2,
        daily_credit_limit=50,
    )

    with caplog.at_level("INFO"):
        first_decision = await limiter.consume("identity", 1)
        second_decision = await limiter.consume("identity", 1)
        redis.error = None
        recovered_decision = await limiter.consume("identity", 1)

    assert first_decision.allowed is True
    assert second_decision.allowed is True
    assert recovered_decision.allowed is True
    assert "allowing anonymous request" in caplog.text
    events = [getattr(record, "json_fields", {}) for record in caplog.records]
    assert [event["event"] for event in events] == [
        "redis_rate_limit_fail_open",
        "redis_rate_limit_recovered",
    ]
    assert events[0]["error_type"] == "ConnectionError"
    assert events[1]["suppressed_failures"] == 1
    assert all(record.exc_info is None for record in caplog.records)
    assert "redis unavailable" not in caplog.text
