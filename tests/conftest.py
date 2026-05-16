"""Shared test fixtures for the dap test suite."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest


@pytest.fixture
def cfd_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with cfd_research_runs + cfd_outcomes schema.

    Returns an empty database; callers insert their own test data.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE cfd_research_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            run_date TEXT,
            score REAL,
            decision TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE cfd_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            return_30d REAL,
            return_60d REAL,
            return_90d REAL,
            FOREIGN KEY (run_id) REFERENCES cfd_research_runs(id)
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()
