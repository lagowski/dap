"""Unit tests for centralized engine logging configuration (#778 Phase 2).

``dap_engine.logging.configure_logging`` replaces the inline
``logging.basicConfig`` call in ``__main__.py`` and adds ``DAP_LOG_LEVEL``
support.
"""

from __future__ import annotations

import logging

import pytest
from dap_engine.logging import LOG_FORMAT, configure_logging


@pytest.fixture
def isolated_root_logger() -> object:
    """Snapshot + restore the root logger so the test can reconfigure it."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield root
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_configure_logging_sets_level_and_format(
    isolated_root_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DAP_LOG_LEVEL", raising=False)
    configure_logging()
    root = logging.getLogger()
    assert root.level == logging.INFO
    formats = [
        h.formatter._fmt
        for h in root.handlers
        if h.formatter is not None
    ]
    assert LOG_FORMAT in formats


def test_configure_logging_honours_env_level(
    isolated_root_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAP_LOG_LEVEL", "debug")
    configure_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_explicit_level_wins_over_env(
    isolated_root_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAP_LOG_LEVEL", "debug")
    configure_logging(level="WARNING")
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_rejects_unknown_level(
    isolated_root_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAP_LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValueError, match="VERBOSE"):
        configure_logging()


def test_configure_logging_is_idempotent(
    isolated_root_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated calls must not stack duplicate handlers on the root logger."""
    monkeypatch.delenv("DAP_LOG_LEVEL", raising=False)
    configure_logging()
    count_after_first = len(logging.getLogger().handlers)
    configure_logging()
    assert len(logging.getLogger().handlers) == count_after_first
