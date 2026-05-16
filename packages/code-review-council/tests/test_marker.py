"""Smoke tests for the re-review marker."""

from __future__ import annotations

from code_review_council.marker import (
    DEFAULT_BOT_LOGIN,
    find_cached_review,
    make_marker,
    make_verdict_tag,
)


def _sha(prefix: str = "f") -> str:
    """40-char hex string for tests."""
    return prefix * 40


def test_make_marker_uses_exact_sha_format() -> None:
    s = _sha("a")
    assert make_marker(s) == f"<!-- ai-review:{s} -->"


def test_make_verdict_tag_accepts_known_values() -> None:
    assert make_verdict_tag("approve") == "<!-- ai-verdict:approve -->"
    assert make_verdict_tag("reject") == "<!-- ai-verdict:reject -->"
    assert make_verdict_tag("malformed") == "<!-- ai-verdict:malformed -->"


def test_make_verdict_tag_rejects_unknown_values() -> None:
    import pytest

    with pytest.raises(ValueError, match="verdict must be one of"):
        make_verdict_tag("ApPrOvE")  # case-sensitive, on purpose
    with pytest.raises(ValueError):
        make_verdict_tag("comment")


def test_finds_marker_on_matching_sha_from_bot() -> None:
    s = _sha("b")
    reviews: list[dict[str, object]] = [
        {
            "id": 42,
            "body": f"prose\n{make_verdict_tag('approve')}\n{make_marker(s)}\nmore prose",
            "submitted_at": "2026-05-16T10:00:00Z",
            "user": {"login": DEFAULT_BOT_LOGIN, "type": "Bot"},
        }
    ]
    hit = find_cached_review(reviews, head_sha=s)
    assert hit is not None
    assert hit.review_id == 42
    assert hit.head_sha == s
    assert hit.verdict == "approve"
    assert hit.submitted_at == "2026-05-16T10:00:00Z"


def test_defaults_verdict_to_approve_when_missing_tag() -> None:
    """Older pre-tag reviews still resolve to a sensible verdict."""
    s = _sha("c")
    reviews: list[dict[str, object]] = [
        {
            "id": 7,
            "body": f"only the marker {make_marker(s)} no verdict tag",
            "submitted_at": "2026-05-16T10:00:00Z",
            "user": {"login": DEFAULT_BOT_LOGIN, "type": "Bot"},
        }
    ]
    hit = find_cached_review(reviews, head_sha=s)
    assert hit is not None
    assert hit.verdict == "approve"


def test_ignores_human_reviewer_with_quoted_marker() -> None:
    """Author filter is the security boundary — humans don't trigger skip."""
    s = _sha("d")
    reviews: list[dict[str, object]] = [
        {
            "id": 1,
            "body": f"I quoted {make_marker(s)} but I'm a human",
            "submitted_at": "2026-05-16T10:00:00Z",
            "user": {"login": "alice", "type": "User"},
        }
    ]
    assert find_cached_review(reviews, head_sha=s) is None


def test_no_match_on_different_sha() -> None:
    """Cache only hits the CURRENT head SHA — any push invalidates."""
    s_marked = _sha("a")
    s_current = _sha("b")
    reviews: list[dict[str, object]] = [
        {
            "id": 1,
            "body": make_marker(s_marked),
            "submitted_at": "2026-05-16T10:00:00Z",
            "user": {"login": DEFAULT_BOT_LOGIN, "type": "Bot"},
        }
    ]
    assert find_cached_review(reviews, head_sha=s_current) is None


def test_strict_40_hex_marker_regex() -> None:
    """A non-40-hex 'marker' must NOT match — defends against [^>]* soakup."""
    s_current = _sha("f")
    reviews: list[dict[str, object]] = [
        {
            "id": 1,
            "body": "<!-- ai-review:notrealsha -->",
            "submitted_at": "2026-05-16T10:00:00Z",
            "user": {"login": DEFAULT_BOT_LOGIN, "type": "Bot"},
        }
    ]
    assert find_cached_review(reviews, head_sha=s_current) is None


def test_accepts_bot_type_even_with_custom_login() -> None:
    """Third-party GitHub Apps post under custom logins but user.type='Bot'."""
    s = _sha("e")
    reviews: list[dict[str, object]] = [
        {
            "id": 99,
            "body": make_marker(s),
            "submitted_at": "2026-05-16T10:00:00Z",
            "user": {"login": "custom-app[bot]", "type": "Bot"},
        }
    ]
    hit = find_cached_review(reviews, head_sha=s)
    assert hit is not None
    assert hit.review_id == 99


def test_handles_missing_or_malformed_review_fields() -> None:
    """Reviews with no body / no user dict are skipped without raising."""
    s = _sha("a")
    reviews: list[dict[str, object]] = [
        {"id": 1},  # no body, no user
        {"id": 2, "body": None, "user": {"login": DEFAULT_BOT_LOGIN}},
        {"id": 3, "body": make_marker(s), "user": "not-a-dict"},
        # The valid one, last:
        {
            "id": 4,
            "body": make_marker(s),
            "submitted_at": "2026-05-16T10:00:00Z",
            "user": {"login": DEFAULT_BOT_LOGIN, "type": "Bot"},
        },
    ]
    hit = find_cached_review(reviews, head_sha=s)
    assert hit is not None
    assert hit.review_id == 4
