"""Tests for the nested-config refactor (audit E8).

EngineConfig used to be a 28-field flat dataclass. After E8 the
fields live in five subgroups (``db`` / ``server`` / ``auth`` /
``oauth`` / ``template_registry`` / ``crypto``) with a custom
``__init__`` that accepts BOTH flat kwargs (legacy — every test
fixture in the repo uses this) AND nested-instance kwargs (preferred
for new code).

These tests pin the back-compat surface so a future change to the
flat→nested mapping table can't silently break the 34+ existing
``EngineConfig(...)`` callsites across ``tests/smoke/`` and the
production CLI.
"""

from __future__ import annotations

import pytest
from dap_engine.app import (
    AuthConfig,
    DatabaseConfig,
    EngineConfig,
    OAuthConfig,
)

# ---------------------------------------------------------------------------
# Flat-kwarg construction (legacy style — what every existing test uses)
# ---------------------------------------------------------------------------


def test_flat_kwargs_route_into_nested_groups() -> None:
    """Each legacy flat kwarg lands in its declared subgroup."""
    cfg = EngineConfig(
        db_path="/tmp/state.db",
        database_url="postgresql+asyncpg://localhost/dap",
        pg_pool_min_size=2,
        pg_pool_max_size=8,
        host="0.0.0.0",
        port=8080,
        cors_origins=["http://localhost:3000"],
        dry_run_budget_usd=1.50,
        auth_jwt_secret="secret",
        auth_access_ttl_seconds=900,
        auth_log_reset_tokens=True,
        oauth_github_client_id="gh-id",
        oauth_github_client_secret="gh-secret",
        oauth_google_client_id="g-id",
        oauth_google_client_secret="g-secret",
        auth_oauth_redirect_url="http://localhost:3000/oauth",
        template_registry_allowed_hosts=["github.com"],
        template_registry_auth_token="ghp_test",
        instance_env_vars_key="fernet-key",
    )

    # Database group
    assert cfg.db.db_path == "/tmp/state.db"
    assert cfg.db.database_url == "postgresql+asyncpg://localhost/dap"
    assert cfg.db.pg_pool_min_size == 2
    assert cfg.db.pg_pool_max_size == 8

    # Server group
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 8080
    assert cfg.server.cors_origins == ["http://localhost:3000"]
    assert cfg.server.dry_run_budget_usd == 1.50

    # Auth group — note the auth_ prefix is stripped on the nested name
    assert cfg.auth.jwt_secret == "secret"
    assert cfg.auth.access_ttl_seconds == 900
    assert cfg.auth.log_reset_tokens is True

    # OAuth group — auth_oauth_redirect_url maps to oauth.redirect_url
    assert cfg.oauth.github_client_id == "gh-id"
    assert cfg.oauth.github_client_secret == "gh-secret"
    assert cfg.oauth.google_client_id == "g-id"
    assert cfg.oauth.google_client_secret == "g-secret"
    assert cfg.oauth.redirect_url == "http://localhost:3000/oauth"

    # Template registry group
    assert cfg.template_registry.allowed_hosts == ["github.com"]
    assert cfg.template_registry.auth_token == "ghp_test"

    # Crypto group
    assert cfg.crypto.instance_env_vars_key == "fernet-key"


def test_defaults_match_v0_3_0() -> None:
    """An empty ``EngineConfig()`` matches the defaults v0.3.0 shipped.

    Pins the defaults so a renamed field or changed default doesn't
    silently shift behaviour for self-hosted operators who pip-update.
    """
    cfg = EngineConfig()
    assert cfg.db.db_path == "./.dap/state.db"
    assert cfg.db.database_url is None
    assert cfg.db.pg_pool_min_size == 4
    assert cfg.db.pg_pool_max_size == 10
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 7333
    assert cfg.server.cors_origins is None
    assert cfg.server.dry_run_budget_usd == 0.50
    assert cfg.auth.jwt_secret is None
    assert cfg.auth.access_ttl_seconds == 60 * 15
    assert cfg.auth.log_reset_tokens is False
    assert cfg.oauth.github_client_id is None
    assert cfg.oauth.redirect_url is None
    assert cfg.template_registry.allowed_hosts == []
    assert cfg.template_registry.auth_token is None
    assert cfg.crypto.instance_env_vars_key is None


def test_unknown_kwarg_raises_typeerror() -> None:
    """Typos in keyword arguments fail loud rather than silently dropped."""
    with pytest.raises(TypeError, match="unexpected keyword argument 'bogus'"):
        EngineConfig(bogus="something")


# ---------------------------------------------------------------------------
# Nested-instance construction (new style — preferred for new code)
# ---------------------------------------------------------------------------


def test_nested_instances_passed_directly() -> None:
    """Construct via fully-built subgroup instances — the new preferred path."""
    cfg = EngineConfig(
        db=DatabaseConfig(db_path="/tmp/nested.db", pg_pool_max_size=20),
        auth=AuthConfig(jwt_secret="nested-secret"),
        oauth=OAuthConfig(github_client_id="gh"),
    )
    assert cfg.db.db_path == "/tmp/nested.db"
    assert cfg.db.pg_pool_max_size == 20
    assert cfg.auth.jwt_secret == "nested-secret"
    assert cfg.oauth.github_client_id == "gh"
    # Unspecified groups get their default-factory instances.
    assert cfg.server.port == 7333
    assert cfg.template_registry.allowed_hosts == []
    assert cfg.crypto.instance_env_vars_key is None


def test_mixed_flat_and_nested_kwargs() -> None:
    """Flat kwargs + nested-instance kwargs can be combined."""
    cfg = EngineConfig(
        auth=AuthConfig(jwt_secret="from-nested"),
        port=9999,  # flat
        instance_env_vars_key="from-flat",  # flat → crypto group
    )
    assert cfg.auth.jwt_secret == "from-nested"
    assert cfg.server.port == 9999
    assert cfg.crypto.instance_env_vars_key == "from-flat"


# ---------------------------------------------------------------------------
# Flat-attribute READ back-compat (via __getattr__ translation)
# ---------------------------------------------------------------------------
# Production code currently reads ``cfg.auth_jwt_secret`` etc. The
# refactor preserves that surface via __getattr__ so internal modules
# can migrate at their own pace.


def test_flat_attribute_reads_translate_to_nested_storage() -> None:
    """``cfg.auth_jwt_secret`` reads through to ``cfg.auth.jwt_secret``."""
    cfg = EngineConfig(auth_jwt_secret="legacy-style", db_path="/tmp/x.db")

    # Read via flat names — these go through __getattr__
    assert cfg.auth_jwt_secret == "legacy-style"
    assert cfg.db_path == "/tmp/x.db"
    assert cfg.port == 7333
    assert cfg.cors_origins is None
    assert cfg.oauth_github_client_id is None
    assert cfg.template_registry_allowed_hosts == []
    assert cfg.instance_env_vars_key is None
    assert cfg.auth_oauth_redirect_url is None  # renamed from auth_oauth_redirect_url


def test_unknown_attribute_read_raises_attributeerror() -> None:
    """A typo on a legacy-style read still fails — typos fail loud."""
    cfg = EngineConfig()
    with pytest.raises(AttributeError, match="has no attribute 'bogus_field'"):
        cfg.bogus_field  # noqa: B018 — intentional attribute access


def test_nested_groups_are_independent_instances() -> None:
    """Two ``EngineConfig()`` instances don't share mutable list state.

    ``template_registry.allowed_hosts`` is a mutable list with a
    ``default_factory``. The dataclass machinery has historically
    bitten people who used ``= []`` as a default; verify our nested
    groups don't have that bug.
    """
    cfg1 = EngineConfig()
    cfg2 = EngineConfig()
    cfg1.template_registry.allowed_hosts.append("evil.example.com")
    assert cfg2.template_registry.allowed_hosts == [], (
        "default-factory list leaked across EngineConfig instances"
    )
