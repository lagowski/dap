"""Tests for #635 second-class fix — timeout_ms: null backward-compat.

The ``timeout_ms`` field is declared ``int`` (not ``int | None``), but a
``mode="before"`` validator on the contracts side rewrites ``None`` to
the default 60_000 ms so legacy cortex bundle exports keep working.
mypy doesn't follow that runtime coercion, so feeding the literal
``timeout_ms=None`` is a type error even though it's the whole point
of this test. File-wide disable rather than per-call ignore.
"""

# mypy: disable-error-code="arg-type"

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
