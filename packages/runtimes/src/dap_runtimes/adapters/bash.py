from __future__ import annotations

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters.base import BaseAdapter


class BashAdapter(BaseAdapter):
    id = "bash"
    display_name = "Bash (shell)"
    kind: RuntimeKind = "shell"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True, version="system")

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        self._not_implemented("execute")
        raise AssertionError("unreachable")  # for type checker
