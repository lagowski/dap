"""Unit tests for :mod:`dap_engine.execution.interaction_recorder` (#785).

Tests the pure recording helper — redaction + truncation + persistence —
that :func:`dap_engine.execution.node_executor._save_execution_log`
delegates to when a model-invoking node finishes.

Integration coverage (that a real pipeline run through the runner writes
an interaction row) rides on top of the existing runner smoke tests and
the persistence layer's own list/purge coverage.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.execution.interaction_recorder import (
    DEFAULT_MAX_BYTES_PER_SIDE,
    is_enabled,
    record_node_interaction,
)
from dap_engine.persistence.db import create_engine_for_sqlite, make_session_factory
from dap_engine.persistence.interaction_log import list_interactions
from dap_types import RuntimeResult
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine_for_sqlite(str(tmp_path / "state.db"))
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def _result(output: str = "reply", tokens: int | None = 42) -> RuntimeResult:
    return RuntimeResult(success=True, output=output, tokens_used=tokens, duration_ms=10)


def _record(
    session: Session,
    *,
    prompt: str = "please summarize",
    output: str = "here you go",
    kind: str = "api",
    runtime_id: str = "api-call",
    known_secrets: dict[str, str] | None = None,
    **overrides: object,
) -> None:
    record_node_interaction(
        session,
        run_id="run-1",
        node_id="node-a",
        execution_id="exec-1",
        runtime_id=runtime_id,
        adapter_kind=kind,
        prompt_xml=prompt,
        result=_result(output=output),
        known_secrets=known_secrets or {},
        provider=overrides.pop("provider", "anthropic"),  # type: ignore[arg-type]
        model=overrides.pop("model", "claude-opus-4-7"),  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Happy path — writes a "node" surface row with per-run metadata
# ---------------------------------------------------------------------------


def test_records_a_node_surface_row_with_provider_and_model(session: Session) -> None:
    _record(session)
    session.commit()

    rows, total = list_interactions(session, offset=0, limit=10, surface="node")
    assert total == 1
    row = rows[0]
    assert row.surface == "node"
    assert row.user_id is None  # background node execution — no interactive user
    assert row.provider == "anthropic"
    assert row.model == "claude-opus-4-7"
    assert row.tokens_used == 42
    assert row.redacted_request == [{"role": "user", "content": "please summarize"}]
    assert row.redacted_response == "here you go"
    assert row.extra is not None
    assert row.extra["run_id"] == "run-1"
    assert row.extra["node_id"] == "node-a"
    assert row.extra["execution_id"] == "exec-1"
    assert row.extra["runtime_id"] == "api-call"
    # No truncation happened — no truncation markers in extra
    assert "request_truncated_from_bytes" not in row.extra
    assert "response_truncated_from_bytes" not in row.extra


# ---------------------------------------------------------------------------
# Skip rules — non-model-invoking runtimes and the disabled-config flag
# ---------------------------------------------------------------------------


def test_skips_shell_kind_runtimes(session: Session) -> None:
    # bash / python-func adapters — no model call, nothing to log
    _record(session, kind="shell", runtime_id="python-func")
    session.commit()
    _, total = list_interactions(session, offset=0, limit=10, surface="node")
    assert total == 0


def test_skips_http_kind_runtimes(session: Session) -> None:
    # generic http adapter — not an LLM call
    _record(session, kind="http", runtime_id="http")
    session.commit()
    _, total = list_interactions(session, offset=0, limit=10, surface="node")
    assert total == 0


def test_records_cli_kind_runtimes(session: Session) -> None:
    # claude-code / codex / aider / gemini-cli all report kind="cli"
    _record(session, kind="cli", runtime_id="claude-code")
    session.commit()
    _, total = list_interactions(session, offset=0, limit=10, surface="node")
    assert total == 1


def test_skips_when_disabled_via_env(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DAP_INTERACTION_LOG_ENABLED", "0")
    _record(session)
    session.commit()
    _, total = list_interactions(session, offset=0, limit=10, surface="node")
    assert total == 0


def test_enabled_by_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAP_INTERACTION_LOG_ENABLED", raising=False)
    assert is_enabled() is True


def test_disabled_when_env_is_false_like(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("0", "false", "False", "no", ""):
        monkeypatch.setenv("DAP_INTERACTION_LOG_ENABLED", v)
        assert is_enabled() is False, f"expected False for {v!r}"


# ---------------------------------------------------------------------------
# Redaction — the strongest layer names the leaked key
# ---------------------------------------------------------------------------


def test_redacts_known_secret_value_appearing_in_prompt(session: Session) -> None:
    secret = "sk-verylongsecretvalue-1234567890"
    _record(
        session,
        prompt=f"Please analyze the config: API_KEY={secret}, continue.",
        known_secrets={"OPENAI_API_KEY": secret},
    )
    session.commit()

    rows, _ = list_interactions(session, offset=0, limit=10, surface="node")
    stored_prompt = rows[0].redacted_request[0]["content"]
    assert secret not in stored_prompt
    assert "[REDACTED:OPENAI_API_KEY]" in stored_prompt


def test_redacts_known_secret_value_appearing_in_response(session: Session) -> None:
    secret = "ghp_" + "a" * 36
    _record(
        session,
        output=f"Debug output: token={secret} (do not commit)",
        known_secrets={"CORTEX_GH_TOKEN_CODE": secret},
    )
    session.commit()

    rows, _ = list_interactions(session, offset=0, limit=10, surface="node")
    stored_response = rows[0].redacted_response
    assert secret not in stored_response
    # Either the exact-value layer (names the key) or pattern layer catches it;
    # exact-value runs first so it wins here.
    assert "[REDACTED:CORTEX_GH_TOKEN_CODE]" in stored_response


def test_pattern_layer_backstops_unknown_secret_in_response(session: Session) -> None:
    # An OpenAI-shaped key we never told the redactor about — the pattern
    # layer should still catch it.
    _record(
        session,
        output="oops the model echoed a key: sk-proj-abcdefghijklmnopqrstuvwxyz1234",
        known_secrets={},  # empty — the exact-value layer is a no-op
    )
    session.commit()

    rows, _ = list_interactions(session, offset=0, limit=10, surface="node")
    stored_response = rows[0].redacted_response
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz1234" not in stored_response
    assert "[REDACTED:OPENAI_KEY]" in stored_response


# ---------------------------------------------------------------------------
# Truncation — cap per-side at DEFAULT_MAX_BYTES_PER_SIDE
# ---------------------------------------------------------------------------


def test_truncates_response_over_cap_and_records_original_size(session: Session) -> None:
    big = "A" * (DEFAULT_MAX_BYTES_PER_SIDE + 1000)
    _record(session, output=big)
    session.commit()

    rows, _ = list_interactions(session, offset=0, limit=10, surface="node")
    row = rows[0]
    # Response was truncated
    assert len(row.redacted_response.encode("utf-8")) <= DEFAULT_MAX_BYTES_PER_SIDE + 100
    assert "[TRUNCATED — original was" in row.redacted_response
    # Original byte count preserved in extra
    assert row.extra is not None
    assert row.extra["response_truncated_from_bytes"] == len(big.encode("utf-8"))
    # Request stayed small — no truncation marker on that side
    assert "request_truncated_from_bytes" not in row.extra


def test_truncates_prompt_over_cap_and_records_original_size(session: Session) -> None:
    big = "B" * (DEFAULT_MAX_BYTES_PER_SIDE + 500)
    _record(session, prompt=big)
    session.commit()

    rows, _ = list_interactions(session, offset=0, limit=10, surface="node")
    row = rows[0]
    prompt = row.redacted_request[0]["content"]
    assert len(prompt.encode("utf-8")) <= DEFAULT_MAX_BYTES_PER_SIDE + 100
    assert "[TRUNCATED — original was" in prompt
    assert row.extra is not None
    assert row.extra["request_truncated_from_bytes"] == len(big.encode("utf-8"))


def test_truncates_on_utf8_boundary_when_multibyte_at_cap_edge(session: Session) -> None:
    # 4-byte emoji straddles the cap — the truncator must NOT emit an
    # invalid UTF-8 sequence.
    filler_len = DEFAULT_MAX_BYTES_PER_SIDE - 2  # emoji is 4 bytes; last 2 bytes are inside cap
    payload = "x" * filler_len + "🚀 tail"
    _record(session, output=payload)
    session.commit()

    rows, _ = list_interactions(session, offset=0, limit=10, surface="node")
    row = rows[0]
    # Round-trip must succeed (ORM already decoded it; assert no lone surrogates)
    assert isinstance(row.redacted_response, str)
    assert "[TRUNCATED" in row.redacted_response
