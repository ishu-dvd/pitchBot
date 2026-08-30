from __future__ import annotations

import tracemalloc

import pytest

from pitchbot.benchmarks.runner import measure_async


@pytest.mark.asyncio
async def test_measure_async_reports_deterministic_percentiles() -> None:
    timer_values = iter([0.0, 0.1, 1.0, 1.2, 2.0, 2.5])
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result, timing = await measure_async(
        operation,
        repetitions=3,
        warmups=1,
        timer=lambda: next(timer_values),
    )

    assert result == "ok"
    assert calls == 4
    assert timing.repetitions == 3
    assert timing.minimum_seconds == pytest.approx(0.1)
    assert timing.median_seconds == pytest.approx(0.2)
    assert timing.p95_seconds == pytest.approx(0.5)
    assert timing.peak_python_bytes >= 0


@pytest.mark.asyncio
async def test_measure_async_supports_none_and_validates_counts() -> None:
    async def operation() -> None:
        return None

    result, _ = await measure_async(operation, repetitions=1, warmups=0)
    assert result is None

    with pytest.raises(ValueError, match="repetitions"):
        await measure_async(operation, repetitions=0)


@pytest.mark.asyncio
async def test_measure_async_rejects_global_tracing_and_invalid_timer() -> None:
    async def operation() -> str:
        return "ok"

    tracemalloc.start()
    try:
        with pytest.raises(RuntimeError, match="already active"):
            await measure_async(operation, repetitions=1, warmups=0)
    finally:
        tracemalloc.stop()

    timer_values = iter([0.0, float("nan")])
    with pytest.raises(ValueError, match="non-finite"):
        await measure_async(
            operation,
            repetitions=1,
            warmups=0,
            timer=lambda: next(timer_values),
        )
