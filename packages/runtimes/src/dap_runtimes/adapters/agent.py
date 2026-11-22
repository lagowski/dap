"""Tool-loop ``agent`` runtime (#813, slice 1).

An LLM node that runs an agentic tool-use loop. Its tools are other DAP
runtime adapters, declared in ``runtime_config.tools``::

    runtime_id: agent
    config:
      provider: anthropic
      model_id: claude-opus-4-7
      system_prompt: "You are a code-review agent."
      tools:
        - runtime_id: github
          description: "Read/comment GitHub issues and PRs"
          config: {repo: owner/name}     # optional, merged under the LLM's input
      max_turns: 20
      budget_usd: 5.0

Each turn sends the conversation to the provider. When the model asks for a
tool, the adapter builds a ``RuntimeTask`` for the named runtime (the tool's
static ``config`` overlaid with the model's input), executes it and returns
the result as a ``tool_result``. The loop ends when the model stops asking for
tools, or halts with :class:`MaxTurnsExhaustedError` /
:class:`BudgetExhaustedError` when ``max_turns`` / ``budget_usd`` run out.
Adapters never raise to the engine (#211): a halt becomes a failed
``RuntimeResult`` whose ``structured["halt_reason"]`` names the limit.

Slice 1 scope: ``anthropic`` provider only. Other providers, the dashboard
tools panel, the #785 audit trail, docs and the example bundle follow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from typing import Any, Final

import anthropic
from dap_types import (
    BackendErrorCategory,
    HealthStatus,
    OutputCallback,
    RuntimeAdapter,
    RuntimeKind,
    RuntimeResult,
    RuntimeTask,
)

from dap_runtimes.adapters._error_category import category_for_status, with_category
from dap_runtimes.adapters._providers._anthropic import DEFAULT_ENV_VAR as ANTHROPIC_ENV_VAR
from dap_runtimes.adapters._providers._anthropic import _calculate_cost
from dap_runtimes.adapters._providers._base import ProviderError, classify_provider_failure
from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.agent")

SUPPORTED_PROVIDERS: Final = frozenset({"anthropic"})
DEFAULT_PROVIDER: Final = "anthropic"
DEFAULT_MAX_TURNS: Final = 20
DEFAULT_MAX_TOKENS: Final = 4096
# A tool result fed back to the model is capped so one verbose tool can't
# blow the context window.
MAX_TOOL_RESULT_CHARS: Final = 20_000
# Generic tool input schema: the tool's runtime_config overrides. Per-runtime
# schemas arrive with the dashboard tools panel; a tool entry may set its own
# ``input_schema`` meanwhile.
DEFAULT_INPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "description": "runtime_config fields for this runtime (merged over the tool's config)",
    "additionalProperties": True,
}

ToolResolver = Callable[[str], RuntimeAdapter]
ClientFactory = Callable[[], Any]


class AgentHaltError(ProviderError):
    """The tool loop hit a bound before the model finished (cortex#551 pattern).

    Subclasses the provider/backend error so callers handling backend failures
    see it; ``halt_reason`` is the machine-readable limit that tripped.
    """

    halt_reason: str = "halted"

    def __init__(self, message: str, *, turns: int, cost_usd: float) -> None:
        super().__init__(message)
        self.turns = turns
        self.cost_usd = cost_usd


class MaxTurnsExhaustedError(AgentHaltError):
    halt_reason = "max_turns_exhausted"


class BudgetExhaustedError(AgentHaltError):
    halt_reason = "budget_exhausted"


class _ConfigError(Exception):
    pass


class AgentAdapter(BaseAdapter):
    """LLM tool-use loop whose tools are other runtime adapters."""

    id = "agent"
    display_name = "Agent (LLM tool loop)"
    kind: RuntimeKind = "api"

    def __init__(
        self,
        tool_resolver: ToolResolver | None = None,
        *,
        tools: Mapping[str, RuntimeAdapter] | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._tools = dict(tools or {})
        self._tool_resolver = tool_resolver
        self._client_factory = client_factory

    async def healthcheck(self) -> HealthStatus:
        if not os.environ.get(ANTHROPIC_ENV_VAR):
            return HealthStatus(available=False, missing=[f"{ANTHROPIC_ENV_VAR} (Anthropic SDK)"])
        return HealthStatus(available=True, version="anthropic")

    async def execute(
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult:
        del on_output  # no subprocess, nothing to stream
        start = time.monotonic()
        loop = _Loop(self, task)
        try:
            loop.prepare()
            output, stop_reason = await loop.run()
        except _ConfigError as exc:
            return loop.failed(start, str(exc))
        except AgentHaltError as exc:
            logger.warning("agent halted: %s", exc)
            return loop.failed(
                start,
                str(exc),
                extra={"halt_reason": exc.halt_reason, "error_type": type(exc).__name__},
            )
        except ProviderError as exc:
            logger.warning("agent provider call failed: %s", exc)
            return loop.failed(start, str(exc), category=exc.category)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("agent loop unexpected error")
            return loop.failed(start, f"unexpected {type(exc).__name__}: {exc}")

        return RuntimeResult(
            success=True,
            output=output,
            tokens_used=loop.tokens,
            cost_usd=loop.cost_usd,
            duration_ms=int((time.monotonic() - start) * 1000),
            structured={**loop.summary(), "stop_reason": stop_reason},
        )

    def resolve_tool(self, runtime_id: str) -> RuntimeAdapter:
        if runtime_id in self._tools:
            return self._tools[runtime_id]
        if self._tool_resolver is not None:
            try:
                return self._tool_resolver(runtime_id)
            except KeyError:
                pass
        raise _ConfigError(f"tool runtime '{runtime_id}' is not available")

    def new_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return anthropic.AsyncAnthropic()


class _Loop:
    """One execution of the tool loop: config, conversation, counters."""

    def __init__(self, adapter: AgentAdapter, task: RuntimeTask) -> None:
        self.adapter = adapter
        self.task = task
        self.config = task.runtime_config
        self.turns = 0
        self.tokens = 0
        self.cost_usd = 0.0
        self.cost_known = True
        self.tool_calls: list[dict[str, Any]] = []
        self.tools: dict[str, tuple[RuntimeAdapter, dict[str, Any]]] = {}
        self.tool_specs: list[dict[str, Any]] = []
        self.max_turns = DEFAULT_MAX_TURNS
        self.budget_usd: float | None = None
        self.model_id = ""

    def prepare(self) -> None:
        cfg = self.config
        provider = cfg.get("provider", DEFAULT_PROVIDER)
        if provider not in SUPPORTED_PROVIDERS:
            raise _ConfigError(
                f"agent provider '{provider}' is not supported yet; "
                f"supported: {sorted(SUPPORTED_PROVIDERS)}"
            )
        model_id = cfg.get("model_id") or cfg.get("model")
        if not isinstance(model_id, str) or not model_id:
            raise _ConfigError("runtime_config.model_id is required (e.g. 'claude-haiku-4-5')")
        self.model_id = model_id

        max_turns = cfg.get("max_turns", DEFAULT_MAX_TURNS)
        if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns <= 0:
            raise _ConfigError("runtime_config.max_turns must be a positive int")
        self.max_turns = max_turns

        budget = cfg.get("budget_usd", self.task.budget_usd)
        if budget is not None:
            if isinstance(budget, bool) or not isinstance(budget, int | float) or budget <= 0:
                raise _ConfigError("runtime_config.budget_usd must be a positive number")
            self.budget_usd = float(budget)

        max_tokens = cfg.get("max_tokens", DEFAULT_MAX_TOKENS)
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise _ConfigError("runtime_config.max_tokens must be a positive int")

        self._prepare_tools(cfg.get("tools") or [])

    def _prepare_tools(self, entries: Any) -> None:
        if not isinstance(entries, list):
            raise _ConfigError("runtime_config.tools must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("runtime_id"), str):
                raise _ConfigError("each runtime_config.tools entry needs a runtime_id")
            runtime_id = entry["runtime_id"]
            if runtime_id == AgentAdapter.id:
                raise _ConfigError("the agent runtime can't be its own tool")
            name = _tool_name(entry.get("name") or runtime_id)
            if name in self.tools:
                raise _ConfigError(f"duplicate tool name '{name}'")
            base = entry.get("config") or {}
            if not isinstance(base, dict):
                raise _ConfigError(f"tool '{name}' config must be a mapping")
            self.tools[name] = (self.adapter.resolve_tool(runtime_id), base)
            self.tool_specs.append(
                {
                    "name": name,
                    "description": str(entry.get("description") or f"The {runtime_id} runtime"),
                    "input_schema": entry.get("input_schema") or DEFAULT_INPUT_SCHEMA,
                }
            )

    async def run(self) -> tuple[str, str | None]:
        cfg = self.config
        messages: list[dict[str, Any]] = [{"role": "user", "content": self.task.prompt_xml}]
        request: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": cfg.get("max_tokens", DEFAULT_MAX_TOKENS),
        }
        system_prompt = cfg.get("system_prompt")
        if isinstance(system_prompt, str) and system_prompt:
            request["system"] = system_prompt
        if self.tool_specs:
            request["tools"] = self.tool_specs

        client = self.adapter.new_client()
        try:
            while True:
                response = await _create(client, {**request, "messages": messages})
                self.turns += 1
                self._account(response)
                content = list(response.content)
                tool_uses = [b for b in content if getattr(b, "type", None) == "tool_use"]
                if response.stop_reason != "tool_use" or not tool_uses:
                    text = "".join(b.text for b in content if getattr(b, "type", None) == "text")
                    return text, response.stop_reason
                self._check_bounds()
                messages.append({"role": "assistant", "content": [_block(b) for b in content]})
                results = [await self._call_tool(b) for b in tool_uses]
                messages.append({"role": "user", "content": results})
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await close()

    def _account(self, response: Any) -> None:
        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.tokens += input_tokens + output_tokens + cache_creation + cache_read
        cost = _calculate_cost(
            model_id=self.model_id,
            input_tokens=input_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            output_tokens=output_tokens,
        )
        if cost is None:
            self.cost_known = False
        else:
            self.cost_usd += cost

    def _check_bounds(self) -> None:
        """Called when the model wants another tool round."""
        if self.budget_usd is not None and self.cost_usd >= self.budget_usd:
            raise BudgetExhaustedError(
                f"agent budget exhausted: spent ${self.cost_usd:.4f} of "
                f"${self.budget_usd:.4f} after {self.turns} turn(s)",
                turns=self.turns,
                cost_usd=self.cost_usd,
            )
        if self.turns >= self.max_turns:
            raise MaxTurnsExhaustedError(
                f"agent max_turns exhausted: {self.turns} of {self.max_turns} turn(s) "
                "used and the model still wants tools",
                turns=self.turns,
                cost_usd=self.cost_usd,
            )

    async def _call_tool(self, block: Any) -> dict[str, Any]:
        name = block.name
        tool_input = block.input if isinstance(block.input, dict) else {}
        record: dict[str, Any] = {"turn": self.turns, "tool": name, "input": tool_input}
        self.tool_calls.append(record)
        if name not in self.tools:
            record["success"] = False
            return _tool_result(block.id, f"unknown tool '{name}'", is_error=True)
        adapter, base = self.tools[name]
        sub_task = self.task.model_copy(
            update={
                "runtime_config": {**base, **tool_input},
                "allowed_tools": None,
            }
        )
        try:
            result = await adapter.execute(sub_task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("agent tool %s raised", name)
            record["success"] = False
            return _tool_result(block.id, f"{type(exc).__name__}: {exc}", is_error=True)
        record["success"] = result.success
        if result.cost_usd:
            self.cost_usd += result.cost_usd
        payload: dict[str, Any] = {"success": result.success, "output": result.output}
        if result.structured:
            payload["structured"] = result.structured
        if result.errors:
            payload["errors"] = result.errors
        text = json.dumps(payload, default=str)
        if len(text) > MAX_TOOL_RESULT_CHARS:
            text = text[:MAX_TOOL_RESULT_CHARS] + "...[truncated]"
        return _tool_result(block.id, text, is_error=not result.success)

    def summary(self) -> dict[str, Any]:
        return {
            "provider": DEFAULT_PROVIDER,
            "model": self.model_id,
            "turns": self.turns,
            "max_turns": self.max_turns,
            "budget_usd": self.budget_usd,
            "tool_calls": self.tool_calls,
        }

    def failed(
        self,
        start: float,
        message: str,
        *,
        category: BackendErrorCategory | None = None,
        extra: dict[str, Any] | None = None,
    ) -> RuntimeResult:
        base = self.summary() if self.turns else {}
        structured: dict[str, Any] = {**base, **(extra or {})}
        return RuntimeResult(
            success=False,
            output="",
            tokens_used=self.tokens or None,
            cost_usd=self.cost_usd if self.turns and self.cost_known else None,
            duration_ms=int((time.monotonic() - start) * 1000),
            errors=[message],
            structured=with_category(structured, category) or None,
        )


async def _create(client: Any, request: dict[str, Any]) -> Any:
    """One Messages API call; SDK errors become ProviderError like api-call."""
    try:
        return await client.messages.create(**request)
    except anthropic.AuthenticationError as exc:
        raise ProviderError(f"Authentication failed: {exc.message}") from exc
    except anthropic.BadRequestError as exc:
        category = classify_provider_failure(exc.message, status_code=400)
        raise ProviderError(f"Bad request: {exc.message}", category=category) from exc
    except anthropic.RateLimitError as exc:
        category = classify_provider_failure(exc.message, status_code=429)
        raise ProviderError(f"Rate limited: {exc.message}", category=category) from exc
    except anthropic.APITimeoutError as exc:
        raise ProviderError(f"API timeout: {exc}", category="timeout") from exc
    except anthropic.APIConnectionError as exc:
        raise ProviderError(f"Connection error: {exc}", category="unavailable") from exc
    except anthropic.APIStatusError as exc:
        raise ProviderError(
            f"API error {exc.status_code}: {exc.message}",
            category=category_for_status(exc.status_code),
        ) from exc


def _block(block: Any) -> dict[str, Any]:
    """Serialize a response content block for the next request."""
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        data = dump(exclude_none=True)
        if isinstance(data, dict):
            return data
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if block.type == "text":
        return {"type": "text", "text": block.text}
    return {"type": block.type}


def _tool_result(tool_use_id: str, content: str, *, is_error: bool) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def _tool_name(raw: str) -> str:
    """Anthropic tool names allow ``[a-zA-Z0-9_-]{1,64}``."""
    name = "".join(c if c.isalnum() or c in "_-" else "_" for c in raw)[:64]
    if not name:
        raise _ConfigError("tool name is empty")
    return name


__all__ = [
    "AgentAdapter",
    "AgentHaltError",
    "BudgetExhaustedError",
    "MaxTurnsExhaustedError",
]
