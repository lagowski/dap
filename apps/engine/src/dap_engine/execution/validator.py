"""Pipeline DAG validator — pure + DB-aware checks before save.

Two layers:
1. Pydantic-level — handled by FastAPI on request parse (correct types,
   edge condition union, etc.)
2. Semantic — this module: agent existence, entry point, reachability,
   ambiguous routing, per-agent input/output cohesion (#59).

Run via `validate_pipeline_dag(payload, session)`. Used by Pipeline Designer
(F7) to surface errors before save.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from graphlib import CycleError, TopologicalSorter
from typing import Any, Final

from dap_types import PipelineState
from dap_types.pipeline import (
    ComparisonCondition,
    EdgeCondition,
    LogicalCondition,
    PipelineEdge,
    PipelineNode,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from dap_engine.contracts import PipelineCreate
from dap_engine.persistence.models import AgentORM, AgentVersionORM

START_SENTINEL: Final = "__start__"
END_SENTINEL: Final = "__end__"
SENTINEL_NODES: Final = frozenset({START_SENTINEL, END_SENTINEL})


def _load_referenced_agents(payload: PipelineCreate, session: Session) -> dict[str, AgentORM]:
    """One SELECT for every agent the pipeline references (#120 hot-path).

    ``validate_pipeline_dag`` runs on every save now, so per-agent
    ``session.get`` calls turned into N+1 round-trips on pipelines
    with many nodes. Single ``WHERE id IN (...)`` keeps the cost
    flat regardless of node count.
    """
    referenced = {n.agent_id for n in payload.nodes}
    if not referenced:
        return {}
    rows = session.scalars(
        select(AgentORM).where(AgentORM.id.in_(referenced)),
    ).all()
    return {agent.id: agent for agent in rows}


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

    # Cohesion checks (#59) need the structural graph + agent contracts.
    # Skipped silently when a structural problem already broke the run.
    if not errors:
        contracts = _load_agent_contracts(payload, session)
        errors.extend(_check_input_contracts(payload, contracts))
        warnings.extend(_check_unused_outputs(payload, contracts))
        warnings.extend(_check_conflicting_writers(payload, contracts))

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
    agents = _load_referenced_agents(payload, session)
    for agent_id in sorted(referenced):
        agent = agents.get(agent_id)
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


# ---------------------------------------------------------------------------
# Cohesion checks (#59) — verify per-agent input/output contracts hang together
# across the pipeline graph.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _AgentContract:
    """Per-agent input/output schemas, normalised for cohesion analysis."""

    input_schema: tuple[str, ...]
    output_schema: tuple[str, ...]


def _load_agent_contracts(
    payload: PipelineCreate,
    session: Session,
) -> dict[str, _AgentContract]:
    """Build ``agent_id → contract`` for every agent referenced in the pipeline.

    Reads the *current* version of each agent. Agents that don't exist or
    are archived are skipped silently — those cases are reported by
    :func:`_check_agents` and we don't want to double-flag them here.

    Legacy dict-shaped schemas (pre-#58 rows) collapse to ``()`` so they
    contribute no constraint either way (matches the runtime fallback).

    Two batched SELECTs total (#120 hot-path): one for the agent rows,
    one for the matching version rows. Pipelines with N nodes used to
    cost ``N + 1`` round-trips; now it's a flat 2 regardless of N.
    """
    agents = _load_referenced_agents(payload, session)
    active_agents = {aid: agent for aid, agent in agents.items() if agent.archived_at is None}
    if not active_agents:
        return {}

    # Pull every candidate version row for the active agents in one
    # query, then index by ``(agent_id, version)`` so we can pick the
    # row matching each agent's ``current_version`` without another
    # round-trip. ``in_(active_agents)`` is fine even with one element.
    version_rows = session.scalars(
        select(AgentVersionORM).where(
            AgentVersionORM.agent_id.in_(active_agents),
        ),
    ).all()
    versions_by_key = {(v.agent_id, v.version): v for v in version_rows}

    out: dict[str, _AgentContract] = {}
    for agent_id, agent in active_agents.items():
        version = versions_by_key.get((agent_id, agent.current_version))
        if version is None:
            continue
        ins = version.input_schema if isinstance(version.input_schema, list) else []
        outs = version.output_schema if isinstance(version.output_schema, list) else []
        out[agent_id] = _AgentContract(
            input_schema=tuple(ins),
            output_schema=tuple(outs),
        )
    return out


# Sentinel "empty" values — a default of one of these means "field hasn't
# been populated yet", not "field has a meaningful initial value".
_TRIVIAL_DEFAULTS: Final[tuple[Any, ...]] = (None, False, 0, 0.0, "", [], {})


def _field_is_born_satisfied(field_name: str) -> bool:
    """A PipelineState field that doesn't need an upstream writer.

    Two ways a field is satisfied without any node writing it:

    1. **Required field** — no default at all → engine guarantees it
       comes from ``initial_state`` at run-time (or the run fails to
       create). ``run_id``, ``repo``, ``branch`` are the obvious
       examples.
    2. **Non-trivial default** — a meaningful initial value like
       ``max_attempts: int = 3`` or ``verification_status: "pending"``.

    Trivial defaults (``[]``, ``""``, ``False``, ``None``) are treated
    as "not yet populated" sentinels — those fields *do* need a writer.
    ``default_factory`` always means an empty container, also a sentinel.
    """
    info = PipelineState.model_fields.get(field_name)
    if info is None:
        return False  # unknown — caller already rejected it via field validators
    if info.is_required():
        return True
    if info.default_factory is not None:
        return False
    return info.default not in _TRIVIAL_DEFAULTS


def _build_dag_adjacency(
    payload: PipelineCreate,
    node_ids: set[str],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Forward + reverse adjacency over real (non-sentinel) nodes only."""
    adj: dict[str, set[str]] = {nid: set() for nid in node_ids}
    rev_adj: dict[str, set[str]] = {nid: set() for nid in node_ids}
    for edge in payload.edges:
        if edge.source in node_ids and edge.target in node_ids:
            adj[edge.source].add(edge.target)
            rev_adj[edge.target].add(edge.source)
    return adj, rev_adj


def _compute_dominators(
    payload: PipelineCreate,
    node_ids: set[str],
) -> dict[str, frozenset[str]] | None:
    """Compute the dominator set of every reachable node, or ``None`` on cycle.

    A node ``M`` *dominates* ``N`` iff every path from ``entry_point``
    to ``N`` passes through ``M``. Dominator analysis is the right tool
    for cohesion: a writer satisfies a reader iff the writer is on
    every path that reaches the reader (covers conditional branches
    conservatively — if only one branch writes the field, it doesn't
    dominate the merge).

    Returns ``None`` for cyclic graphs — strict dominator semantics
    don't translate cleanly, so the caller skips cohesion entirely
    rather than emit false positives on retry-loop pipelines.
    """
    _, rev_adj = _build_dag_adjacency(payload, node_ids)
    try:
        topo_order = list(TopologicalSorter(rev_adj).static_order())
    except CycleError:
        return None

    dom: dict[str, frozenset[str]] = {}
    for node in topo_order:
        preds = [p for p in rev_adj.get(node, set()) if p in dom]
        if not preds:
            dom[node] = frozenset({node})
        else:
            dom[node] = frozenset({node}) | frozenset.intersection(*(dom[p] for p in preds))
    return dom


def _compute_descendants(
    payload: PipelineCreate,
    node_ids: set[str],
) -> dict[str, set[str]]:
    """For each node, the set of nodes reachable from it (excluding itself)."""
    adj, _ = _build_dag_adjacency(payload, node_ids)
    out: dict[str, set[str]] = {}
    for nid in node_ids:
        visited: set[str] = set()
        queue: deque[str] = deque([nid])
        while queue:
            cur = queue.popleft()
            for nxt in adj.get(cur, set()):
                if nxt != nid and nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        out[nid] = visited
    return out


def _check_input_contracts(
    payload: PipelineCreate,
    contracts: dict[str, _AgentContract],
) -> list[str]:
    """Every declared input must be covered by writers on every path, or born satisfied.

    Semantics: a reader N is satisfied iff every path from
    ``entry_point`` to N passes through at least one node M with the
    field in ``output_schema``. Different writers on different paths
    are fine — as long as each path has *some* writer (covers diamond
    cases where parallel branches all happen to write the field).

    Strict dominator semantics ("one writer must be on every path")
    was too strict — it falsely flagged diamond merges as unsatisfied
    even when both branches wrote the field.
    """
    out: list[str] = []
    node_ids = {n.id for n in payload.nodes}
    if _compute_dominators(payload, node_ids) is None:
        return out  # cyclic graph — skip cohesion entirely

    adj, _ = _build_dag_adjacency(payload, node_ids)
    nodes_by_id = {n.id: n for n in payload.nodes}

    for node_id in sorted(node_ids):
        contract = contracts.get(nodes_by_id[node_id].agent_id)
        if contract is None:
            continue
        for field in contract.input_schema:
            if _field_is_born_satisfied(field):
                continue
            writers = _writer_ids_for(field, contracts, nodes_by_id, node_ids) - {node_id}
            if not _every_path_passes_writer(
                payload.entry_point,
                target=node_id,
                writers=writers,
                adj=adj,
            ):
                out.append(
                    f"Node '{node_id}' requires '{field}' but no upstream node writes it.",
                )
    return out


def _writer_ids_for(
    field: str,
    contracts: dict[str, _AgentContract],
    nodes_by_id: dict[str, PipelineNode],
    node_ids: set[str],
) -> frozenset[str]:
    """Return the set of node ids whose agent declares ``field`` as an output."""
    return frozenset(
        nid
        for nid in node_ids
        if (c := contracts.get(nodes_by_id[nid].agent_id)) is not None and field in c.output_schema
    )


def _every_path_passes_writer(
    entry: str,
    *,
    target: str,
    writers: frozenset[str],
    adj: dict[str, set[str]],
) -> bool:
    """True iff every path from ``entry`` to ``target`` includes a writer.

    Implementation: BFS from ``entry`` treating writer nodes as walls
    (we don't traverse INTO them — going through one means the field
    is set, so the path is covered). If ``target`` is reached without
    crossing a writer, that path is *uncovered* → return False.
    """
    if entry == target:
        # Reader is the entry point — no upstream writer can save it.
        return False
    if entry in writers:
        # Entry writes the field; every path from entry through its
        # successors carries the write forward.
        return True

    visited: set[str] = {entry}
    queue: deque[str] = deque([entry])
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, set()):
            if nxt == target:
                # Reached target via a path that didn't cross a writer.
                return False
            if nxt in writers:
                continue  # wall — paths through here are covered
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)
    return True


def _check_unused_outputs(
    payload: PipelineCreate,
    contracts: dict[str, _AgentContract],
) -> list[str]:
    """Warn when a declared output isn't read by any descendant — likely a wiring bug.

    Terminal nodes (no descendants in the real-node graph — their only
    successor is ``__end__`` or none) are skipped: their outputs are by
    design consumed only by the final PipelineState snapshot or external
    callers reading via ``/runs/{id}/state``. Warning on them produces
    noise on legitimate final-reporter agents (e.g. ``finalize``,
    ``pr_creator``) and trains operators to ignore the warning class. (#204)
    """
    out: list[str] = []
    node_ids = {n.id for n in payload.nodes}
    descendants = _compute_descendants(payload, node_ids)
    nodes_by_id = {n.id: n for n in payload.nodes}

    for node_id in sorted(node_ids):
        # Terminal node — no real-node descendants → nothing in-pipeline can
        # read its outputs, but that's by design for end-of-pipeline writers.
        if not descendants[node_id]:
            continue
        node = nodes_by_id[node_id]
        contract = contracts.get(node.agent_id)
        if contract is None:
            continue
        for field in contract.output_schema:
            if not _has_downstream_reader(field, descendants[node_id], nodes_by_id, contracts):
                out.append(
                    f"Node '{node_id}' writes '{field}' but no downstream node reads it.",
                )
    return out


def _has_downstream_reader(
    field: str,
    descendant_ids: set[str],
    nodes_by_id: dict[str, PipelineNode],
    contracts: dict[str, _AgentContract],
) -> bool:
    for desc_id in descendant_ids:
        node = nodes_by_id.get(desc_id)
        if node is None:
            continue
        contract = contracts.get(node.agent_id)
        if contract is None:
            continue
        if field in contract.input_schema:
            return True
    return False


def _check_conflicting_writers(
    payload: PipelineCreate,
    contracts: dict[str, _AgentContract],
) -> list[str]:
    """Warn when two parallel-branch nodes both declare the same output field.

    "Parallel" = neither node dominates the other, so at runtime LangGraph
    has to merge concurrent state diffs with no declared strategy —
    almost always a bug.
    """
    out: list[str] = []
    node_ids = {n.id for n in payload.nodes}
    dominators = _compute_dominators(payload, node_ids)
    if dominators is None:
        return out

    nodes_by_id = {n.id: n for n in payload.nodes}
    writers_by_field: dict[str, list[str]] = {}
    for node_id in node_ids:
        contract = contracts.get(nodes_by_id[node_id].agent_id)
        if contract is None:
            continue
        for field in contract.output_schema:
            writers_by_field.setdefault(field, []).append(node_id)

    for field in sorted(writers_by_field):
        writers = sorted(writers_by_field[field])
        # Need at least two writers to have a conflict.
        if len(writers) < 2:  # noqa: PLR2004 — "two" is a structural threshold, not a magic constant
            continue
        for i, a in enumerate(writers):
            conflict = next(
                (
                    b
                    for b in writers[i + 1 :]
                    if a not in dominators.get(b, frozenset())
                    and b not in dominators.get(a, frozenset())
                ),
                None,
            )
            if conflict is not None:
                out.append(
                    f"Field '{field}' is written by both '{a}' and '{conflict}' on parallel "
                    f"branches; merge order is undefined.",
                )
                break  # one warning per field is enough
    return out
