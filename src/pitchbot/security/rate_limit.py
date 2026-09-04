"""A token bucket, sized for a server whose unit of work costs seconds of CPU.

Rate limiting on this API is not about protecting a database from chatty clients. One
spoken turn was measured at 4,507 ms end to end, almost all of it CPU-bound inference that
holds cores away from every other session, and the process is effectively single-digit
concurrent. A caller issuing turns in a loop does not degrade the service, it stops it.

Deliberately dependency-free. ``slowapi`` would bring in a middleware stack and a storage
abstraction to express roughly forty lines, and this repository already prefers a small
verified implementation over a dependency for exactly this kind of thing.

The limiter is per-process. Two workers behind a load balancer each enforce their own
budget, so the effective limit is the configured one times the worker count - stated here
because it is the sort of thing that is otherwise discovered during an incident.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Whether a request may proceed, and when to try again if not."""

    allowed: bool
    retry_after_seconds: float

    @property
    def retry_after_header(self) -> str:
        """``Retry-After`` is defined in whole seconds, and must never round down to 0.

        A ``Retry-After: 0`` invites an immediate retry, which is precisely the behaviour
        a limiter exists to stop.
        """

        return str(max(1, int(self.retry_after_seconds + 0.999)))


class _Bucket:
    __slots__ = ("tokens", "updated_at")

    def __init__(self, tokens: float, updated_at: float) -> None:
        self.tokens = tokens
        self.updated_at = updated_at


class RateLimiter:
    """Token bucket keyed by credential name.

    Keyed by *credential*, never by client address. An address is attacker-chosen and
    unbounded, so keying on it would let one caller allocate unlimited buckets and turn the
    limiter itself into the memory exhaustion it was added to prevent. Credentials are
    configured, therefore bounded, therefore safe to key on.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be positive")
        self._capacity = float(capacity)
        self._refill_per_second = refill_per_second
        self._time_source = time_source
        self._buckets: dict[str, _Bucket] = {}

    @property
    def capacity(self) -> int:
        return int(self._capacity)

    def check(self, key: str) -> RateLimitDecision:
        """Spend one token for `key`, or report how long until one exists."""

        now = self._time_source()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self._capacity, updated_at=now)
            self._buckets[key] = bucket
        else:
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_per_second)
            bucket.updated_at = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return RateLimitDecision(allowed=True, retry_after_seconds=0.0)
        deficit = 1.0 - bucket.tokens
        return RateLimitDecision(
            allowed=False,
            retry_after_seconds=deficit / self._refill_per_second,
        )

    def forget(self, key: str) -> None:
        """Drop a bucket. Only used by tests and by credential reconfiguration."""

        self._buckets.pop(key, None)


__all__ = ["RateLimitDecision", "RateLimiter"]
