"""Unit tests for the interaction-log redactor (#722).

Acceptance criterion: ``redact()`` scrubs known instance-env-var values (exact)
plus common secret patterns, proven with real secret-shaped inputs and a
no-clear-text-leak assertion.
"""

from __future__ import annotations

import pytest
from dap_engine.redaction import MIN_EXACT_SECRET_LEN, redact


def _assemble(*parts: str) -> str:
    """Join fragments at runtime so the committed source never holds a contiguous
    secret-shaped literal. Without this, GitHub push protection / secret scanning
    blocks the whole file (e.g. it flags a literal ``xoxb-...`` as a Slack token).
    The redactor still receives the fully-assembled string under test."""
    return "".join(parts)


# Realistic secret-shaped fixtures (not real credentials), assembled from
# fragments — see :func:`_assemble`.
OPENAI_KEY = _assemble("sk-", "proj-", "abcdEFGH1234", "ijklMNOP5678qrstUVWX")
ANTHROPIC_KEY = _assemble("sk-", "ant-", "xyz0987654321", "ABCDEFGHIJ")
GITHUB_PAT = _assemble("ghp_", "A1b2C3d4E5f6G7h8I9j0", "K1l2M3n4O5p6Q7r8")
GITHUB_FINE = _assemble("github_pat_", "11ABCDEFG0abcdefghij_", "klmnopqrstuvwxyz1234567890ABCDEF")
AWS_KEY = _assemble("AKIA", "IOSFODNN7", "EXAMPLE")
SLACK_TOKEN = _assemble("xox", "b-", "123456789012-", "1234567890123-", "AbCdEfGhIjKlMnOpQrStUvWx")
JWT = _assemble("eyJhbGciOiJIUzI1NiJ9", ".eyJzdWIiOiIxMjM0NTY3ODkwIn0", ".abc123DEF456ghi")


class TestExactValueScrub:
    def test_replaces_known_secret_value_and_names_the_key(self) -> None:
        out = redact(
            "the db password is hunter2-very-secret here",
            known_secrets={"POSTGRES_PASSWORD": "hunter2-very-secret"},
        )
        assert "hunter2-very-secret" not in out
        assert "[REDACTED:POSTGRES_PASSWORD]" in out

    def test_replaces_every_occurrence(self) -> None:
        out = redact(
            "tok=s3cr3t-value and again s3cr3t-value",
            known_secrets={"API_TOKEN": "s3cr3t-value"},
        )
        assert "s3cr3t-value" not in out
        assert out.count("[REDACTED:API_TOKEN]") == 2

    def test_longest_value_wins_when_one_contains_another(self) -> None:
        out = redact(
            "value is abcdef-1234567890",
            known_secrets={"SHORT": "abcdef", "LONG": "abcdef-1234567890"},
        )
        # The longer secret is replaced whole; the shorter one must not fragment it.
        assert "abcdef" not in out
        assert "[REDACTED:LONG]" in out
        assert "[REDACTED:SHORT]" not in out

    def test_skips_values_below_minimum_length(self) -> None:
        short = "ab"  # below MIN_EXACT_SECRET_LEN — scrubbing it would nuke text
        assert len(short) < MIN_EXACT_SECRET_LEN
        out = redact("a fabulous absolute cab", known_secrets={"X": short})
        assert out == "a fabulous absolute cab"

    def test_literal_replacement_is_safe_for_regex_special_values(self) -> None:
        # A secret value containing regex metacharacters must be matched literally.
        secret = "p@ss.w+rd(x)*"
        out = redact(f"creds={secret}", known_secrets={"PW": secret})
        assert secret not in out
        assert "[REDACTED:PW]" in out

    def test_a_secret_value_cannot_fragment_an_earlier_placeholder(self) -> None:
        # Adversarial: one secret's value is a substring of the [REDACTED:...]
        # text inserted for another. A single-pass replace must not re-scan the
        # inserted placeholder, so the first redaction stays intact.
        out = redact(
            "real LONGSECRETVALUE here",
            known_secrets={"LONGSECRETKEY": "LONGSECRETVALUE", "FRAG": "ACTED"},
        )
        assert "LONGSECRETVALUE" not in out
        assert "[REDACTED:LONGSECRETKEY]" in out  # placeholder not corrupted by "ACTED"


class TestPatternScrub:
    @pytest.mark.parametrize(
        ("value", "label"),
        [
            (OPENAI_KEY, "OPENAI_KEY"),
            (GITHUB_PAT, "GITHUB_TOKEN"),
            (GITHUB_FINE, "GITHUB_TOKEN"),
            (AWS_KEY, "AWS_KEY"),
            (SLACK_TOKEN, "SLACK_TOKEN"),
            (JWT, "JWT"),
        ],
    )
    def test_scrubs_known_secret_shapes(self, value: str, label: str) -> None:
        out = redact(f"my token is {value} ok")
        assert value not in out
        assert f"[REDACTED:{label}]" in out

    def test_scrubs_bearer_token(self) -> None:
        out = redact("Authorization: Bearer abc123.def-456_ghi")
        assert "abc123.def-456_ghi" not in out
        assert "[REDACTED:BEARER]" in out
        assert "Bearer [REDACTED:BEARER]" in out

    def test_scrubs_connection_string_credentials_keeps_host(self) -> None:
        out = redact("dsn=postgres://appuser:s3cr3tpw@db.internal:5432/app")
        assert "s3cr3tpw" not in out
        assert "appuser" not in out
        assert "[REDACTED:CREDENTIALS]" in out
        # Host/scheme are preserved — useful for debugging, not secret.
        assert "db.internal:5432/app" in out
        assert out.startswith("dsn=postgres://")

    def test_connection_string_keeps_host_with_no_path(self) -> None:
        # No trailing path — the host must still survive (the userinfo match
        # backtracks to the separating '@', it does not swallow the host).
        out = redact("postgres://user:s3cr3tpw@db.internal:5432")
        assert "s3cr3tpw" not in out
        assert "db.internal:5432" in out
        assert out == "postgres://[REDACTED:CREDENTIALS]@db.internal:5432"

    def test_scrubs_connection_string_password_containing_at_sign(self) -> None:
        # A password with '@' must be redacted whole — the regex consumes
        # userinfo up to the LAST '@' before the host, not the first.
        out = redact("postgresql://user:p@ss@w0rd@db.internal:5432/app")
        assert "p@ss@w0rd" not in out
        assert "ss@w0rd" not in out
        assert "[REDACTED:CREDENTIALS]" in out
        assert "db.internal:5432/app" in out

    def test_does_not_touch_innocent_text(self) -> None:
        text = "The pipeline writes tests_passed and max_tokens=4096."
        assert redact(text) == text

    def test_scheme_prefix_with_long_at_less_blob_is_left_intact(self) -> None:
        # A crafted "scheme://" + huge run of non-slash chars without an '@' must
        # not match the connection-string rule (nothing to redact) and must
        # return promptly — the bounded quantifier prevents ReDoS backtracking.
        text = "weird://" + ("a" * 50_000)
        assert redact(text) == text


class TestPII:
    def test_email_not_redacted_by_default(self) -> None:
        text = "contact alice@example.com for access"
        assert redact(text) == text

    def test_email_redacted_when_enabled(self) -> None:
        out = redact("contact alice@example.com", redact_pii=True)
        assert "alice@example.com" not in out
        assert "[REDACTED:EMAIL]" in out


class TestGeneralProperties:
    def test_empty_text_is_returned_unchanged(self) -> None:
        assert redact("") == ""

    def test_no_known_secrets_still_applies_patterns(self) -> None:
        out = redact(f"key {OPENAI_KEY}", known_secrets=None)
        assert OPENAI_KEY not in out

    def test_is_idempotent(self) -> None:
        text = (
            f"db=postgres://u:p@h/x token={GITHUB_PAT} key={OPENAI_KEY} "
            "pw=hunter2-very-secret jwt=" + JWT
        )
        secrets = {"POSTGRES_PASSWORD": "hunter2-very-secret"}
        once = redact(text, known_secrets=secrets)
        twice = redact(once, known_secrets=secrets)
        assert once == twice

    def test_mixed_content_leaves_no_secret_in_clear_text(self) -> None:
        secrets = {
            "POSTGRES_PASSWORD": "hunter2-very-secret",
            "ANTHROPIC_API_KEY": ANTHROPIC_KEY,
        }
        text = (
            "User pasted: openai=" + OPENAI_KEY + ", gh=" + GITHUB_PAT + ", "
            "aws=" + AWS_KEY + ", db=postgresql://svc:hunter2-very-secret@h:5432/d, "
            "claude=" + ANTHROPIC_KEY + ", auth=Bearer " + JWT
        )
        out = redact(text, known_secrets=secrets)
        for leaked in (
            OPENAI_KEY,
            GITHUB_PAT,
            AWS_KEY,
            "hunter2-very-secret",
            ANTHROPIC_KEY,
            JWT,
        ):
            assert leaked not in out, f"clear-text leak: {leaked}"
