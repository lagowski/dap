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
import threading

import pytest
from dap_engine.execution import RunRegistry


async def _running_task() -> None:
    """A task that blocks until cancelled — useful as a registry occupant."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise


async def _completed_task() -> None:
    """A task that resolves immediately. Returns ``None`` so the type matches
    ``RunRegistry._tasks: dict[str, asyncio.Task[None]]`` for stubbed slots."""
    return None


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

    finished_task: asyncio.Task[None] = asyncio.create_task(_completed_task())
    await finished_task  # ensure it's done

    # Manually plant the done task — simulates the race window before the
    # cleanup callback runs.
    registry._tasks[run_id] = finished_task

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
async def test_register_thread_safe_under_real_concurrency() -> None:
    """Two OS threads racing to register the same run_id collapse to one
    winner — exercises the threading.Lock added in #258. Pure asyncio
    coroutines wouldn't actually race here (``register`` has no await,
    so single-loop scheduling makes the second call see the first's
    state deterministically); we need real preemption from two threads
    plus a ``Barrier`` to maximise the chance of the threads entering
    the critical section concurrently."""
    registry = RunRegistry()
    run_id = "run-thread-race"
    task_a = asyncio.create_task(_running_task())
    task_b = asyncio.create_task(_running_task())

    barrier = threading.Barrier(parties=2)
    results: list[bool] = []
    results_lock = threading.Lock()

    def attempt(task: asyncio.Task[None]) -> None:
        # Both threads block on the barrier so they enter ``register``
        # at as close to the same wall-clock instant as possible.
        barrier.wait()
        try:
            registry.register(run_id, task)
            ok = True
        except ValueError:
            ok = False
        with results_lock:
            results.append(ok)

    try:
        threads = [
            threading.Thread(target=attempt, args=(task_a,)),
            threading.Thread(target=attempt, args=(task_b,)),
        ]
        for thr in threads:
            thr.start()
        for thr in threads:
            thr.join()

        # Exactly one wins; the other observes the winner's slot under
        # the lock and raises ValueError. Neither outcome on its own
        # would surface a missing lock — but breaking the lock would let
        # both writes through, leaving the registry in an undefined state.
        assert sorted(results) == [False, True]
        winner = registry.get(run_id)
        assert winner in (task_a, task_b)
    finally:
        for t in (task_a, task_b):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
