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
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from dap_runtimes import ApiCallAdapter, ClaudeCodeAdapter
from dap_runtimes.adapters._providers import PROVIDER_REGISTRY
from dap_runtimes.adapters.base import BaseAdapter
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

# Opt-in CLI provider: route the assistant through the claude-code CLI on the
# Pro/Max subscription ($0, no API key — uses the host's ``claude login``
# session) instead of a metered provider API. Selected only when explicitly set
# via DAP_ASSISTANT_PROVIDER=claude-code; never auto-picked (needs the CLI +
# login present on the engine host).
_CLI_PROVIDER = "claude-code"
_CLI_DEFAULT_MODEL = "claude-haiku-4-5"

_SYSTEM_INSTRUCTIONS = """\
You are the DAP configuration assistant. Help the user map an intent (e.g. "an
agent that reviews PRs cheaply") onto a concrete DAP configuration: runtime,
model/provider, role, key prompt/contract notes, and the provider/env
requirements. Ground every recommendation in the reference below — do NOT
invent config fields that aren't in it. Be concise and concrete. These are
suggestions the user applies manually; never claim anything was saved, and
never ask for or include secret values (only env-var NAMES).

## Current context (optional)
The user's message may be preceded by a "## Current context" block describing
the page they're on and the (non-secret) form/pipeline state they're editing.
When present, tailor your advice to it — fill the gaps in what they've started,
reference fields they've already set, and prefer a prefill that completes their
in-progress config rather than starting from scratch. The block never contains
secret values; do not ask the user to paste any.

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
    "I can't answer yet — no LLM provider is configured on this instance. An admin can "
    "set one under Admin → Settings → instance environment variables: add a provider "
    "API key (e.g. ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY), or set "
    "ASSISTANT_PROVIDER=claude-code to use the Claude CLI subscription. Then I'll start "
    "giving grounded config advice."
)


@dataclass
class AssistantReply:
    text: str
    grounded: bool = False
    actions: list[dict[str, Any]] = field(default_factory=list)


# Defence-in-depth (#689 phase 2): the page context is assembled client-side
# from non-secret config fields, but we redact any value whose KEY name looks
# secret-ish before it reaches the model prompt — belt-and-suspenders against a
# page that mistakenly stuffs a token/password into the context.
_SECRET_KEY_RE = re.compile(
    r"(token|secret|passwd|password|api[_-]?key|\bkey\b|auth|bearer|credential|private)",
    re.IGNORECASE,
)
# Cap the serialized context so a large form/pipeline can't blow the prompt
# budget. Generous enough for a realistic agent form or pipeline outline.
_CONTEXT_CHAR_CAP = 4_000


def _redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively replace secret-keyed values with ``[redacted]``."""
    if key is not None and _SECRET_KEY_RE.search(key):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {k: _redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def render_context(context: Mapping[str, Any] | None) -> str:
    """Render the page/form context into a redacted prompt block (#689 phase 2).

    Returns ``""`` for empty/None or unserialisable context. Values whose key
    name looks secret-ish are redacted, and the whole block is size-capped so a
    big pipeline can't dominate the prompt. Names/shape only — never secrets.
    """
    if not context:
        return ""
    try:
        body = json.dumps(_redact(dict(context)), indent=2, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""
    if len(body) > _CONTEXT_CHAR_CAP:
        body = body[:_CONTEXT_CHAR_CAP] + "\n… (truncated)"
    return (
        "## Current context\n"
        "The user is on this page with this (non-secret) form/pipeline state:\n"
        f"```json\n{body}\n```\n\n"
    )


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

    Honours the provider/model override, else walks the priority order. Returns
    ``None`` when no provider key is available.

    The override is read from ``DAP_ASSISTANT_PROVIDER`` (engine env only —
    ``DAP_`` is a reserved instance-env prefix) OR ``ASSISTANT_PROVIDER``, the
    non-reserved alias that *can* be set as an instance env var from the admin
    UI. The ``DAP_``-prefixed form wins when both are present.
    """
    override = env.get("DAP_ASSISTANT_PROVIDER") or env.get("ASSISTANT_PROVIDER")
    model_override = env.get("DAP_ASSISTANT_MODEL") or env.get("ASSISTANT_MODEL")
    # claude-code is a CLI runtime, not an api-call provider with a key in the
    # registry — honour it only as an explicit opt-in, without an API-key check.
    if override == _CLI_PROVIDER:
        return _CLI_PROVIDER, model_override or _CLI_DEFAULT_MODEL
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


async def _run_via_claude_code(
    *,
    system_prompt: str,
    user_text: str,
    model_id: str,
    adapter: BaseAdapter | None = None,
    execution_id: str = "assistant",
) -> str | None:
    """One single-shot completion via the claude-code CLI on the subscription.

    claude-code has no separate system-prompt field — it reads the prompt from
    stdin — so we pipe ``system_prompt`` + the transcript as one prompt.
    ``use_subscription`` runs it on the unmetered Pro/Max pool ($0) using the
    host's ``claude login`` session; no API key is overlaid. Runs in a throwaway
    working directory so the agentic CLI can't touch the repo. Returns ``None``
    on failure (the raw CLI error may be auth/secret-tainted — don't surface it).
    """
    with tempfile.TemporaryDirectory(prefix="dap-assistant-") as workdir:
        task = RuntimeTask(
            execution_id=execution_id,
            prompt_xml=f"{system_prompt}\n\n{user_text}",
            working_directory=workdir,
            timeout_ms=120_000,
            runtime_config={"model_id": model_id, "use_subscription": True},
            instance_env_vars={},
        )
        result = await (adapter or ClaudeCodeAdapter()).execute(task)
    if not result.success:
        logger.warning("assistant: claude-code call failed")
        return None
    return result.output.strip()


async def run_llm(
    *,
    system_prompt: str,
    user_text: str,
    provider_id: str,
    model_id: str,
    env: Mapping[str, str],
    adapter: BaseAdapter | None = None,
    execution_id: str = "assistant",
    max_tokens: int = 1024,
) -> str | None:
    """One grounded single-shot completion.

    The provider is already selected by the caller (via :func:`select_provider`).
    Routes to the claude-code CLI (subscription, $0) when ``provider_id`` is
    ``claude-code``, else to :class:`ApiCallAdapter` — overlaying ONLY that
    provider's key, never the whole instance env. Returns the model text, or
    ``None`` on failure (raw provider errors are secret-tainted, so we don't
    surface or log their content).
    """
    if provider_id == _CLI_PROVIDER:
        return await _run_via_claude_code(
            system_prompt=system_prompt,
            user_text=user_text,
            model_id=model_id,
            adapter=adapter,
            execution_id=execution_id,
        )

    key_env = PROVIDER_REGISTRY[provider_id].default_env_var
    instance_overlay: dict[str, str] = {}
    if key_env and key_env in env and key_env not in os.environ:
        instance_overlay[key_env] = env[key_env]

    task = RuntimeTask(
        execution_id=execution_id,
        prompt_xml=user_text,
        working_directory=".",
        timeout_ms=60_000,
        runtime_config={
            "provider": provider_id,
            "model_id": model_id,
            "system_prompt": system_prompt,
            "max_tokens": max_tokens,
        },
        instance_env_vars=instance_overlay,
    )
    result = await (adapter or ApiCallAdapter()).execute(task)
    if not result.success:
        logger.warning("assistant: provider call failed")
        return None
    return result.output.strip()


async def generate_reply(
    messages: list[dict[str, str]],
    *,
    env: Mapping[str, str],
    context: Mapping[str, Any] | None = None,
    adapter: BaseAdapter | None = None,
) -> AssistantReply:
    """Produce one grounded assistant reply, or a graceful no-provider message.

    ``context`` is the optional page/form state (#689 phase 2) — rendered into a
    redacted block ahead of the transcript so advice is tailored to what the
    user is editing. Secret-keyed values are stripped by :func:`render_context`.
    """
    choice = select_provider(env)
    if choice is None:
        return AssistantReply(text=_NO_PROVIDER_MESSAGE, grounded=False)
    provider_id, model_id = choice
    text = await run_llm(
        system_prompt=_SYSTEM_INSTRUCTIONS + DOCS_CORPUS,
        user_text=render_context(context) + build_transcript(messages),
        provider_id=provider_id,
        model_id=model_id,
        env=env,
        adapter=adapter,
        execution_id="assistant",
    )
    if text is None:
        return AssistantReply(
            text="The model call failed — check the provider key/quota in Settings.",
            grounded=False,
        )
    clean, actions = parse_actions(text)
    return AssistantReply(text=clean, grounded=True, actions=actions)
