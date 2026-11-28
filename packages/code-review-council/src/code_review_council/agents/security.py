"""SecurityAgent — auth / authz / secrets / injection / data exposure."""

from __future__ import annotations

from typing import ClassVar

from code_review_council.agents.base import BaseAgent


class SecurityAgent(BaseAgent):
    name: ClassVar[str] = "Security"
    scope: ClassVar[str] = (
        "authentication, authorization, secrets handling, injection vectors, "
        "data exposure, CSRF/XSS, anti-enumeration, audit trail"
    )
    focus_areas: ClassVar[list[str]] = [
        # Each bullet is concrete enough that the model can scan the diff
        # for it without making leaps. Concrete > abstract for prompts.
        "Authentication: any new route that should be auth-gated but isn't.",
        "Authorization: missing admin gates; admin endpoints leaking 403 instead "
        "of 404 (anti-enumeration); ownership checks on resource CRUD.",
        "Secrets: API keys / passwords / tokens hardcoded; secrets in audit log "
        "``event_data``; secrets in error messages or HTTP responses.",
        "Injection: SQL via f-string instead of bound params; shell command "
        "interpolation; HTML rendered from user input without escape; eval/exec.",
        "Data exposure: PII or token values returned in responses; user IDs / "
        "emails leaked in 404 messages; OAuth state not validated on callback.",
        "Crypto: missing input validation before encryption; predictable nonces; "
        "weak hashing (MD5/SHA1 for passwords); reuse of single-use tokens.",
        "Audit: writes to security-relevant state without ``record_audit_event``; "
        "event_data carrying the secret/PII instead of just the key/id.",
    ]
