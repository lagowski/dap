"""Regression tests for ``RunRegistry`` registration races (#258).

The audit B3 flagged ``register`` as a TOCTOU window: between
``self._tasks.get(run_id)`` and ``self._tasks[run_id] = task`` two
mutators could in principle slip past the duplicate check. In a
single-threaded asyncio loop the body runs without an await so it's
already atomic; the threading.Lock added in #258 makes that explicit
and protects the registry from off-loop callers (e.g. shutdown
handlers invoked from a different thread).

These tests exercise the documented semantics: duplicate registers
raise, stale done-task slots are replaced, the cleanup callback
removes the slot it owns.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from dap_engine.execution import RunRegistry


async def _running_task() -> None:
    """A task that blocks until cancelled — useful as a registry occupant."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise


async def _completed_task(value: int = 0) -> int:
    """A task that resolves immediately."""
    return value


@pytest.mark.asyncio
async def test_register_rejects_duplicate_for_running_run() -> None:
    """A second register for the same run_id while the first task is still
    running must raise — concurrent triggers should not orphan a task in
    the registry."""
    registry = RunRegistry()
    run_id = "run-1"
    first_task = asyncio.create_task(_running_task())
    registry.register(run_id, first_task)
    try:
        second_task = asyncio.create_task(_running_task())
        try:
            with pytest.raises(ValueError, match="already registered"):
                registry.register(run_id, second_task)
        finally:
            second_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await second_task
    finally:
        first_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first_task


@pytest.mark.asyncio
async def test_register_replaces_stale_done_task_slot() -> None:
    """When the previous task is already done, register must accept the new
    task — the cleanup callback may not have fired yet (it dispatches on a
    later loop tick), so the slot can still hold the done task."""
    registry = RunRegistry()
    run_id = "run-stale"

    finished_task: asyncio.Task[int] = asyncio.create_task(_completed_task(7))
    await finished_task  # ensure it's done

    # Manually plant the done task — simulates the race window before the
    # cleanup callback runs.
    registry._tasks[run_id] = finished_task  # noqa: SLF001  (intentional white-box)

    fresh_task = asyncio.create_task(_running_task())
    try:
        registry.register(run_id, fresh_task)
        assert registry.get(run_id) is fresh_task
    finally:
        fresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fresh_task


@pytest.mark.asyncio
async def test_cleanup_callback_clears_slot_after_task_done() -> None:
    """When a registered task completes the cleanup callback should remove
    it from the registry — otherwise a later trigger would see a stale slot."""
    registry = RunRegistry()
    run_id = "run-cleanup"

    task = asyncio.create_task(_completed_task())
    registry.register(run_id, task)

    await task
    # Yield to the loop so the done callback gets a chance to run.
    await asyncio.sleep(0)

    assert registry.get(run_id) is None
    assert not registry.is_running(run_id)


@pytest.mark.asyncio
async def test_concurrent_register_attempts_serialise() -> None:
    """Two coroutines reaching ``register`` for the same run_id within the
    same event-loop tick must not both succeed — the second must observe
    the first's task and raise."""
    registry = RunRegistry()
    run_id = "run-concurrent"
    task_a = asyncio.create_task(_running_task())
    task_b = asyncio.create_task(_running_task())

    async def _try_register(t: asyncio.Task[None]) -> bool:
        try:
            registry.register(run_id, t)
        except ValueError:
            return False
        return True

    results = await asyncio.gather(_try_register(task_a), _try_register(task_b))
    try:
        # Exactly one must succeed; the other gets ValueError → False.
        assert sorted(results) == [False, True]
        # Whichever won is the one we'll see in the registry.
        winner = task_a if results[0] else task_b
        assert registry.get(run_id) is winner
    finally:
        for t in (task_a, task_b):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
