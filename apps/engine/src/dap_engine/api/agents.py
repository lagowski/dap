"""REST CRUD for agents."""

from __future__ import annotations

from typing import Any

from dap_types import Agent
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_session
from dap_engine.api.schemas import AgentCreate, AgentUpdate
from dap_engine.persistence import repository as repo

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
def list_agents(
    session: Session = Depends(get_session),
    role: str | None = Query(default=None),
    archived: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items, total = repo.list_agents(
        session,
        role=role,
        archived=archived,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [a.model_dump(mode="json") for a in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Agent)
def create_agent(payload: AgentCreate, session: Session = Depends(get_session)) -> Agent:
    return repo.create_agent(session, payload)


@router.get("/{agent_id}", response_model=Agent)
def get_agent(agent_id: str, session: Session = Depends(get_session)) -> Agent:
    try:
        return repo.get_agent(session, agent_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{agent_id}", response_model=Agent)
def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    session: Session = Depends(get_session),
) -> Agent:
    try:
        return repo.update_agent(session, agent_id, payload)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_agent(agent_id: str, session: Session = Depends(get_session)) -> Response:
    try:
        repo.archive_agent(session, agent_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{agent_id}/versions", response_model=list[Agent])
def list_agent_versions(agent_id: str, session: Session = Depends(get_session)) -> list[Agent]:
    try:
        return repo.list_agent_versions(session, agent_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{agent_id}/versions/{version}", response_model=Agent)
def get_agent_version(
    agent_id: str,
    version: int,
    session: Session = Depends(get_session),
) -> Agent:
    try:
        return repo.get_agent_version(session, agent_id, version)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
