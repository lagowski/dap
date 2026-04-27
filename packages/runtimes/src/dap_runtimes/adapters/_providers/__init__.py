"""Provider abstraction for the api-call adapter.

Each provider module exposes a small, identical surface so the public
adapter can dispatch by name without knowing SDK details:

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

Adding a new provider = drop another module here and register it in
``PROVIDERS``. No changes to ``api_call.py``.
"""

from __future__ import annotations

from types import ModuleType

from dap_runtimes.adapters._providers import _anthropic, _gemini, _openai
from dap_runtimes.adapters._providers._base import ProviderError, ProviderResult

# Registry. ``openai-compat`` shares the OpenAI module — same SDK with
# a custom ``base_url`` and a per-agent ``api_key_env`` override.
PROVIDERS: dict[str, ModuleType] = {
    "anthropic": _anthropic,
    "openai": _openai,
    "openai-compat": _openai,
    "gemini": _gemini,
}


def get_provider(provider_id: str) -> ModuleType | None:
    """Look up a provider module by id, or ``None`` if unknown."""
    return PROVIDERS.get(provider_id)


def list_provider_ids() -> list[str]:
    """All registered provider ids — used by the adapter healthcheck."""
    return list(PROVIDERS.keys())


__all__ = [
    "PROVIDERS",
    "ProviderError",
    "ProviderResult",
    "get_provider",
    "list_provider_ids",
]
