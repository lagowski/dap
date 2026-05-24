"""Unit tests for pool reconnect_timeout and health endpoint pool stats (#580).

No database required — all pool interactions are mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from dap_engine.app import DatabaseConfig, EngineConfig

# ── DatabaseConfig defaults ──────────────────────────────────────────


class TestDatabaseConfigReconnectTimeout:
    def test_default_value(self) -> None:
        cfg = DatabaseConfig()
        assert cfg.pg_pool_reconnect_timeout == 300.0

    def test_custom_value(self) -> None:
        cfg = DatabaseConfig(pg_pool_reconnect_timeout=30.0)
        assert cfg.pg_pool_reconnect_timeout == 30.0

    def test_zero_value(self) -> None:
        cfg = DatabaseConfig(pg_pool_reconnect_timeout=0)
        assert cfg.pg_pool_reconnect_timeout == 0


# ── EngineConfig forwarding ──────────────────────────────────────────


class TestEngineConfigReconnectTimeout:
    def test_flat_kwarg_forwarded(self) -> None:
        cfg = EngineConfig(pg_pool_reconnect_timeout=60.0)
        assert cfg.db.pg_pool_reconnect_timeout == 60.0

    def test_nested_kwarg_forwarded(self) -> None:
        cfg = EngineConfig(db=DatabaseConfig(pg_pool_reconnect_timeout=42.0))
        assert cfg.pg_pool_reconnect_timeout == 42.0

    def test_default_forwarded(self) -> None:
        cfg = EngineConfig()
        assert cfg.pg_pool_reconnect_timeout == 300.0


# ── Health endpoint pool stats ───────────────────────────────────────


class TestHealthEndpointPoolStats:
    """Test the health endpoint with mocked pool stats."""

    def _make_request(
        self,
        *,
        pool: object | None = None,
        dialect: str = "postgresql",
        engine: object | None = None,
    ) -> MagicMock:
        state = SimpleNamespace(
            db_dialect=dialect,
            db_engine=engine,
            checkpointer_pool=pool,
        )
        app = MagicMock()
        app.state = state
        app.version = "0.0.0-test"
        request = MagicMock()
        request.app = app
        return request

    def test_pool_stats_surfaced(self) -> None:
        from dap_engine.api.health import health

        pool = MagicMock()
        pool.get_stats.return_value = {
            "pool_size": 4,
            "pool_available": 3,
            "requests_waiting": 0,
        }
        request = self._make_request(pool=pool)
        result = health(request)
        assert result["pool_size"] == 4
        assert result["pool_available"] == 3
        assert result["pool_exhausted"] is False
        assert result["db_reachable"] is True

    def test_all_stale_detected(self) -> None:
        from dap_engine.api.health import health

        pool = MagicMock()
        pool.get_stats.return_value = {
            "pool_size": 4,
            "pool_available": 0,
            "requests_waiting": 0,
        }
        request = self._make_request(pool=pool)
        result = health(request)
        assert result["db_reachable"] is False
        assert result["pool_available"] == 0

    def test_pool_exhausted_flag(self) -> None:
        from dap_engine.api.health import health

        pool = MagicMock()
        pool.get_stats.return_value = {
            "pool_size": 4,
            "pool_available": 0,
            "requests_waiting": 2,
        }
        request = self._make_request(pool=pool)
        result = health(request)
        assert result["pool_exhausted"] is True

    def test_sqlite_fallback_no_pool(self) -> None:
        from dap_engine.api.health import health

        request = self._make_request(pool=None, dialect="sqlite")
        result = health(request)
        assert "pool_size" not in result
        assert "pool_available" not in result
        assert "pool_exhausted" not in result
        assert result["db_dialect"] == "sqlite"

    def test_pool_stats_exception_falls_back(self) -> None:
        from dap_engine.api.health import health

        pool = MagicMock()
        pool.get_stats.side_effect = RuntimeError("pool closed")
        request = self._make_request(pool=pool)
        result = health(request)
        # Falls back to SELECT 1 probe path (no pool stats keys)
        assert "pool_size" not in result

    def test_pool_size_zero_falls_back_to_probe(self) -> None:
        """When pool hasn't opened yet (size=0), fall back to SELECT 1."""
        from dap_engine.api.health import health

        pool = MagicMock()
        pool.get_stats.return_value = {
            "pool_size": 0,
            "pool_available": 0,
            "requests_waiting": 0,
        }
        request = self._make_request(pool=pool)
        result = health(request)
        # Still surfaces pool stats but db_reachable comes from probe
        assert result["pool_size"] == 0
