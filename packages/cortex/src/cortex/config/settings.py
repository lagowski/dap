"""Cortex settings — loaded from environment variables and agents.yaml."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProjectConfig(BaseModel):
    """Per-project configuration for token overrides and display metadata."""

    repo: str
    display_name: str
    token_config: dict[str, str] = Field(default_factory=dict)


class GitHubTokens(BaseSettings):
    """GitHub tokens per role. Each agent node gets only the token it needs."""

    model_config = SettingsConfigDict(env_prefix="CORTEX_GH_TOKEN_")

    read: str = Field(default="", description="Read-only repo access")
    issues: str = Field(default="", description="Create/edit issues")
    code: str = Field(default="", description="Push commits, create PRs")
    merge: str = Field(default="", description="Merge PRs (different user from code!)")

    def get_token(self, role: str) -> str:
        """Get token by role name."""
        return getattr(self, role, "")


class CortexSettings(BaseSettings):
    """Global Cortex settings."""

    model_config = SettingsConfigDict(
        env_prefix="CORTEX_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="postgresql://cortex:cortex@localhost:5432/cortex",
        description="PostgreSQL connection string for checkpoints and audit",
    )

    # Ollama
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama base URL for local models",
    )

    # API keys (only needed for 'api' backend type)
    deepseek_api_key: str = Field(default="", description="DeepSeek API key")
    openai_api_key: str = Field(default="", description="OpenAI API key")
    anthropic_api_key: str = Field(default="", description="Anthropic API key (only if using api backend)")

    # Non-prefixed API keys (read directly from .env without CORTEX_ prefix)
    gemini_api_key: str = Field(
        default="",
        description="Google Gemini API key (fallback / last-resort backend)",
        validation_alias="GEMINI_API_KEY",
    )

    # Observability
    langsmith_api_key: str = Field(default="", description="LangSmith API key (optional)")
    langsmith_project: str = Field(default="cortex", description="LangSmith project name")
    tracing_enabled: bool = Field(default=True, description="Enable LLM call tracing")

    # GitHub tokens — flat (CORTEX_GH_TOKEN_READ, CORTEX_GH_TOKEN_ISSUES, etc.)
    gh_token_read: str = Field(default="", description="Read-only repo access")
    gh_token_issues: str = Field(default="", description="Create/edit issues")
    gh_token_code: str = Field(default="", description="Push commits, create PRs")
    gh_token_merge: str = Field(default="", description="Merge PRs (different user!)")

    # Defaults
    default_repo: str = Field(default="", description="Default GitHub repo (owner/name)")

    # DAP engine (optional — only needed when Cortex runs inside DAP)
    dap_engine_url: str = Field(
        default="",
        description="DAP engine base URL (e.g. http://127.0.0.1:7333). "
        "When set, approve/reject/retry forward to DAP REST API instead of "
        "the local LangGraph checkpointer.",
        validation_alias="DAP_ENGINE_URL",
    )
    dap_auth_token: str = Field(
        default="",
        description="DAP engine bearer token (v0.1: optional, DAP is local-only)",
        validation_alias="DAP_AUTH_TOKEN",
    )

    def get_github_token(self, role: str, project_id: str | None = None) -> str:
        """Get token by role name, with optional per-project override.

        When ``project_id`` is given, resolves the env-var name from
        ``ProjectConfig.token_config[role]`` and looks it up in ``os.environ``.
        Falls back to the global ``CORTEX_GH_TOKEN_<ROLE>`` env var.
        """
        if project_id is not None:
            from cortex.persistence.audit import _get_connection

            try:
                with _get_connection() as conn:
                    row = conn.execute(
                        "SELECT token_config FROM cortex_projects WHERE id = %s",
                        (project_id,),
                    ).fetchone()
                if row and row[0] and role in row[0]:
                    env_var_name = row[0][role]
                    value = os.environ.get(env_var_name, "")
                    if value:
                        return value
            except Exception:
                pass
        return getattr(self, f"gh_token_{role}", "")


def load_settings() -> CortexSettings:
    """Load settings from environment variables and .env file."""
    return CortexSettings()  # type: ignore[call-arg]


def load_agent_configs() -> dict[str, dict]:
    """Load agent configurations from agents.yaml.

    Returns:
        Dict mapping agent name to its config dict.
    """
    config_path = Path(__file__).parent / "agents.yaml"
    if not config_path.exists():
        return {}

    with open(config_path) as f:
        data = yaml.safe_load(f)

    # agents.yaml has agents at top level (not under an "agents:" key)
    configs = data if isinstance(data, dict) else {}

    from cortex.plugins.loader import load_plugins

    return load_plugins(configs)


def _inject_env(config: dict, settings: CortexSettings) -> dict:
    """Inject env-derived runtime values into a single backend config dict.

    Called once for the primary backend, and once per fallback entry — so
    fallback configs (typically `api` with a Gemini key) also get their
    secrets populated from the environment.
    """
    backend_type = config.get("backend", "ollama")

    if backend_type == "ollama":
        config.setdefault("base_url", settings.ollama_base_url)

    elif backend_type == "api":
        provider = config.get("provider", "deepseek")
        if provider == "deepseek":
            config.setdefault("api_key", settings.deepseek_api_key)
        elif provider == "openai":
            config.setdefault("api_key", settings.openai_api_key)
        elif provider == "anthropic":
            config.setdefault("api_key", settings.anthropic_api_key)
        elif provider == "gemini":
            config.setdefault("api_key", settings.gemini_api_key)

    return config


def get_agent_backend_config(agent_name: str) -> dict:
    """Get the backend config for a specific agent, with env vars injected.

    Merges agents.yaml config with runtime settings (API keys, URLs).
    Also walks any ``fallback:`` list and injects env into each fallback
    config so the fallback chain works without manual per-fallback secrets.
    """
    agents = load_agent_configs()
    config = agents.get(agent_name, {})
    settings = load_settings()

    _inject_env(config, settings)

    # Inject env for each fallback config too (issue #37)
    for fb_config in config.get("fallback") or []:
        _inject_env(fb_config, settings)

    return config
