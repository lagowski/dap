"""Generic node executor — same function shape for every pipeline node.

Each node:
1. Renders prompt template with state as Jinja context (F4)
2. Builds RuntimeTask with the rendered XML
3. Calls runtime adapter (F3)
4. Records NodeExecutionLog + StateSnapshot
5. Returns state diff (dict that LangGraph merges into state)

For F5 MVP, state diff is `{}` on success and `{"final_status": "failed"}`
on error. Per-role output parsing (e.g. test_author → state.test_files)
is a future issue — adapters can opt into structured output by returning
RuntimeResult.structured with keys matching state fields, and we'll
merge them.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from dap_prompt_dsl import PromptBuildError, build_prompt
from dap_runtimes import RuntimeRegistry
from dap_types import PipelineState, RuntimeResult, RuntimeTask
from sqlalchemy.orm import Session

from dap_engine.execution.output_parser import parse_node_output
from dap_engine.persistence.models import (
    AgentORM,
    AgentVersionORM,
    NodeExecutionLogORM,
    StateSnapshotORM,
)

logger = logging.getLogger("dap.engine.execution")

NodeFn = Callable[[PipelineState], Coroutine[Any, Any, dict[str, Any]]]


class PauseRequestedError(Exception):
    """Raised when a node's RuntimeResult has pause_requested=True.

    Propagates through the LangGraph graph → PipelineRunner → background
    task handler, which catches it and transitions the run to "paused".
    """


class NodeContext:
    """Bound context for a single pipeline node — captured in node closure."""

    def __init__(
        self,
        *,
        run_id: str,
        node_id: str,
        agent: AgentORM,
        agent_version: AgentVersionORM,
        registry: RuntimeRegistry,
        session: Session,
        runtime_overrides: dict[str, Any] | None = None,
        timeout_override_ms: int | None = None,
        project_working_directory: str | None = None,
        project_env_vars: dict[str, str] | None = None,
    ) -> None:
        self.run_id = run_id
        self.node_id = node_id
        self.agent = agent
        self.agent_version = agent_version
        self.registry = registry
        self.session = session
        self.runtime_overrides = runtime_overrides or {}
        self.timeout_override_ms = timeout_override_ms
        # Project context (#65) — propagated into RuntimeTask so adapters
        # can run subprocesses in the right cwd with the right env.
        # ``None`` / ``{}`` for ad-hoc runs without a project.
        self.project_working_directory = project_working_directory
        self.project_env_vars = project_env_vars or {}

    @property
    def merged_runtime_config(self) -> dict[str, Any]:
        """Agent's runtime_config merged with per-node overrides."""
        return {**self.agent_version.runtime_config, **self.runtime_overrides}

    @property
    def timeout_ms(self) -> int:
        return self.timeout_override_ms or self.agent_version.timeout_ms


def make_node_fn(ctx: NodeContext) -> NodeFn:
    """Build an async node function bound to this NodeContext.

    The returned function is what LangGraph calls during graph execution.
    """

    async def node_fn(state: PipelineState) -> dict[str, Any]:
        started_at = datetime.now(UTC)
        execution_id = str(uuid.uuid4())

        # 1. Render prompt — scoped to declared inputs when the agent
        # version carries a contract (v0.5+); legacy dict-shaped or
        # missing schemas pass through with full state visibility.
        raw_input_schema = ctx.agent_version.input_schema
        input_schema = list(raw_input_schema) if isinstance(raw_input_schema, list) else None
        try:
            build_result = build_prompt(
                ctx.agent_version.prompt_template,
                state.model_dump(),
                input_schema=input_schema,
                # Auto-inject agent identity (#113) so templates can use
                # ``{{ role }}`` without forcing every agent to declare
                # ``role`` as an input. ``PipelineState`` doesn't carry
                # it; ``role`` lives on the parent ``AgentORM`` (not
                # the version row, which is per-revision data).
                agent_metadata={"role": ctx.agent.role},
            )
        except PromptBuildError as exc:
            return _record_failure(
                ctx=ctx,
                started_at=started_at,
                execution_id=execution_id,
                prompt_xml="",
                error=f"Prompt build failed: {exc}",
                state=state,
            )

        if not build_result.valid:
            return _record_failure(
                ctx=ctx,
                started_at=started_at,
                execution_id=execution_id,
                prompt_xml=build_result.xml,
                error=f"Invalid prompt XML: {'; '.join(build_result.errors)}",
                state=state,
            )

        prompt_xml = build_result.xml

        # 2. Build RuntimeTask. ``working_directory`` defaults to the
        # project's ``working_directory`` when bound (#65), falling back
        # to the legacy ``"."`` for ad-hoc runs.
        task = RuntimeTask(
            execution_id=execution_id,
            prompt_xml=prompt_xml,
            working_directory=ctx.project_working_directory or ".",
            timeout_ms=ctx.timeout_ms,
            runtime_config=ctx.merged_runtime_config,
            project_env_vars=ctx.project_env_vars,
        )

        # 3. Call adapter
        adapter = ctx.registry.get(ctx.agent_version.runtime_id)
        result: RuntimeResult = await adapter.execute(task)
        ended_at = datetime.now(UTC)

        # 4. Record execution log + snapshot
        _save_execution_log(
            ctx=ctx,
            execution_id=execution_id,
            started_at=started_at,
            ended_at=ended_at,
            prompt_xml=prompt_xml,
            result=result,
        )

        if not result.success:
            error_msg = "; ".join(result.errors) if result.errors else "Unknown error"
            state_diff: dict[str, Any] = {
                "final_status": "failed",
                "verification_reason": f"Node {ctx.node_id} failed: {error_msg}",
            }
        else:
            # Two paths into state, both safe to combine:
            # 1. Adapter-supplied structured fields (e.g. bash exit_code,
            #    api-call usage) — only keys that match PipelineState are
            #    kept; the rest are stored on the execution log only.
            # 2. Per-role parsing of result.output — turns raw LLM text
            #    into a typed state diff for known roles. Wins over (1)
            #    on conflicts since it reflects the agent's intentional
            #    response, not adapter telemetry.
            state_diff = _merge_structured_into_state(result.structured)
            parsed_diff = _parse_agent_output(ctx, result.output)
            state_diff.update(parsed_diff)
            state_diff = _route_extensions(state, state_diff)

        # Save snapshot AFTER computing diff (snapshot reflects state going forward)
        merged_state = state.model_copy(update=state_diff)
        _save_snapshot(ctx=ctx, state=merged_state)
        ctx.session.flush()

        if result.pause_requested:
            raise PauseRequestedError(
                f"Node {ctx.node_id} requested pause via __pause sentinel"
            )

        return state_diff

    return node_fn


def _record_failure(
    *,
    ctx: NodeContext,
    started_at: datetime,
    execution_id: str,
    prompt_xml: str,
    error: str,
    state: PipelineState,
) -> dict[str, Any]:
    ended_at = datetime.now(UTC)
    log = NodeExecutionLogORM(
        id=execution_id,
        run_id=ctx.run_id,
        node_id=ctx.node_id,
        agent_id=ctx.agent.id,
        runtime_id=ctx.agent_version.runtime_id,
        started_at=started_at,
        ended_at=ended_at,
        prompt_xml=prompt_xml,
        stdout="",
        stderr="",
        output_json=None,
        tokens_used=0,
        cost_usd=0.0,
        duration_ms=int((ended_at - started_at).total_seconds() * 1000),
        status="failed",
        error_message=error,
        extra_data=None,
    )
    ctx.session.add(log)
    state_diff = {
        "final_status": "failed",
        "verification_reason": f"Node {ctx.node_id}: {error}",
    }
    _save_snapshot(ctx=ctx, state=state.model_copy(update=state_diff))
    ctx.session.flush()
    logger.warning("node %s failed: %s", ctx.node_id, error)
    return state_diff


def _save_execution_log(
    *,
    ctx: NodeContext,
    execution_id: str,
    started_at: datetime,
    ended_at: datetime,
    prompt_xml: str,
    result: RuntimeResult,
) -> None:
    log = NodeExecutionLogORM(
        id=execution_id,
        run_id=ctx.run_id,
        node_id=ctx.node_id,
        agent_id=ctx.agent.id,
        runtime_id=ctx.agent_version.runtime_id,
        started_at=started_at,
        ended_at=ended_at,
        prompt_xml=prompt_xml,
        stdout=result.output,
        stderr="",
        output_json=result.structured,
        tokens_used=result.tokens_used or 0,
        cost_usd=result.cost_usd or 0.0,
        duration_ms=result.duration_ms,
        status="success" if result.success else "failed",
        error_message="; ".join(result.errors) if result.errors else None,
        # ``extra_data`` stores domain-specific audit metadata produced by
        # python-func adapters.  A python-func node signals audit data by
        # including an ``__audit`` key in its return dict; the adapter moves
        # that dict to ``result.structured["audit"]`` before returning.
        # CLI/LLM adapters never set "audit", so this evaluates to ``None``
        # for those runtimes.  The shape is intentionally open — see
        # ``NodeExecutionLog.extra_data`` for documented common keys.
        extra_data=result.structured.get("audit") if result.structured else None,
    )
    ctx.session.add(log)


def _save_snapshot(*, ctx: NodeContext, state: PipelineState) -> None:
    snapshot = StateSnapshotORM(
        id=str(uuid.uuid4()),
        run_id=ctx.run_id,
        node_id=ctx.node_id,
        timestamp=datetime.now(UTC),
        state=state.model_dump(mode="json"),
    )
    ctx.session.add(snapshot)


def _merge_structured_into_state(structured: dict[str, Any] | None) -> dict[str, Any]:
    """Merge adapter's structured output into state diff.

    Only keys that exist in PipelineState are merged. Foreign keys are ignored.
    Permissive — strict per-agent validation lives in `_parse_agent_output`.
    """
    if not structured:
        return {}
    state_fields = set(PipelineState.model_fields.keys())
    return {k: v for k, v in structured.items() if k in state_fields}


def _route_extensions(state: PipelineState, diff: dict[str, Any]) -> dict[str, Any]:
    """Route unknown keys in *diff* into ``extensions`` instead of rejecting them.

    Known top-level keys are passed through unchanged.  Unknown keys (those not
    in ``PipelineState.model_fields``) are merged into the existing
    ``state.extensions`` dict so callers don't need to know the field name.
    The sentinel key ``__audit`` is silently dropped here — it has already
    been extracted into ``NodeExecutionLogORM.extra_data`` by
    ``_save_execution_log`` and must not propagate into pipeline state.
    """
    known = set(PipelineState.model_fields.keys())
    top_level = {k: v for k, v in diff.items() if k in known}
    extra = {k: v for k, v in diff.items() if k not in known and k not in {"__audit", "__pause"}}
    if not extra:
        return top_level
    return {
        **top_level,
        "extensions": {**state.extensions, **extra},
    }


def _parse_agent_output(ctx: NodeContext, output: str) -> dict[str, Any]:
    """Apply per-agent output parsing when the agent declares a schema.

    Resolves via :func:`parse_node_output`: the version's
    ``output_schema`` wins; falls back to ``ROLE_FIELDS[agent.role]``
    for legacy agents with no declared schema. Parse failures are
    logged but don't fail the run — the user can inspect the node log
    and use retry-node / skip-node to recover (issue #33). The
    resulting diff overrides adapter-supplied structured fields on
    conflict so an agent's intentional response wins over telemetry.

    Legacy DB rows may still hold a dict-shaped ``output_schema`` (the
    pre-v0.5 placeholder format). Coerce those back to ``[]`` here so
    ``list(dict)`` (= dict keys) doesn't accidentally land in the
    parser as a per-agent contract.
    """
    raw_schema = ctx.agent_version.output_schema
    output_schema = (
        list(raw_schema) if isinstance(raw_schema, list) else []
    )  # legacy dict / None / anything else → fall back to ROLE_FIELDS
    parse_result = parse_node_output(
        ctx.agent.role,
        output_schema,
        output,
    )
    if parse_result.skipped:
        return {}
    if not parse_result.success:
        logger.warning(
            "node %s (role=%s) output parse failed: %s",
            ctx.node_id,
            ctx.agent.role,
            "; ".join(parse_result.errors),
        )
        return {}
    return parse_result.parsed
