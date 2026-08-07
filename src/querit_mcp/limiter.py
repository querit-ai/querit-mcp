"""Anonymous free-tier rate limiting."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol

from redis.exceptions import RedisError

from querit_mcp.logging_config import log_event

logger = logging.getLogger(__name__)

_QPS_WINDOW_MILLISECONDS = 1_000
_FAILURE_LOG_INTERVAL_SECONDS = 60.0

_CONSUME_SCRIPT = """
local qps_limit = tonumber(ARGV[1])
local daily_limit = tonumber(ARGV[2])
local daily_cost = tonumber(ARGV[3])
local daily_ttl = tonumber(ARGV[4])
local qps_member = ARGV[5]
local qps_window_ms = tonumber(ARGV[6])

local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local window_start_ms = now_ms - qps_window_ms

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', window_start_ms)
local qps_current = tonumber(redis.call('ZCARD', KEYS[1]) or '0')
local daily_current = tonumber(redis.call('GET', KEYS[2]) or '0')

if qps_current + 1 > qps_limit or daily_current + daily_cost > daily_limit then
  return {0, qps_current, daily_current}
end

redis.call('ZADD', KEYS[1], now_ms, qps_member)
redis.call('PEXPIRE', KEYS[1], qps_window_ms + 1000)
local daily_after = redis.call('INCRBY', KEYS[2], daily_cost)
if daily_after == daily_cost then redis.call('EXPIRE', KEYS[2], daily_ttl) end
return {1, qps_current + 1, daily_after}
"""


@dataclass(frozen=True, slots=True)
class LimitDecision:
    allowed: bool


class FreeTierLimiter(Protocol):
    async def consume(self, identity: str, credits: int) -> LimitDecision: ...


class RedisClient(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> Awaitable[Any]: ...


class RedisFreeTierLimiter:
    def __init__(
        self,
        redis: RedisClient,
        *,
        qps_limit: int,
        daily_credit_limit: int,
        prefix: str = "querit-mcp:free",
    ) -> None:
        self.redis = redis
        self.qps_limit = qps_limit
        self.daily_credit_limit = daily_credit_limit
        self.prefix = prefix
        self._redis_failure_started_at: float | None = None
        self._last_failure_log_at: float | None = None
        self._suppressed_failures = 0

    async def consume(self, identity: str, credits: int) -> LimitDecision:
        now = int(time.time())
        day_bucket = now // 86_400
        seconds_until_day_reset = 86_400 - (now % 86_400) + 60
        qps_key = f"{self.prefix}:{{{identity}}}:qps"
        daily_key = f"{self.prefix}:{{{identity}}}:daily:{day_bucket}"
        try:
            result = await self.redis.eval(
                _CONSUME_SCRIPT,
                2,
                qps_key,
                daily_key,
                self.qps_limit,
                self.daily_credit_limit,
                credits,
                seconds_until_day_reset,
                uuid.uuid4().hex,
                _QPS_WINDOW_MILLISECONDS,
            )
        except RedisError as exc:
            self._record_redis_failure(exc)
            return LimitDecision(allowed=True)
        self._record_redis_recovery()
        return LimitDecision(allowed=bool(result and int(result[0]) == 1))

    def _record_redis_failure(self, exc: RedisError) -> None:
        now = time.monotonic()
        if self._redis_failure_started_at is None:
            self._redis_failure_started_at = now

        should_log = (
            self._last_failure_log_at is None or now - self._last_failure_log_at >= _FAILURE_LOG_INTERVAL_SECONDS
        )
        if not should_log:
            self._suppressed_failures += 1
            return

        log_event(
            logger,
            logging.ERROR,
            "redis_rate_limit_fail_open",
            "Redis rate-limit check failed; allowing anonymous request",
            error_type=type(exc).__name__,
            suppressed_failures=self._suppressed_failures,
        )
        self._last_failure_log_at = now
        self._suppressed_failures = 0

    def _record_redis_recovery(self) -> None:
        if self._redis_failure_started_at is None:
            return

        outage_duration_ms = round((time.monotonic() - self._redis_failure_started_at) * 1_000, 2)
        log_event(
            logger,
            logging.INFO,
            "redis_rate_limit_recovered",
            "Redis rate-limit check recovered",
            outage_duration_ms=outage_duration_ms,
            suppressed_failures=self._suppressed_failures,
        )
        self._redis_failure_started_at = None
        self._last_failure_log_at = None
        self._suppressed_failures = 0
