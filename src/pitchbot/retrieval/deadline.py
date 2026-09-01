from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from time import monotonic_ns
from typing import TypeVar

DEADLINE_CHECK_INTERVAL = 32

_Item = TypeVar("_Item")


class RetrievalDeadlineExceededError(RuntimeError):
    """A cooperative retrieval step observed an exhausted wall-clock budget."""


@dataclass(frozen=True, slots=True)
class RetrievalDeadline:
    """A single wall-clock budget shared by every step of one retrieval."""

    started_ns: int
    limit_ns: int
    clock: Callable[[], int]

    @classmethod
    def start(
        cls,
        deadline_ms: int,
        *,
        clock: Callable[[], int] = monotonic_ns,
    ) -> RetrievalDeadline:
        if deadline_ms < 1:
            raise ValueError("retrieval deadline must be at least one millisecond")
        return cls(started_ns=clock(), limit_ns=deadline_ms * 1_000_000, clock=clock)

    def elapsed_ns(self) -> int:
        return max(0, self.clock() - self.started_ns)

    def elapsed_ms(self) -> float:
        return self.elapsed_ns() / 1_000_000

    def expired(self) -> bool:
        return self.elapsed_ns() >= self.limit_ns

    def check(self) -> None:
        if self.expired():
            raise RetrievalDeadlineExceededError("retrieval deadline exceeded")

    def guard(
        self,
        items: Iterable[_Item],
        *,
        interval: int = DEADLINE_CHECK_INTERVAL,
    ) -> Iterator[_Item]:
        if interval < 1:
            raise ValueError("retrieval deadline check interval must be positive")
        for position, item in enumerate(items):
            if position % interval == 0:
                self.check()
            yield item
