"""Secret/PII redaction for interaction logging (#722).

DAP wants to log the **content** of model interactions (transcript + reply) for
EU AI Act record-keeping, while never writing a secret in clear text (the
security rule, and CodeQL "Clear-text logging of sensitive information"). The
resolution is *redact-then-log*: scrub secrets out of the text at the
persistence boundary, then store the redacted result.

:func:`redact` has two layers, applied in this order:

1. **Exact-value scrub (strongest).** At request time we hold the *decrypted*
   instance env-var values, so we can replace any exact occurrence of a known
   secret value with ``[REDACTED:<KEY_NAME>]``. Deterministic — no false
   negatives for a configured secret, and the placeholder names which key
   leaked. This is the reliable layer.
2. **Pattern scrub (best-effort).** Regexes for common secret *shapes* (OpenAI
   ``sk-``, GitHub ``ghp_``/PAT, AWS ``AKIA``, Slack ``xox*``, ``Bearer`` tokens,
   JWTs, ``scheme://user:pass@host`` credentials) catch tokens that aren't in
   the configured set — e.g. a user pasting a token, or a provider echoing a key
   in an error string. Regex can't catch every shape; this backstops layer 1.

PII redaction (emails) is opt-in via ``redact_pii`` — pseudonymisation is a GDPR
product decision, off by default.

The function is pure and idempotent: redacting already-redacted text is a no-op
(placeholders don't match any secret pattern).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

# An exact secret value shorter than this is ignored by the exact-value layer.
# Scrubbing a 1-3 character "secret" (e.g. a value that happens to be ``"1"`` or
# ``"dev"``) would nuke unrelated text and destroy the log's usefulness. Pattern
# scrub still covers genuinely secret-shaped short tokens.
MIN_EXACT_SECRET_LEN = 4


def _redacted(label: str) -> str:
    return f"[REDACTED:{label}]"


# (compiled pattern, replacement) — replacement is a backref-aware string or a
# callable, per ``re.sub`` semantics. Order matters: connection-string and
# Bearer rules run before the bare-token rules so the more specific shape wins.
_PATTERN_RULES: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    # scheme://userinfo@host — keep scheme + host, drop the credentials.
    # The userinfo run greedily consumes up to the LAST ``@`` before the host, so
    # a password that itself contains ``@`` (e.g. ``postgres://u:p@ss@host``) is
    # fully redacted, not just up to the first ``@``. A raw (unencoded) ``/``
    # inside a password is indistinguishable from the path separator and is left
    # to the exact-value layer — best-effort here. The run is length-bounded
    # ({1,256}) so a crafted ``scheme://`` + huge ``@``-less blob in an untrusted
    # transcript can't trigger pathological backtracking (ReDoS); real userinfo
    # is far shorter than 256 chars.
    (
        re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^/\s]{1,256}@"),
        lambda m: f"{m.group(1)}{_redacted('CREDENTIALS')}@",
    ),
    # Authorization: Bearer <token>
    (
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/\-]+=*", re.IGNORECASE),
        f"Bearer {_redacted('BEARER')}",
    ),
    # JWT (header.payload.signature) — base64url segments, ``eyJ`` == ``{"``.
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
        _redacted("JWT"),
    ),
    # OpenAI keys: sk-..., sk-proj-..., sk-svcacct-...
    (
        re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}"),
        _redacted("OPENAI_KEY"),
    ),
    # GitHub tokens: ghp_/gho_/ghu_/ghs_/ghr_ + 36 chars, and fine-grained PATs.
    (
        re.compile(r"\bgh[poursa]_[A-Za-z0-9]{30,}"),
        _redacted("GITHUB_TOKEN"),
    ),
    (
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
        _redacted("GITHUB_TOKEN"),
    ),
    # AWS access key id.
    (
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        _redacted("AWS_KEY"),
    ),
    # Slack tokens: xoxb-/xoxp-/xoxa-/xoxr-/xoxs-...
    (
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
        _redacted("SLACK_TOKEN"),
    ),
]

# Applied only when redact_pii=True.
_EMAIL_RULE: tuple[re.Pattern[str], str] = (
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    _redacted("EMAIL"),
)


def redact(
    text: str,
    *,
    known_secrets: Mapping[str, str] | None = None,
    redact_pii: bool = False,
) -> str:
    """Return ``text`` with secrets (and optionally PII) scrubbed.

    Args:
        text: free-form content (a transcript, a model reply, an error string).
        known_secrets: mapping of ``KEY_NAME -> decrypted secret value`` (e.g.
            instance env-var values resolved at request time). Each exact
            occurrence of a value is replaced with ``[REDACTED:<KEY_NAME>]``.
            Values shorter than :data:`MIN_EXACT_SECRET_LEN` are skipped to avoid
            catastrophic over-redaction.
        redact_pii: when ``True``, also pseudonymise emails to
            ``[REDACTED:EMAIL]``.

    The exact-value layer runs first (strongest, names the leaked key), then the
    pattern layer. Pure and idempotent.
    """
    if not text:
        return text

    if known_secrets:
        # Single left-to-right pass over the ORIGINAL text via one alternation,
        # longest value first so a secret containing another is matched whole.
        # ``re.sub`` does not re-scan the text it inserts, so a later/shorter
        # secret value can never fragment an already-inserted ``[REDACTED:...]``
        # placeholder. ``re.escape`` keeps values literal (no pattern injection).
        name_by_value: dict[str, str] = {}
        for name, value in sorted(known_secrets.items(), key=lambda kv: len(kv[1]), reverse=True):
            if value and len(value) >= MIN_EXACT_SECRET_LEN:
                name_by_value.setdefault(value, name)
        if name_by_value:
            alternation = re.compile("|".join(re.escape(value) for value in name_by_value))
            text = alternation.sub(lambda m: _redacted(name_by_value[m.group(0)]), text)

    for pattern, replacement in _PATTERN_RULES:
        text = pattern.sub(replacement, text)

    if redact_pii:
        pattern, replacement = _EMAIL_RULE
        text = pattern.sub(replacement, text)

    return text
