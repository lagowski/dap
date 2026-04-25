from __future__ import annotations

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters.base import BaseAdapter


class CodexAdapter(BaseAdapter):
    id = "codex"
    display_name = "OpenAI Codex CLI"
    kind: RuntimeKind = "cli"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=False, missing=["codex binary"])

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        self._not_implemented("execute")
        raise AssertionError("unreachable")
