"""Waiting for the code under test to reach an adapter, without waiting forever.

Concurrency tests here drive a service until it blocks inside a fake adapter, then assert
on what the service did on the way in. Every one of them did that with a bare
``await adapter.started.wait()``, which is unbounded - so any failure *before* the adapter
is reached never sets the signal and the test waits forever.

A hang is a much worse outcome than a failure: it produces no assertion, no traceback and
no test name, and it costs the whole job timeout rather than a second. That is not
hypothetical. Mutation-testing PR 56 made a deck raise a validation error before it called
its artifact adapter, and ``test_concurrent_deck_admission_cannot_exceed_capacity`` hung
for forty minutes instead of failing in two seconds with the error that caused it.
"""

from __future__ import annotations

import asyncio

# How long a concurrency test will wait for the code under test to reach an adapter before
# deciding it never will. Generous enough that a slow machine is not a flake, short enough
# that a stuck test is a failure rather than a burnt CI job.
SIGNAL_TIMEOUT_SECONDS = 10.0


async def reached(signal: asyncio.Event, *, task: asyncio.Task[object] | None = None) -> None:
    """Wait until the code under test reaches an adapter, or say why it never did.

    Passing ``task`` gives the best diagnostic available: if the work finishes or raises
    before signalling, that outcome is surfaced directly instead of a timeout.
    """

    if task is None:
        await asyncio.wait_for(signal.wait(), SIGNAL_TIMEOUT_SECONDS)
        return
    waiter = asyncio.create_task(signal.wait())
    done, _ = await asyncio.wait(
        {waiter, task},
        timeout=SIGNAL_TIMEOUT_SECONDS,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if waiter in done:
        return
    waiter.cancel()
    if task in done:
        # Re-raises whatever the work raised, which is the thing worth reading.
        task.result()
        raise AssertionError("Work completed without ever reaching the adapter")
    raise TimeoutError(f"Adapter was not reached within {SIGNAL_TIMEOUT_SECONDS}s")
