"""Tests for the aider runtime adapter (#929).

The CLI subprocess is mocked here — real aider calls would burn tokens. The real-git
behaviour (branch + push + guards through a stand-in ``aider`` executable) is in
``test_aider_git.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from dap_runtimes import AiderAdapter
from dap_runtimes.adapters._cli_base import _BaseCliAdapter
from dap_runtimes.adapters.aider import parse_aider_usage, provider_key_for
from dap_types import RuntimeTask

from .conftest import build_subprocess_mock

_PATCH_PATH = "dap_runtimes.adapters._cli_base.asyncio.create_subprocess_exec"
_WHICH_PATH = "dap_runtimes.adapters._cli_base.shutil.which"

_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
)

_AIDER_STDOUT = (
    b"Aider v0.86.1\n"
    b"Main model: claude-sonnet-4-5 with diff edit format\n"
    b"Git repo: .git with 3 files\n"
    b"I updated hello.py to print a greeting.\n"
    b"Tokens: 2.4k sent, 120 received. Cost: $0.0090 message, $0.0090 session.\n"
    b"Applied edit to hello.py\n"
    b"Commit 1a2b3c4 feat: greet the user\n"
)


@pytest.fixture(autouse=True)
def no_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def with_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    yield


def _task(
    *,
    model_id: str = "claude-sonnet-4-5",
    extra_args: Any = None,
    **config: Any,
) -> RuntimeTask:
    runtime_config: dict[str, Any] = {"model_id": model_id, **config}
    if extra_args is not None:
        runtime_config["extra_args"] = extra_args
    return RuntimeTask(
        execution_id="exec-929",
        prompt_xml="<agent_prompt><task>do it</task></agent_prompt>",
        working_directory="/tmp",
        timeout_ms=60_000,
        runtime_config=runtime_config,
    )


async def _execute(task: RuntimeTask, stdout: bytes = _AIDER_STDOUT, **proc: Any) -> Any:
    process = build_subprocess_mock(stdout=stdout, **proc)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/aider"),
        patch(_PATCH_PATH, AsyncMock(return_value=process)) as create_mock,
    ):
        result = await AiderAdapter().execute(task)
    return result, create_mock, process


def test_aider_is_built_on_the_shared_cli_base() -> None:
    assert issubclass(AiderAdapter, _BaseCliAdapter)
    assert "execute" not in AiderAdapter.__dict__
    assert AiderAdapter.id == "aider"
    assert AiderAdapter.kind == "cli"


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


async def test_healthcheck_missing_binary(with_api_key: None) -> None:
    with patch(_WHICH_PATH, return_value=None):
        health = await AiderAdapter().healthcheck()
    assert health.available is False
    assert health.missing is not None
    assert any("aider" in m for m in health.missing)


async def test_healthcheck_without_any_provider_key_is_unavailable() -> None:
    with patch(_WHICH_PATH, return_value="/usr/local/bin/aider"):
        health = await AiderAdapter().healthcheck()
    assert health.available is False
    assert health.missing is not None
    assert any("ANTHROPIC_API_KEY" in m and "OPENAI_API_KEY" in m for m in health.missing)


@pytest.mark.parametrize("key", ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"])
async def test_healthcheck_available_with_binary_and_any_provider_key(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setenv(key, "k")
    proc = build_subprocess_mock(stdout=b"aider 0.86.1\n")
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/aider"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        health = await AiderAdapter().healthcheck()
    assert health.available is True
    assert health.version == "aider 0.86.1"


# ---------------------------------------------------------------------------
# Provider key resolution + validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_id", "key"),
    [
        ("claude-sonnet-4-5", "ANTHROPIC_API_KEY"),
        ("anthropic/claude-opus-4-1", "ANTHROPIC_API_KEY"),
        ("sonnet", "ANTHROPIC_API_KEY"),
        ("gpt-5", "OPENAI_API_KEY"),
        ("openai/gpt-4.1", "OPENAI_API_KEY"),
        ("o3-mini", "OPENAI_API_KEY"),
        ("4o", "OPENAI_API_KEY"),
        ("gemini/gemini-2.5-pro", "GEMINI_API_KEY"),
        ("gemini-2.5-flash", "GEMINI_API_KEY"),
        ("deepseek/deepseek-chat", "DEEPSEEK_API_KEY"),
        ("openrouter/anthropic/claude-sonnet-4", "OPENROUTER_API_KEY"),
        ("ollama_chat/llama3", None),
        ("some-local-model", None),
    ],
)
def test_provider_key_for_model(model_id: str, key: str | None) -> None:
    assert provider_key_for(model_id) == key


async def test_missing_model_id_returns_error(with_api_key: None) -> None:
    result, create_mock, _ = await _execute(_task(model_id=""))
    assert result.success is False
    assert any("model_id" in e for e in result.errors)
    create_mock.assert_not_called()


async def test_missing_provider_key_for_the_model_returns_error() -> None:
    result, create_mock, _ = await _execute(_task(model_id="gpt-5"))
    assert result.success is False
    assert any("OPENAI_API_KEY" in e and "gpt-5" in e for e in result.errors)
    create_mock.assert_not_called()


async def test_provider_key_in_runtime_config_env_satisfies_validation() -> None:
    result, _, _ = await _execute(_task(model_id="gpt-5", env={"OPENAI_API_KEY": "sk-x"}))
    assert result.success is True, result.errors


@pytest.mark.parametrize("layer", ["project_env_vars", "instance_env_vars"])
async def test_provider_key_from_project_or_instance_env_satisfies_validation(
    layer: str,
) -> None:
    """The key reaches aider through any env layer, so the check reads the merged env."""
    task = _task(model_id="gpt-5").model_copy(update={layer: {"OPENAI_API_KEY": "sk-x"}})
    result, _, _ = await _execute(task)
    assert result.success is True, result.errors


async def test_model_without_a_known_provider_key_is_not_rejected() -> None:
    """Local / unknown providers (ollama, custom litellm routes) carry no key to check."""
    result, _, _ = await _execute(_task(model_id="ollama_chat/llama3"))
    assert result.success is True, result.errors


async def test_binary_not_found_returns_error(with_api_key: None) -> None:
    with patch(_WHICH_PATH, return_value=None):
        result = await AiderAdapter().execute(_task())
    assert result.success is False
    assert any("Aider binary not found" in e for e in result.errors)
    assert result.structured is not None
    assert result.structured["error_category"] == "unavailable"


async def test_binary_path_must_be_string(with_api_key: None) -> None:
    result, _, _ = await _execute(_task(binary_path=123))
    assert result.success is False
    assert any("binary_path" in e for e in result.errors)


async def test_extra_args_must_be_list_of_strings(with_api_key: None) -> None:
    result, _, _ = await _execute(_task(extra_args="--verbose"))
    assert result.success is False
    assert any("extra_args" in e for e in result.errors)


async def test_auto_commits_must_be_boolean(with_api_key: None) -> None:
    result, _, _ = await _execute(_task(auto_commits="no"))
    assert result.success is False
    assert any("auto_commits" in e for e in result.errors)


async def test_auto_commits_off_with_push_is_rejected(with_api_key: None) -> None:
    """With auto-commits off the edits stay uncommitted, so a push would send nothing."""
    result, create_mock, _ = await _execute(_task(auto_commits=False, branch="feat/x", push=True))
    assert result.success is False
    assert any("auto_commits" in e and "push" in e for e in result.errors)
    create_mock.assert_not_called()


# ---------------------------------------------------------------------------
# argv
# ---------------------------------------------------------------------------


async def test_argv_is_non_interactive_and_reads_the_prompt_from_stdin(
    with_api_key: None,
) -> None:
    result, create_mock, process = await _execute(_task())

    assert result.success is True, result.errors
    argv = list(create_mock.call_args.args)
    assert argv[0] == "aider"
    assert argv[argv.index("--message-file") + 1] == "/dev/stdin"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-5"
    for flag in (
        "--yes-always",
        "--no-pretty",
        "--no-stream",
        "--no-fancy-input",
        "--no-check-update",
        "--no-show-model-warnings",
        "--no-gitignore",
        "--no-dirty-commits",
        # Nothing aider or the repo's config suggests runs as a command (#929 review).
        "--no-suggest-shell-commands",
        "--no-auto-test",
        "--no-auto-lint",
        "--no-analytics",
    ):
        assert flag in argv, flag
    # History files stay out of the workspace: they hold the whole prompt and reply.
    assert argv[argv.index("--chat-history-file") + 1] == "/dev/null"
    assert argv[argv.index("--input-history-file") + 1] == "/dev/null"
    # Auto-commits are kept by default (the git guards and push rely on commits).
    assert "--no-auto-commits" not in argv
    sent = process.stdin.write.call_args.args[0].decode("utf-8")
    assert sent.startswith("<agent_prompt>")


async def test_auto_commits_false_passes_no_auto_commits(with_api_key: None) -> None:
    _, create_mock, _ = await _execute(_task(auto_commits=False))
    assert "--no-auto-commits" in create_mock.call_args.args


async def test_extra_args_come_last(with_api_key: None) -> None:
    _, create_mock, _ = await _execute(_task(extra_args=["--edit-format", "whole"]))
    argv = list(create_mock.call_args.args)
    assert argv[-2:] == ["--edit-format", "whole"]


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


async def test_output_is_the_text_and_usage_comes_from_the_tokens_line(
    with_api_key: None,
) -> None:
    result, _, _ = await _execute(_task())

    assert result.success is True
    assert "I updated hello.py" in result.output
    assert result.tokens_used == 2520  # 2.4k sent + 120 received
    assert result.cost_usd == pytest.approx(0.009)
    structured = result.structured
    assert structured is not None
    assert structured["provider"] == "aider"
    assert structured["model"] == "claude-sonnet-4-5"
    assert structured["usage"] == {"input_tokens": 2400, "output_tokens": 120}
    assert structured["exit_code"] == 0
    assert structured["timed_out"] is False


async def test_plain_text_output_without_usage_succeeds_with_zero_tokens(
    with_api_key: None,
) -> None:
    result, _, _ = await _execute(_task(), stdout=b"Nothing to change.\n")
    assert result.success is True
    assert result.output == "Nothing to change."
    assert result.tokens_used == 0
    assert result.cost_usd is None


async def test_empty_stdout_is_not_a_json_error(with_api_key: None) -> None:
    result, _, _ = await _execute(_task(), stdout=b"")
    assert result.success is True, result.errors
    assert result.output == ""


async def test_non_zero_exit_marks_failure(with_api_key: None) -> None:
    result, _, _ = await _execute(
        _task(), stdout=b"", stderr=b"litellm.AuthenticationError: bad key\n", returncode=1
    )
    assert result.success is False
    assert any("Aider CLI exited with code 1" in e for e in result.errors)


def test_usage_sums_every_message_and_takes_the_last_session_cost() -> None:
    text = (
        "Tokens: 1.5k sent, 2 cache write, 1k cache hit, 300 received. "
        "Cost: $0.01 message, $0.01 session.\n"
        "Tokens: 2k sent, 1.2k received. Cost: $0.02 message, $0.03 session.\n"
    )
    usage = parse_aider_usage(text)
    assert usage == {"input_tokens": 3500, "output_tokens": 1500, "cost_usd": 0.03}


def test_usage_understands_million_suffix_and_missing_cost() -> None:
    usage = parse_aider_usage("Tokens: 1.1M sent, 12 received.\n")
    assert usage == {"input_tokens": 1_100_000, "output_tokens": 12, "cost_usd": None}
