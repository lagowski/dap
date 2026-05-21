"""Centralized vocabulary of audit-log event types (#audit-E4).

Every call to :func:`dap_engine.auth.audit.record_audit_event` (and its
``_async`` counterpart) must pass an ``event_type`` that's in this
list. Typing the parameter as ``AuditEventType`` (a :data:`Literal`)
turns a class of typo bugs into mypy errors — without this gate, a
silent typo (``"user.loggedin"`` vs ``"user.logged_in"``) would write
unfindable rows that never match any filter on
``/audit/events?event_type=…``.

Convention: ``<resource>.<verb>``. Resource may be a dotted namespace
(``settings.env_var``) when the resource itself has sub-kinds. Verbs
use past-tense snake_case (``logged_in`` not ``login``).

Audit finding E4 — see
``.claude/plans/chce-zebys-zrobil-pelny-snuggly-unicorn.md``.
"""

from __future__ import annotations

from typing import Literal

__all__ = ["AuditEventType"]


# Keep the alphabetically-grouped lists in sync with the call sites —
# mypy enforces it, but a human reader scanning the file should see
# at a glance which subsystem owns which prefixes.
AuditEventType = Literal[
    # ------------------------------------------------------------------
    # Auth (fastapi-users hooks + custom routes)
    # ------------------------------------------------------------------
    "user.registered",
    "user.logged_in",
    # Mirrors the fastapi-users ``on_after_forgot_password`` hook name —
    # captures the *request* to reset, distinct from the actual reset
    # completion below. The audit row carries no token (credentials);
    # only the event + actor.
    "user.forgot_password",
    "user.password_reset",
    # Self-service password rotation: PATCH /users/me with a new
    # ``password`` field. Distinct from ``user.password_reset`` (the
    # forgot-password email flow) because a change requires the
    # current session token — different threat model, different
    # audit semantics. Fired by the ``on_after_update`` hook in
    # UserManager whenever ``update_dict`` contains the ``password``
    # key. Never carries the value, only the event + actor.
    "user.password_changed",
    "user.deleted",
    # ------------------------------------------------------------------
    # OAuth — link side. Triggered by our ``oauth_callback`` override
    # when a NEW OAuth provider account is associated with a user
    # that ALREADY existed (i.e. the ``associate_by_email`` branch
    # in fastapi-users). Meaningful security event because it grants
    # a new authentication surface to an existing account.
    #
    # NOT triggered by:
    # - Token refreshes on an already-linked provider (just rotating
    #   credentials, no new attack surface — adds noise without
    #   value).
    # - New-user-via-OAuth (fires ``user.registered`` upstream; we
    #   don't want to double-audit that single event).
    #
    # ``oauth.unlinked`` is intentionally absent — DAP has no
    # unlink endpoint today. Add it (and an entry here) when that
    # surface ships.
    # ------------------------------------------------------------------
    "oauth.linked",
    # ------------------------------------------------------------------
    # API tokens (CRUD via /auth/api-tokens; admin via /auth/api-tokens/admin)
    # ------------------------------------------------------------------
    "api_token.created",
    "api_token.revoked",
    # ------------------------------------------------------------------
    # Resources — every CRUD path on agents / pipelines / projects.
    # Naming locked: ``<resource>.<verb>`` with verb in past tense.
    # ``archived`` covers soft-delete (we don't expose hard-delete).
    # ------------------------------------------------------------------
    "agent.created",
    "agent.updated",
    "agent.archived",
    "pipeline.created",
    "pipeline.updated",
    "pipeline.ui_metadata_updated",
    "pipeline.archived",
    # Bundle import is its own event (distinct from ``pipeline.created``)
    # so the audit query can spot externally-sourced pipelines without
    # joining other tables. Same shape as the standard verbs.
    "pipeline.imported_from_url",
    "project.created",
    "project.updated",
    "project.archived",
    # ------------------------------------------------------------------
    # Runs — only the trigger is audited; the rest of the lifecycle
    # lives in ``node_execution_logs`` (would otherwise drown the log).
    # ------------------------------------------------------------------
    "run.triggered",
    # Security policy denials that block execution before a run or dry-run
    # can invoke a runtime.
    "runtime_policy.denied",
    # ------------------------------------------------------------------
    # Settings (instance env vars — #388). The ``settings.env_var.*``
    # triple is the one place we use a three-segment name; the extra
    # segment makes room for future ``settings.<other_kind>.*`` siblings
    # without needing to widen this enum.
    # ------------------------------------------------------------------
    "settings.env_var.created",
    "settings.env_var.updated",
    "settings.env_var.deleted",
]
