"""In-memory tracker of running asyncio tasks per run_id.

Required for abort + graceful shutdown. Process-local — restarting the
engine forgets the registry; persistent state lives in the DB (Run rows).
"""

from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger("dap.engine.execution.registry")


class RunRegistry:
    """Maps run_id → asyncio.Task.

    Mutations of the underlying ``_tasks`` / ``_paused`` collections are
    serialised under a ``threading.Lock`` (#258). In normal single-loop
    operation the lock is uncontended — Python bytecode for the
    check-then-set in ``register`` already runs without yielding the
    event loop — but the explicit lock makes the registry correct under
    multi-thread access (e.g. shutdown handlers running off the loop)
    without relying on that implicit single-thread invariant.

    The DB-side TOCTOU guard for resume / revive lives in
    :func:`dap_engine.persistence.runs.try_claim_resume` (#185); this
    registry only protects its own in-memory state.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._paused: set[str] = set()
        # Sync mutex (not asyncio.Lock) so callers don't have to be async
        # — register is invoked from sync code paths (e.g. POST handlers'
        # post-claim block) and the critical section is a few dict ops.
        self._lock = threading.Lock()

    def register(self, run_id: str, task: asyncio.Task[None]) -> None:
        with self._lock:
            existing = self._tasks.get(run_id)
            if existing is not None and not existing.done():
                raise ValueError(f"Run already registered: {run_id}")
            # Replace stale done-task slot (e.g. resume after pause where the
            # done-callback has not been dispatched yet by the loop).
            self._tasks[run_id] = task
            self._paused.discard(run_id)

        def _cleanup(_t: asyncio.Task[None]) -> None:
            # Only clear the slot if it still points at *this* task —
            # a fast resume may have replaced it with a fresh task.
            with self._lock:
                if self._tasks.get(run_id) is task:
                    self._tasks.pop(run_id, None)
                    self._paused.discard(run_id)

        task.add_done_callback(_cleanup)

    def get(self, run_id: str) -> asyncio.Task[None] | None:
        return self._tasks.get(run_id)

    def is_running(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    def list_running(self) -> list[str]:
        return [run_id for run_id, t in self._tasks.items() if not t.done()]

    async def abort(self, run_id: str) -> bool:
        """Cancel the task for `run_id`. Returns True if a running task was found.

        Caller is responsible for updating the Run row's final_status.
        """
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("task for run %s raised during cancellation", run_id)
        return True

    async def pause(self, run_id: str) -> bool:
        """Mark a run as paused and cancel its task.

        Pausing relies on LangGraph's checkpointer: the task is cancelled
        between node boundaries, but the checkpointed state remains, so
        a later resume() can pick up where it left off.

        Returns True if a running task was found and cancelled.
        """
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        self._paused.add(run_id)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("task for run %s raised during pause", run_id)
        return True

    def was_paused(self, run_id: str) -> bool:
        """Whether the (now-cancelled) task was cancelled via pause(), not abort()."""
        return run_id in self._paused

    async def shutdown(self, *, timeout: float = 5.0) -> list[str]:
        """Cancel all running tasks. Returns list of run_ids that were cancelled.

        Used at engine teardown to ensure no orphan tasks leak.
        """
        running_ids = self.list_running()
        if not running_ids:
            return []

        for run_id in running_ids:
            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                task.cancel()

        # Wait for cancellation to settle, with timeout
        tasks = [self._tasks[rid] for rid in running_ids if rid in self._tasks]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=timeout,
                )
            except TimeoutError:
                logger.warning(
                    "shutdown timeout (%.1fs) — some run tasks may not have finalized",
                    timeout,
                )
        return running_ids
