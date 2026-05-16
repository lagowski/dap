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
        # Intentionally type-incorrect: the whole point of this test is
        # to confirm that bogus kwargs blow up at runtime too — mypy
        # already catches them at compile time thanks to the
        # ``Unpack[EngineConfigKwargs]`` annotation on __init__.
        EngineConfig(bogus="something")  # type: ignore[call-arg]


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


# ---------------------------------------------------------------------------
# Hardening — round-2 council findings on this same PR (#457)
# ---------------------------------------------------------------------------


def test_init_does_not_mutate_passed_in_nested_instances() -> None:
    """Passing a nested instance + a flat kwarg must not mutate the instance.

    Council finding HIGH (#457 round 1): ``EngineConfig.__init__`` was
    setattr'ing the caller's nested instance when flat kwargs landed
    on the same group. ``dataclasses.replace(cfg, auth_jwt_secret=
    "new")`` would mutate the original ``cfg.auth`` because replace()
    passes the existing ``cfg.auth`` object back into ``__init__``.
    Fix: ``copy.copy`` the nested instance before mutating.
    """
    original_auth = AuthConfig(jwt_secret="original")
    cfg = EngineConfig(auth=original_auth, auth_access_ttl_seconds=42)

    # The nested instance inside cfg has the updated TTL...
    assert cfg.auth.access_ttl_seconds == 42
    # ... but the CALLER's instance is unchanged.
    assert original_auth.access_ttl_seconds == 60 * 15, (
        "EngineConfig.__init__ mutated the caller's AuthConfig instance — "
        "breaks dataclasses.replace() contract"
    )


def test_dataclasses_replace_does_not_leak_state() -> None:
    """``dataclasses.replace`` returns a fresh, independent instance.

    The contract: ``replace(original, ...)`` returns NEW state; the
    original is untouched. Without the ``copy.copy`` in ``__init__``,
    flat-style replace would mutate the original's nested objects.
    """
    import dataclasses

    cfg1 = EngineConfig(auth_jwt_secret="first", db_path="/tmp/orig.db")
    cfg2 = dataclasses.replace(cfg1, auth=AuthConfig(jwt_secret="second"))

    # cfg2 has the new value.
    assert cfg2.auth.jwt_secret == "second"
    # cfg1 is untouched — that's the whole point of replace().
    assert cfg1.auth.jwt_secret == "first", (
        "dataclasses.replace leaked state into the original EngineConfig"
    )
    # And the unrelated field carries through to cfg2.
    assert cfg2.db.db_path == "/tmp/orig.db"


def test_sensitive_fields_not_in_repr() -> None:
    """Secrets must not appear in ``__repr__`` (leak vector via logs).

    Council finding MEDIUM (#457 round 1): all five secret-bearing
    fields (jwt_secret, github_client_secret, google_client_secret,
    template_registry.auth_token, instance_env_vars_key) need
    ``repr=False`` so a Sentry traceback or operator's ``print(cfg)``
    doesn't leak the value.
    """
    cfg = EngineConfig(
        auth_jwt_secret="JWT-SECRET-VALUE",
        oauth_github_client_secret="GH-SECRET-VALUE",
        oauth_google_client_secret="G-SECRET-VALUE",
        template_registry_auth_token="REGISTRY-TOKEN-VALUE",
        instance_env_vars_key="FERNET-KEY-VALUE",
    )

    rendered = repr(cfg)
    assert "JWT-SECRET-VALUE" not in rendered
    assert "GH-SECRET-VALUE" not in rendered
    assert "G-SECRET-VALUE" not in rendered
    assert "REGISTRY-TOKEN-VALUE" not in rendered
    assert "FERNET-KEY-VALUE" not in rendered

    # Non-secret fields should still appear (verify repr isn't all-off)
    assert "127.0.0.1" in rendered or "host=" in rendered  # ServerConfig.host default


def test_setattr_routes_legacy_flat_writes_to_nested() -> None:
    """``cfg.auth_jwt_secret = "new"`` updates the nested storage.

    Council finding MEDIUM (#457 round 1): without ``__setattr__``,
    a legacy flat write would create a divergent instance attribute
    and the nested ``cfg.auth.jwt_secret`` would silently retain the
    old value. Migrated code reading nested vs. unmigrated code
    reading flat would see different values. Routing the write keeps
    both surfaces in sync.
    """
    cfg = EngineConfig(auth_jwt_secret="old")
    assert cfg.auth.jwt_secret == "old"
    assert cfg.auth_jwt_secret == "old"  # via __getattr__

    # Write via legacy flat name.
    cfg.auth_jwt_secret = "new"

    # Both surfaces show the new value — they're the same storage.
    assert cfg.auth.jwt_secret == "new", "legacy flat write didn't reach nested storage"
    assert cfg.auth_jwt_secret == "new"
