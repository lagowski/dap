"""Sequential batch pipeline execution (#473).

Runs a list of issue numbers through the same pipeline one at a time,
waiting for each run to reach a terminal status before starting the next.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from dap_types import PipelineState
from dap_runtimes import RuntimeRegistry
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.execution.run_orchestrator import execute_run_background
from dap_engine.execution.run_registry import RunRegistry
from dap_engine.persistence import repository as repo
from dap_engine.persistence import batch_runs as batch_runs_repo

logger = logging.getLogger("dap.engine.execution.batch_runner")

_TERMINAL = frozenset({"success", "failed", "aborted"})


async def execute_batch_run_background(
    *,
    batch_run_id: str,
    pipeline_id: str,
    pipeline_version: int,
    project_id: str | None,
    user_id: uuid.UUID,
    issue_numbers: list[int],
    stop_on_failure: bool,
    auto_approve: bool,
    base_state: dict[str, Any],
    session_factory: sessionmaker[Session],
    registry: RuntimeRegistry,
    run_registry: RunRegistry,
    checkpointer: BaseCheckpointSaver[Any],
    instance_env_vars_key: str | None,
    allow_bash_runtime_for_non_admin: bool,
    actor_is_admin: bool,
) -> None:
    has_failure = False

    for idx, issue_number in enumerate(issue_numbers):
        # Merge issue_number (and auto_approve) into extensions for this run.
        extensions: dict[str, Any] = dict(base_state.get("extensions") or {})
        extensions["issue_number"] = issue_number
        if auto_approve:
            extensions["auto_approve"] = True

        state_dict: dict[str, Any] = {**base_state, "extensions": extensions}
        try:
            initial_state = PipelineState.model_validate(state_dict)
        except Exception:
            logger.exception("batch %s: invalid state for issue %d", batch_run_id, issue_number)
            _record_result(
                session_factory, batch_run_id, issue_number, None, "failed", idx + 1, "running"
            )
            if stop_on_failure:
                has_failure = True
                break
            has_failure = True
            continue

        with session_factory() as session:
            run_orm = repo.create_run(
                session,
                user_id=user_id,
                pipeline_id=pipeline_id,
                pipeline_version=pipeline_version,
                trigger_source="api",
                initial_state=initial_state,
                project_id=project_id,
            )
            session.commit()
            run_id: str = run_orm.id

        initial_state_with_run_id = initial_state.model_copy(update={"run_id": run_id})

        task = asyncio.create_task(
            execute_run_background(
                run_id=run_id,
                pipeline_id=pipeline_id,
                pipeline_version=pipeline_version,
                initial_state=initial_state_with_run_id,
                session_factory=session_factory,
                registry=registry,
                run_registry=run_registry,
                checkpointer=checkpointer,
                resume=False,
                instance_env_vars_key=instance_env_vars_key,
                allow_bash_runtime_for_non_admin=allow_bash_runtime_for_non_admin,
                actor_is_admin=actor_is_admin,
            )
        )
        run_registry.register(run_id, task)
        await task

        with session_factory() as session:
            run = repo.get_run(session, run_id, actor_id=user_id, is_admin=True)
            run_status = run.final_status

        _record_result(
            session_factory,
            batch_run_id,
            issue_number,
            run_id,
            run_status,
            idx + 1,
            "running",
        )

        if run_status in {"failed", "aborted"}:
            has_failure = True
            if stop_on_failure:
                break

    final_status = "failed" if has_failure else "success"
    with session_factory() as session:
        batch_runs_repo.finalize_batch_run(session, batch_run_id, status=final_status)
        session.commit()


def _record_result(
    session_factory: sessionmaker[Session],
    batch_run_id: str,
    issue_number: int,
    run_id: str | None,
    run_status: str,
    new_index: int,
    batch_status: str,
) -> None:
    with session_factory() as session:
        batch_runs_repo.append_batch_result(
            session,
            batch_run_id,
            issue_number=issue_number,
            run_id=run_id,
            run_status=run_status,
            new_index=new_index,
            batch_status=batch_status,
        )
        session.commit()
