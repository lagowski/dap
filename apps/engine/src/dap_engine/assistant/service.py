"""Config-assistant inference (#689 slice 2).

Backs the assistant chat with a real LLM by reusing the tested
:class:`ApiCallAdapter` — it already normalises providers, resolves the
provider key from the env overlay, validates config, and handles errors/cost.
We pick whichever provider has a key available (os.environ + the decrypted
instance env vars), stuff the curated docs into the system prompt for
grounding, render the transcript, and run one api-call.

No secrets leave this boundary: the docs + the user's transcript go into the
prompt; provider key VALUES never do (the adapter reads them from the env
overlay, they're not interpolated into any string we build).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from dap_runtimes import ApiCallAdapter
from dap_runtimes.adapters._providers import PROVIDER_REGISTRY
from dap_types import RuntimeTask

from dap_engine.assistant.docs_corpus import DOCS_CORPUS

logger = logging.getLogger("dap.engine.assistant")

# Provider preference + a sensible cheap default model for each. Overridable
# per-instance via DAP_ASSISTANT_PROVIDER / DAP_ASSISTANT_MODEL env vars.
_PROVIDER_PRIORITY: tuple[str, ...] = ("anthropic", "openai", "gemini", "glm", "openrouter")
_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "glm": "glm-4.5",
    "openrouter": "anthropic/claude-3.5-haiku",
}

_SYSTEM_INSTRUCTIONS = """\
You are the DAP configuration assistant. Help the user map an intent (e.g. "an
agent that reviews PRs cheaply") onto a concrete DAP configuration: runtime,
model/provider, role, key prompt/contract notes, and the provider/env
requirements. Ground every recommendation in the reference below — do NOT
invent config fields that aren't in it. Be concise and concrete. These are
suggestions the user applies manually; never claim anything was saved, and
never ask for or include secret values (only env-var NAMES).

## Actions (optional)
When your answer recommends a concrete next step, you MAY append ONE block at
the very end of your reply, exactly:
<dap:actions>[ ... ]</dap:actions>
containing a JSON array of action objects. The UI renders them as buttons.
Allowed actions:
- {"kind":"navigate","label":"Create this agent","href":"/agents/new"}
- {"kind":"doc","label":"Runtimes","href":"/docs/runtimes.md"}
- {"kind":"prefill","label":"Use these values","target":"agent","values":{
    "name":"PR reviewer","role":"verifier","runtime_id":"api-call",
    "runtime_config":{"provider":"anthropic","model_id":"claude-haiku-4-5"},
    "prompt_template":"<agent_prompt>...</agent_prompt>"}}
Rules: only emit a prefill when you proposed a concrete agent config; values
must use real fields from the reference; never put secret VALUES anywhere. Omit
the block entirely if no action applies. Keep the prose answer above the block.

# Reference
"""

_NO_PROVIDER_MESSAGE = (
    "I can't answer yet — no LLM provider is configured on this instance. Add a "
    "provider API key in Settings → environment variables (e.g. ANTHROPIC_API_KEY, "
    "OPENAI_API_KEY, or GEMINI_API_KEY) and I'll start giving grounded config advice."
)


@dataclass
class AssistantReply:
    text: str
    grounded: bool = False
    actions: list[dict[str, Any]] = field(default_factory=list)


# Match the whole block regardless of inner content so a malformed payload is
# still stripped from the visible reply (we just emit no actions for it).
_ACTIONS_RE = re.compile(r"<dap:actions>(.*?)</dap:actions>", re.DOTALL)
_VALID_ACTION_KINDS = frozenset({"navigate", "doc", "prefill"})


def parse_actions(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Pull a trailing ``<dap:actions>[...]</dap:actions>`` block out of the reply.

    Returns ``(clean_text, actions)``. Best-effort and defensive: no block,
    malformed JSON, or non-list payload → ``(text, [])``. Each action is kept
    only if it has a known ``kind`` and a string ``label``; unknown fields are
    dropped so a hallucinated shape can't reach the client.
    """
    match = _ACTIONS_RE.search(text)
    if not match:
        return text.strip(), []
    clean = (text[: match.start()] + text[match.end() :]).strip()
    try:
        raw = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return clean, []
    if not isinstance(raw, list):
        return clean, []
    actions: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        label = item.get("label")
        if kind not in _VALID_ACTION_KINDS or not isinstance(label, str) or not label:
            continue
        action: dict[str, Any] = {"kind": kind, "label": label}
        if isinstance(item.get("href"), str):
            action["href"] = item["href"]
        if isinstance(item.get("target"), str):
            action["target"] = item["target"]
        if isinstance(item.get("values"), dict):
            action["values"] = item["values"]
        actions.append(action)
    return clean, actions


def select_provider(env: Mapping[str, str]) -> tuple[str, str] | None:
    """Pick (provider_id, model_id) whose key is present in ``env``.

    Honours DAP_ASSISTANT_PROVIDER / DAP_ASSISTANT_MODEL overrides, else walks
    the priority order. Returns ``None`` when no provider key is available.
    """
    override = env.get("DAP_ASSISTANT_PROVIDER")
    model_override = env.get("DAP_ASSISTANT_MODEL")
    candidates = (override, *_PROVIDER_PRIORITY) if override else _PROVIDER_PRIORITY
    for pid in candidates:
        info = PROVIDER_REGISTRY.get(pid)
        if info is None or info.default_env_var is None:
            continue
        if env.get(info.default_env_var):
            model = model_override or _DEFAULT_MODELS.get(pid)
            if model:
                return pid, model
    return None


def build_transcript(messages: list[dict[str, str]]) -> str:
    """Render the chat history into a plain transcript for the prompt."""
    lines = []
    for m in messages:
        who = "User" if m.get("role") == "user" else "Assistant"
        lines.append(f"{who}: {m.get('content', '')}")
    lines.append("Assistant:")
    return "\n".join(lines)


async def generate_reply(
    messages: list[dict[str, str]],
    *,
    env: Mapping[str, str],
    adapter: ApiCallAdapter | None = None,
) -> AssistantReply:
    """Produce one grounded assistant reply, or a graceful no-provider message."""
    choice = select_provider(env)
    if choice is None:
        return AssistantReply(text=_NO_PROVIDER_MESSAGE, grounded=False)
    provider_id, model_id = choice

    # Overlay ONLY the selected provider's key — never the whole instance env
    # (which holds DB URLs, other API keys, etc.). If the key is already in
    # os.environ the adapter reads it directly and no overlay is needed.
    key_env = PROVIDER_REGISTRY[provider_id].default_env_var
    instance_overlay: dict[str, str] = {}
    if key_env and key_env in env and key_env not in os.environ:
        instance_overlay[key_env] = env[key_env]

    task = RuntimeTask(
        execution_id="assistant",
        prompt_xml=build_transcript(messages),
        working_directory=".",
        timeout_ms=60_000,
        runtime_config={
            "provider": provider_id,
            "model_id": model_id,
            "system_prompt": _SYSTEM_INSTRUCTIONS + DOCS_CORPUS,
            "max_tokens": 1024,
        },
        instance_env_vars=instance_overlay,
    )

    result = await (adapter or ApiCallAdapter()).execute(task)
    if not result.success:
        # Raw provider errors can carry endpoint URLs / key fragments / stack
        # traces (secret-tainted). Log only the fact — not the errors, and not
        # ``provider_id`` (it's derived from the env dict, so CodeQL taints it
        # even though it's just a provider name). The provider adapter logs its
        # own diagnostics. A redaction layer (separate issue) would let us log
        # richer detail safely.
        logger.warning("assistant: provider call failed")
        return AssistantReply(
            text="The model call failed — check the provider key/quota in Settings.",
            grounded=False,
        )
    text, actions = parse_actions(result.output)
    return AssistantReply(text=text, grounded=True, actions=actions)
