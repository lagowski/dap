"""Batch run persistence — create, read, update BatchRunORM rows (#473)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from dap_types.batch_run import BatchRun, BatchRunResult
from sqlalchemy.orm import Session

from dap_engine.persistence._common import NotFoundError, _new_id, _now
from dap_engine.persistence.models import BatchRunORM


def _batch_run_from_orm(row: BatchRunORM) -> BatchRun:
    results = [BatchRunResult(**r) for r in (row.results or [])]
    return BatchRun(
        id=row.id,
        pipeline_id=row.pipeline_id,
        pipeline_version=row.pipeline_version,
        project_id=row.project_id,
        issue_numbers=row.issue_numbers,
        stop_on_failure=bool(row.stop_on_failure),
        auto_approve=bool(row.auto_approve),
        current_index=row.current_index,
        status=row.status,  # type: ignore[arg-type]
        results=results,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def create_batch_run(
    session: Session,
    *,
    user_id: uuid.UUID,
    pipeline_id: str,
    pipeline_version: int | None,
    project_id: str | None,
    issue_numbers: list[int],
    stop_on_failure: bool,
    auto_approve: bool,
) -> BatchRunORM:
    now = _now()
    row = BatchRunORM(
        id=_new_id(),
        user_id=user_id,
        project_id=project_id,
        pipeline_id=pipeline_id,
        pipeline_version=pipeline_version,
        issue_numbers=issue_numbers,
        stop_on_failure=int(stop_on_failure),
        auto_approve=int(auto_approve),
        current_index=0,
        status="running",
        results=[],
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def get_batch_run(session: Session, batch_run_id: str) -> BatchRun:
    row = session.get(BatchRunORM, batch_run_id)
    if row is None:
        raise NotFoundError(f"BatchRun not found: {batch_run_id}")
    return _batch_run_from_orm(row)


def append_batch_result(
    session: Session,
    batch_run_id: str,
    *,
    issue_number: int,
    run_id: str | None,
    run_status: str,
    new_index: int,
    batch_status: str,
) -> None:
    """Append one issue outcome to results and advance current_index."""
    row = session.get(BatchRunORM, batch_run_id)
    if row is None:
        raise NotFoundError(f"BatchRun not found: {batch_run_id}")
    results: list[dict[str, Any]] = list(row.results or [])
    results.append({"issue_number": issue_number, "run_id": run_id, "status": run_status})
    # SQLAlchemy won't detect in-place list mutation on JSON columns.
    row.results = results
    row.current_index = new_index
    row.status = batch_status
    row.updated_at = _now()
    session.flush()


def finalize_batch_run(
    session: Session,
    batch_run_id: str,
    *,
    status: str,
) -> None:
    row = session.get(BatchRunORM, batch_run_id)
    if row is None:
        raise NotFoundError(f"BatchRun not found: {batch_run_id}")
    row.status = status
    row.updated_at = _now()
    session.flush()
