"""Fetch the last 50 BUY calls with 30/60/90-day return outcomes.

Usage::

    python scripts/track_record.py <db_path>

Outputs a JSON object with keys: rows, summary.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any


TRACK_RECORD_SQL = """
SELECT
    r.symbol,
    r.run_date,
    r.score,
    o.return_30d,
    o.return_60d,
    o.return_90d
FROM cfd_research_runs r
JOIN cfd_outcomes o ON r.id = o.run_id
WHERE r.decision = 'BUY'
ORDER BY r.run_date DESC
LIMIT 50
"""


def get_track_record(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return the last 50 BUY calls with outcome fields and win/loss flag.

    Each row dict has keys: symbol, run_date, score, return_30d, return_60d,
    return_90d, is_win.  Win is defined as return_60d > 0.
    """
    cursor = conn.execute(TRACK_RECORD_SQL)
    columns = [desc[0] for desc in cursor.description]
    rows: list[dict[str, Any]] = []
    for raw in cursor.fetchall():
        row = dict(zip(columns, raw))
        row["is_win"] = (row.get("return_60d") or 0) > 0
        rows.append(row)
    return rows


def compute_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate stats from track-record rows.

    Returns dict with: total, win_rate (0–100 float), avg_return_60d (float
    with one decimal precision).
    """
    total = len(rows)
    if total == 0:
        return {"total": 0, "win_rate": 0.0, "avg_return_60d": 0.0}

    wins = sum(1 for r in rows if r.get("is_win"))
    win_rate = round(wins / total * 100, 1)
    avg_return_60d = round(
        sum(r.get("return_60d", 0) or 0 for r in rows) / total, 1
    )
    return {"total": total, "win_rate": win_rate, "avg_return_60d": avg_return_60d}


def main() -> dict[str, Any]:
    """CLI entry-point: read from SQLite DB, print JSON."""
    if len(sys.argv) < 2:
        print("Usage: python scripts/track_record.py DB_PATH", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    conn = sqlite3.connect(db_path)
    try:
        rows = get_track_record(conn)
        summary = compute_summary(rows)
        result = {"rows": rows, "summary": summary}
        print(json.dumps(result, indent=2, default=str))
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    main()
