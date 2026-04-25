from __future__ import annotations

from abc import ABC, abstractmethod

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask


class BaseAdapter(ABC):
    """Bazowa klasa dla runtime adapterów. F0 stuby rozszerzają tę klasę."""

    id: str
    display_name: str
    kind: RuntimeKind

    @abstractmethod
    async def healthcheck(self) -> HealthStatus: ...

    @abstractmethod
    async def execute(self, task: RuntimeTask) -> RuntimeResult: ...

    def _not_implemented(self, method: str) -> None:
        raise NotImplementedError(
            f"{self.id}.{method}() not implemented yet — scheduled for F3/F9",
        )
