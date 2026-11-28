"""Unit tests for the extracted config module (#778 Phase 2).

The seven per-subsystem config dataclasses + ``EngineConfig`` moved from
``app.py`` (808 lines, mixed concerns) to ``dap_engine.config``.
``app.py`` re-exports the public names so the 50+ existing
``from dap_engine.app import EngineConfig`` call sites keep working —
these tests pin both surfaces to the *same* objects.

Behavioural coverage of flat/nested kwargs lives in
``test_engine_config.py``; this file only guards the module split.
"""

from __future__ import annotations

from dap_engine import app as app_module
from dap_engine import config as config_module


def test_config_module_exports_all_config_names() -> None:
    for name in (
        "DEFAULT_CORS_ORIGINS",
        "AuthConfig",
        "CryptoConfig",
        "DatabaseConfig",
        "EngineConfig",
        "OAuthConfig",
        "RuntimePolicyConfig",
        "ServerConfig",
        "TemplateRegistryConfig",
        "parse_cors_origins",
    ):
        assert hasattr(config_module, name), f"dap_engine.config missing {name}"


def test_app_reexports_are_the_same_objects() -> None:
    """A class moved, not forked — isinstance checks across the two import
    paths must keep agreeing."""
    assert app_module.EngineConfig is config_module.EngineConfig
    assert app_module.DatabaseConfig is config_module.DatabaseConfig
    assert app_module.parse_cors_origins is config_module.parse_cors_origins
    assert app_module.DEFAULT_CORS_ORIGINS is config_module.DEFAULT_CORS_ORIGINS


def test_engine_config_constructs_from_new_module() -> None:
    cfg = config_module.EngineConfig(db_path="/tmp/x.db", auth_jwt_secret="s" * 32)
    assert cfg.db.db_path == "/tmp/x.db"
    assert cfg.auth_jwt_secret == "s" * 32  # legacy flat read still routed
