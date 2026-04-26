"""PipelineRunner — converts a Pipeline JSON into a LangGraph StateGraph and runs it.

Optional `checkpointer` enables LangGraph state persistence — required for
pause/resume. When supplied, `run_id` doubles as the LangGraph thread_id.
"""

from __future__ import annotations

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

        graph = self._build_graph(
            run_id=run_id,
            pipeline=pipeline,
            agent_lookup=agent_lookup,
        )

        config: dict[str, Any] = {"recursion_limit": self.recursion_limit}
        if self.checkpointer is not None:
            config["configurable"] = {"thread_id": run_id}

        invoke_input: PipelineState | None = None if resume else initial_state

        try:
            result = await graph.ainvoke(invoke_input, config=config)
        except Exception as exc:
            logger.exception("pipeline execution failed for run %s", run_id)
            msg = f"Execution failed: {type(exc).__name__}: {exc}"
            raise RunnerError(msg) from exc

        return PipelineState.model_validate(result)

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
        graph = self._build_graph(
            run_id=run_id,
            pipeline=pipeline,
            agent_lookup=agent_lookup,
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

        return builder.compile(checkpointer=self.checkpointer)

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
