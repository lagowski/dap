"""In-app configuration assistant (#689).

Slice 2 wires a real model: pick whichever provider has a key configured
(os.environ + the decrypted instance env vars), stuff the curated DAP docs
into the system prompt for grounding, and answer via the tested
:class:`ApiCallAdapter`. No provider configured → a graceful "no model" reply.

The response carries an ``actions`` list (navigate / prefill / doc) — empty for
now, but the contract is in place so slice 3 (apply / scaffold from a
recommendation) is a pure-UI change.

Constraints: no secrets in prompts — the ``context`` hint and the prompt carry
page/form shape and env-var *names*, never values.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_engine_config, get_session
from dap_engine.assistant.service import generate_reply
from dap_engine.auth.audit import record_audit_event
from dap_engine.auth.users import current_active_user
from dap_engine.instance_env import load_instance_env
from dap_engine.persistence.interaction_log import record_interaction
from dap_engine.persistence.models import UserORM
from dap_engine.redaction import redact

if TYPE_CHECKING:
    from dap_engine.app import EngineConfig

logger = logging.getLogger("dap.engine.api.assistant")

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Citation(BaseModel):
    """A grounded reference back to a DAP docs section."""

    label: str
    href: str


class AssistantAction(BaseModel):
    """A suggested follow-up the UI can offer as a button (#689).

    ``kind``: ``navigate`` (go to ``href``), ``prefill`` (insert ``values`` into
    the form named by ``target``), or ``doc`` (open ``href``). Advisory only —
    the UI never auto-applies. Populated from slice 3; empty in slice 2.
    """

    kind: Literal["navigate", "prefill", "doc"]
    label: str
    href: str | None = None
    target: str | None = None
    values: dict[str, Any] | None = None


class AssistantChatRequest(BaseModel):
    messages: list[AssistantMessage]
    # Optional page/form context (slice 2/3). Names/shape only — never secrets.
    context: dict[str, Any] | None = None


class AssistantChatResponse(BaseModel):
    message: AssistantMessage
    grounded: bool = False
    citations: list[Citation] = []
    actions: list[AssistantAction] = []


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(
    payload: AssistantChatRequest,
    session: Session = Depends(get_session),
    config: EngineConfig = Depends(get_engine_config),
    user: UserORM = Depends(current_active_user),
) -> AssistantChatResponse:
    """One grounded assistant turn (#689). Auth-gated.

    Reuses ApiCallAdapter against whichever provider key is configured;
    a docs-grounded system prompt keeps advice accurate. Records a
    ``assistant.chat`` audit event (no message content — just that a turn
    happened, by whom).
    """
    # Instance env vars need the Fernet key to decrypt; without it the
    # assistant simply falls back to provider keys present in os.environ.
    instance_env = load_instance_env(session, config.crypto.instance_env_vars_key)
    # os.environ wins over instance vars (matches the run-time resolution order).
    env = {**instance_env, **os.environ}

    reply = await generate_reply(
        [m.model_dump() for m in payload.messages],
        env=env,
        context=payload.context,
    )

    record_audit_event(
        session,
        user_id=user.id,
        event_type="assistant.chat",
        event_data={"grounded": reply.grounded, "turns": len(payload.messages)},
    )

    if config.interaction_log.enabled:
        # EU AI Act record-keeping (#722): store the full transcript + reply,
        # redacted at this persistence boundary. ``instance_env`` holds the
        # *decrypted* configured secrets — the exact-value layer names any
        # leaked key; the pattern layer backstops pasted/echoed tokens.
        record_interaction(
            session,
            user_id=user.id,
            surface="assistant",
            provider=reply.provider,
            model=reply.model,
            redacted_request=[
                {
                    "role": m.role,
                    "content": redact(m.content, known_secrets=instance_env),
                }
                for m in payload.messages
            ],
            redacted_response=redact(reply.text, known_secrets=instance_env),
            grounded=reply.grounded,
        )

    # Commit the audit + interaction rows explicitly — don't rely on the
    # session dependency's end-of-request commit alone.
    session.commit()

    return AssistantChatResponse(
        message=AssistantMessage(role="assistant", content=reply.text),
        grounded=reply.grounded,
        citations=[],
        actions=[AssistantAction.model_validate(a) for a in reply.actions],
    )
