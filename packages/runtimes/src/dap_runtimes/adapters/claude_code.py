from __future__ import annotations

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters.base import BaseAdapter


class ClaudeCodeAdapter(BaseAdapter):
    id = "claude-code"
    display_name = "Claude Code CLI"
    kind: RuntimeKind = "cli"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(
            available=False,
            missing=["claude binary (install: https://docs.claude.com/claude-code)"],
        )

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        self._not_implemented("execute")
        raise AssertionError("unreachable")
