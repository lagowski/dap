"""Pipeline DAG validator — pure + DB-aware checks before save.

Two layers:
1. Pydantic-level — handled by FastAPI on request parse (correct types,
   edge condition union, etc.)
2. Semantic — this module: agent existence, entry point, reachability,
   ambiguous routing.

Run via `validate_pipeline_dag(payload, session)`. Used by Pipeline Designer
(F7) to surface errors before save.
"""

from __future__ import annotations

from collections import deque
from typing import Final

from dap_types import PipelineState
from dap_types.pipeline import (
    ComparisonCondition,
    EdgeCondition,
    LogicalCondition,
    PipelineEdge,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from dap_engine.api.schemas import PipelineCreate
from dap_engine.persistence.models import AgentORM

START_SENTINEL: Final = "__start__"
END_SENTINEL: Final = "__end__"
SENTINEL_NODES: Final = frozenset({START_SENTINEL, END_SENTINEL})


class ValidationResult(BaseModel):
    """Outcome of pipeline DAG validation."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def validate_pipeline_dag(payload: PipelineCreate, session: Session) -> ValidationResult:
    """Validate pipeline structure + references. Returns ValidationResult.

    Errors block save; warnings inform but allow save.
    """
    errors: list[str] = []
    warnings: list[str] = []

    node_id_set = {n.id for n in payload.nodes}

    errors.extend(_check_duplicate_ids(payload))
    errors.extend(_check_entry_point(payload, node_id_set))
    errors.extend(_check_edge_references(payload, node_id_set))
    errors.extend(_check_agents(payload, session))

    # If structural errors, skip reachability — graph is bogus.
    if errors:
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    errors.extend(_check_ambiguous_routing(payload))
    errors.extend(_check_reachability(payload))
    warnings.extend(_check_condition_fields(payload))

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _check_duplicate_ids(payload: PipelineCreate) -> list[str]:
    out: list[str] = []
    node_ids = [n.id for n in payload.nodes]
    if len(node_ids) != len(set(node_ids)):
        dups = sorted({nid for nid in node_ids if node_ids.count(nid) > 1})
        out.append(f"Duplicate node ids: {dups}")
    edge_ids = [e.id for e in payload.edges]
    if len(edge_ids) != len(set(edge_ids)):
        dups = sorted({eid for eid in edge_ids if edge_ids.count(eid) > 1})
        out.append(f"Duplicate edge ids: {dups}")
    return out


def _check_entry_point(payload: PipelineCreate, node_id_set: set[str]) -> list[str]:
    if payload.entry_point in node_id_set:
        return []
    return [
        f"entry_point '{payload.entry_point}' does not match any node.id "
        f"(known: {sorted(node_id_set)})",
    ]


def _check_edge_references(payload: PipelineCreate, node_id_set: set[str]) -> list[str]:
    out: list[str] = []
    valid_refs = node_id_set | SENTINEL_NODES
    for edge in payload.edges:
        if edge.source not in valid_refs:
            out.append(f"Edge '{edge.id}' source '{edge.source}' is not a known node")
        if edge.target not in valid_refs:
            out.append(f"Edge '{edge.id}' target '{edge.target}' is not a known node")
    return out


def _check_agents(payload: PipelineCreate, session: Session) -> list[str]:
    out: list[str] = []
    referenced = {n.agent_id for n in payload.nodes}
    for agent_id in sorted(referenced):
        agent = session.get(AgentORM, agent_id)
        if agent is None:
            out.append(f"Agent not found: '{agent_id}'")
        elif agent.archived_at is not None:
            out.append(f"Agent is archived: '{agent_id}'")
    return out


def _check_ambiguous_routing(payload: PipelineCreate) -> list[str]:
    out: list[str] = []
    edges_by_source: dict[str, list[PipelineEdge]] = {}
    for edge in payload.edges:
        edges_by_source.setdefault(edge.source, []).append(edge)
    for source, edges in edges_by_source.items():
        unconditional = [e for e in edges if e.condition is None]
        if len(unconditional) > 1:
            out.append(
                f"Source '{source}' has {len(unconditional)} unconditional edges — "
                f"ambiguous routing. Use exactly one unconditional edge or all conditional."
            )
    return out


def _check_reachability(payload: PipelineCreate) -> list[str]:
    out: list[str] = []
    forward_adj: dict[str, set[str]] = {}
    reverse_adj: dict[str, set[str]] = {}
    for edge in payload.edges:
        forward_adj.setdefault(edge.source, set()).add(edge.target)
        reverse_adj.setdefault(edge.target, set()).add(edge.source)

    # Implicit edge START → entry_point
    forward_adj.setdefault(START_SENTINEL, set()).add(payload.entry_point)
    reverse_adj.setdefault(payload.entry_point, set()).add(START_SENTINEL)

    reachable_from_start = _bfs(forward_adj, START_SENTINEL)
    reaches_end = _bfs(reverse_adj, END_SENTINEL)

    for node in payload.nodes:
        if node.id not in reachable_from_start:
            out.append(f"Node '{node.id}' is unreachable from START")
        if node.id not in reaches_end:
            out.append(f"Node '{node.id}' has no path to END")
    return out


def _check_condition_fields(payload: PipelineCreate) -> list[str]:
    out: list[str] = []
    state_fields = set(PipelineState.model_fields.keys())
    for edge in payload.edges:
        if edge.condition is not None:
            unknown = _collect_unknown_fields(edge.condition, state_fields)
            for field in sorted(unknown):
                out.append(
                    f"Edge '{edge.id}' references unknown PipelineState field: '{field}'",
                )
    return out


def _bfs(adjacency: dict[str, set[str]], start: str) -> set[str]:
    """Return set of nodes reachable from `start` in the given adjacency map."""
    visited = {start}
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency.get(current, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return visited


def _collect_unknown_fields(
    condition: EdgeCondition,
    known_fields: set[str],
) -> set[str]:
    """Walk the condition tree and return field names not in known_fields."""
    unknown: set[str] = set()
    if isinstance(condition, ComparisonCondition):
        if condition.field not in known_fields:
            unknown.add(condition.field)
    elif isinstance(condition, LogicalCondition):
        for child in condition.children:
            unknown |= _collect_unknown_fields(child, known_fields)
    return unknown
