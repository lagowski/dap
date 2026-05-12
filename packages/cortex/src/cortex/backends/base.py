"""Base interface for all LLM backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class LLMRequest:
    """What we send to any backend."""

    system_prompt: str
    user_prompt: str
    temperature: float = 0.1
    max_tokens: int = 4000


@dataclass
class LLMResponse:
    """What we get back from any backend."""

    content: str
    model: str = ""
    session_id: str = ""  # For CLI backends that support resume
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    backend: str = ""
    raw: dict = field(default_factory=dict)  # Full raw response for audit


BackendType = Literal["ollama", "claude_cli", "api"]


# --- Backend errors (#128) -------------------------------------------------
# Backends raise these so callers can distinguish "no work happened" from
# "work happened with a bad response". Before #128, CLI subprocess backends
# returned a fake LLMResponse with content="ERROR: ... timed out" on
# subprocess.TimeoutExpired, which the execution wrapper happily logged as
# success and routed past the human gate. Typed exceptions make the failure
# explicit at the API boundary.


class BackendError(Exception):
    """Base for backend invocation failures.

    Attributes:
        backend: Backend identifier (e.g. ``"claude_cli"``) for diagnostics.
    """

    def __init__(self, message: str, *, backend: str = ""):
        super().__init__(message)
        self.backend = backend


class BackendTimeoutError(BackendError):
    """The backend's underlying call exceeded its timeout budget.

    Attributes:
        timeout_sec: The budget that was exceeded.
    """

    def __init__(self, *, backend: str, timeout_sec: int):
        super().__init__(
            f"{backend} invocation timed out after {timeout_sec}s",
            backend=backend,
        )
        self.timeout_sec = timeout_sec


class BackendUnavailableError(BackendError):
    """The backend executable / endpoint isn't reachable.

    For CLI backends this is typically ``FileNotFoundError`` when the binary
    isn't on PATH; for HTTP backends it would be a connection refusal.
    """

    def __init__(self, message: str, *, backend: str = ""):
        super().__init__(message, backend=backend)


class Backend(ABC):
    """Abstract backend. Each agent gets exactly one."""

    backend_type: BackendType

    @abstractmethod
    def invoke(self, request: LLMRequest) -> LLMResponse:
        """Send a prompt, get a response.

        Raises:
            BackendTimeoutError: if the backend's underlying call times out.
            BackendUnavailableError: if the backend isn't reachable.
        """
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """Is this backend reachable?"""
        ...
