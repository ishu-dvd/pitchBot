from __future__ import annotations

import pytest

from pitchbot.security.rate_limit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="capacity must be at least 1"):
        RateLimiter(capacity=0, refill_per_second=1.0)


def test_refill_must_be_positive() -> None:
    with pytest.raises(ValueError, match="refill_per_second must be positive"):
        RateLimiter(capacity=1, refill_per_second=0.0)


def test_burst_is_admitted_then_refused() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=3, refill_per_second=1.0, time_source=clock)
    assert [limiter.check("web").allowed for _ in range(3)] == [True, True, True]
    assert limiter.check("web").allowed is False


def test_tokens_refill_over_time() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=2, refill_per_second=2.0, time_source=clock)
    limiter.check("web")
    limiter.check("web")
    assert limiter.check("web").allowed is False
    clock.advance(0.5)  # 2 tokens/second for half a second == one token
    assert limiter.check("web").allowed is True


def test_bucket_does_not_refill_beyond_capacity() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=2, refill_per_second=1.0, time_source=clock)
    limiter.check("web")
    clock.advance(3_600)
    assert [limiter.check("web").allowed for _ in range(3)] == [True, True, False]


def test_credentials_have_independent_budgets() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=1, refill_per_second=1.0, time_source=clock)
    assert limiter.check("web").allowed is True
    assert limiter.check("web").allowed is False
    assert limiter.check("ops").allowed is True


def test_retry_after_never_invites_an_immediate_retry() -> None:
    """A Retry-After of 0 tells the caller to do exactly what was just refused."""

    clock = FakeClock()
    limiter = RateLimiter(capacity=1, refill_per_second=100.0, time_source=clock)
    limiter.check("web")
    decision = limiter.check("web")
    assert decision.allowed is False
    assert decision.retry_after_seconds < 1.0
    assert int(decision.retry_after_header) >= 1


def test_retry_after_rounds_up_to_whole_seconds() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=1, refill_per_second=0.5, time_source=clock)
    limiter.check("web")
    decision = limiter.check("web")
    assert decision.retry_after_seconds == pytest.approx(2.0)
    assert decision.retry_after_header == "2"


def test_a_clock_that_goes_backwards_does_not_grant_tokens() -> None:
    """Monotonic clocks are assumed, but a negative delta must never mint credit."""

    clock = FakeClock()
    limiter = RateLimiter(capacity=1, refill_per_second=1.0, time_source=clock)
    assert limiter.check("web").allowed is True
    clock.now = -100.0
    assert limiter.check("web").allowed is False
