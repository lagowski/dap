"""Tests for scripts/track_record.py — track record query and summary stats."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest


def _make_in_memory_db(rows: list[tuple]) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with cfd_research_runs + cfd_outcomes tables.

    *rows* is a list of tuples:
        (symbol, run_date, score, decision, return_30d, return_60d, return_90d)
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
    for symbol, run_date, score, decision, r30, r60, r90 in rows:
        cursor = conn.execute(
            "INSERT INTO cfd_research_runs (symbol, run_date, score, decision) VALUES (?, ?, ?, ?)",
            (symbol, run_date, score, decision),
        )
        run_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO cfd_outcomes (run_id, return_30d, return_60d, return_90d) VALUES (?, ?, ?, ?)",
            (run_id, r30, r60, r90),
        )
    conn.commit()
    return conn


class TestGetTrackRecord:
    """get_track_record() returns the last 50 BUY calls sorted DESC."""

    def test_returns_last_50_buy_calls_sorted_desc(self) -> None:
        # Insert 60 BUY rows with sequential dates
        rows = [
            (f"SYM{i}", f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", 0.8, "BUY", 5.0, 10.0, 15.0)
            for i in range(60)
        ]
        conn = _make_in_memory_db(rows)

        from scripts.track_record import get_track_record

        result = get_track_record(conn)
        assert len(result) == 50
        # Verify sorted by run_date DESC
        dates = [r["run_date"] for r in result]
        assert dates == sorted(dates, reverse=True)
        conn.close()

    def test_row_contains_all_outcome_fields(self) -> None:
        rows = [("AAPL", "2025-01-15", 0.75, "BUY", 3.2, 7.5, 12.1)]
        conn = _make_in_memory_db(rows)

        from scripts.track_record import get_track_record

        result = get_track_record(conn)
        assert len(result) == 1
        expected_keys = {"symbol", "run_date", "score", "return_30d", "return_60d", "return_90d", "is_win"}
        assert expected_keys.issubset(result[0].keys())
        conn.close()

    def test_win_loss_classification(self) -> None:
        rows = [
            ("WIN", "2025-03-01", 0.9, "BUY", 5.0, 10.0, 15.0),   # return_60d > 0 → win
            ("LOSS", "2025-03-02", 0.4, "BUY", -2.0, -5.0, -8.0),  # return_60d < 0 → loss
            ("ZERO", "2025-03-03", 0.5, "BUY", 1.0, 0.0, -1.0),    # return_60d == 0 → loss
        ]
        conn = _make_in_memory_db(rows)

        from scripts.track_record import get_track_record

        result = get_track_record(conn)
        by_symbol = {r["symbol"]: r for r in result}
        assert by_symbol["WIN"]["is_win"] is True
        assert by_symbol["LOSS"]["is_win"] is False
        assert by_symbol["ZERO"]["is_win"] is False
        conn.close()

    def test_only_buy_decisions_included(self) -> None:
        rows = [
            ("BUY1", "2025-01-01", 0.8, "BUY", 5.0, 10.0, 15.0),
            ("SELL1", "2025-01-02", 0.6, "SELL", 5.0, 10.0, 15.0),
            ("HOLD1", "2025-01-03", 0.5, "HOLD", 5.0, 10.0, 15.0),
        ]
        conn = _make_in_memory_db(rows)

        from scripts.track_record import get_track_record

        result = get_track_record(conn)
        assert len(result) == 1
        assert result[0]["symbol"] == "BUY1"
        conn.close()

    def test_empty_result_set(self) -> None:
        conn = _make_in_memory_db([])

        from scripts.track_record import get_track_record

        result = get_track_record(conn)
        assert result == []
        conn.close()

    def test_sql_queries_buy_with_all_return_columns(self) -> None:
        from scripts.track_record import TRACK_RECORD_SQL

        sql_upper = TRACK_RECORD_SQL.upper()
        assert "RETURN_30D" in sql_upper
        assert "RETURN_60D" in sql_upper
        assert "RETURN_90D" in sql_upper
        assert "DECISION = 'BUY'" in sql_upper
        assert "ORDER BY" in sql_upper
        assert "RUN_DATE DESC" in sql_upper or "R.RUN_DATE DESC" in sql_upper
        assert "LIMIT 50" in sql_upper


class TestComputeSummary:
    """compute_summary() calculates aggregate stats."""

    def test_summary_stats(self) -> None:
        from scripts.track_record import compute_summary

        rows: list[dict[str, Any]] = [
            {"return_60d": 10.0, "is_win": True},
            {"return_60d": 8.0, "is_win": True},
            {"return_60d": -4.0, "is_win": False},
            {"return_60d": 12.0, "is_win": True},
            {"return_60d": 2.0, "is_win": True},
        ]
        summary = compute_summary(rows)
        assert summary["total"] == 5
        assert summary["win_rate"] == 80.0  # 4/5
        assert summary["avg_return_60d"] == 5.6  # (10+8-4+12+2)/5

    def test_empty_summary(self) -> None:
        from scripts.track_record import compute_summary

        summary = compute_summary([])
        assert summary == {"total": 0, "win_rate": 0.0, "avg_return_60d": 0.0}

    def test_all_losses(self) -> None:
        from scripts.track_record import compute_summary

        rows: list[dict[str, Any]] = [
            {"return_60d": -5.0, "is_win": False},
            {"return_60d": -3.0, "is_win": False},
        ]
        summary = compute_summary(rows)
        assert summary["total"] == 2
        assert summary["win_rate"] == 0.0
        assert summary["avg_return_60d"] == -4.0
