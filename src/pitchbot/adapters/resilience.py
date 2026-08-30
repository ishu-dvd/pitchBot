from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from pitchbot.adapters.clock import Clock
from pitchbot.adapters.errors import (
    AdapterTimeoutError,
    CircuitOpenError,
    TransientAdapterError,
)

type Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    attempt_timeout_seconds: float = 10.0
    initial_delay_seconds: float = 0.1
    maximum_delay_seconds: float = 2.0
    backoff_multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.attempt_timeout_seconds <= 0:
            raise ValueError("attempt_timeout_seconds must be positive")
        if self.initial_delay_seconds < 0 or self.maximum_delay_seconds < 0:
            raise ValueError("retry delays must not be negative")
        if self.initial_delay_seconds > self.maximum_delay_seconds:
            raise ValueError("initial_delay_seconds must not exceed maximum_delay_seconds")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be at least 1")


async def execute_with_retry[T](
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    sleeper: Sleeper = asyncio.sleep,
) -> T:
    delay = policy.initial_delay_seconds
    last_error: TransientAdapterError | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await asyncio.wait_for(
                operation(),
                timeout=policy.attempt_timeout_seconds,
            )
        except TimeoutError as error:
            last_error = AdapterTimeoutError(
                f"Adapter attempt {attempt} timed out after "
                f"{policy.attempt_timeout_seconds} seconds"
            )
            last_error.__cause__ = error
        except TransientAdapterError as error:
            last_error = error

        if attempt < policy.max_attempts:
            await sleeper(delay)
            delay = min(delay * policy.backoff_multiplier, policy.maximum_delay_seconds)

    if last_error is None:
        raise RuntimeError("Retry loop completed without a result or error")
    raise last_error


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class CircuitBreaker:
    def __init__(
        self,
        clock: Clock,
        *,
        failure_threshold: int = 3,
        recovery_timeout: timedelta = timedelta(seconds=30),
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if recovery_timeout.total_seconds() <= 0:
            raise ValueError("recovery_timeout must be positive")
        self._clock = clock
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._opened_at: datetime | None = None
        self._state = CircuitState.CLOSED
        self._half_open_probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        if (
            self._state is CircuitState.OPEN
            and self._opened_at is not None
            and self._clock.now() - self._opened_at >= self._recovery_timeout
        ):
            return CircuitState.HALF_OPEN
        return self._state

    def before_call(self) -> None:
        state = self.state
        if state is CircuitState.OPEN:
            raise CircuitOpenError("Adapter circuit is open")
        if state is CircuitState.HALF_OPEN:
            if self._half_open_probe_in_flight:
                raise CircuitOpenError("Adapter circuit half-open probe is already running")
            self._state = CircuitState.HALF_OPEN
            self._half_open_probe_in_flight = True

    def record_success(self) -> None:
        self._failure_count = 0
        self._opened_at = None
        self._state = CircuitState.CLOSED
        self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        self._failure_count += 1
        self._half_open_probe_in_flight = False
        if self._state is CircuitState.HALF_OPEN or self._failure_count >= self._failure_threshold:
            self._opened_at = self._clock.now()
            self._state = CircuitState.OPEN

    def record_aborted_probe(self) -> None:
        if self._half_open_probe_in_flight:
            self._half_open_probe_in_flight = False
            self._opened_at = self._clock.now()
            self._state = CircuitState.OPEN


async def execute_with_circuit_breaker[T](
    operation: Callable[[], Awaitable[T]],
    circuit_breaker: CircuitBreaker,
) -> T:
    circuit_breaker.before_call()
    try:
        result = await operation()
    except TransientAdapterError:
        circuit_breaker.record_failure()
        raise
    except BaseException:
        circuit_breaker.record_aborted_probe()
        raise
    else:
        circuit_breaker.record_success()
        return result
