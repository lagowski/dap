from __future__ import annotations

from dap_types import RuntimeAdapter

from dap_runtimes.adapters.aider import AiderAdapter
from dap_runtimes.adapters.api_call import ApiCallAdapter
from dap_runtimes.adapters.bash import BashAdapter
from dap_runtimes.adapters.claude_code import ClaudeCodeAdapter
from dap_runtimes.adapters.codex import CodexAdapter
from dap_runtimes.adapters.gemini_cli import GeminiCliAdapter
from dap_runtimes.adapters.http import HttpAdapter


class RuntimeRegistry:
    """Lookup registry dla runtime adapterów."""

    def __init__(self) -> None:
        self._adapters: dict[str, RuntimeAdapter] = {}

    def register(self, adapter: RuntimeAdapter) -> None:
        if adapter.id in self._adapters:
            raise ValueError(f"Runtime adapter already registered: {adapter.id}")
        self._adapters[adapter.id] = adapter

    def get(self, adapter_id: str) -> RuntimeAdapter:
        if adapter_id not in self._adapters:
            raise KeyError(f"Runtime adapter not found: {adapter_id}")
        return self._adapters[adapter_id]

    def has(self, adapter_id: str) -> bool:
        return adapter_id in self._adapters

    def list(self) -> list[RuntimeAdapter]:
        return list(self._adapters.values())


async def create_default_registry() -> RuntimeRegistry:
    """Tworzy registry z 7 wbudowanymi adapterami (F0 = stuby)."""
    registry = RuntimeRegistry()
    registry.register(BashAdapter())
    registry.register(HttpAdapter())
    registry.register(ApiCallAdapter())
    registry.register(ClaudeCodeAdapter())
    registry.register(GeminiCliAdapter())
    registry.register(CodexAdapter())
    registry.register(AiderAdapter())
    return registry
