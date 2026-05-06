from __future__ import annotations

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters.base import BaseAdapter


class AiderAdapter(BaseAdapter):
    """Stub adapter for aider — registered but not yet wired up.

    Healthcheck reports the adapter as unavailable. Execute returns a
    structured ``_failed`` result rather than raising, matching the
    family-wide error contract that python-func, bash, http, etc. all
    follow. An agent misconfigured with ``runtime_id="aider"`` therefore
    surfaces as a normal node failure instead of an uncaught exception
    that takes down the engine error path. (#210)
    """

    id = "aider"
    display_name = "Aider"
    kind: RuntimeKind = "cli"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=False, missing=["aider binary"])

    async def execute(self, _task: RuntimeTask) -> RuntimeResult:
        return RuntimeResult(
            success=False,
            output="",
            duration_ms=0,
            errors=[
                "aider runtime is not yet implemented (stub) — "
                "scheduled for F3/F9. Pick a different runtime_id "
                "(claude-code / codex / gemini-cli / api-call) for now."
            ],
            structured={"state_delta": {}, "audit": {}},
        )
