from __future__ import annotations


class ScriptedClock:
    """A monotonic nanosecond clock that replays ticks and then holds the last one."""

    def __init__(self, *ticks: int) -> None:
        if not ticks:
            raise ValueError("scripted clock requires at least one tick")
        if list(ticks) != sorted(ticks):
            raise ValueError("scripted clock ticks must not move backwards")
        self._ticks = list(ticks)

    def __call__(self) -> int:
        if len(self._ticks) > 1:
            return self._ticks.pop(0)
        return self._ticks[0]


class SteppingClock:
    """A monotonic nanosecond clock that advances a fixed step on every read."""

    def __init__(self, step_ns: int, *, start_ns: int = 0) -> None:
        if step_ns < 0:
            raise ValueError("stepping clock step must not be negative")
        self._now = start_ns
        self._step = step_ns

    def __call__(self) -> int:
        now = self._now
        self._now += self._step
        return now
