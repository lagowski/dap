"""Tests for #635 second-class fix — timeout_ms: null backward-compat."""
from dap_engine.contracts import AgentNamedPayload


def test_timeout_ms_null_normalizes_to_default() -> None:
    p = AgentNamedPayload(
        name="x",
        role="r",
        runtime_id="cli",
        prompt_template="hello",
        timeout_ms=None,
    )
    assert p.timeout_ms == 60_000


def test_timeout_ms_int_passes_through() -> None:
    p = AgentNamedPayload(
        name="x",
        role="r",
        runtime_id="cli",
        prompt_template="hello",
        timeout_ms=30_000,
    )
    assert p.timeout_ms == 30_000


def test_timeout_ms_missing_uses_default() -> None:
    p = AgentNamedPayload(
        name="x",
        role="r",
        runtime_id="cli",
        prompt_template="hello",
    )
    assert p.timeout_ms == 60_000
