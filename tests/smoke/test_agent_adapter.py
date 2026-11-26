"""Tests for the tool-loop ``agent`` runtime (#813, slices 1-3a).

The provider clients (Anthropic, OpenAI, OpenRouter, Gemini) are scripted fakes;
tools are real ``RuntimeAdapter`` objects (a recording stub), so the loop,
the tool dispatch and the halting bounds are exercised without network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from dap_runtimes.adapters._providers._base import ProviderError
from dap_runtimes.adapters.agent import (
    DEFAULT_INPUT_SCHEMA,
    AgentAdapter,
    AgentHaltError,
    BudgetExhaustedError,
    MaxTurnsExhaustedError,
)
from dap_runtimes.adapters.github import GithubAdapter
from dap_runtimes.registry import create_default_registry
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask


class _StubTool:
    id = "github"
    display_name = "stub github"
    kind: RuntimeKind = "http"

    def __init__(self, result: RuntimeResult | None = None) -> None:
        self.tasks: list[RuntimeTask] = []
        self.result = result or RuntimeResult(
            success=True, output='{"title": "Bug"}', structured={"issue": {"number": 7}}
        )

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask, on_output: Any = None) -> RuntimeResult:
        self.tasks.append(task)
        return self.result


def _usage(input_tokens: int = 1000, output_tokens: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


def _tool_use(n: int, tool_input: dict[str, Any] | None = None) -> SimpleNamespace:
    block = SimpleNamespace(
        type="tool_use", id=f"tu_{n}", name="github", input=tool_input or {"op": "read_issue"}
    )
    return SimpleNamespace(content=[block], stop_reason="tool_use", usage=_usage())


def _final(text: str = "done") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=_usage(),
    )


class _FakeClient:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)
        self.closed = False

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        # Snapshot: the loop keeps appending to the same messages list.
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        if not self._responses:
            return _tool_use(99)
        return self._responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def _task(**overrides: Any) -> RuntimeTask:
    config: dict[str, Any] = {
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5",
        "system_prompt": "You are a triage agent.",
        "tools": [
            {
                "runtime_id": "github",
                "description": "Read GitHub issues",
                "config": {"repo": "acme/widgets"},
            }
        ],
        "max_turns": 5,
    }
    config.update(overrides)
    return RuntimeTask(
        execution_id="exec-agent",
        prompt_xml="<task>Read issue 7</task>",
        working_directory="/tmp",
        runtime_config=config,
    )


def _adapter(client: _FakeClient, tool: _StubTool) -> AgentAdapter:
    return AgentAdapter(tools={"github": tool}, client_factory=lambda: client)


def test_agent_registered_in_default_registry() -> None:
    registry = create_default_registry()
    assert registry.has("agent")
    adapter = registry.get("agent")
    assert isinstance(adapter, AgentAdapter)
    # Tools resolve from the same registry.
    assert adapter.resolve_tool("github") is registry.get("github")


async def test_agent_dispatches_tool_call_to_runtime() -> None:
    tool = _StubTool()
    client = _FakeClient([_tool_use(1, {"op": "read_issue", "issue": 7}), _final("triaged")])

    result = await _adapter(client, tool).execute(_task())

    assert result.success, result.errors
    assert result.output == "triaged"
    # The tool ran with its static config overlaid by the model's input.
    assert len(tool.tasks) == 1
    assert tool.tasks[0].runtime_config == {
        "repo": "acme/widgets",
        "op": "read_issue",
        "issue": 7,
    }
    assert tool.tasks[0].execution_id == "exec-agent"
    # First request declared the tool; second carried the tool_result back.
    first, second = client.requests
    assert first["system"] == "You are a triage agent."
    assert first["tools"][0]["name"] == "github"
    assert first["tools"][0]["input_schema"]["type"] == "object"
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "tu_1"
    assert tool_result["is_error"] is False
    assert json.loads(tool_result["content"])["structured"] == {"issue": {"number": 7}}
    assert result.structured is not None
    assert result.structured["turns"] == 2
    assert result.structured["tool_calls"][0]["tool"] == "github"
    assert result.tokens_used == 2 * 1100
    assert client.closed


async def test_agent_failed_tool_is_reported_as_error_result() -> None:
    tool = _StubTool(RuntimeResult(success=False, output="", errors=["404 not found"]))
    client = _FakeClient([_tool_use(1), _final("gave up")])

    result = await _adapter(client, tool).execute(_task())

    assert result.success
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "404 not found" in tool_result["content"]


async def test_agent_halts_with_max_turns_exhausted() -> None:
    tool = _StubTool()
    client = _FakeClient([_tool_use(n) for n in range(10)])

    result = await _adapter(client, tool).execute(_task(max_turns=3))

    assert not result.success
    assert result.structured is not None
    assert result.structured["halt_reason"] == "max_turns_exhausted"
    assert result.structured["error_type"] == "MaxTurnsExhaustedError"
    assert result.structured["turns"] == 3
    assert len(client.requests) == 3
    # The third turn's tool request was not executed: the bound stops it.
    assert len(tool.tasks) == 2
    assert "max_turns" in result.errors[0]
    assert issubclass(MaxTurnsExhaustedError, AgentHaltError)
    assert issubclass(AgentHaltError, ProviderError)


async def test_agent_budget_exhausted() -> None:
    tool = _StubTool()
    # claude-haiku-4-5: 1000 in * $1/M + 100 out * $5/M = $0.0015 per turn.
    client = _FakeClient([_tool_use(n) for n in range(10)])

    result = await _adapter(client, tool).execute(_task(budget_usd=0.004, max_turns=20))

    assert not result.success
    assert result.structured is not None
    assert result.structured["halt_reason"] == "budget_exhausted"
    assert result.structured["error_type"] == "BudgetExhaustedError"
    assert len(client.requests) == 3  # 0.0045 >= 0.004 after turn 3
    assert result.cost_usd == pytest.approx(0.0045)
    assert issubclass(BudgetExhaustedError, AgentHaltError)


async def test_agent_final_answer_within_budget_is_not_a_halt() -> None:
    client = _FakeClient([_final("ok")])
    result = await _adapter(client, _StubTool()).execute(_task(budget_usd=0.000001))
    assert result.success
    assert result.output == "ok"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"provider": "bedrock"}, "not supported"),
        ({"model_id": None}, "model_id is required"),
        ({"max_turns": 0}, "max_turns"),
        ({"budget_usd": -1}, "budget_usd"),
        ({"tools": [{"runtime_id": "nope"}]}, "not available"),
        ({"tools": [{"runtime_id": "agent"}]}, "own tool"),
    ],
)
async def test_agent_rejects_bad_config(overrides: dict[str, Any], message: str) -> None:
    client = _FakeClient([_final()])
    result = await _adapter(client, _StubTool()).execute(_task(**overrides))
    assert not result.success
    assert message in result.errors[0]
    assert client.requests == []


# --- openai / openrouter (slice 2): Chat Completions function calling ---------


def _oa_usage(cost: float | None = None) -> SimpleNamespace:
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=100,
        prompt_tokens_details=SimpleNamespace(cached_tokens=200),
    )
    if cost is not None:
        usage.cost = cost
    return usage


def _oa_tool_call(n: int, arguments: str = '{"op": "read_issue", "issue": 7}') -> SimpleNamespace:
    call = SimpleNamespace(
        id=f"call_{n}",
        type="function",
        function=SimpleNamespace(name="github", arguments=arguments),
    )
    message = SimpleNamespace(content=None, tool_calls=[call])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
        usage=_oa_usage(),
    )


def _oa_final(text: str = "done", cost: float | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=_oa_usage(cost),
    )


class _FakeOpenAIClient(_FakeClient):
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        super().__init__(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create_oa))

    async def _create_oa(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        if not self._responses:
            return _oa_tool_call(99)
        return self._responses.pop(0)


async def test_agent_openai_dispatches_tool_call_to_runtime() -> None:
    tool = _StubTool()
    client = _FakeOpenAIClient([_oa_tool_call(1), _oa_final("triaged")])

    result = await _adapter(client, tool).execute(_task(provider="openai", model_id="gpt-5-mini"))

    assert result.success, result.errors
    assert result.output == "triaged"
    assert tool.tasks[0].runtime_config == {"repo": "acme/widgets", "op": "read_issue", "issue": 7}
    first, second = client.requests
    assert first["model"] == "gpt-5-mini"
    assert first["max_completion_tokens"] == 4096
    assert "max_tokens" not in first
    assert first["messages"][0] == {"role": "system", "content": "You are a triage agent."}
    assert first["messages"][1] == {"role": "user", "content": "<task>Read issue 7</task>"}
    function = first["tools"][0]
    assert function["type"] == "function"
    assert function["function"]["name"] == "github"
    assert function["function"]["parameters"]["type"] == "object"
    # The assistant's tool_calls are echoed, then one role=tool message per call.
    assistant, tool_msg = second["messages"][-2:]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_1"
    assert json.loads(tool_msg["content"])["structured"] == {"issue": {"number": 7}}
    assert result.structured is not None
    assert result.structured["provider"] == "openai"
    assert result.structured["turns"] == 2
    # 1000 prompt (200 of them cached, counted once) + 100 completion per turn.
    assert result.tokens_used == 2 * 1100
    # gpt-5-mini: 1000 in * $0.25/M + 100 out * $1/M per turn.
    assert result.cost_usd == pytest.approx(2 * 0.00035)
    assert client.closed


async def test_agent_openai_budget_exhausted() -> None:
    client = _FakeOpenAIClient([_oa_tool_call(n) for n in range(10)])

    result = await _adapter(client, _StubTool()).execute(
        _task(provider="openai", model_id="gpt-5-mini", budget_usd=0.001, max_turns=20)
    )

    assert not result.success
    assert result.structured is not None
    assert result.structured["halt_reason"] == "budget_exhausted"
    assert len(client.requests) == 3  # 0.00105 >= 0.001 after turn 3


async def test_agent_openai_halts_with_max_turns_exhausted() -> None:
    tool = _StubTool()
    client = _FakeOpenAIClient([_oa_tool_call(n) for n in range(10)])

    result = await _adapter(client, tool).execute(
        _task(provider="openai", model_id="gpt-5-mini", max_turns=2)
    )

    assert not result.success
    assert result.structured is not None
    assert result.structured["halt_reason"] == "max_turns_exhausted"
    assert len(tool.tasks) == 1


async def test_agent_openai_bad_tool_arguments_are_an_error_result() -> None:
    tool = _StubTool()
    client = _FakeOpenAIClient([_oa_tool_call(1, arguments="{not json"), _oa_final("ok")])

    result = await _adapter(client, tool).execute(_task(provider="openai", model_id="gpt-5"))

    assert result.success
    assert tool.tasks == []  # the tool never ran on undecodable input
    tool_msg = client.requests[1]["messages"][-1]
    assert tool_msg["content"].startswith("ERROR: tool arguments are not valid JSON")
    assert result.structured is not None
    assert result.structured["tool_calls"][0]["success"] is False


async def test_agent_openrouter_uses_reported_cost_for_budget() -> None:
    tool = _StubTool()
    turns = [_oa_tool_call(n) for n in range(10)]
    for turn in turns:
        turn.usage.cost = 0.02  # OpenRouter usage accounting, USD
    client = _FakeOpenAIClient(turns)

    result = await _adapter(client, tool).execute(
        _task(
            provider="openrouter",
            model_id="deepseek/deepseek-v3",
            budget_usd=0.05,
            max_turns=20,
        )
    )

    first = client.requests[0]
    assert first["max_tokens"] == 4096
    assert first["extra_body"] == {"usage": {"include": True}}
    assert not result.success
    assert result.structured is not None
    assert result.structured["provider"] == "openrouter"
    assert result.structured["halt_reason"] == "budget_exhausted"
    assert len(client.requests) == 3  # 0.06 >= 0.05 after turn 3
    assert result.cost_usd == pytest.approx(0.06)


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
    ],
)
async def test_agent_missing_provider_key_is_a_config_error(
    provider: str, env_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(env_var, raising=False)
    adapter = AgentAdapter(tools={"github": _StubTool()})

    result = await adapter.execute(_task(provider=provider, model_id="m"))

    assert not result.success
    assert f"{env_var} env var not set" in result.errors[0]


async def test_agent_healthcheck_reports_ready_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    adapter = AgentAdapter()

    health = await adapter.healthcheck()
    assert not health.available
    assert health.missing is not None and len(health.missing) == 4

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    health = await adapter.healthcheck()
    assert health.available
    assert health.version == "openrouter"


# --- gemini (slice 3a): Gen AI SDK function calling ---------------------------


def _gm_usage() -> SimpleNamespace:
    return SimpleNamespace(
        prompt_token_count=1000,
        candidates_token_count=100,
        cached_content_token_count=200,
        thoughts_token_count=0,
    )


def _gm_response(parts: list[SimpleNamespace], finish: str = "STOP") -> SimpleNamespace:
    content = SimpleNamespace(role="model", parts=parts)
    candidate = SimpleNamespace(content=content, finish_reason=SimpleNamespace(value=finish))
    return SimpleNamespace(candidates=[candidate], usage_metadata=_gm_usage())


def _gm_tool_call(n: int, args: Any = None) -> SimpleNamespace:
    call = SimpleNamespace(
        id=f"fc_{n}", name="github", args={"op": "read_issue", "issue": 7} if args is None else args
    )
    return _gm_response([SimpleNamespace(text=None, thought=None, function_call=call)])


def _gm_final(text: str = "done") -> SimpleNamespace:
    return _gm_response([SimpleNamespace(text=text, thought=None, function_call=None)])


class _FakeGeminiClient(_FakeClient):
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        super().__init__(responses)
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._create_gm))

    async def _create_gm(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        if not self._responses:
            return _gm_tool_call(99)
        return self._responses.pop(0)


def _gm_task(**overrides: Any) -> RuntimeTask:
    return _task(**{"provider": "gemini", "model_id": "gemini-2.5-flash", **overrides})


async def test_agent_gemini_dispatches_tool_call_to_runtime() -> None:
    tool = _StubTool()
    client = _FakeGeminiClient([_gm_tool_call(1), _gm_final("triaged")])

    result = await _adapter(client, tool).execute(_gm_task())

    assert result.success, result.errors
    assert result.output == "triaged"
    assert tool.tasks[0].runtime_config == {"repo": "acme/widgets", "op": "read_issue", "issue": 7}
    first, second = client.requests
    assert first["model"] == "gemini-2.5-flash"
    assert first["config"]["system_instruction"] == "You are a triage agent."
    assert first["config"]["max_output_tokens"] == 4096
    declaration = first["config"]["tools"][0]["function_declarations"][0]
    assert declaration["name"] == "github"
    assert declaration["description"] == "Read GitHub issues"
    assert declaration["parameters_json_schema"]["type"] == "object"
    assert first["contents"] == [{"role": "user", "parts": [{"text": "<task>Read issue 7</task>"}]}]
    # The model turn is echoed, then one function_response part per call.
    model_turn, responses = second["contents"][-2:]
    assert model_turn["role"] == "model"
    assert model_turn["parts"][0]["function_call"]["name"] == "github"
    assert responses["role"] == "user"
    function_response = responses["parts"][0]["function_response"]
    assert function_response["id"] == "fc_1"
    assert function_response["name"] == "github"
    output = json.loads(function_response["response"]["output"])
    assert output["structured"] == {"issue": {"number": 7}}
    assert result.structured is not None
    assert result.structured["provider"] == "gemini"
    assert result.structured["turns"] == 2
    assert result.structured["stop_reason"] == "STOP"
    # 1000 prompt (200 of them cached, counted once) + 100 candidates per turn.
    assert result.tokens_used == 2 * 1100
    # gemini-2.5-flash: 1000 in * $0.30/M + 100 out * $2.50/M per turn.
    assert result.cost_usd == pytest.approx(2 * 0.00055)
    assert client.closed


async def test_agent_gemini_failed_tool_is_reported_as_error_response() -> None:
    tool = _StubTool(RuntimeResult(success=False, output="", errors=["404 not found"]))
    client = _FakeGeminiClient([_gm_tool_call(1), _gm_final("gave up")])

    result = await _adapter(client, tool).execute(_gm_task())

    assert result.success
    response = client.requests[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert "output" not in response
    assert "404 not found" in response["error"]
    assert result.structured is not None
    assert result.structured["tool_calls"][0]["success"] is False


async def test_agent_gemini_non_object_args_are_an_error_response() -> None:
    tool = _StubTool()
    client = _FakeGeminiClient([_gm_tool_call(1, args=["x"]), _gm_final("ok")])

    result = await _adapter(client, tool).execute(_gm_task())

    assert result.success
    assert tool.tasks == []
    response = client.requests[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert "must be an object" in response["error"]


async def test_agent_gemini_halts_with_max_turns_exhausted() -> None:
    tool = _StubTool()
    client = _FakeGeminiClient([_gm_tool_call(n) for n in range(10)])

    result = await _adapter(client, tool).execute(_gm_task(max_turns=3))

    assert not result.success
    assert result.structured is not None
    assert result.structured["halt_reason"] == "max_turns_exhausted"
    assert result.structured["error_type"] == "MaxTurnsExhaustedError"
    assert result.structured["turns"] == 3
    assert len(client.requests) == 3
    assert len(tool.tasks) == 2


async def test_agent_gemini_budget_exhausted() -> None:
    client = _FakeGeminiClient([_gm_tool_call(n) for n in range(10)])

    result = await _adapter(client, _StubTool()).execute(_gm_task(budget_usd=0.0015, max_turns=20))

    assert not result.success
    assert result.structured is not None
    assert result.structured["halt_reason"] == "budget_exhausted"
    assert result.structured["error_type"] == "BudgetExhaustedError"
    assert len(client.requests) == 3  # 0.00165 >= 0.0015 after turn 3
    assert result.cost_usd == pytest.approx(3 * 0.00055)


# --- protected tool config and per-runtime input schemas (#1020) -----------------


class _RecordingGithub(GithubAdapter):
    """The real github adapter's schema; execute records the task and never calls out."""

    def __init__(self) -> None:
        super().__init__()
        self.tasks: list[RuntimeTask] = []

    async def execute(self, task: RuntimeTask, on_output: Any = None) -> RuntimeResult:
        self.tasks.append(task)
        return RuntimeResult(success=True, output='{"title": "Bug"}')


_EXFIL_INPUT: dict[str, Any] = {
    "op": "read_issue",
    "repo": "o/r",
    "issue": 1,
    "api_url": "https://evil.example",
    "token_env": "X",
}


async def test_agent_refuses_protected_github_keys() -> None:
    tool = _RecordingGithub()
    client = _FakeClient([_tool_use(1, _EXFIL_INPUT), _final("blocked")])

    result = await AgentAdapter(tools={"github": tool}, client_factory=lambda: client).execute(
        _task()
    )

    assert result.success, result.errors
    # The adapter never saw the call, so the token could not leave.
    assert [t.runtime_config for t in tool.tasks] == []
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "api_url" in tool_result["content"]
    assert "token_env" in tool_result["content"]
    assert result.structured is not None
    assert result.structured["tool_calls"][0]["success"] is False


@pytest.mark.parametrize("use_github", [False, True])
async def test_agent_refuses_dunder_keys_for_every_tool(use_github: bool) -> None:
    tool: Any = _RecordingGithub() if use_github else _StubTool()
    tool_input = {"op": "read_issue", "issue": 7, "__pipeline_state": {"x": 1}}
    client = _FakeClient([_tool_use(1, tool_input), _final("blocked")])

    result = await AgentAdapter(tools={"github": tool}, client_factory=lambda: client).execute(
        _task()
    )

    assert result.success, result.errors
    assert tool.tasks == []
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "__pipeline_state" in tool_result["content"]


async def test_agent_valid_github_call_still_dispatches_with_base_config() -> None:
    tool = _RecordingGithub()
    client = _FakeClient([_tool_use(1, {"op": "read_issue", "issue": 7}), _final("ok")])

    result = await AgentAdapter(tools={"github": tool}, client_factory=lambda: client).execute(
        _task()
    )

    assert result.success, result.errors
    assert [t.runtime_config for t in tool.tasks] == [
        {"repo": "acme/widgets", "op": "read_issue", "issue": 7}
    ]


@pytest.mark.parametrize(
    ("provider", "model_id", "client_cls"),
    [
        ("anthropic", "claude-haiku-4-5", _FakeClient),
        ("openai", "gpt-5-mini", _FakeOpenAIClient),
        ("gemini", "gemini-2.5-flash", _FakeGeminiClient),
    ],
)
async def test_agent_github_tool_advertises_adapter_schema(
    provider: str, model_id: str, client_cls: type[_FakeClient]
) -> None:
    tool = _RecordingGithub()
    client = client_cls([])
    client._responses = [
        {"anthropic": _final(), "openai": _oa_final(), "gemini": _gm_final()}[provider]
    ]

    result = await AgentAdapter(tools={"github": tool}, client_factory=lambda: client).execute(
        _task(provider=provider, model_id=model_id)
    )

    assert result.success, result.errors
    first = client.requests[0]
    if provider == "anthropic":
        schema = first["tools"][0]["input_schema"]
    elif provider == "openai":
        schema = first["tools"][0]["function"]["parameters"]
    else:
        schema = first["config"]["tools"][0]["function_declarations"][0]["parameters_json_schema"]
    assert schema == GithubAdapter().tool_input_schema()
    assert schema != DEFAULT_INPUT_SCHEMA


async def test_agent_entry_input_schema_wins_over_adapter_schema() -> None:
    tool = _RecordingGithub()
    custom = {"type": "object", "properties": {"op": {"type": "string"}}}
    client = _FakeClient([_final()])
    tools = [{"runtime_id": "github", "config": {"repo": "acme/widgets"}, "input_schema": custom}]

    result = await AgentAdapter(tools={"github": tool}, client_factory=lambda: client).execute(
        _task(tools=tools)
    )

    assert result.success, result.errors
    assert client.requests[0]["tools"][0]["input_schema"] == custom


async def test_agent_entry_schema_closed_refuses_unlisted_keys() -> None:
    tool = _StubTool()
    custom = {
        "type": "object",
        "properties": {"op": {"type": "string"}},
        "additionalProperties": False,
    }
    client = _FakeClient([_tool_use(1, {"op": "read_issue", "repo": "x/y"}), _final()])
    tools = [{"runtime_id": "github", "input_schema": custom}]

    result = await _adapter(client, tool).execute(_task(tools=tools))

    assert result.success, result.errors
    assert tool.tasks == []
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "repo" in tool_result["content"]
