from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from pitchbot.adapters import (
    AdapterTimeoutError,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    FakeClock,
    RetryPolicy,
    execute_with_circuit_breaker,
    execute_with_retry,
)
from pitchbot.adapters.errors import PermanentAdapterError, TransientAdapterError


def test_retry_rejects_initial_delay_above_maximum() -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        RetryPolicy(initial_delay_seconds=2, maximum_delay_seconds=1)


@pytest.mark.asyncio
async def test_retry_uses_bounded_backoff_then_succeeds() -> None:
    attempts = 0
    delays: list[float] = []

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TransientAdapterError("retry")
        return "ok"

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    result = await execute_with_retry(
        operation,
        RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=0.1,
            maximum_delay_seconds=0.15,
            backoff_multiplier=2,
        ),
        sleeper=sleeper,
    )

    assert result == "ok"
    assert attempts == 3
    assert delays == [0.1, 0.15]


@pytest.mark.asyncio
async def test_retry_does_not_retry_permanent_failure() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise PermanentAdapterError("invalid request")

    with pytest.raises(PermanentAdapterError, match="invalid request"):
        await execute_with_retry(operation, RetryPolicy(max_attempts=3))

    assert attempts == 1


@pytest.mark.asyncio
async def test_retry_converts_attempt_timeout() -> None:
    async def operation() -> str:
        await asyncio.sleep(0.05)
        return "late"

    with pytest.raises(AdapterTimeoutError, match="timed out"):
        await execute_with_retry(
            operation,
            RetryPolicy(max_attempts=1, attempt_timeout_seconds=0.001),
        )


@pytest.mark.asyncio
async def test_circuit_breaker_opens_and_recovers_with_fake_clock() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    breaker = CircuitBreaker(
        clock,
        failure_threshold=2,
        recovery_timeout=timedelta(seconds=30),
    )

    async def failure() -> str:
        raise TransientAdapterError("down")

    for _ in range(2):
        with pytest.raises(TransientAdapterError):
            await execute_with_circuit_breaker(failure, breaker)

    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()

    clock.advance(timedelta(seconds=30))
    assert breaker.state.value == CircuitState.HALF_OPEN.value
    breaker.before_call()
    with pytest.raises(CircuitOpenError, match="probe is already running"):
        breaker.before_call()
    breaker.record_success()

    async def success() -> str:
        return "ok"

    assert await execute_with_circuit_breaker(success, breaker) == "ok"
    assert breaker.state.value == CircuitState.CLOSED.value


@pytest.mark.asyncio
async def test_cancelled_half_open_probe_releases_slot_and_reopens() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    breaker = CircuitBreaker(
        clock,
        failure_threshold=1,
        recovery_timeout=timedelta(seconds=10),
    )

    async def transient_failure() -> str:
        raise TransientAdapterError("down")

    with pytest.raises(TransientAdapterError):
        await execute_with_circuit_breaker(transient_failure, breaker)

    clock.advance(timedelta(seconds=10))

    async def cancelled_probe() -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await execute_with_circuit_breaker(cancelled_probe, breaker)

    assert breaker.state.value == CircuitState.OPEN.value
    clock.advance(timedelta(seconds=10))
    assert breaker.state.value == CircuitState.HALF_OPEN.value


def test_fake_clock_rejects_naive_and_backward_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FakeClock(datetime(2026, 1, 1))

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(timedelta(seconds=-1))
    with pytest.raises(ValueError, match="backwards"):
        clock.set(datetime(2025, 1, 1, tzinfo=UTC))
