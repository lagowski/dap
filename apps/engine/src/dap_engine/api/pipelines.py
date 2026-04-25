"""REST CRUD for pipelines."""

from __future__ import annotations

from typing import Any

from dap_types import Pipeline
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_session
from dap_engine.api.schemas import PipelineCreate, PipelineUpdate
from dap_engine.persistence import repository as repo

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.get("")
def list_pipelines(
    session: Session = Depends(get_session),
    archived: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items, total = repo.list_pipelines(
        session,
        archived=archived,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [p.model_dump(mode="json") for p in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Pipeline)
def create_pipeline(
    payload: PipelineCreate,
    session: Session = Depends(get_session),
) -> Pipeline:
    return repo.create_pipeline(session, payload)


@router.get("/{pipeline_id}", response_model=Pipeline)
def get_pipeline(pipeline_id: str, session: Session = Depends(get_session)) -> Pipeline:
    try:
        return repo.get_pipeline(session, pipeline_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{pipeline_id}", response_model=Pipeline)
def update_pipeline(
    pipeline_id: str,
    payload: PipelineUpdate,
    session: Session = Depends(get_session),
) -> Pipeline:
    try:
        return repo.update_pipeline(session, pipeline_id, payload)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_pipeline(
    pipeline_id: str,
    session: Session = Depends(get_session),
) -> Response:
    try:
        repo.archive_pipeline(session, pipeline_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{pipeline_id}/versions", response_model=list[Pipeline])
def list_pipeline_versions(
    pipeline_id: str,
    session: Session = Depends(get_session),
) -> list[Pipeline]:
    try:
        return repo.list_pipeline_versions(session, pipeline_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{pipeline_id}/versions/{version}", response_model=Pipeline)
def get_pipeline_version(
    pipeline_id: str,
    version: int,
    session: Session = Depends(get_session),
) -> Pipeline:
    try:
        return repo.get_pipeline_version(session, pipeline_id, version)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
