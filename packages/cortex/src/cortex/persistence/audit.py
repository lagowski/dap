"""Audit logging layer for Cortex pipeline events.

Every LLM call, issue mutation, git operation, and run lifecycle event
gets recorded in the cortex_* PostgreSQL tables. This module provides
the write interface; the schema is defined in schema.sql.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import psycopg

from cortex.config.settings import load_settings

logger = logging.getLogger(__name__)


def _get_connection() -> psycopg.Connection:
    """Create a new database connection from settings."""
    settings = load_settings()
    return psycopg.connect(settings.database_url, autocommit=True)


# --- Run lifecycle ---


def log_run_start(
    issue_url: str,
    repo: str,
    issue_number: int,
    thread_id: str,
    project_id: str | None = None,
) -> str:
    """Create a new run record. Returns the run_id (UUID as string)."""
    with _get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO cortex_runs (issue_url, repo, issue_number, thread_id, status, project_id)
            VALUES (%s, %s, %s, %s, 'running', %s)
            RETURNING id
            """,
            (issue_url, repo, issue_number, thread_id, project_id),
        ).fetchone()
    return str(row[0])


def log_run_finish(
    run_id: str,
    status: str,
    error: str | None = None,
) -> None:
    """Mark a run as finished with final status and optional error."""
    with _get_connection() as conn:
        conn.execute(
            """
            UPDATE cortex_runs
            SET status = %s,
                finished_at = %s,
                error = %s
            WHERE id = %s
            """,
            (status, datetime.now(UTC), error, run_id),
        )


# --- LLM decisions ---


def log_decision(
    run_id: str,
    agent: str,
    backend: str,
    model: str,
    phase: str,
    system_prompt: str,
    user_prompt: str,
    response: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    duration_ms: int,
    project_id: str | None = None,
) -> str:
    """Log an LLM call. Returns the decision_id."""
    with _get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO cortex_decisions
                (run_id, agent, backend, model, phase,
                 system_prompt, user_prompt, response,
                 input_tokens, output_tokens, cost_usd, duration_ms,
                 project_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                run_id,
                agent,
                backend,
                model,
                phase,
                system_prompt,
                user_prompt,
                response,
                input_tokens,
                output_tokens,
                cost_usd,
                duration_ms,
                project_id,
            ),
        ).fetchone()
    return str(row[0])


# --- Issue updates ---


def log_issue_update(
    run_id: str,
    agent: str,
    issue_number: int,
    section: str,
    content_before: str,
    content_after: str,
    github_comment_url: str | None = None,
) -> str:
    """Log an agent's mutation to the issue body. Returns the update_id."""
    with _get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO cortex_issue_updates
                (run_id, agent, issue_number, section,
                 content_before, content_after, github_comment_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                run_id,
                agent,
                issue_number,
                section,
                content_before,
                content_after,
                github_comment_url,
            ),
        ).fetchone()
    return str(row[0])


# --- Git operations ---


def log_git_op(
    run_id: str,
    agent: str,
    operation: str,
    repo: str,
    branch: str | None = None,
    commit_sha: str | None = None,
    pr_number: int | None = None,
    github_user: str | None = None,
    details: dict | None = None,
) -> str:
    """Log a git operation (branch, commit, PR). Returns the op_id."""
    with _get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO cortex_git_ops
                (run_id, agent, operation, repo, branch,
                 commit_sha, pr_number, github_user, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (
                run_id,
                agent,
                operation,
                repo,
                branch,
                commit_sha,
                pr_number,
                github_user,
                psycopg.types.json.Json(details) if details else None,
            ),
        ).fetchone()
    return str(row[0])


# --- Agent registry ---


def register_agent(
    name: str,
    backend: str,
    model: str,
    token_role: str,
) -> None:
    """Register or update an agent in the registry."""
    with _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO cortex_agents (name, backend, model, github_token_role, project_id)
            VALUES (
                %s, %s, %s, %s,
                (SELECT id FROM cortex_projects WHERE repo = 'default' LIMIT 1)
            )
            ON CONFLICT (name, project_id) DO UPDATE
            SET backend = EXCLUDED.backend,
                model = EXCLUDED.model,
                github_token_role = EXCLUDED.github_token_role
            """,
            (name, backend, model, token_role),
        )


# --- Read queries ---


_TERMINAL_STATUSES = ("completed", "rejected", "failed", "cancelled")


def cancel_thread_runs(
    thread_id: str,
    reason: str = "reset by operator",
    status: str = "cancelled",
    project_id: str | None = None,
) -> int:
    """Mark any non-terminal runs for ``thread_id`` with a terminal ``status``.

    Used by ``cortex reset`` (issue #106) — default ``status='cancelled'`` —
    and by ``cortex retry`` (issue #179) — ``status='rejected'`` so the row
    matches ``consume_pending_feedback``'s filter and the operator's
    feedback survives into the next run. Returns the number of rows
    updated; 0 means there was nothing active to cancel.
    """
    extra_clause = " AND project_id = %s" if project_id is not None else ""
    with _get_connection() as conn:
        params: list = [status, datetime.now(UTC), reason, thread_id, list(_TERMINAL_STATUSES)]
        if project_id is not None:
            params.append(project_id)
        result = conn.execute(
            f"""
            UPDATE cortex_runs
            SET status = %s,
                finished_at = %s,
                error = %s
            WHERE thread_id = %s
              AND NOT (status = ANY(%s)){extra_clause}
            """,
            params,
        )
        return result.rowcount


def gc_stale_runs(
    max_age_hours: float = 2.0, dry_run: bool = False, project_id: str | None = None
) -> dict:
    """Detect and cancel stale ``status='running'`` rows.

    A run is stale if ANY of:
    1. No LangGraph checkpoint exists for its ``thread_id``.
    2. ``started_at`` exceeds *max_age_hours* AND no decision has been
       logged in ``cortex_decisions`` within the last *max_age_hours*.

    Returns ``{"no_checkpoint": int, "idle": int, "cancelled_ids": list}``.
    """
    project_filter = " AND r.project_id = %s" if project_id is not None else ""
    with _get_connection() as conn:
        # Heuristic 1: running with no checkpoint at all
        cp_params: list = []
        if project_id is not None:
            cp_params.append(project_id)
        rows_no_cp = conn.execute(
            f"""
            SELECT r.id, r.thread_id
            FROM cortex_runs r
            LEFT JOIN checkpoints cp ON cp.thread_id = r.thread_id
            WHERE r.status = 'running'
              AND cp.thread_id IS NULL{project_filter}
            """,
            cp_params,
        ).fetchall()

        # Heuristic 2: running + old + no recent decisions
        idle_params: list = [max_age_hours, max_age_hours, [r[0] for r in rows_no_cp] or []]
        if project_id is not None:
            idle_params.append(project_id)
        rows_idle = conn.execute(
            f"""
            SELECT r.id, r.thread_id
            FROM cortex_runs r
            WHERE r.status = 'running'
              AND r.started_at < NOW() - INTERVAL '%s hours'
              AND NOT EXISTS (
                  SELECT 1 FROM cortex_decisions d
                  WHERE d.run_id = r.id
                    AND d.created_at > NOW() - INTERVAL '%s hours'
              )
              AND r.id NOT IN (
                  SELECT unnest(%s::uuid[])
              ){project_filter}
            """,
            idle_params,
        ).fetchall()

        all_ids = [r[0] for r in rows_no_cp] + [r[0] for r in rows_idle]

        if all_ids and not dry_run:
            conn.execute(
                """
                UPDATE cortex_runs
                SET status = 'cancelled',
                    finished_at = %s,
                    error = 'gc: detected stale run'
                WHERE id = ANY(%s)
                """,
                (datetime.now(UTC), all_ids),
            )

    return {
        "no_checkpoint": len(rows_no_cp),
        "idle": len(rows_idle),
        "cancelled_ids": all_ids,
    }


# --- Rejection feedback persistence (#125) ---
#
# Lives on ``cortex_runs`` so it survives ``cortex reset`` (which deletes
# LangGraph checkpoints but doesn't touch terminal-status run rows). The
# rejection-feedback loop is: ``reject`` writes the column on the rejected
# run; ``run`` reads it from the most-recent rejected run for the thread,
# seeds ``initial_state["human_feedback"]``, and atomically marks it
# consumed so it isn't re-applied on subsequent runs.


def store_rejection_feedback(thread_id: str, feedback: str) -> int:
    """Attach rejection feedback to the most-recent run for ``thread_id``.

    Called by ``cortex reject`` so the feedback survives the subsequent
    ``cortex reset`` that wipes LangGraph checkpoints (#125).

    Returns 1 if a row was updated, 0 if the thread has no run rows.
    """
    with _get_connection() as conn:
        result = conn.execute(
            """
            UPDATE cortex_runs
            SET rejection_feedback = %s
            WHERE id = (
                SELECT id FROM cortex_runs
                WHERE thread_id = %s
                ORDER BY started_at DESC
                LIMIT 1
            )
            """,
            (feedback, thread_id),
        )
        return result.rowcount


def consume_pending_feedback(thread_id: str) -> str | None:
    """Read and atomically claim any unconsumed rejection feedback for ``thread_id``.

    Looks up the most-recent rejected run with a non-null
    ``rejection_feedback`` and a null ``feedback_consumed_at``, marks it
    consumed (idempotent: the same feedback can't be returned twice), and
    returns the feedback string. Returns ``None`` if there is nothing
    pending.

    Called by ``cortex run`` before invoking the graph so the new run's
    initial state can carry the operator's prior rejection reasoning into
    Phase 1 (#125).
    """
    with _get_connection() as conn:
        row = conn.execute(
            """
            UPDATE cortex_runs
            SET feedback_consumed_at = NOW()
            WHERE id = (
                SELECT id FROM cortex_runs
                WHERE thread_id = %s
                  AND status = 'rejected'
                  AND rejection_feedback IS NOT NULL
                  AND feedback_consumed_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
            )
            RETURNING rejection_feedback
            """,
            (thread_id,),
        ).fetchone()
        return row[0] if row else None


def list_runs(all: bool = False, project_id: str | None = None) -> list[dict]:
    """List pipeline runs. By default only non-terminal statuses."""
    with _get_connection() as conn:
        if all:
            if project_id is not None:
                rows = conn.execute(
                    """
                    SELECT id, thread_id, repo, issue_number, status, started_at
                    FROM cortex_runs
                    WHERE project_id = %s
                    ORDER BY started_at DESC
                    """,
                    (project_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, thread_id, repo, issue_number, status, started_at
                    FROM cortex_runs
                    ORDER BY started_at DESC
                    """
                ).fetchall()
        # psycopg can't expand a tuple into SQL `IN` — use array + ANY instead
        elif project_id is not None:
            rows = conn.execute(
                """
                    SELECT id, thread_id, repo, issue_number, status, started_at
                    FROM cortex_runs
                    WHERE NOT (status = ANY(%s))
                      AND project_id = %s
                    ORDER BY started_at DESC
                    """,
                (list(_TERMINAL_STATUSES), project_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                    SELECT id, thread_id, repo, issue_number, status, started_at
                    FROM cortex_runs
                    WHERE NOT (status = ANY(%s))
                    ORDER BY started_at DESC
                    """,
                (list(_TERMINAL_STATUSES),),
            ).fetchall()
    return [
        {
            "id": str(r[0]),
            "thread_id": r[1],
            "repo": r[2],
            "issue_number": r[3],
            "status": r[4],
            "started_at": r[5],
        }
        for r in rows
    ]


def list_decisions(
    run_id: str | None = None,
    agent: str | None = None,
    limit: int | None = 50,
    since: datetime | None = None,
    until: datetime | None = None,
    decision_id: str | None = None,
    include_full: bool = False,
    project_id: str | None = None,
) -> list[dict]:
    """Query cortex_decisions with optional filters. Returns dicts ordered by created_at DESC.

    ``limit`` caps the number of rows returned; default 50. Pass ``0`` or
    ``None`` for unlimited. Negative values raise ``ValueError``.

    ``since`` filters to rows with ``created_at >= since``.
    ``until`` filters to rows with ``created_at <= until``.
    ``decision_id`` filters to a single row by primary key.
    ``include_full`` adds ``system_prompt``, ``user_prompt``, ``response`` to the result.
    """
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit}")

    columns_sql = (
        "id, agent, backend, model, phase, "
        "input_tokens, output_tokens, cost_usd, duration_ms, created_at"
    )
    if include_full:
        columns_sql += ", system_prompt, user_prompt, response"
    query = f"SELECT {columns_sql} FROM cortex_decisions"
    conditions: list[str] = []
    params: list = []
    if decision_id is not None:
        conditions.append("id = %s")
        params.append(decision_id)
    if run_id is not None:
        conditions.append("run_id = %s")
        params.append(run_id)
    if agent is not None:
        conditions.append("agent = %s")
        params.append(agent)
    if since is not None:
        conditions.append("created_at >= %s")
        params.append(since)
    if until is not None:
        conditions.append("created_at <= %s")
        params.append(until)
    if project_id is not None:
        conditions.append("run_id IN (SELECT id FROM cortex_runs WHERE project_id = %s)")
        params.append(project_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC"
    if limit is not None and limit > 0:
        query += " LIMIT %s"
        params.append(limit)

    with _get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    columns = [
        "id",
        "agent",
        "backend",
        "model",
        "phase",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "duration_ms",
        "created_at",
    ]
    if include_full:
        columns.extend(["system_prompt", "user_prompt", "response"])
    return [dict(zip(columns, row, strict=False)) for row in rows]


def _build_run_filter(
    since: datetime | None = None,
    until: datetime | None = None,
    project_id: str | None = None,
) -> tuple[list[str], list, str]:
    """Build WHERE clause parts for filtering cortex_runs by started_at.

    Returns (where_clauses, params, where_sql) where where_sql includes
    the leading ' WHERE ' when clauses are non-empty.
    """
    where_clauses: list[str] = []
    params: list = []
    if since is not None:
        where_clauses.append("r.started_at >= %s")
        params.append(since)
    if until is not None:
        where_clauses.append("r.started_at <= %s")
        params.append(until)
    if project_id is not None:
        where_clauses.append("r.project_id = %s")
        params.append(project_id)
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    return where_clauses, params, where_sql


def summarize_runs(
    since: datetime | None = None,
    until: datetime | None = None,
    project_id: str | None = None,
) -> dict:
    """Aggregate stats across cortex_runs and cortex_decisions.

    Returns a dict with: total_runs, status_counts, total_tokens,
    total_input_tokens, total_output_tokens, phase_durations (median
    duration_ms per phase), and failure_shapes (error first-line grouping
    with counts).
    """
    with _get_connection() as conn:
        # --- Run counts and cost rollup ---
        where_clauses, params, where_sql = _build_run_filter(since, until, project_id)

        # Total runs + per-status counts
        rows = conn.execute(
            f"""
            SELECT r.status, COUNT(*) AS cnt
            FROM cortex_runs r
            {where_sql}
            GROUP BY r.status
            """,
            params,
        ).fetchall()

        status_counts: dict[str, int] = {}
        total_runs = 0
        for status, cnt in rows:
            status_counts[status] = cnt
            total_runs += cnt

        # Token rollup from cortex_decisions joined to filtered runs
        token_row = conn.execute(
            f"""
            SELECT COALESCE(SUM(d.input_tokens + d.output_tokens), 0),
                   COALESCE(SUM(d.input_tokens), 0),
                   COALESCE(SUM(d.output_tokens), 0)
            FROM cortex_decisions d
            JOIN cortex_runs r ON r.id = d.run_id
            {where_sql}
            """,
            params,
        ).fetchone()
        total_tokens = int(token_row[0])
        total_input_tokens = int(token_row[1])
        total_output_tokens = int(token_row[2])

        # Median per-phase duration from cortex_decisions
        phase_rows = conn.execute(
            f"""
            SELECT d.phase,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY d.duration_ms) AS median_ms
            FROM cortex_decisions d
            JOIN cortex_runs r ON r.id = d.run_id
            {where_sql}
            GROUP BY d.phase
            """,
            params,
        ).fetchall()
        phase_durations: dict[str, float] = {
            phase: float(median_ms) for phase, median_ms in phase_rows
        }

        # Failure breakdown: group by first line of error
        failure_rows = conn.execute(
            f"""
            SELECT split_part(r.error, E'\\n', 1) AS shape, COUNT(*) AS cnt
            FROM cortex_runs r
            {where_sql}
            {"AND" if where_clauses else "WHERE"} r.error IS NOT NULL
            GROUP BY shape
            ORDER BY cnt DESC
            """,
            params,
        ).fetchall()
        failure_shapes = [{"shape": shape, "count": cnt} for shape, cnt in failure_rows]

    return {
        "total_runs": total_runs,
        "status_counts": status_counts,
        "total_tokens": total_tokens,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "phase_durations": phase_durations,
        "failure_shapes": failure_shapes,
    }


def agent_breakdown(
    since: datetime | None = None,
    until: datetime | None = None,
    project_id: str | None = None,
) -> list[dict]:
    """Per-agent token breakdown across cortex_decisions.

    Returns a list of dicts (agent, calls, in_tokens, out_tokens,
    avg_out_per_call), sorted by out_tokens DESC.
    """
    _where_clauses, params, where_sql = _build_run_filter(since, until, project_id)

    with _get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT d.agent,
                   COUNT(*)                    AS calls,
                   COALESCE(SUM(d.input_tokens), 0)  AS in_tokens,
                   COALESCE(SUM(d.output_tokens), 0) AS out_tokens
            FROM cortex_decisions d
            JOIN cortex_runs r ON r.id = d.run_id
            {where_sql}
            GROUP BY d.agent
            ORDER BY out_tokens DESC
            """,
            params,
        ).fetchall()

    return [
        {
            "agent": agent,
            "calls": calls,
            "in_tokens": int(in_tok),
            "out_tokens": int(out_tok),
            "avg_out_per_call": int(out_tok) // calls if calls else 0,
        }
        for agent, calls, in_tok, out_tok in rows
    ]


def poll_decisions_since(run_id: str, after_ts: datetime) -> list[dict]:
    """Return decisions for run_id logged after after_ts, sorted ascending.

    Used by the live-progress watcher in cli.py to surface new agent
    completions in real time while graph.invoke() is blocking.
    Returns empty list on any DB error so callers can safely ignore failures.
    """
    try:
        with _get_connection() as conn:
            rows = conn.execute(
                """
                SELECT agent, backend, model, phase, duration_ms, created_at
                FROM cortex_decisions
                WHERE run_id = %s AND created_at > %s
                ORDER BY created_at ASC
                """,
                (run_id, after_ts),
            ).fetchall()
        return [
            {
                "agent": r[0],
                "backend": r[1],
                "model": r[2],
                "phase": r[3],
                "duration_ms": r[4] or 0,
                "created_at": r[5],
            }
            for r in rows
        ]
    except Exception:
        return []


def latest_decision_ts(run_id: str) -> datetime:
    """Return the created_at of the most recent decision for run_id.

    Returns datetime.min (UTC) when no decisions exist yet, so the first
    poll_decisions_since() call after an approve will only show NEW events.
    """
    try:
        with _get_connection() as conn:
            row = conn.execute(
                "SELECT MAX(created_at) FROM cortex_decisions WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        if row and row[0] is not None:
            ts = row[0]
            return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    except Exception:
        pass
    return datetime.min.replace(tzinfo=UTC)
