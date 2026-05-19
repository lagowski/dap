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
from dap_types import PipelineDefaults, PipelineState
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
from dap_engine.persistence.models import PipelineORM, PipelineVersionORM, RunORM

logger = logging.getLogger("dap.engine.execution.orchestrator")


def _auto_approve_nodes_for_run(session: Session, run_id: str) -> frozenset[str]:
    """Return the auto_approve_nodes list from the run's persisted initial_state.

    Reads ``initial_state.extensions.auto_approve_nodes`` from the DB row so
    the check works for both initial and resumed executions (the list is
    always stored in the original initial_state at trigger time).
    """
    run_orm = session.get(RunORM, run_id)
    if run_orm is None:
        return frozenset()
    extensions: dict[str, Any] = (run_orm.initial_state or {}).get("extensions") or {}
    nodes = extensions.get("auto_approve_nodes")
    if not isinstance(nodes, list):
        return frozenset()
    return frozenset(n for n in nodes if isinstance(n, str))


def _gate_node_from_interrupt(
    interrupt: RunnerInterrupt, version_orm: PipelineVersionORM
) -> str | None:
    """Return the first approval-gate node staged in *interrupt*, or None.

    Filters ``interrupt.next_nodes`` to the pipeline's
    ``approval_required_nodes`` so a non-gate node appearing earlier in the
    list (which LangGraph may stage alongside the gate) doesn't shadow the
    real gate id stored in the Run row (#363 Copilot review).
    """
    approval_nodes = set(
        PipelineDefaults.model_validate(version_orm.defaults).approval_required_nodes
    )
    return next((n for n in interrupt.next_nodes if n in approval_nodes), None)


_TERMINAL_FINAL_STATUSES: frozenset[str] = frozenset({"success", "failed", "aborted"})


def _resolve_terminal_status(
    final_state: PipelineState,
    *,
    requires_terminal: bool,
) -> tuple[str, str | None]:
    """Map a runner-completed ``PipelineState`` to the row-level final status.

    Used by ``execute_run_background`` and ``execute_rewind_background``
    after the runner returns cleanly. Returns ``(status, failure_reason)``
    where ``failure_reason`` is non-None only on the defensive failure
    paths so the caller knows whether to persist a reason.

    Behaviour (#381):

    - Explicit terminal values (``success`` / ``failed`` / ``aborted``)
      pass through unchanged. The orchestrator must never overwrite a
      deliberate node-set status.
    - When ``requires_terminal=False`` (the default for generic
      pipelines), a non-terminal ``final_status`` is silently coerced
      to ``success`` — preserves the historical "no node raised" =
      "successful run" contract for pipelines that don't manage
      state-level ``final_status``.
    - When ``requires_terminal=True`` (cortex bundle opts in via
      ``PipelineDefaults.requires_terminal_final_status``), a residual
      ``running`` means *no node ever set a terminal status* and is
      treated as ``failed`` with an operator-readable reason. A
      residual ``paused`` would also be a bug (a real pause raises
      ``RunnerInterrupt`` long before this code runs) and any other
      value is rejected on the same grounds.
    """
    status = final_state.final_status
    if status in _TERMINAL_FINAL_STATUSES:
        return status, None

    if not requires_terminal:
        # Historical default: trust that the runner completing without
        # exceptions means the pipeline reached its goal, even if no
        # node bothered to set state.final_status.
        return "success", None

    if status == "running":
        return "failed", (
            "pipeline finished without setting a terminal final_status "
            "(a node likely returned success without updating state on refusal)"
        )
    if status == "paused":
        return "failed", (
            "pipeline finished with final_status='paused' but the runner "
            "did not signal a real pause — treating as failure"
        )
    return "failed", f"pipeline finished with unknown final_status={status!r}"


def _gate_payload_from_interrupt(interrupt: RunnerInterrupt) -> dict[str, Any] | None:
    """Return a serialisable gate payload for storage on the Run row (#364).

    Extracts task_assignments (and optionally spec/description) from
    ``interrupt.gate_state`` so the dashboard can show gate context without
    querying the checkpoint store directly.
    Returns None when gate_state is absent or contains no useful context.
    """
    state = interrupt.gate_state
    if not state:
        return None
    # Pull the fields the dashboard needs; ignore everything else to keep
    # the stored payload small and pipeline-agnostic.
    extensions: dict[str, Any] = state.get("extensions") or {}
    task_assignments = extensions.get("task_assignments")
    if not task_assignments:
        return None
    payload: dict[str, Any] = {"task_assignments": task_assignments}
    # Carry description/spec if present — useful for Reject feedback UX.
    if spec := extensions.get("spec"):
        payload["spec"] = spec
    return payload


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
    instance_env_vars_key: str | None = None,
    allow_bash_runtime_for_non_admin: bool = False,
    actor_is_admin: bool = False,
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
                instance_env_vars_key=instance_env_vars_key,
                allow_bash_runtime_for_non_admin=allow_bash_runtime_for_non_admin,
                actor_is_admin=actor_is_admin,
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
                paused_at_node = _gate_node_from_interrupt(interrupt, version_orm)
                repo.pause_run(
                    bg_session,
                    run_id,
                    paused_at_node=paused_at_node,
                    gate_payload=_gate_payload_from_interrupt(interrupt),
                )
                bg_session.commit()
                # Auto-resume if this gate is in the run's auto_approve_nodes (#477).
                if paused_at_node is not None:
                    auto_nodes = _auto_approve_nodes_for_run(bg_session, run_id)
                    if paused_at_node in auto_nodes:
                        logger.info(
                            "auto-approving gate %s for run %s", paused_at_node, run_id
                        )
                        if repo.try_claim_resume(bg_session, run_id):
                            bg_session.commit()
                            # Spawn resume without re-registering: the current
                            # task is still in the registry and run_registry
                            # raises if we try to register a second slot while
                            # the first is non-done. The run is now in "running"
                            # state (try_claim_resume set it), so any concurrent
                            # POST /approve will see it as non-paused and 409.
                            asyncio.create_task(
                                execute_run_background(
                                    run_id=run_id,
                                    pipeline_id=pipeline_id,
                                    pipeline_version=pipeline_version,
                                    initial_state=None,
                                    session_factory=session_factory,
                                    registry=registry,
                                    run_registry=run_registry,
                                    checkpointer=checkpointer,
                                    resume=True,
                                    instance_env_vars_key=instance_env_vars_key,
                                    allow_bash_runtime_for_non_admin=allow_bash_runtime_for_non_admin,
                                    actor_is_admin=actor_is_admin,
                                )
                            )
                return
            except RunnerError as exc:
                logger.exception("run %s failed: %s", run_id, exc)
                repo.finalize_run(bg_session, run_id, final_status="failed")
                bg_session.commit()
                return

            requires_terminal = PipelineDefaults.model_validate(
                version_orm.defaults
            ).requires_terminal_final_status
            final_status, failure_reason = _resolve_terminal_status(
                final_state, requires_terminal=requires_terminal
            )
            repo.finalize_run(
                bg_session,
                run_id,
                final_status=final_status,
                failure_reason=failure_reason,
            )
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
    instance_env_vars_key: str | None = None,
    allow_bash_runtime_for_non_admin: bool = False,
    actor_is_admin: bool = False,
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
                instance_env_vars_key=instance_env_vars_key,
                allow_bash_runtime_for_non_admin=allow_bash_runtime_for_non_admin,
                actor_is_admin=actor_is_admin,
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
                repo.pause_run(
                    bg_session,
                    run_id,
                    paused_at_node=_gate_node_from_interrupt(interrupt, version_orm),
                    gate_payload=_gate_payload_from_interrupt(interrupt),
                )
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

            requires_terminal = PipelineDefaults.model_validate(
                version_orm.defaults
            ).requires_terminal_final_status
            final_status, failure_reason = _resolve_terminal_status(
                final_state, requires_terminal=requires_terminal
            )
            repo.finalize_run(
                bg_session,
                run_id,
                final_status=final_status,
                failure_reason=failure_reason,
            )
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
