from __future__ import annotations

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters.base import BaseAdapter


class HttpAdapter(BaseAdapter):
    id = "http"
    display_name = "HTTP endpoint"
    kind: RuntimeKind = "http"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        self._not_implemented("execute")
        raise AssertionError("unreachable")
