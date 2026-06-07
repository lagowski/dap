"""In-app configuration assistant (#689).

Slice 1 ships the **contract + UI shell**: a chat endpoint the right-dock
assistant panel talks to. The reply is a stub — the model backend (a direct
call to a provider configured in ``instance_env_vars``, grounded on the DAP
docs by prompt-stuffing) lands in the next slice and slots in behind this same
request/response shape.

Constraints carried forward (see the issue): no secrets in prompts — the
``context`` hint may carry page/form *shape* and names, never env-var values.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dap_engine.auth.users import current_active_user
from dap_engine.persistence.models import UserORM

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Citation(BaseModel):
    """A grounded reference back to a DAP docs section."""

    label: str
    href: str


class AssistantChatRequest(BaseModel):
    messages: list[AssistantMessage]
    # Optional page/form context (slice 2). Names/shape only — never secrets.
    context: dict[str, Any] | None = None


class AssistantChatResponse(BaseModel):
    message: AssistantMessage
    # True once the reply is grounded on the docs corpus; stub is ungrounded.
    grounded: bool = False
    citations: list[Citation] = []


_STUB_REPLY = (
    "The configuration assistant is wired up, but its model backend isn't "
    "connected yet — that's the next step. Soon I'll answer questions like "
    '"an agent that reviews PRs cheaply" or "a deterministic, free classifier" '
    "with concrete DAP configs (runtime, model, role, contracts, providers) and "
    "links to the docs. For now, the docs under /docs are the best reference."
)


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(
    payload: AssistantChatRequest,
    user: UserORM = Depends(current_active_user),
) -> AssistantChatResponse:
    """Stub chat turn (#689 slice 1) — auth-gated; returns a placeholder reply.

    The real implementation will call the instance's configured provider with
    a docs-grounded system prompt and return grounded citations.
    """
    return AssistantChatResponse(
        message=AssistantMessage(role="assistant", content=_STUB_REPLY),
        grounded=False,
        citations=[],
    )
