"""Tests for the Alembic introduction (audit E6).

Pins the coexistence contract between the legacy
``MIGRATIONS[]`` list and the new Alembic chain:

- Fresh DB → both ``schema_migrations`` AND ``alembic_version``
  populated; baseline stamped.
- Pre-Alembic dev DB simulated by manually inserting legacy
  migration rows → on next ``create_engine_for_sqlite``, Alembic
  picks up + stamps baseline without re-running legacy migrations.
- ``apply_migrations`` is idempotent across multiple engine
  constructions (matches the pre-E6 invariant).
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dap_engine.persistence.db import create_engine_for_sqlite
from sqlalchemy import inspect, text

# ---------------------------------------------------------------------------
# Fresh-DB path
# ---------------------------------------------------------------------------


def test_fresh_sqlite_db_gets_both_legacy_and_alembic_tables() -> None:
    """A fresh SQLite DB after engine construction has the full pair.

    Legacy ``schema_migrations`` records all 18 frozen migrations
    (idempotent on a fresh DB — the bodies short-circuit, but the
    names still get inserted). Alembic ``alembic_version`` carries
    the baseline revision.
    """
    tmp = tempfile.mkdtemp(prefix="dap-e6-fresh-")
    engine = create_engine_for_sqlite(str(Path(tmp) / "state.db"))

    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        assert "schema_migrations" in tables, (
            "legacy migration table missing — apply_migrations didn't run"
        )
        assert "alembic_version" in tables, (
            "alembic_version table missing — _apply_alembic_migrations didn't run"
        )

        # All 18 legacy migrations recorded
        legacy_count = conn.execute(
            text("SELECT COUNT(*) FROM schema_migrations"),
        ).scalar()
        assert legacy_count == 18, f"expected 18 legacy migrations applied, got {legacy_count}"

        # Alembic baseline stamped
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == "0007_node_output_chunks"


# ---------------------------------------------------------------------------
# Pre-Alembic dev DB path (the case that motivated the audit-E6 contract)
# ---------------------------------------------------------------------------


def test_pre_alembic_dev_db_gets_baseline_stamped_without_rerunning_legacy() -> None:
    """A DB that pre-dates Alembic (has ``schema_migrations`` rows but
    no ``alembic_version``) should be brought forward cleanly.

    Simulates the upgrade path real operators see when they update to
    a DAP version that introduces Alembic: their existing dev DB
    already has all 18 legacy migrations applied, but no
    ``alembic_version`` table. ``_apply_alembic_migrations`` should
    create that table and stamp baseline without re-running the
    legacy migrations or failing.
    """
    tmp = tempfile.mkdtemp(prefix="dap-e6-pre-alembic-")
    db_path = str(Path(tmp) / "state.db")

    # First construction: get to a known v0.3.x state (all legacy
    # migrations applied, alembic_version present at baseline).
    engine1 = create_engine_for_sqlite(db_path)
    engine1.dispose()

    # Simulate a pre-Alembic operator DB: drop the alembic_version
    # table that the first construction created. The legacy
    # ``schema_migrations`` rows stay — they represent the "I already
    # ran the legacy MIGRATIONS[]" state.
    from sqlalchemy import create_engine

    raw = create_engine(f"sqlite:///{db_path}")
    with raw.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
    raw.dispose()

    # Second construction: Alembic should detect the missing
    # alembic_version, create it, and stamp baseline. Legacy
    # apply_migrations sees all 18 entries already in
    # schema_migrations and short-circuits.
    engine2 = create_engine_for_sqlite(db_path)
    try:
        with engine2.connect() as conn:
            assert "alembic_version" in set(inspect(conn).get_table_names()), (
                "_apply_alembic_migrations didn't recreate alembic_version"
            )
            version = conn.execute(
                text("SELECT version_num FROM alembic_version"),
            ).scalar()
            assert version == "0007_node_output_chunks"
            # Legacy count unchanged — no double-apply.
            legacy_count = conn.execute(
                text("SELECT COUNT(*) FROM schema_migrations"),
            ).scalar()
            assert legacy_count == 18
    finally:
        engine2.dispose()


# ---------------------------------------------------------------------------
# Idempotency across restarts
# ---------------------------------------------------------------------------


def test_repeated_engine_construction_is_idempotent() -> None:
    """Three back-to-back constructions against the same DB are no-ops
    after the first. Models the engine being restarted in dev.
    """
    tmp = tempfile.mkdtemp(prefix="dap-e6-idempotent-")
    db_path = str(Path(tmp) / "state.db")

    for _ in range(3):
        engine = create_engine_for_sqlite(db_path)
        engine.dispose()

    # Final state: schema_migrations still has exactly 18, alembic
    # still at baseline (no duplicate rows, no errors raised).
    from sqlalchemy import create_engine

    raw = create_engine(f"sqlite:///{db_path}")
    try:
        with raw.connect() as conn:
            legacy_count = conn.execute(
                text("SELECT COUNT(*) FROM schema_migrations"),
            ).scalar()
            assert legacy_count == 18
            version = conn.execute(
                text("SELECT version_num FROM alembic_version"),
            ).scalar()
            assert version == "0007_node_output_chunks"
    finally:
        raw.dispose()


def test_alembic_version_table_has_single_row() -> None:
    """``alembic_version`` is supposed to track exactly one revision.

    A bug where Alembic accidentally inserts duplicate rows (e.g.,
    via concurrent racing engine starts) would show up here.
    Defensive check that pins the invariant.
    """
    tmp = tempfile.mkdtemp(prefix="dap-e6-single-row-")
    engine = create_engine_for_sqlite(str(Path(tmp) / "state.db"))

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
        assert len(rows) == 1, f"expected single alembic_version row, got {len(rows)}: {rows}"


# ---------------------------------------------------------------------------
# Frozen state of the legacy migrations list
# ---------------------------------------------------------------------------


def test_legacy_migrations_list_frozen_at_18() -> None:
    """``MIGRATIONS[]`` is frozen as of v0.3.x — new schema changes
    land as Alembic revisions, not as new entries here.

    This test is the guardrail: if someone appends to ``MIGRATIONS[]``,
    the failure should send them to a new Alembic revision instead of
    updating this expected count.
    """
    from dap_engine.persistence.migrations import MIGRATIONS

    assert len(MIGRATIONS) == 18, (
        f"MIGRATIONS[] is frozen at 18; got {len(MIGRATIONS)}. New schema "
        "changes belong in an Alembic revision under "
        "src/dap_engine/alembic/versions/, not in MIGRATIONS[]."
    )


# ---------------------------------------------------------------------------
# Baseline revision is genuinely a no-op (defends the contract)
# ---------------------------------------------------------------------------


def _load_baseline_module() -> object:
    """Load the ``0001_baseline`` revision module via importlib.

    Python's ``import`` syntax can't reference a module name that
    starts with a digit (``0001_baseline``) — but ``importlib`` can.
    Both alembic and this test reach the same file via this path
    so the test exercises the real revision module, not a copy.
    """
    import importlib

    return importlib.import_module("dap_engine.alembic.versions.0001_baseline")


def test_baseline_revision_upgrade_is_a_noop() -> None:
    """Invoking ``upgrade()`` on the baseline revision must not raise
    and must not change the DB. The baseline is a marker, not a
    schema migration.
    """
    baseline_module = _load_baseline_module()

    # No setup needed — the function takes no args and is pure.
    # Calling it twice should also be safe (idempotency check).
    baseline_module.upgrade()  # type: ignore[attr-defined]
    baseline_module.upgrade()  # type: ignore[attr-defined]
    baseline_module.downgrade()  # type: ignore[attr-defined]


def test_baseline_revision_has_no_predecessors() -> None:
    """Revision graph anchor: ``0001_baseline`` is the chain root."""
    baseline_module = _load_baseline_module()

    assert baseline_module.revision == "0001_baseline"  # type: ignore[attr-defined]
    assert baseline_module.down_revision is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# applied_at timestamps are real (regression check on the legacy path)
# ---------------------------------------------------------------------------


def test_legacy_migrations_record_iso_timestamps() -> None:
    """Each row in ``schema_migrations`` has a parseable ISO-8601
    ``applied_at`` value. Catches a regression where someone changes
    the runner to write a non-timestamp string into the column.
    """
    tmp = tempfile.mkdtemp(prefix="dap-e6-ts-")
    engine = create_engine_for_sqlite(str(Path(tmp) / "state.db"))

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, applied_at FROM schema_migrations"),
        ).fetchall()

    assert len(rows) == 18
    for name, applied_at in rows:
        # ``fromisoformat`` accepts the engine's format
        # (``datetime.now(UTC).isoformat()``).
        parsed = datetime.fromisoformat(applied_at)
        assert parsed.tzinfo is not None, (
            f"migration {name!r} has timezone-naive timestamp: {applied_at!r}"
        )
        assert parsed.tzinfo.utcoffset(parsed) == UTC.utcoffset(parsed), (
            f"migration {name!r} not in UTC: {applied_at!r}"
        )


# ---------------------------------------------------------------------------
# Error path — a broken revision must propagate cleanly to engine startup
# ---------------------------------------------------------------------------


def test_alembic_failure_propagates_to_engine_startup() -> None:
    """A migration that raises must abort engine startup loudly.

    Council follow-up finding (post-#458): the happy-path tests
    above don't exercise what happens when a future Alembic
    revision blows up. Verifies the policy stated in
    ``_apply_alembic_migrations``'s docstring: errors propagate to
    the caller so the engine refuses to start against a
    half-migrated DB.

    We monkeypatch ``alembic.command.upgrade`` to raise — same
    surface real revision-script failures expose — then assert
    ``create_engine_for_sqlite`` re-raises rather than swallowing.
    """
    from unittest.mock import patch

    tmp = tempfile.mkdtemp(prefix="dap-e6-error-")
    db_path = str(Path(tmp) / "state.db")

    class BogusRevisionError(RuntimeError):
        """Marker exception the test mock raises."""

    # Patch ``alembic.command.upgrade`` itself — ``command`` is
    # imported lazily inside ``_apply_alembic_migrations`` (local
    # import to keep alembic out of the hot import graph), so the
    # patch target is the source module, not the importing one.
    with patch(
        "alembic.command.upgrade",
        side_effect=BogusRevisionError("broken revision boom"),
    ):
        try:
            create_engine_for_sqlite(db_path)
        except BogusRevisionError as exc:
            assert "broken revision boom" in str(exc)
        else:
            raise AssertionError(
                "create_engine_for_sqlite swallowed the Alembic exception — "
                "engine startup should fail loudly on migration errors",
            )
