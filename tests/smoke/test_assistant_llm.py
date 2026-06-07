"""Config-assistant inference — provider selection + grounded reply (#689 slice 2)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dap_engine.assistant.service import (
    AssistantReply,
    build_transcript,
    generate_reply,
    render_context,
    run_llm,
    select_provider,
)
from dap_types import RuntimeResult, RuntimeTask
from fastapi.testclient import TestClient

# ---- provider selection (pure) -------------------------------------------


def test_select_provider_claude_code_override_needs_no_key() -> None:
    # Explicit opt-in routes the assistant through the claude-code CLI on the
    # subscription — no API key required.
    assert select_provider({"DAP_ASSISTANT_PROVIDER": "claude-code"}) == (
        "claude-code",
        "claude-haiku-4-5",
    )


def test_select_provider_claude_code_honours_model_override() -> None:
    assert select_provider(
        {"DAP_ASSISTANT_PROVIDER": "claude-code", "DAP_ASSISTANT_MODEL": "claude-sonnet-4-6"}
    ) == ("claude-code", "claude-sonnet-4-6")


def test_select_provider_picks_anthropic_when_key_present() -> None:
    choice = select_provider({"ANTHROPIC_API_KEY": "x"})
    assert choice == ("anthropic", "claude-haiku-4-5")


def test_select_provider_none_when_no_key() -> None:
    assert select_provider({"FOO": "bar"}) is None


def test_select_provider_honours_overrides() -> None:
    choice = select_provider(
        {
            "OPENAI_API_KEY": "x",
            "DAP_ASSISTANT_PROVIDER": "openai",
            "DAP_ASSISTANT_MODEL": "gpt-4o",
        }
    )
    assert choice == ("openai", "gpt-4o")


def test_select_provider_priority_prefers_anthropic_over_gemini() -> None:
    choice = select_provider({"GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a"})
    assert choice == ("anthropic", "claude-haiku-4-5")


def test_build_transcript_renders_turns() -> None:
    t = build_transcript(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    )
    assert "User: hi" in t
    assert "Assistant: yo" in t
    assert t.rstrip().endswith("Assistant:")


# ---- render_context (pure, #689 phase 2) ----------------------------------


def test_render_context_none_is_empty() -> None:
    assert render_context(None) == ""
    assert render_context({}) == ""


def test_render_context_includes_route_and_fields() -> None:
    block = render_context({"route": "/agents/new", "name": "PR reviewer", "role": "verifier"})
    assert "Current context" in block
    assert "/agents/new" in block
    assert "PR reviewer" in block
    assert "verifier" in block


def test_render_context_redacts_secret_looking_keys() -> None:
    block = render_context(
        {
            "route": "/settings",
            "api_key": "sk-super-secret",
            "github_token": "ghp_zzz",
            "password": "hunter2",
            "name": "ok-to-show",
        }
    )
    assert "sk-super-secret" not in block
    assert "ghp_zzz" not in block
    assert "hunter2" not in block
    assert "[redacted]" in block
    # non-secret fields still surface
    assert "ok-to-show" in block
    assert "/settings" in block


def test_render_context_caps_size() -> None:
    block = render_context({"blob": "x" * 50_000})
    assert len(block) <= 4_500  # capped well below the raw payload


# ---- generate_reply (provider call injected) ------------------------------


class _FakeAdapter:
    def __init__(self, result: RuntimeResult) -> None:
        self._result = result
        self.last_task: RuntimeTask | None = None

    async def execute(self, task: RuntimeTask, on_output: object = None) -> RuntimeResult:
        self.last_task = task
        return self._result


@pytest.mark.asyncio
async def test_generate_reply_no_provider() -> None:
    reply = await generate_reply([{"role": "user", "content": "hi"}], env={})
    assert isinstance(reply, AssistantReply)
    assert reply.grounded is False
    assert "no LLM provider" in reply.text


@pytest.mark.asyncio
async def test_run_llm_routes_claude_code_to_cli_subscription() -> None:
    fake = _FakeAdapter(RuntimeResult(success=True, output="  use api-call + haiku  "))
    text = await run_llm(
        system_prompt="SYS-INSTRUCTIONS",
        user_text="User: cheap reviewer\nAssistant:",
        provider_id="claude-code",
        model_id="claude-haiku-4-5",
        env={"ANTHROPIC_API_KEY": "should-not-be-used"},
        adapter=fake,  # type: ignore[arg-type]
    )
    assert text == "use api-call + haiku"
    assert fake.last_task is not None
    cfg = fake.last_task.runtime_config
    # Runs on the subscription pool (free), not a metered api-call.
    assert cfg["use_subscription"] is True
    assert cfg["model_id"] == "claude-haiku-4-5"
    assert "provider" not in cfg  # not the ApiCallAdapter shape
    # system prompt + transcript are piped together (claude-code has no system field).
    assert "SYS-INSTRUCTIONS" in fake.last_task.prompt_xml
    assert "cheap reviewer" in fake.last_task.prompt_xml
    # No API key overlaid — the CLI uses the host's `claude login` session.
    assert fake.last_task.instance_env_vars == {}


@pytest.mark.asyncio
async def test_generate_reply_via_claude_code_subscription() -> None:
    fake = _FakeAdapter(RuntimeResult(success=True, output="use api-call + haiku"))
    reply = await generate_reply(
        [{"role": "user", "content": "cheap reviewer"}],
        env={"DAP_ASSISTANT_PROVIDER": "claude-code"},
        adapter=fake,  # type: ignore[arg-type]
    )
    assert reply.grounded is True
    assert reply.text == "use api-call + haiku"
    assert fake.last_task is not None
    assert fake.last_task.runtime_config["use_subscription"] is True


@pytest.mark.asyncio
async def test_generate_reply_success_is_grounded() -> None:
    fake = _FakeAdapter(RuntimeResult(success=True, output="  use api-call + haiku  "))
    secret = "sk-secret-zzz-do-not-leak"
    reply = await generate_reply(
        [{"role": "user", "content": "cheap PR reviewer"}],
        env={"ANTHROPIC_API_KEY": secret},
        adapter=fake,  # type: ignore[arg-type]
    )
    assert reply.grounded is True
    assert reply.text == "use api-call + haiku"
    # The docs corpus is stuffed into the system prompt — grounding, no secrets.
    assert fake.last_task is not None
    sysprompt = fake.last_task.runtime_config["system_prompt"]
    assert "DAP configuration reference" in sysprompt
    assert secret not in sysprompt  # the key VALUE never enters the prompt
    assert secret not in fake.last_task.prompt_xml


@pytest.mark.asyncio
async def test_generate_reply_threads_context_into_prompt() -> None:
    fake = _FakeAdapter(RuntimeResult(success=True, output="ok"))
    await generate_reply(
        [{"role": "user", "content": "what runtime should this agent use?"}],
        env={"ANTHROPIC_API_KEY": "x"},
        context={"route": "/agents/new", "name": "PR reviewer", "api_key": "sk-leak"},
        adapter=fake,  # type: ignore[arg-type]
    )
    assert fake.last_task is not None
    prompt = fake.last_task.prompt_xml
    # the page context reaches the model …
    assert "/agents/new" in prompt
    assert "PR reviewer" in prompt
    # … but a secret-looking value is redacted even if the client sent one.
    assert "sk-leak" not in prompt


@pytest.mark.asyncio
async def test_generate_reply_passes_only_the_provider_key_to_adapter() -> None:
    # Only the selected provider's key may reach the adapter env — never other
    # instance secrets (DB URLs, unrelated keys). #721 review (HIGH security).
    fake = _FakeAdapter(RuntimeResult(success=True, output="ok"))
    await generate_reply(
        [{"role": "user", "content": "x"}],
        env={
            "ANTHROPIC_API_KEY": "the-provider-key",
            "ZZ_DATABASE_URL": "postgres://secret",
            "ZZ_OTHER_SECRET": "nope",
        },
        adapter=fake,  # type: ignore[arg-type]
    )
    assert fake.last_task is not None
    assert fake.last_task.instance_env_vars == {"ANTHROPIC_API_KEY": "the-provider-key"}


@pytest.mark.asyncio
async def test_generate_reply_failure_is_graceful() -> None:
    fake = _FakeAdapter(RuntimeResult(success=False, output="", errors=["boom"]))
    reply = await generate_reply(
        [{"role": "user", "content": "x"}],
        env={"ANTHROPIC_API_KEY": "x"},
        adapter=fake,  # type: ignore[arg-type]
    )
    assert reply.grounded is False
    assert "failed" in reply.text.lower()


# ---- endpoint -------------------------------------------------------------


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    return authed_client


def test_chat_endpoint_contract(client: TestClient) -> None:
    resp = client.post("/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["message"]["role"] == "assistant"
    assert isinstance(body["message"]["content"], str) and body["message"]["content"]
    # actions contract is present (empty until slice 3).
    assert body["actions"] == []


@pytest.fixture
def client_no_auth(engine_config_factory) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    from dap_engine.app import create_app

    app = create_app(engine_config_factory())
    with TestClient(app) as c:
        yield c


def test_chat_endpoint_requires_auth(client_no_auth: TestClient) -> None:
    resp = client_no_auth.post(
        "/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 401


# ---- action parsing (#689 slice 3) ---------------------------------------


def test_parse_actions_none() -> None:
    from dap_engine.assistant.service import parse_actions

    text, actions = parse_actions("just a plain answer, no actions")
    assert actions == []
    assert text == "just a plain answer, no actions"


def test_parse_actions_extracts_and_strips_block() -> None:
    from dap_engine.assistant.service import parse_actions

    reply = (
        "Use api-call + claude-haiku-4-5 as a verifier.\n"
        '<dap:actions>[{"kind":"navigate","label":"Create this agent","href":"/agents/new"},'
        '{"kind":"prefill","label":"Use these values","target":"agent",'
        '"values":{"name":"PR reviewer","role":"verifier"}}]</dap:actions>'
    )
    text, actions = parse_actions(reply)
    assert "<dap:actions>" not in text
    assert text.startswith("Use api-call")
    assert len(actions) == 2
    assert actions[0] == {"kind": "navigate", "label": "Create this agent", "href": "/agents/new"}
    assert actions[1]["kind"] == "prefill"
    assert actions[1]["values"]["role"] == "verifier"


def test_parse_actions_malformed_json_is_ignored() -> None:
    from dap_engine.assistant.service import parse_actions

    text, actions = parse_actions("answer <dap:actions>[not json}</dap:actions>")
    assert actions == []
    assert "<dap:actions>" not in text


def test_parse_actions_drops_invalid_items() -> None:
    from dap_engine.assistant.service import parse_actions

    reply = (
        '<dap:actions>[{"kind":"bogus","label":"x"},'
        '{"kind":"doc"},'  # missing label
        '{"kind":"doc","label":"Runtimes","href":"/docs/runtimes.md"}]</dap:actions>'
    )
    _text, actions = parse_actions(reply)
    assert len(actions) == 1
    assert actions[0] == {"kind": "doc", "label": "Runtimes", "href": "/docs/runtimes.md"}
