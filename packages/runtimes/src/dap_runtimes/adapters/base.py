from __future__ import annotations

from abc import ABC, abstractmethod

from dap_types import HealthStatus, OutputCallback, RuntimeKind, RuntimeResult, RuntimeTask


class BaseAdapter(ABC):
    """Bazowa klasa dla runtime adapterów. F0 stuby rozszerzają tę klasę."""

    id: str
    display_name: str
    kind: RuntimeKind

    @abstractmethod
    async def healthcheck(self) -> HealthStatus: ...

    @abstractmethod
    async def execute(
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult:
        """Execute ``task``.

        ``on_output`` (#662): optional sync sink invoked with each incremental
        stdout chunk as the subprocess produces it. Streaming is wired only for
        the shared CLI subprocess base in Phase 3b-2a; other adapters
        accept-and-ignore it for now. ``on_output=None`` (the default, and what
        real runs pass today) is the unchanged, non-streaming path.
        """

    def _not_implemented(self, method: str) -> None:
        raise NotImplementedError(
            f"{self.id}.{method}() not implemented yet — scheduled for F3/F9",
        )
