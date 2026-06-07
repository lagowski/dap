"""Deterministic error explainer for failed nodes (#691, slice 1).

Turns a node's raw ``error_message`` into a plain-language cause plus concrete
suggested actions, by matching the known shapes the engine actually emits
(claude-bridge gate, provider quota/credits, missing provider key,
unimportable python-func callable, contract mismatch, timeout, budget). For
unrecognised failures it returns a generic explanation flagged
``recognized=False`` — that's the hook where an LLM fallback (slice 2, needs
the assistant backend from #689) will take over.

No LLM, no secrets: this works purely from the error text. Env-var and
extension *names* may appear in the suggestions, never values.
"""

from __future__ import annotations

import re

from dap_runtimes import classify_provider_failure
from pydantic import BaseModel

_DOCS_BASE = "https://github.com/lagowski/dap/blob/develop/docs"


class SuggestedAction(BaseModel):
    """One concrete next step. ``kind``/``target`` let the UI offer a button.

    ``kind`` is an open hint: ``set_extension`` (``target`` = extension key),
    ``add_env`` (``target`` = env-var name), ``edit_agent``, ``retry``,
    ``raise_budget``, or ``None`` for a plain instruction. The UI **never**
    auto-applies — it only offers the affordance.
    """

    text: str
    kind: str | None = None
    target: str | None = None


class DocLink(BaseModel):
    label: str
    href: str


class ErrorExplanation(BaseModel):
    cause: str
    actions: list[SuggestedAction] = []
    docs: list[DocLink] = []
    # True when a known error shape matched; False = generic fallback (the
    # LLM-explanation seam for slice 2).
    recognized: bool = False
    # "deterministic" now; "llm" once the fallback lands.
    source: str = "deterministic"


_ENV_VAR_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")


_ENV_VAR_NON_ENV_WORDS = frozenset(
    {
        "DAP",
        "AI",
        "LLM",
        "PR",
        "CI",
        "URL",
        "JSON",
        "HTTP",
        "API",
        "KEY",
        "SECRET",
        "TOKEN",
        "OAUTH",
    }
)


def _is_generic_token(token: str) -> bool:
    """A token whose every underscore-part is a generic word (e.g. ``API_KEY``)."""
    return all(part in _ENV_VAR_NON_ENV_WORDS for part in token.split("_"))


def _find_env_var(text: str) -> str | None:
    """Pull the most likely env-var name from an error message.

    Real env vars almost always contain an underscore (``ANTHROPIC_API_KEY``),
    so prefer an underscored ALL-CAPS token first — otherwise a bare word like
    "API" in "Invalid API key — set ANTHROPIC_API_KEY" would win. Skip tokens
    whose every part is generic (``API_KEY`` appearing in prose), then fall
    back to a standalone ALL-CAPS token that isn't a common non-env word.
    """
    candidates = [m.group(1) for m in _ENV_VAR_RE.finditer(text)]
    for token in candidates:
        if "_" in token and not _is_generic_token(token):
            return token
    for token in candidates:
        if token not in _ENV_VAR_NON_ENV_WORDS:
            return token
    return None


def explain_node_error(  # noqa: PLR0911 — a dispatcher: one return per known error shape
    error_message: str | None,
    *,
    runtime_id: str | None = None,
) -> ErrorExplanation:
    """Explain a failed node's error deterministically (#691)."""
    if not error_message or not error_message.strip():
        return ErrorExplanation(
            cause="No error message was recorded for this node.",
            recognized=False,
        )

    text = error_message
    low = text.lower()

    # 1) Claude-bridge review gate — a fully structured, opt-in/opt-out error.
    if "claude_bridge_enabled" in low or "claude-bridge" in low:
        return ErrorExplanation(
            cause=(
                "The claude-bridge review gate refused to proceed because it was "
                "neither opted into nor explicitly bypassed for this run."
            ),
            actions=[
                SuggestedAction(
                    text="Opt in: set extensions.claude_bridge_enabled=true on the run/project.",
                    kind="set_extension",
                    target="claude_bridge_enabled",
                ),
                SuggestedAction(
                    text="Or bypass: set extensions.skip_claude_bridge_review=true to skip it.",
                    kind="set_extension",
                    target="skip_claude_bridge_review",
                ),
            ],
            recognized=True,
        )

    # 2) python-func callable can't be imported (missing package, e.g. dap-cortex).
    if "cannot import" in low and "python-func" in low:
        return ErrorExplanation(
            cause=(
                "This node runs a Python callable that the engine can't import — "
                "the package providing it isn't installed in the engine venv."
            ),
            actions=[
                SuggestedAction(
                    text=(
                        "Install the package that provides this callable in the engine "
                        "venv (e.g. `uv add dap-cortex` for cortex nodes), then retry."
                    ),
                ),
                SuggestedAction(
                    text="Verify the agent's runtime_config.callable_path is correct.",
                    kind="edit_agent",
                ),
            ],
            recognized=True,
        )

    # 3) python-func callable_path malformed / missing.
    if "callable_path" in low:
        return ErrorExplanation(
            cause="The python-func agent's callable_path is missing or malformed.",
            actions=[
                SuggestedAction(
                    text="Fix runtime_config.callable_path — it must be 'module.path:func_name'.",
                    kind="edit_agent",
                )
            ],
            recognized=True,
        )

    # 4) Provider quota / credits / rate-limit (reuses the #692 classifier).
    category = classify_provider_failure(text)
    if category == "out_of_credits":
        return ErrorExplanation(
            cause="The model provider rejected the call: quota or credit balance is exhausted.",
            actions=[
                SuggestedAction(text="Top up billing / raise the quota with the provider."),
                SuggestedAction(
                    text="Or switch this agent to a different provider or model.", kind="edit_agent"
                ),
            ],
            recognized=True,
        )
    if category == "rate_limit":
        return ErrorExplanation(
            cause="The model provider throttled the call (rate limit). This is usually transient.",
            actions=[
                SuggestedAction(
                    text="Retry the node — rate limits typically clear on their own.", kind="retry"
                ),
                SuggestedAction(text="Or lower run concurrency / the agent's request rate."),
            ],
            recognized=True,
        )

    # 5) Missing / invalid provider key (auth).
    if (
        "api key" in low
        or "api_key" in low
        or "unauthorized" in low
        or "401" in low
        or "authentication" in low
    ):
        env = _find_env_var(text)
        action = (
            SuggestedAction(
                text=f"Add or fix the provider key `{env}` in Settings → environment variables.",
                kind="add_env",
                target=env,
            )
            if env
            else SuggestedAction(
                text="Add or fix the provider API key in Settings → environment variables.",
                kind="add_env",
            )
        )
        return ErrorExplanation(
            cause="The model provider rejected authentication — the API key is missing or invalid.",
            actions=[action],
            recognized=True,
        )

    # 6) Output contract mismatch.
    if (
        "output_schema" in low
        or "contract" in low
        or "required field" in low
        or "did not match" in low
    ):
        field = None
        m = re.search(r"field[s]?\s+['\"]?([a-zA-Z0-9_]+)", text)
        if m:
            field = m.group(1)
        cause = (
            f"The agent's output didn't satisfy its declared contract"
            f"{f' (missing/invalid field `{field}`)' if field else ''}."
        )
        return ErrorExplanation(
            cause=cause,
            actions=[
                SuggestedAction(
                    text=(
                        "Adjust the agent prompt to emit the required output fields, "
                        "or relax the agent's output_schema."
                    ),
                    kind="edit_agent",
                )
            ],
            recognized=True,
        )

    # 7) Timeout.
    if "timed out" in low or "timeout" in low:
        return ErrorExplanation(
            cause="The node exceeded its time limit before finishing.",
            actions=[
                SuggestedAction(
                    text="Increase the agent's timeout_ms if the work legitimately takes longer.",
                    kind="edit_agent",
                ),
                SuggestedAction(
                    text="Or investigate why the node hung (external call, infinite loop).",
                    kind="retry",
                ),
            ],
            recognized=True,
        )

    # 8) Budget exceeded.
    if "budget" in low and ("exceed" in low or "limit" in low):
        return ErrorExplanation(
            cause="The run hit its budget limit before this node could complete.",
            actions=[
                SuggestedAction(
                    text="Raise the pipeline's budget_limit_usd, or reduce token usage.",
                    kind="raise_budget",
                ),
            ],
            recognized=True,
        )

    # Fallback — unrecognised. This is the seam an LLM explanation fills (#691 slice 2).
    return ErrorExplanation(
        cause="This failure doesn't match a known pattern.",
        actions=[
            SuggestedAction(
                text=(
                    "Review the raw error above. A richer AI explanation will appear "
                    "here once an LLM is configured."
                ),
            )
        ],
        docs=[DocLink(label="Runtimes & troubleshooting", href=f"{_DOCS_BASE}/runtimes.md")],
        recognized=False,
    )
