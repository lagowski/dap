"""Tests for PipelineState.description field (#154)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dap_types.state import PipelineState, StateSnapshot


def _minimal(**overrides: Any) -> PipelineState:
    defaults: dict[str, Any] = {"run_id": "r", "repo": "r", "branch": "b"}
    defaults.update(overrides)
    return PipelineState(**defaults)


def test_pipeline_state_has_description_field() -> None:
    assert "description" in PipelineState.model_fields


def test_pipeline_state_description_defaults_to_none() -> None:
    state = _minimal()
    assert state.description is None


def test_pipeline_state_description_accepts_string() -> None:
    state = _minimal(description="nightly")
    assert state.description == "nightly"


def test_pipeline_state_description_in_serialization() -> None:
    state = _minimal()
    dumped = state.model_dump()
    assert "description" in dumped
    assert dumped["description"] is None


def test_pipeline_state_description_round_trip_via_snapshot() -> None:
    state = _minimal(description="hotfix triage")
    snap = StateSnapshot(
        id="snap-1",
        run_id="r",
        node_id="n",
        timestamp=datetime.now(tz=timezone.utc),
        state=state,
    )
    rebuilt = StateSnapshot.model_validate(snap.model_dump())
    assert rebuilt.state.description == "hotfix triage"
