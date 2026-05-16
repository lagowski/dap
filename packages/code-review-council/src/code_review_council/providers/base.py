"""Abstract provider — narrow contract every agent uses to hit an LLM.

The shape is deliberately minimal: ``run_structured`` takes a system
instruction, a user payload, and a Pydantic model class describing the
expected output, and returns an instance of that model. Anything more
provider-specific (thinking levels, tool calls, multi-turn) lives in
the concrete subclass.

Synchronous by design for MVP. Async upgrade is a follow-up — for two
sequential agents the latency cost is small (one Gemini call is ~10-30s
on Flash; two is ~20-60s; still inside CI's 5-minute budget).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from pydantic import BaseModel

T = TypeVar("T", bound="BaseModel")


class BaseProvider(Protocol):
    """Minimal contract for an LLM adapter.

    A provider must be able to take a system instruction + user content
    + a Pydantic schema, and produce an instance of that schema. The
    specifics (which API, structured-output mechanism, model name) are
    each provider's concern.
    """

    name: str
    """Short identifier for logging — e.g. 'gemini-2.5-flash'."""

    def run_structured(
        self,
        *,
        system_instruction: str,
        user_content: str,
        response_schema: type[T],
    ) -> T:
        """Run inference and return a parsed Pydantic instance.

        Implementations are expected to:
        - Pass ``response_schema.model_json_schema()`` (or the model
          class directly, per SDK convention) so the LLM is forced
          into structured output.
        - Raise on empty / unparseable responses — silent failure is
          worse than a loud one for a CI gate.
        """
        ...
