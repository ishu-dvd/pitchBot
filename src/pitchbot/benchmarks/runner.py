from __future__ import annotations

import math
import statistics
import time
import tracemalloc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True, slots=True)
class TimingSummary:
    repetitions: int
    median_seconds: float
    p95_seconds: float
    minimum_seconds: float
    maximum_seconds: float
    peak_python_bytes: int


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


async def measure_async[T](
    operation: Callable[[], Awaitable[T]],
    *,
    repetitions: int = 5,
    warmups: int = 1,
    timer: Callable[[], float] = time.perf_counter,
) -> tuple[T, TimingSummary]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    if warmups < 0:
        raise ValueError("warmups must not be negative")

    for _ in range(warmups):
        await operation()

    if tracemalloc.is_tracing():
        raise RuntimeError("another Python allocation measurement is already active")

    timings: list[float] = []
    sentinel = object()
    result: T | object = sentinel
    tracemalloc.start()
    try:
        for _ in range(repetitions):
            started = timer()
            result = await operation()
            elapsed = timer() - started
            if not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError("timer produced a non-finite or negative duration")
            timings.append(elapsed)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    if result is sentinel:
        raise RuntimeError("measurement completed without a result")
    return cast(T, result), TimingSummary(
        repetitions=repetitions,
        median_seconds=statistics.median(timings),
        p95_seconds=_nearest_rank(timings, 0.95),
        minimum_seconds=min(timings),
        maximum_seconds=max(timings),
        peak_python_bytes=peak_bytes,
    )
