"""Runtime-policy helpers for run-triggering endpoints."""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from dap_engine.persistence.models import AgentORM, AgentVersionORM, PipelineVersionORM
from dap_engine.runtime_policy import runtime_policy_error

logger = logging.getLogger("dap.engine.api.run_runtime_policy")


def pipeline_runtime_policy_error(
    session: Session,
    pipeline_version: PipelineVersionORM,
    *,
    is_admin: bool,
    allow_bash_runtime_for_non_admin: bool,
) -> str | None:
    """Return the first runtime policy denial for a pipeline version, if any."""
    agent_ids: set[str] = set()
    for node in pipeline_version.nodes:
        if not isinstance(node, dict):
            logger.error(
                "pipeline %s@v%s has malformed node entry: %r",
                pipeline_version.pipeline_id,
                pipeline_version.version,
                node,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Pipeline version contains malformed node data",
            )
        agent_id = node.get("agent_id")
        if isinstance(agent_id, str):
            agent_ids.add(agent_id)
    if not agent_ids:
        return None

    rows = session.execute(
        select(AgentORM, AgentVersionORM)
        .join(
            AgentVersionORM,
            (AgentVersionORM.agent_id == AgentORM.id)
            & (AgentVersionORM.version == AgentORM.current_version),
        )
        .where(AgentORM.id.in_(agent_ids))
    ).all()
    for _agent, version in rows:
        denial = runtime_policy_error(
            version.runtime_id,
            is_admin=is_admin,
            allow_bash_runtime_for_non_admin=allow_bash_runtime_for_non_admin,
        )
        if denial is not None:
            return denial
    return None
