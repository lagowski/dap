"""Unit tests for shared ValidationError→HTTP mapping helpers (#778 Phase 2).

``dap_engine.api.validation`` consolidates the per-endpoint
``except ValidationError: raise HTTPException(...)`` boilerplate.
"""

from __future__ import annotations

import pytest
from dap_engine.api.validation import (
    http_422_on_validation_error,
    http_500_on_export_revalidation_error,
)
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError


class _Probe(BaseModel):
    n: int


def _trigger_validation_error() -> None:
    _Probe.model_validate({"n": "not-an-int"})


def test_http_422_passes_through_without_error() -> None:
    with http_422_on_validation_error():
        _Probe.model_validate({"n": 1})


def test_http_422_maps_validation_error() -> None:
    with pytest.raises(HTTPException) as exc_info, http_422_on_validation_error():
        _trigger_validation_error()
    assert exc_info.value.status_code == 422
    assert isinstance(exc_info.value.detail, list)
    assert exc_info.value.detail[0]["loc"] == ("n",)
    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_http_422_with_prefix_renders_string_detail() -> None:
    with (
        pytest.raises(HTTPException) as exc_info,
        http_422_on_validation_error(prefix="Invalid initial_state"),
    ):
        _trigger_validation_error()
    assert exc_info.value.status_code == 422
    assert isinstance(exc_info.value.detail, str)
    assert exc_info.value.detail.startswith("Invalid initial_state: ")


def test_http_422_does_not_swallow_other_errors() -> None:
    with pytest.raises(RuntimeError), http_422_on_validation_error():
        raise RuntimeError("boom")


def test_http_500_export_revalidation() -> None:
    with (
        pytest.raises(HTTPException) as exc_info,
        http_500_on_export_revalidation_error("agent"),
    ):
        _trigger_validation_error()
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail.startswith("Stored agent could not be re-validated for export: ")
