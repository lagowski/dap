"""Provider abstraction for the api-call adapter.

The registry holds *metadata* about each provider (id, module path, env
var name) — not the imported module itself. ``get_provider()`` does the
import on-demand via :func:`importlib.import_module`, so loading
``dap_runtimes`` doesn't pull in every SDK (anthropic, openai,
google-genai). A user who only ever calls Anthropic never pays the
import cost of the OpenAI or Google SDKs.

Each provider module exposes the same surface so the public adapter can
dispatch by name without knowing SDK details:

- ``ID: str`` — registry key (e.g. ``"anthropic"``).
- ``DEFAULT_ENV_VAR: str`` — canonical env var holding the API key.
- ``validate_config(config) -> str | None`` — returns an error string if
  the per-call config is bad, ``None`` when OK. The adapter turns the
  string into a failed ``RuntimeResult``.
- ``env_var_for(config) -> str`` — which env var to check for a given
  call; lets ``openai-compat`` override the canonical name.
- ``async call(provider_id, config, prompt_xml) -> ProviderResult`` —
  the actual SDK call. Raises ``ProviderError`` on API/network failures.
- ``healthcheck() -> tuple[bool, str | None, list[str] | None]`` —
  returns ``(available, version, missing)`` for the adapter-level
  healthcheck aggregation.

Adding a new provider = drop a module under ``_providers/`` and append
an entry to ``PROVIDER_REGISTRY``. No changes to ``api_call.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Final

from dap_runtimes.adapters._providers._base import ProviderError, ProviderResult


@dataclass(frozen=True)
class ProviderInfo:
    """Metadata for a registered provider — usable without importing it.

    ``default_env_var`` is ``None`` for providers whose API key is named
    per-agent via ``runtime_config.api_key_env`` (currently only the
    OpenAI-compatible path).
    """

    id: str
    module_path: str
    default_env_var: str | None
    display_name: str


PROVIDER_REGISTRY: Final[dict[str, ProviderInfo]] = {
    "anthropic": ProviderInfo(
        id="anthropic",
        module_path="dap_runtimes.adapters._providers._anthropic",
        default_env_var="ANTHROPIC_API_KEY",
        display_name="Anthropic SDK",
    ),
    "openai": ProviderInfo(
        id="openai",
        module_path="dap_runtimes.adapters._providers._openai",
        default_env_var="OPENAI_API_KEY",
        display_name="OpenAI SDK",
    ),
    "openai-compat": ProviderInfo(
        id="openai-compat",
        module_path="dap_runtimes.adapters._providers._openai",
        default_env_var=None,
        display_name="OpenAI-compatible (custom base_url)",
    ),
    "glm": ProviderInfo(
        id="glm",
        # Reuses the OpenAI-compatible client with a hardcoded base_url
        # and the ``GLM_API_KEY`` env var — operator no longer has to
        # repeat them on every agent like the legacy ``openai-compat``
        # recipe did.
        module_path="dap_runtimes.adapters._providers._openai",
        default_env_var="GLM_API_KEY",
        display_name="Z.AI GLM (OpenAI-compatible)",
    ),
    "gemini": ProviderInfo(
        id="gemini",
        module_path="dap_runtimes.adapters._providers._gemini",
        default_env_var="GEMINI_API_KEY",
        display_name="Google Gen AI SDK",
    ),
}


def get_provider(provider_id: str) -> ModuleType | None:
    """Import (lazily) and return the module for a provider, or ``None``."""
    info = PROVIDER_REGISTRY.get(provider_id)
    if info is None:
        return None
    # importlib caches in sys.modules — repeated calls don't re-import.
    return import_module(info.module_path)


def get_provider_info(provider_id: str) -> ProviderInfo | None:
    """Return metadata for a provider without importing the SDK."""
    return PROVIDER_REGISTRY.get(provider_id)


def list_provider_ids() -> list[str]:
    """All registered provider ids."""
    return list(PROVIDER_REGISTRY.keys())


__all__ = [
    "PROVIDER_REGISTRY",
    "ProviderError",
    "ProviderInfo",
    "ProviderResult",
    "get_provider",
    "get_provider_info",
    "list_provider_ids",
]
