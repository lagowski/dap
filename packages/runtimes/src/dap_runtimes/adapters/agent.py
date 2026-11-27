"""Tool-loop ``agent`` runtime (#813, slices 1-3b).

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
the result as a ``tool_result``. The model's input is checked first (#1020,
#1022), at the top level of the input, for every tool and whatever schema it
advertises:

- keys starting with ``__`` are refused;
- keys on the fixed deny-list :data:`PROTECTED_INPUT_KEYS` (``url``,
  ``api_url``, ``base_url``, ``env``, ``cwd``, ``command``, ``module``,
  ``function``, ``state_key``, ``auth``, ``headers``, plus the executable
  pickers ``callable_path``, ``shell``, ``binary_path``, ``extra_args``, and
  http's ``request_template``, which is Jinja rendered with ``runtime_config``
  in scope, #1037) and any key ending in ``_env`` are refused, whatever the
  key's case. They redirect where a call goes, which
  env var becomes a credential, or what code runs; only the tool entry's static
  ``config`` sets them, and an entry ``input_schema`` listing one doesn't
  open it;
- unless the schema sets ``additionalProperties: true``, any key outside its
  ``properties`` is refused (a schema that omits it counts as closed, #1037);
- an object- or list-valued key is refused unless the schema's ``properties``
  list it.

A tool's schema is the entry's ``input_schema``, else the runtime's own
``tool_input_schema()`` (github, http, bash and api-call list only their data
fields), else :data:`DEFAULT_INPUT_SCHEMA`, which is closed: a runtime without a
schema of its own takes no model input beyond its base config. A refused call
is a failed tool result naming the keys; the tool doesn't run. The loop ends
when the model stops asking for tools, or halts with :class:`MaxTurnsExhaustedError` /
:class:`BudgetExhaustedError` when ``max_turns`` / ``budget_usd`` run out.
Adapters never raise to the engine (#211): a halt becomes a failed
``RuntimeResult`` whose ``structured["halt_reason"]`` names the limit.

Providers: ``anthropic`` (Messages API tool use); since slice 2, ``openai``
and ``openrouter`` (Chat Completions function calling, the same SDK and
endpoints as ``api-call``); since slice 3a, ``gemini`` (Google Gen AI SDK
function calling, key from ``GEMINI_API_KEY``). Each provider is a small
*dialect* that builds the request, parses tool calls and appends tool results
in its own wire shape; the loop, the tool dispatch and the bounds are shared.
The dashboard tools panel, the #785 audit trail, docs and the example bundle
follow.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import anthropic
import httpx
import openai
from dap_types import (
    BackendErrorCategory,
    HealthStatus,
    OutputCallback,
    RuntimeAdapter,
    RuntimeKind,
    RuntimeResult,
    RuntimeTask,
)
from google import genai
from google.genai import errors as genai_errors

from dap_runtimes.adapters._error_category import (
    category_for_status,
    category_for_transport_error,
    with_category,
)
from dap_runtimes.adapters._providers._anthropic import DEFAULT_ENV_VAR as ANTHROPIC_ENV_VAR
from dap_runtimes.adapters._providers._anthropic import _calculate_cost
from dap_runtimes.adapters._providers._base import ProviderError, classify_provider_failure
from dap_runtimes.adapters._providers._gemini import _calculate_cost as _gemini_cost
from dap_runtimes.adapters._providers._gemini import env_var_for as _gemini_env_var_for
from dap_runtimes.adapters._providers._openai import (
    DEFAULT_ENV_VAR as OPENAI_ENV_VAR,
)
from dap_runtimes.adapters._providers._openai import (
    OPENROUTER_BASE_URL,
    OPENROUTER_ENV_VAR,
    OPENROUTER_HEADERS,
)
from dap_runtimes.adapters._providers._openai import (
    _calculate_cost as _openai_cost,
)
from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.agent")

SUPPORTED_PROVIDERS: Final = frozenset({"anthropic", "openai", "openrouter", "gemini"})
DEFAULT_PROVIDER: Final = "anthropic"
DEFAULT_MAX_TURNS: Final = 20
DEFAULT_MAX_TOKENS: Final = 4096
# A tool result fed back to the model is capped so one verbose tool can't
# blow the context window.
MAX_TOOL_RESULT_CHARS: Final = 20_000
# Generic tool input schema, used for a runtime without a ``tool_input_schema()``
# of its own when the tool entry sets no ``input_schema``. Closed (#1022): such a
# tool runs with its base config only; an entry ``input_schema`` opens fields.
DEFAULT_INPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
# Model-input keys refused for every tool whatever its schema says (#1022), as
# is any key ending in ``_env``. See the module docstring.
PROTECTED_INPUT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "url",
        "api_url",
        "base_url",
        "env",
        "cwd",
        "command",
        "module",
        "function",
        "state_key",
        "auth",
        "headers",
        "callable_path",
        "shell",
        "binary_path",
        "extra_args",
        "request_template",
    }
)

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
        """Available when at least one provider has its API key set."""
        ready = sorted(p for p, d in _DIALECTS.items() if os.environ.get(d.env_var))
        if not ready:
            return HealthStatus(
                available=False,
                missing=[f"{d.env_var} ({p})" for p, d in sorted(_DIALECTS.items())],
            )
        return HealthStatus(available=True, version=", ".join(ready))

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

    def new_client(self, dialect: _Dialect) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return dialect.new_client()


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
        self.tools: dict[str, tuple[RuntimeAdapter, dict[str, Any], dict[str, Any]]] = {}
        self.tool_specs: list[dict[str, Any]] = []
        self.max_turns = DEFAULT_MAX_TURNS
        self.budget_usd: float | None = None
        self.model_id = ""
        self.dialect: _Dialect = _DIALECTS[DEFAULT_PROVIDER]

    def prepare(self) -> None:
        cfg = self.config
        provider = cfg.get("provider", DEFAULT_PROVIDER)
        if provider not in SUPPORTED_PROVIDERS:
            raise _ConfigError(
                f"agent provider '{provider}' is not supported yet; "
                f"supported: {sorted(SUPPORTED_PROVIDERS)}"
            )
        self.dialect = _DIALECTS[provider]
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
            tool = self.adapter.resolve_tool(runtime_id)
            schema = entry.get("input_schema") or _runtime_schema(tool) or DEFAULT_INPUT_SCHEMA
            if not isinstance(schema, dict):
                raise _ConfigError(f"tool '{name}' input_schema must be a mapping")
            self.tools[name] = (tool, base, schema)
            self.tool_specs.append(
                {
                    "name": name,
                    "description": str(entry.get("description") or f"The {runtime_id} runtime"),
                    "input_schema": schema,
                }
            )

    async def run(self) -> tuple[str, str | None]:
        cfg = self.config
        system_prompt = cfg.get("system_prompt")
        system = system_prompt if isinstance(system_prompt, str) and system_prompt else None
        dialect = self.dialect
        request, messages = dialect.build_request(
            model_id=self.model_id,
            max_tokens=cfg.get("max_tokens", DEFAULT_MAX_TOKENS),
            system=system,
            prompt=self.task.prompt_xml,
            tool_specs=self.tool_specs,
        )

        client = self.adapter.new_client(dialect)
        try:
            while True:
                response = await dialect.create(client, {**request, "messages": messages})
                self.turns += 1
                turn = dialect.parse(response)
                self._account(turn.usage)
                if not turn.tool_calls:
                    return turn.text, turn.stop_reason
                self._check_bounds()
                messages.extend(dialect.assistant_messages(response))
                results = [(call, *await self._call_tool(call)) for call in turn.tool_calls]
                messages.extend(dialect.tool_result_messages(results))
        finally:
            await dialect.close(client)

    def _account(self, usage: _Usage) -> None:
        self.tokens += usage.total
        cost = self.dialect.cost(self.model_id, usage)
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

    async def _call_tool(self, call: _ToolCall) -> tuple[str, bool]:
        """Run one tool call; returns the result text and whether it failed."""
        name = call.name
        tool_input = call.input
        record: dict[str, Any] = {"turn": self.turns, "tool": name, "input": tool_input}
        self.tool_calls.append(record)
        if call.input_error is not None:
            record["success"] = False
            return call.input_error, True
        if name not in self.tools:
            record["success"] = False
            return f"unknown tool '{name}'", True
        adapter, base, schema = self.tools[name]
        refused = _refused_keys(tool_input, schema)
        if refused:
            record["success"] = False
            return (
                f"refused tool input keys for '{name}': {', '.join(refused)} "
                "(protected, or not allowed by the tool's input schema)"
            ), True
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
            return f"{type(exc).__name__}: {exc}", True
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
        return text, not result.success

    def summary(self) -> dict[str, Any]:
        return {
            "provider": self.dialect.provider,
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


@dataclass(frozen=True)
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    # Cost the provider reported itself (OpenRouter usage accounting).
    reported_cost_usd: float | None = None

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )


@dataclass(frozen=True)
class _ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    # Set when the model's arguments could not be decoded; fed back as an error.
    input_error: str | None = None


@dataclass(frozen=True)
class _Turn:
    text: str
    tool_calls: list[_ToolCall]
    stop_reason: str | None
    usage: _Usage


class _Dialect:
    """One provider's wire shape for the tool loop."""

    provider: str = ""
    env_var: str = ""

    def new_client(self) -> Any:
        raise NotImplementedError

    def build_request(
        self,
        *,
        model_id: str,
        max_tokens: int,
        system: str | None,
        prompt: str,
        tool_specs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        raise NotImplementedError

    async def create(self, client: Any, request: dict[str, Any]) -> Any:
        raise NotImplementedError

    def parse(self, response: Any) -> _Turn:
        raise NotImplementedError

    def assistant_messages(self, response: Any) -> list[dict[str, Any]]:
        raise NotImplementedError

    def tool_result_messages(
        self, results: list[tuple[_ToolCall, str, bool]]
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def cost(self, model_id: str, usage: _Usage) -> float | None:
        return usage.reported_cost_usd

    async def close(self, client: Any) -> None:
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result

    def _require_key(self) -> str:
        key = os.environ.get(self.env_var)
        if not key:
            raise _ConfigError(f"{self.env_var} env var not set (agent provider '{self.provider}')")
        return key


class _AnthropicDialect(_Dialect):
    """Messages API: ``tool_use`` blocks out, ``tool_result`` blocks back."""

    provider = "anthropic"
    env_var = ANTHROPIC_ENV_VAR

    def new_client(self) -> Any:
        self._require_key()
        return anthropic.AsyncAnthropic()

    def build_request(
        self,
        *,
        model_id: str,
        max_tokens: int,
        system: str | None,
        prompt: str,
        tool_specs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        request: dict[str, Any] = {"model": model_id, "max_tokens": max_tokens}
        if system:
            request["system"] = system
        if tool_specs:
            request["tools"] = tool_specs
        return request, [{"role": "user", "content": prompt}]

    async def create(self, client: Any, request: dict[str, Any]) -> Any:
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

    def parse(self, response: Any) -> _Turn:
        content = list(response.content)
        calls: list[_ToolCall] = []
        if response.stop_reason == "tool_use":
            calls = [
                _ToolCall(
                    id=b.id,
                    name=b.name,
                    input=b.input if isinstance(b.input, dict) else {},
                )
                for b in content
                if getattr(b, "type", None) == "tool_use"
            ]
        text = "".join(b.text for b in content if getattr(b, "type", None) == "text")
        usage = response.usage
        return _Turn(
            text=text,
            tool_calls=calls,
            stop_reason=response.stop_reason,
            usage=_Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            ),
        )

    def assistant_messages(self, response: Any) -> list[dict[str, Any]]:
        return [{"role": "assistant", "content": [_block(b) for b in response.content]}]

    def tool_result_messages(
        self, results: list[tuple[_ToolCall, str, bool]]
    ) -> list[dict[str, Any]]:
        blocks = [
            {"type": "tool_result", "tool_use_id": call.id, "content": text, "is_error": is_error}
            for call, text, is_error in results
        ]
        return [{"role": "user", "content": blocks}]

    def cost(self, model_id: str, usage: _Usage) -> float | None:
        return _calculate_cost(
            model_id=model_id,
            input_tokens=usage.input_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            output_tokens=usage.output_tokens,
        )


class _OpenAIDialect(_Dialect):
    """Chat Completions: ``tool_calls`` out, one ``role: tool`` message per result."""

    provider = "openai"
    env_var = OPENAI_ENV_VAR
    # Native OpenAI reasoning models (gpt-5, o-series) reject ``max_tokens``.
    max_tokens_field = "max_completion_tokens"

    def new_client(self) -> Any:
        return openai.AsyncOpenAI(api_key=self._require_key())

    def build_request(
        self,
        *,
        model_id: str,
        max_tokens: int,
        system: str | None,
        prompt: str,
        tool_specs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        request: dict[str, Any] = {"model": model_id, self.max_tokens_field: max_tokens}
        if tool_specs:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec["name"],
                        "description": spec["description"],
                        "parameters": spec["input_schema"],
                    },
                }
                for spec in tool_specs
            ]
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return request, messages

    async def create(self, client: Any, request: dict[str, Any]) -> Any:
        """One Chat Completions call; SDK errors become ProviderError like api-call."""
        try:
            return await client.chat.completions.create(**request)
        except openai.AuthenticationError as exc:
            raise ProviderError(f"Authentication failed: {exc}") from exc
        except openai.PermissionDeniedError as exc:
            raise ProviderError(f"Permission denied: {exc}") from exc
        except openai.NotFoundError as exc:
            raise ProviderError(f"Model not found: {exc}") from exc
        except openai.BadRequestError as exc:
            category = classify_provider_failure(str(exc), status_code=400)
            label = "Out of credits / quota" if category == "out_of_credits" else "Bad request"
            raise ProviderError(f"{label}: {exc}", category=category) from exc
        except openai.RateLimitError as exc:
            category = classify_provider_failure(str(exc), status_code=429)
            label = "Out of credits / quota" if category == "out_of_credits" else "Rate limited"
            raise ProviderError(f"{label}: {exc}", category=category) from exc
        except openai.APITimeoutError as exc:
            raise ProviderError(f"API timeout: {exc}", category="timeout") from exc
        except openai.APIConnectionError as exc:
            raise ProviderError(f"Connection error: {exc}", category="unavailable") from exc
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"API error {exc.status_code}: {exc}",
                category=category_for_status(exc.status_code),
            ) from exc

    def parse(self, response: Any) -> _Turn:
        choice = response.choices[0]
        message = choice.message
        calls = [_openai_tool_call(tc) for tc in (getattr(message, "tool_calls", None) or [])]
        usage = response.usage
        cache_read = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cache_read = getattr(details, "cached_tokens", 0) or 0
        prompt_tokens = (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        reported = getattr(usage, "cost", None) if usage else None
        return _Turn(
            text=message.content or "",
            tool_calls=calls,
            stop_reason=choice.finish_reason,
            usage=_Usage(
                # OpenAI counts cached tokens inside prompt_tokens; split them
                # out so the token total isn't counted twice.
                input_tokens=prompt_tokens - cache_read,
                output_tokens=(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
                cache_read_tokens=cache_read,
                reported_cost_usd=(
                    float(reported)
                    if isinstance(reported, int | float) and not isinstance(reported, bool)
                    else None
                ),
            ),
        )

    def assistant_messages(self, response: Any) -> list[dict[str, Any]]:
        message = response.choices[0].message
        return [
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls or []
                ],
            }
        ]

    def tool_result_messages(
        self, results: list[tuple[_ToolCall, str, bool]]
    ) -> list[dict[str, Any]]:
        # Chat Completions has no is_error flag: an error is marked in the text.
        return [
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": f"ERROR: {text}" if is_error else text,
            }
            for call, text, is_error in results
        ]

    def cost(self, model_id: str, usage: _Usage) -> float | None:
        return _openai_cost(
            model_id, usage.input_tokens + usage.cache_read_tokens, usage.output_tokens
        )


class _OpenRouterDialect(_OpenAIDialect):
    """OpenRouter: the OpenAI shape on its gateway; cost comes from its usage accounting."""

    provider = "openrouter"
    env_var = OPENROUTER_ENV_VAR
    max_tokens_field = "max_tokens"

    def new_client(self) -> Any:
        return openai.AsyncOpenAI(
            api_key=self._require_key(),
            base_url=OPENROUTER_BASE_URL,
            default_headers=OPENROUTER_HEADERS,
        )

    def build_request(
        self,
        *,
        model_id: str,
        max_tokens: int,
        system: str | None,
        prompt: str,
        tool_specs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        request, messages = super().build_request(
            model_id=model_id,
            max_tokens=max_tokens,
            system=system,
            prompt=prompt,
            tool_specs=tool_specs,
        )
        # Ask OpenRouter to report the USD cost of each call in ``usage.cost``,
        # so budget_usd is enforced for any model on the gateway.
        request["extra_body"] = {"usage": {"include": True}}
        return request, messages

    def cost(self, model_id: str, usage: _Usage) -> float | None:
        return usage.reported_cost_usd


class _GeminiDialect(_Dialect):
    """Gen AI SDK: ``function_call`` parts out, ``function_response`` parts back."""

    provider = "gemini"
    env_var = _gemini_env_var_for({})

    def new_client(self) -> Any:
        return genai.Client(api_key=self._require_key())

    def build_request(
        self,
        *,
        model_id: str,
        max_tokens: int,
        system: str | None,
        prompt: str,
        tool_specs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        config: dict[str, Any] = {"max_output_tokens": max_tokens}
        if system:
            config["system_instruction"] = system
        if tool_specs:
            # parameters_json_schema takes full JSON Schema (additionalProperties
            # included), which the OpenAPI-subset ``parameters`` field rejects.
            config["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": spec["name"],
                            "description": spec["description"],
                            "parameters_json_schema": spec["input_schema"],
                        }
                        for spec in tool_specs
                    ]
                }
            ]
            # The loop runs the tools itself: no SDK automatic function calling.
            config["automatic_function_calling"] = {"disable": True}
        request: dict[str, Any] = {"model": model_id, "config": config}
        return request, [{"role": "user", "parts": [{"text": prompt}]}]

    async def create(self, client: Any, request: dict[str, Any]) -> Any:
        """One generate_content call; SDK errors become ProviderError like api-call."""
        payload = dict(request)
        contents = payload.pop("messages")
        try:
            return await client.aio.models.generate_content(contents=contents, **payload)
        except genai_errors.APIError as exc:
            status = getattr(exc, "code", None)
            status_code = status if isinstance(status, int) else None
            category = classify_provider_failure(str(exc), status_code=status_code)
            prefix = (
                "Out of credits / quota"
                if category == "out_of_credits"
                else "Rate limited"
                if category == "rate_limit"
                else f"Gemini API error {status}"
            )
            raise ProviderError(
                f"{prefix}: {exc}", category=category or category_for_status(status_code)
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Gemini call failed: {exc}", category=category_for_transport_error(exc)
            ) from exc

    def parse(self, response: Any) -> _Turn:
        parts = _gemini_parts(response)
        calls = [
            _gemini_tool_call(part.function_call)
            for part in parts
            if getattr(part, "function_call", None) is not None
        ]
        text = "".join(
            part.text
            for part in parts
            if isinstance(getattr(part, "text", None), str) and not getattr(part, "thought", None)
        )
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        cache_read = getattr(usage, "cached_content_token_count", 0) or 0
        # Thinking tokens are billed as output.
        output = (getattr(usage, "candidates_token_count", 0) or 0) + (
            getattr(usage, "thoughts_token_count", 0) or 0
        )
        candidates = getattr(response, "candidates", None) or []
        reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        return _Turn(
            text=text,
            tool_calls=calls,
            stop_reason=None if reason is None else str(getattr(reason, "value", reason)),
            usage=_Usage(
                # prompt_token_count includes the cached tokens; split them out
                # so the token total isn't counted twice.
                input_tokens=prompt_tokens - cache_read,
                output_tokens=output,
                cache_read_tokens=cache_read,
            ),
        )

    def assistant_messages(self, response: Any) -> list[dict[str, Any]]:
        content = response.candidates[0].content
        dump = getattr(content, "model_dump", None)
        if callable(dump):
            # Keeps thought signatures, which Gemini 3 requires on the echo.
            data = dump(exclude_none=True)
            if isinstance(data, dict):
                return [data]
        parts: list[dict[str, Any]] = []
        for part in _gemini_parts(response):
            call = getattr(part, "function_call", None)
            if call is not None:
                fc: dict[str, Any] = {"name": call.name, "args": call.args}
                if getattr(call, "id", None):
                    fc["id"] = call.id
                parts.append({"function_call": fc})
            elif isinstance(getattr(part, "text", None), str):
                parts.append({"text": part.text})
        return [{"role": "model", "parts": parts}]

    def tool_result_messages(
        self, results: list[tuple[_ToolCall, str, bool]]
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for call, text, is_error in results:
            # Gemini's convention: "output" for a result, "error" for a failure.
            function_response: dict[str, Any] = {
                "name": call.name,
                "response": {"error": text} if is_error else {"output": text},
            }
            if call.id:
                function_response["id"] = call.id
            parts.append({"function_response": function_response})
        return [{"role": "user", "parts": parts}]

    def cost(self, model_id: str, usage: _Usage) -> float | None:
        return _gemini_cost(
            model_id, usage.input_tokens + usage.cache_read_tokens, usage.output_tokens
        )

    async def close(self, client: Any) -> None:
        aio = getattr(client, "aio", None)
        aclose = getattr(aio, "aclose", None)
        if callable(aclose):
            await aclose()
        await super().close(client)


_DIALECTS: Final[dict[str, _Dialect]] = {
    d.provider: d
    for d in (_AnthropicDialect(), _OpenAIDialect(), _OpenRouterDialect(), _GeminiDialect())
}


def _gemini_parts(response: Any) -> list[Any]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    content = getattr(candidates[0], "content", None)
    return list(getattr(content, "parts", None) or [])


def _gemini_tool_call(function_call: Any) -> _ToolCall:
    # The Gemini Developer API may omit call ids; the id is then left empty and
    # the function_response is matched by name and order.
    call_id = getattr(function_call, "id", None) or ""
    name = function_call.name or ""
    args = function_call.args
    if args is None:
        return _ToolCall(id=call_id, name=name, input={})
    if not isinstance(args, dict):
        return _ToolCall(
            id=call_id, name=name, input={}, input_error="tool arguments must be an object"
        )
    return _ToolCall(id=call_id, name=name, input=dict(args))


def _openai_tool_call(tool_call: Any) -> _ToolCall:
    function = tool_call.function
    raw = function.arguments or "{}"
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return _ToolCall(
            id=tool_call.id,
            name=function.name,
            input={},
            input_error=f"tool arguments are not valid JSON: {exc}",
        )
    if not isinstance(decoded, dict):
        return _ToolCall(
            id=tool_call.id,
            name=function.name,
            input={},
            input_error="tool arguments must be a JSON object",
        )
    return _ToolCall(id=tool_call.id, name=function.name, input=decoded)


def _block(block: Any) -> dict[str, Any]:
    """Serialize an Anthropic response content block for the next request."""
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


def _runtime_schema(tool: RuntimeAdapter) -> dict[str, Any] | None:
    """The runtime's own tool input schema, if it declares one (#1020)."""
    method = getattr(tool, "tool_input_schema", None)
    if not callable(method):
        return None
    schema = method()
    return schema if isinstance(schema, dict) else None


def _refused_keys(tool_input: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Top-level input keys the tool may not take (#1020, #1022).

    ``__``-prefixed, deny-listed and ``*_env`` keys always, compared
    lower-cased (#1037); unless the schema is explicitly open
    (``additionalProperties: true``) any key outside ``properties``; with any
    schema an object- or list-valued key outside ``properties``.
    """
    listed = schema.get("properties")
    allowed = listed if isinstance(listed, dict) else {}
    closed = schema.get("additionalProperties") is not True
    return sorted(
        key
        for key, value in tool_input.items()
        if _is_protected(key)
        or (key not in allowed and (closed or isinstance(value, (dict, list))))
    )


def _is_protected(key: str) -> bool:
    """A key no schema can open: ``__``-prefixed, deny-listed or ``*_env``, any case."""
    folded = key.casefold()
    return folded.startswith("__") or folded in PROTECTED_INPUT_KEYS or folded.endswith("_env")


def _tool_name(raw: str) -> str:
    """Anthropic tool names allow ``[a-zA-Z0-9_-]{1,64}``."""
    name = "".join(c if c.isalnum() or c in "_-" else "_" for c in raw)[:64]
    if not name:
        raise _ConfigError("tool name is empty")
    return name


__all__ = [
    "PROTECTED_INPUT_KEYS",
    "AgentAdapter",
    "AgentHaltError",
    "BudgetExhaustedError",
    "MaxTurnsExhaustedError",
]
