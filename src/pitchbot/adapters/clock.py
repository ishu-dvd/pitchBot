from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    def __init__(self, current: datetime) -> None:
        if current.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware datetime")
        self._current = current.astimezone(UTC)

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> datetime:
        if delta.total_seconds() < 0:
            raise ValueError("FakeClock cannot move backwards")
        self._current += delta
        return self._current

    def set(self, current: datetime) -> None:
        if current.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware datetime")
        normalized = current.astimezone(UTC)
        if normalized < self._current:
            raise ValueError("FakeClock cannot move backwards")
        self._current = normalized
