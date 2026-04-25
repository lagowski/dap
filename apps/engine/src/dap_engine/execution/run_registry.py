"""In-memory tracker of running asyncio tasks per run_id.

Required for abort + graceful shutdown. Process-local — restarting the
engine forgets the registry; persistent state lives in the DB (Run rows).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("dap.engine.execution.registry")


class RunRegistry:
    """Maps run_id → asyncio.Task. Thread-unsafe; intended for asyncio loop only."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def register(self, run_id: str, task: asyncio.Task[None]) -> None:
        if run_id in self._tasks:
            raise ValueError(f"Run already registered: {run_id}")
        self._tasks[run_id] = task
        # Auto-cleanup on completion
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))

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
