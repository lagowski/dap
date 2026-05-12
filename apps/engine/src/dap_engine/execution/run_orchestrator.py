"""Background orchestration of pipeline runs (#254).

Lifted out of ``api/runs.py``: the two coroutines below are pure
runner-orchestration with session handling, not HTTP routing. The
router endpoints (``POST /runs``, ``POST /runs/{id}/resume``,
``POST /runs/{id}/nodes/{n}/{retry,skip,approve}``) wrap calls to
these functions in ``asyncio.create_task`` and register the resulting
task with :class:`RunRegistry`.

Each coroutine opens its own DB session via ``session_factory`` so
the request-scoped session that handled the original POST stays
short-lived.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dap_runtimes import RuntimeRegistry
from dap_types import PipelineState
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.execution.run_registry import RunRegistry
from dap_engine.execution.runner import (
    CheckpointNotFoundError,
    PipelineRunner,
    RunnerError,
    RunnerInterrupt,
)
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import PipelineORM, PipelineVersionORM

logger = logging.getLogger("dap.engine.execution.orchestrator")


async def execute_run_background(
    *,
    run_id: str,
    pipeline_id: str,
    pipeline_version: int,
    initial_state: PipelineState | None,
    session_factory: sessionmaker[Session],
    registry: RuntimeRegistry,
    run_registry: RunRegistry,
    checkpointer: BaseCheckpointSaver[Any],
    resume: bool,
) -> None:
    """Run the pipeline in a fresh DB session and finalize the Run row."""
    try:
        with session_factory() as bg_session:
            pipeline_orm = bg_session.get(PipelineORM, pipeline_id)
            version_orm = bg_session.scalar(
                select(PipelineVersionORM)
                .where(PipelineVersionORM.pipeline_id == pipeline_id)
                .where(PipelineVersionORM.version == pipeline_version)
            )
            if pipeline_orm is None or version_orm is None:
                logger.error(
                    "background run %s: pipeline %s@v%s vanished",
                    run_id,
                    pipeline_id,
                    pipeline_version,
                )
                repo.finalize_run(bg_session, run_id, final_status="failed")
                bg_session.commit()
                return

            runner = PipelineRunner(
                session=bg_session,
                registry=registry,
                checkpointer=checkpointer,
            )
            try:
                final_state = await runner.run(
                    run_id=run_id,
                    pipeline_orm=pipeline_orm,
                    pipeline_version_orm=version_orm,
                    initial_state=initial_state,
                    resume=resume,
                )
            except RunnerInterrupt as interrupt:
                # Graph paused at an approval-required node (interrupt_before).
                # Store the gate node so the dashboard can show a targeted
                # "Approve" action (#363).
                paused_at = interrupt.next_nodes[0] if interrupt.next_nodes else None
                repo.pause_run(bg_session, run_id, paused_at_node=paused_at)
                bg_session.commit()
                return
            except RunnerError as exc:
                logger.exception("run %s failed: %s", run_id, exc)
                repo.finalize_run(bg_session, run_id, final_status="failed")
                bg_session.commit()
                return

            final_status = final_state.final_status
            if final_status not in {"success", "failed", "aborted"}:
                final_status = "success"
            repo.finalize_run(bg_session, run_id, final_status=final_status)
            bg_session.commit()
    except asyncio.CancelledError:
        # Cancelled via pause() or abort(). Distinguish via was_paused() flag —
        # paused runs keep their checkpoint and can be resumed (no ended_at);
        # aborted runs are terminal.
        was_paused = run_registry.was_paused(run_id)
        try:
            with session_factory() as cancel_session:
                if was_paused:
                    repo.pause_run(cancel_session, run_id)
                else:
                    repo.finalize_run(cancel_session, run_id, final_status="aborted")
                cancel_session.commit()
        except Exception:
            logger.exception("failed to finalize cancelled run %s", run_id)
        raise  # propagate so the registry sees the cancellation
    except Exception:
        logger.exception("background run %s crashed", run_id)
        try:
            with session_factory() as crash_session:
                repo.finalize_run(crash_session, run_id, final_status="failed")
                crash_session.commit()
        except Exception:
            logger.exception("failed to finalize crashed run %s", run_id)


async def execute_rewind_background(
    *,
    run_id: str,
    pipeline_id: str,
    pipeline_version: int,
    target_node: str,
    mode: str,
    session_factory: sessionmaker[Session],
    registry: RuntimeRegistry,
    run_registry: RunRegistry,
    checkpointer: BaseCheckpointSaver[Any],
) -> None:
    """Run a retry/skip rewind in a fresh DB session and finalize the Run row."""
    try:
        with session_factory() as bg_session:
            pipeline_orm = bg_session.get(PipelineORM, pipeline_id)
            version_orm = bg_session.scalar(
                select(PipelineVersionORM)
                .where(PipelineVersionORM.pipeline_id == pipeline_id)
                .where(PipelineVersionORM.version == pipeline_version)
            )
            if pipeline_orm is None or version_orm is None:
                logger.error(
                    "rewind run %s: pipeline %s@v%s vanished",
                    run_id,
                    pipeline_id,
                    pipeline_version,
                )
                repo.finalize_run(bg_session, run_id, final_status="failed")
                bg_session.commit()
                return

            runner = PipelineRunner(
                session=bg_session,
                registry=registry,
                checkpointer=checkpointer,
            )
            try:
                final_state = await runner.rewind_and_run(
                    run_id=run_id,
                    pipeline_orm=pipeline_orm,
                    pipeline_version_orm=version_orm,
                    target_node=target_node,
                    mode=mode,
                )
            except RunnerInterrupt as interrupt:
                paused_at = interrupt.next_nodes[0] if interrupt.next_nodes else None
                repo.pause_run(bg_session, run_id, paused_at_node=paused_at)
                bg_session.commit()
                return
            except CheckpointNotFoundError as exc:
                logger.warning("rewind run %s: %s", run_id, exc)
                repo.finalize_run(bg_session, run_id, final_status="failed")
                bg_session.commit()
                return
            except RunnerError as exc:
                logger.exception("rewind run %s failed: %s", run_id, exc)
                repo.finalize_run(bg_session, run_id, final_status="failed")
                bg_session.commit()
                return

            final_status = final_state.final_status
            if final_status not in {"success", "failed", "aborted"}:
                final_status = "success"
            repo.finalize_run(bg_session, run_id, final_status=final_status)
            bg_session.commit()
    except asyncio.CancelledError:
        was_paused = run_registry.was_paused(run_id)
        try:
            with session_factory() as cancel_session:
                if was_paused:
                    repo.pause_run(cancel_session, run_id)
                else:
                    repo.finalize_run(cancel_session, run_id, final_status="aborted")
                cancel_session.commit()
        except Exception:
            logger.exception("failed to finalize cancelled rewind run %s", run_id)
        raise
    except Exception:
        logger.exception("background rewind run %s crashed", run_id)
        try:
            with session_factory() as crash_session:
                repo.finalize_run(crash_session, run_id, final_status="failed")
                crash_session.commit()
        except Exception:
            logger.exception("failed to finalize crashed rewind run %s", run_id)
