"""Shared ValidationError → HTTP mapping helpers (#778 Phase 2).

Consolidates the per-endpoint ``except ValidationError: raise
HTTPException(...)`` boilerplate. Routes that already funnel through a
dedicated error type (``PipelineBundleError`` and its single
``_raise_bundle_http_error`` converter) keep their architecture — these
helpers are for the endpoints that raise ``HTTPException`` directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, status
from pydantic import ValidationError

__all__ = [
    "http_422_on_validation_error",
    "http_500_on_export_revalidation_error",
]


@contextmanager
def http_422_on_validation_error(prefix: str | None = None) -> Iterator[None]:
    """Map a Pydantic ``ValidationError`` raised inside the block to HTTP 422.

    Without ``prefix`` the response detail is the structured
    ``exc.errors()`` list (the FastAPI-native shape clients already parse
    for request-body validation failures). With ``prefix`` the detail is a
    single human-readable string — used where the validated payload is a
    nested fragment (e.g. ``initial_state``) and a bare error list would
    lack the context of *what* failed to validate.
    """
    try:
        yield
    except ValidationError as exc:
        detail: object = f"{prefix}: {exc.errors()}" if prefix else exc.errors()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=detail,
        ) from exc


@contextmanager
def http_500_on_export_revalidation_error(entity_label: str) -> Iterator[None]:
    """Map a ``ValidationError`` during export re-validation to HTTP 500.

    Export endpoints re-validate stored rows before serialising them; a
    failure there is a server-side data problem (corrupt/legacy row), not
    a client input error — hence 500, not 422.
    """
    try:
        yield
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored {entity_label} could not be re-validated for export: {exc.errors()}",
        ) from exc
