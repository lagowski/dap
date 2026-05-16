"""PipelineRunner — converts a Pipeline JSON into a LangGraph StateGraph and runs it.

Optional `checkpointer` enables LangGraph state persistence — required for
pause/resume. When supplied, `run_id` doubles as the LangGraph thread_id.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

from dap_runtimes import RuntimeRegistry
from dap_types import Pipeline, PipelineState
from dap_types.pipeline import PipelineEdge
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from dap_engine.execution.conditions import evaluate_condition
from dap_engine.execution.node_executor import NodeContext, make_node_fn
from dap_engine.persistence.models import (
    AgentORM,
    AgentVersionORM,
    PipelineORM,
    PipelineVersionORM,
    ProjectORM,
    RunORM,
)

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger("dap.engine.execution.runner")

DEFAULT_RECURSION_LIMIT = 50

# Mode for rewind_and_run() — see method docstring.
REWIND_RETRY = "retry"
REWIND_SKIP = "skip"


class RunnerError(Exception):
    """Raised when pipeline cannot be built or executed."""


class CheckpointNotFoundError(RunnerError):
    """Raised when no checkpoint matches the requested rewind target."""


class RunnerInterrupt(Exception):
    """Raised when the graph pauses at an approval-required node (interrupt_before).

    This is a *normal* stop, not an error — the run should be marked as
    ``paused`` and resumed later via ``POST /runs/{id}/resume`` or the
    semantically clearer ``POST /runs/{id}/nodes/{node_id}/approve``.

    Attributes:
        next_nodes: LangGraph nodes staged as next when the interrupt fired.
        gate_state: Full graph state values at the interrupt checkpoint.
            Stored on the Run row so the dashboard can surface gate context
            (e.g. task_assignments) without querying the checkpoint store (#364).
    """

    def __init__(
        self,
        next_nodes: list[str],
        gate_state: dict[str, Any] | None = None,
    ) -> None:
        self.next_nodes = next_nodes
        self.gate_state = gate_state
        super().__init__(f"Pipeline paused before: {next_nodes}")


@dataclasses.dataclass(frozen=True)
class _ProjectContext:
    """Project context propagated into NodeContext (#65).

    Captured once at run start so the same values flow through every
    node — avoids re-querying the project for each node and keeps
    behaviour stable if the project is edited mid-run.
    """

    working_directory: str | None
    env_vars: dict[str, str]


class PipelineRunner:
    """Build a LangGraph StateGraph from a pipeline JSON and execute it."""

    def __init__(
        self,
        *,
        session: Session,
        registry: RuntimeRegistry,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        recursion_limit: int = DEFAULT_RECURSION_LIMIT,
    ) -> None:
        self.session = session
        self.registry = registry
        self.checkpointer = checkpointer
        self.recursion_limit = recursion_limit

    async def run(
        self,
        *,
        run_id: str,
        pipeline_orm: PipelineORM,
        pipeline_version_orm: PipelineVersionORM,
        initial_state: PipelineState | None,
        resume: bool = False,
    ) -> PipelineState:
        """Build graph for the given pipeline version + run end-to-end.

        Returns the final PipelineState. Persists snapshots + node logs along
        the way via the bound Session (caller commits).

        When `resume=True`, `initial_state` is ignored and LangGraph resumes
        from the checkpointed state for the given `run_id` (used as thread_id).
        Requires a checkpointer to be configured.
        """
        if resume and self.checkpointer is None:
            msg = "Cannot resume without a checkpointer"
            raise RunnerError(msg)
        if not resume and initial_state is None:
            msg = "initial_state required when resume=False"
            raise RunnerError(msg)

        pipeline = self._pipeline_from_orm(pipeline_orm, pipeline_version_orm)

        # Pre-load agents referenced by the pipeline + verify they exist.
        agent_lookup = self._load_agents(pipeline)

        # Project context (#65) — working_directory + env_vars overlay.
        # Bails out with a descriptive RunnerError when the bound project
        # has been archived between trigger and execution (e.g. resumed
        # paused run after the user archived the project).
        project_context = self._load_project_context(run_id)

        graph = self._build_graph(
            run_id=run_id,
            pipeline=pipeline,
            agent_lookup=agent_lookup,
            project_context=project_context,
        )

        config: dict[str, Any] = {"recursion_limit": self.recursion_limit}
        if self.checkpointer is not None:
            config["configurable"] = {"thread_id": run_id}

        invoke_input: PipelineState | None = None if resume else initial_state

        approval_nodes = set(pipeline.defaults.approval_required_nodes)

        try:
            result = await graph.ainvoke(invoke_input, config=config)
        except Exception as exc:
            logger.exception("pipeline execution failed for run %s", run_id)
            msg = f"Execution failed: {type(exc).__name__}: {exc}"
            raise RunnerError(msg) from exc

        # Detect interrupt_before pause: when approval_required_nodes is configured
        # and the graph stopped early, aget_state().next contains the gated node.
        # This is a normal stop — raise RunnerInterrupt so the caller marks the
        # run as paused rather than finalized (#164).
        #
        # Exception (#389): when the Run row carries
        # ``initial_state.extensions.auto_approve = True``, drive the graph
        # past each gate by re-invoking with ``None`` until no approval
        # node is pending. The gate node code still executes — we only
        # suppress the interrupt-before pause. The auto-approve resume
        # loop has defensive caps (see ``_drive_auto_approve_loop``) so
        # a misconfigured graph can't spin the background task forever.
        if approval_nodes and self.checkpointer is not None:
            # Pin the auto-approve decision *once* — read from the Run row's
            # persisted ``initial_state``, not the LangGraph snapshot, so
            # a mid-run state_delta can't flip the flag (see
            # ``_read_auto_approve_from_run``). Lazy: only evaluated when
            # the pipeline can actually pause, keeping the no-gate hot
            # path free of an extra DB read.
            auto_approve = _read_auto_approve_from_run(self.session, run_id)
            result = await self._drive_auto_approve_loop(
                graph=graph,
                run_id=run_id,
                invoke_config=config,
                approval_nodes=approval_nodes,
                auto_approve=auto_approve,
                last_result=result,
            )

        return PipelineState.model_validate(result)

    async def _drive_auto_approve_loop(
        self,
        *,
        graph: Any,
        run_id: str,
        invoke_config: dict[str, Any],
        approval_nodes: set[str],
        auto_approve: bool,
        last_result: Any,
    ) -> Any:
        """Resolve a graph that may have paused at an approval gate (#389).

        Three exit paths:

        - **Naturally exits** when no pending node is in ``approval_nodes``
          — returns the most recent ``ainvoke`` result unchanged.
        - **Raises ``RunnerInterrupt``** (normal pause path) when at
          least one approval node is pending and ``auto_approve`` is
          False. The orchestrator catches this and marks the run paused.
        - **Raises ``RunnerError``** (defensive caps) when auto-approve
          is enabled but the graph fails to advance: either the same
          set of pending nodes appears two iterations in a row (genuine
          no-progress) or the loop exceeds ``max(2 * |approval_nodes|, 8)``
          iterations (a cyclic / over-gated graph). Both signal a
          misconfiguration the operator must fix; failing fast is
          safer than burning CPU on an infinite resume.

        Extracted from ``run()`` so the cap logic is unit-testable
        without spinning up a full LangGraph + DB stack (Copilot
        review on PR #436).
        """
        checkpoint_config: dict[str, Any] = {"configurable": {"thread_id": run_id}}
        snap = await graph.aget_state(checkpoint_config)
        pending = list(snap.next) if snap.next else []

        # Each ``ainvoke(None, ...)`` resets LangGraph's recursion_limit
        # budget, so an upper bound on resume iterations is the only
        # thing standing between a misconfigured graph and an infinite
        # background loop. ``len(approval_nodes) * 2`` allows healthy
        # pipelines two passes per gate before bailing; the floor of 8
        # covers single-gate pipelines that might legitimately churn a
        # few times during a complex resume.
        max_resume_iterations = max(len(approval_nodes) * 2, 8)
        iterations = 0
        previous_pending: list[str] | None = None
        result = last_result

        while pending and any(n in approval_nodes for n in pending):
            if not auto_approve:
                logger.info("run %s interrupted before approval node(s): %s", run_id, pending)
                raise RunnerInterrupt(
                    next_nodes=pending,
                    gate_state=snap.values if isinstance(snap.values, dict) else None,
                )

            if previous_pending is not None and pending == previous_pending:
                msg = (
                    f"auto-approve resume made no progress past nodes {pending} "
                    f"for run {run_id} — likely cyclic or misconfigured "
                    "approval_required_nodes."
                )
                logger.error(msg)
                raise RunnerError(msg)

            if iterations >= max_resume_iterations:
                msg = (
                    f"auto-approve exceeded {max_resume_iterations} resume "
                    f"iterations for run {run_id} (still pending: {pending}) "
                    "— refusing to spin further; check pipeline "
                    "approval_required_nodes for cycles."
                )
                logger.error(msg)
                raise RunnerError(msg)

            logger.warning(
                "run %s: auto_approve=True — skipping approval gate(s) %s",
                run_id,
                pending,
            )
            previous_pending = pending
            iterations += 1
            try:
                result = await graph.ainvoke(None, config=invoke_config)
            except Exception as exc:
                logger.exception(
                    "pipeline execution failed for run %s during auto-approve resume",
                    run_id,
                )
                msg = f"Execution failed: {type(exc).__name__}: {exc}"
                raise RunnerError(msg) from exc
            snap = await graph.aget_state(checkpoint_config)
            pending = list(snap.next) if snap.next else []

        return result

    async def rewind_and_run(
        self,
        *,
        run_id: str,
        pipeline_orm: PipelineORM,
        pipeline_version_orm: PipelineVersionORM,
        target_node: str,
        mode: str,
    ) -> PipelineState:
        """Rewind the graph to just before `target_node`, then resume.

        - ``mode="retry"``: rewind so the next step is `target_node`; resume
          re-executes that node.
        - ``mode="skip"``: pretend `target_node` ran with no state changes;
          resume proceeds to its downstream successors.

        Both modes require a checkpointer and a prior execution that reached
        the target node. Raises ``CheckpointNotFoundError`` when no historical
        checkpoint has the target node staged as next.
        """
        if self.checkpointer is None:
            msg = "Cannot rewind without a checkpointer"
            raise RunnerError(msg)
        if mode not in {REWIND_RETRY, REWIND_SKIP}:
            msg = f"Unknown rewind mode: {mode}"
            raise RunnerError(msg)

        pipeline = self._pipeline_from_orm(pipeline_orm, pipeline_version_orm)
        agent_lookup = self._load_agents(pipeline)
        project_context = self._load_project_context(run_id)
        graph = self._build_graph(
            run_id=run_id,
            pipeline=pipeline,
            agent_lookup=agent_lookup,
            project_context=project_context,
        )

        base_config: dict[str, Any] = {"configurable": {"thread_id": run_id}}

        # Find the most recent checkpoint where `target_node` is the next
        # step to execute. aget_state_history yields newest-first.
        target_config: dict[str, Any] | None = None
        async for snapshot in graph.aget_state_history(base_config):
            if target_node in (snapshot.next or ()):
                target_config = snapshot.config
                break

        if target_config is None:
            msg = (
                f"No checkpoint found where '{target_node}' is staged as next. "
                f"The node may not have been reached during prior execution."
            )
            raise CheckpointNotFoundError(msg)

        # Branch from the historical checkpoint: aupdate_state writes a new
        # checkpoint and returns its config; subsequent ainvoke runs from it.
        if mode == REWIND_SKIP:
            new_config = await graph.aupdate_state(target_config, values={}, as_node=target_node)
        else:  # retry
            # values=None + no as_node → no logical change, but we still get
            # a new branch tip from which the target node will run again.
            new_config = await graph.aupdate_state(target_config, values=None)

        invoke_config: dict[str, Any] = {"recursion_limit": self.recursion_limit}
        invoke_config["configurable"] = new_config["configurable"]

        try:
            result = await graph.ainvoke(None, config=invoke_config)
        except Exception as exc:
            logger.exception("pipeline rewind/run failed for run %s", run_id)
            msg = f"Execution failed: {type(exc).__name__}: {exc}"
            raise RunnerError(msg) from exc

        return PipelineState.model_validate(result)

    # -----------------------------------------------------------------------
    # Graph construction
    # -----------------------------------------------------------------------

    def _build_graph(
        self,
        *,
        run_id: str,
        pipeline: Pipeline,
        agent_lookup: dict[str, tuple[AgentORM, AgentVersionORM]],
        project_context: _ProjectContext | None = None,
    ) -> Any:
        builder = StateGraph(PipelineState)

        for node in pipeline.nodes:
            agent, version = agent_lookup[node.agent_id]
            ctx = NodeContext(
                run_id=run_id,
                node_id=node.id,
                agent=agent,
                agent_version=version,
                registry=self.registry,
                session=self.session,
                runtime_overrides=(node.overrides.runtime_config if node.overrides else None),
                timeout_override_ms=(node.overrides.timeout_ms if node.overrides else None),
                project_working_directory=(
                    project_context.working_directory if project_context else None
                ),
                project_env_vars=(dict(project_context.env_vars) if project_context else None),
            )
            builder.add_node(node.id, make_node_fn(ctx))  # type: ignore[call-overload]

        # Entry edge
        if pipeline.entry_point not in {n.id for n in pipeline.nodes}:
            msg = f"entry_point '{pipeline.entry_point}' not in pipeline.nodes"
            raise RunnerError(msg)
        builder.add_edge(START, pipeline.entry_point)

        # Group edges by source node, then add either unconditional or conditional.
        edges_by_source: dict[str, list[PipelineEdge]] = {}
        for edge in pipeline.edges:
            edges_by_source.setdefault(edge.source, []).append(edge)

        for source, edges in edges_by_source.items():
            if source in (START, "__start__"):
                continue  # entry edge already handled above
            if all(e.condition is None for e in edges):
                # Single straight edge per source. Multiple unconditional edges
                # would be ambiguous — pick the first.
                target = _resolve_target(edges[0].target)
                builder.add_edge(source, target)
            else:
                _add_conditional_edges(builder, source, edges)

        # approval_required_nodes drives interrupt_before so LangGraph pauses
        # automatically before those nodes. RunnerInterrupt is raised in run()
        # when the graph stops early at one of these gates (#164).
        interrupt_nodes = list(pipeline.defaults.approval_required_nodes)
        return builder.compile(
            checkpointer=self.checkpointer,
            interrupt_before=interrupt_nodes or [],
        )

    def _load_project_context(self, run_id: str) -> _ProjectContext | None:
        """Resolve the run's project, if any, into a ``_ProjectContext``.

        - Returns ``None`` for ad-hoc runs (no ``project_id``) — keeps
          legacy behaviour with ``working_directory="."`` and no env
          overlay.
        - Raises ``RunnerError`` if the bound project has been
          archived between trigger and execution. The trigger
          endpoint already 422s archived projects (#64), but a paused
          run resumed after the project was archived would still
          land here.
        """
        run = self.session.get(RunORM, run_id)
        if run is None or run.project_id is None:
            return None
        project = self.session.get(ProjectORM, run.project_id)
        if project is None:
            msg = f"Project not found: {run.project_id}"
            raise RunnerError(msg)
        if project.archived_at is not None:
            msg = f"Project is archived: {run.project_id}"
            raise RunnerError(msg)
        return _ProjectContext(
            working_directory=project.working_directory,
            env_vars=dict(project.env_vars),
        )

    # -----------------------------------------------------------------------
    # ORM helpers
    # -----------------------------------------------------------------------

    def _pipeline_from_orm(
        self,
        pipeline_orm: PipelineORM,
        version_orm: PipelineVersionORM,
    ) -> Pipeline:
        from dap_types.pipeline import (  # noqa: PLC0415 — break circular pretty-fmt
            PipelineDefaults,
            PipelineEdge,
            PipelineNode,
        )

        return Pipeline(
            id=pipeline_orm.id,
            name=pipeline_orm.name,
            description=pipeline_orm.description,
            version=version_orm.version,
            schema_version=version_orm.schema_version,  # type: ignore[arg-type]
            state_schema_ref=version_orm.state_schema_ref,
            entry_point=version_orm.entry_point,
            nodes=[PipelineNode.model_validate(n) for n in version_orm.nodes],
            edges=[PipelineEdge.model_validate(e) for e in version_orm.edges],
            defaults=PipelineDefaults.model_validate(version_orm.defaults),
            created_at=pipeline_orm.created_at,
            updated_at=pipeline_orm.updated_at,
            is_active=pipeline_orm.archived_at is None,
        )

    def _load_agents(
        self,
        pipeline: Pipeline,
    ) -> dict[str, tuple[AgentORM, AgentVersionORM]]:
        lookup: dict[str, tuple[AgentORM, AgentVersionORM]] = {}
        for node in pipeline.nodes:
            agent_id = node.agent_id
            if agent_id in lookup:
                continue
            agent = self.session.get(AgentORM, agent_id)
            if agent is None:
                msg = f"Pipeline references unknown agent: {agent_id}"
                raise RunnerError(msg)
            version_orm = self.session.scalar(
                select(AgentVersionORM)
                .where(AgentVersionORM.agent_id == agent_id)
                .where(AgentVersionORM.version == agent.current_version)
            )
            if version_orm is None:
                msg = (
                    f"Agent {agent_id} has current_version={agent.current_version} "
                    f"but no version row found"
                )
                raise RunnerError(msg)
            lookup[agent_id] = (agent, version_orm)
        return lookup


def _resolve_target(target: str) -> Any:
    """Map sentinel '__end__' / END to LangGraph END constant."""
    if target in (END, "__end__"):
        return END
    return target


def _read_auto_approve_from_run(session: Session, run_id: str) -> bool:
    """Return True iff the Run row's persisted ``initial_state.extensions
    .auto_approve`` is the literal boolean ``True`` (#389).

    Why the Run row, not the LangGraph snapshot:
        ``snap.values.extensions`` is part of the mutable pipeline state.
        Node executors merge arbitrary keys into ``extensions`` via
        ``state_delta`` (see ``_route_extensions`` in
        ``node_executor.py``), so a node — buggy, malicious, or simply
        templated by an attacker-controlled prompt — could flip
        ``auto_approve = True`` mid-run and silently bypass every
        downstream gate. The ``run.triggered`` audit log wouldn't
        capture it either, since that's already been written.

        The Run row's ``initial_state`` is persisted at trigger time and
        never touched again. Reading from there pins auto-approve as a
        trigger-time, operator-controlled decision for the whole run
        (and across resume).

    Defensive against malformed rows — only the literal boolean ``True``
    enables the bypass; other truthy values (strings, numbers, nested
    dicts) deliberately do *not* trigger it.
    """
    run = session.get(RunORM, run_id)
    if run is None:
        return False
    initial_state = run.initial_state
    if not isinstance(initial_state, dict):
        return False
    extensions = initial_state.get("extensions")
    if not isinstance(extensions, dict):
        return False
    return extensions.get("auto_approve") is True


def _add_conditional_edges(
    builder: StateGraph[PipelineState, None, PipelineState, PipelineState],
    source: str,
    edges: list[PipelineEdge],
) -> None:
    """Wire conditional edges from `source` based on EdgeCondition matching."""

    # Pre-extract for closure capture.
    edge_specs = [(e.condition, _resolve_target(e.target)) for e in edges]

    def chooser(state: PipelineState) -> str:
        """Return target node id (or END sentinel) for current state."""
        for cond, target in edge_specs:
            if cond is None or evaluate_condition(cond, state):
                return str(target)
        return str(END)

    # LangGraph's add_conditional_edges needs a path_map. Build the union of
    # possible targets so the framework knows the node graph topology.
    targets = {target for _, target in edge_specs}
    targets.add(END)
    path_map = {t: t for t in targets}

    builder.add_conditional_edges(source, chooser, path_map)
