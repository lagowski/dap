"""Depth guard for export redaction (#778 audit security #6).

``scrub_secret_like_keys`` recurses into nested dicts with no depth
limit — a pathologically nested ``runtime_config`` (attacker-supplied
JSON) could blow the recursion limit on export. Beyond the guard depth
the subtree is replaced with an explicit placeholder instead of
crashing the endpoint.
"""

from __future__ import annotations

from dap_engine.api.export_redaction import (
    MAX_SCRUB_DEPTH,
    scrub_secret_like_keys,
)


def _nested(depth: int) -> dict[str, object]:
    leaf: dict[str, object] = {"api_key": "s3cret", "plain": "ok"}
    node: dict[str, object] = leaf
    for _ in range(depth):
        node = {"child": node}
    return node


def test_normal_nesting_still_redacts() -> None:
    result = scrub_secret_like_keys(_nested(5))
    node: dict[str, object] = result
    for _ in range(5):
        node = node["child"]  # type: ignore[assignment]
    assert node["api_key"] == "<redacted>"
    assert node["plain"] == "ok"


def test_pathological_nesting_does_not_recurse_forever() -> None:
    """10k levels must neither raise RecursionError nor leak the secret."""
    result = scrub_secret_like_keys(_nested(10_000))
    assert "s3cret" not in repr(result)


def test_subtree_beyond_max_depth_is_replaced_with_placeholder() -> None:
    result = scrub_secret_like_keys(_nested(MAX_SCRUB_DEPTH + 5))
    node: object = result
    for _ in range(MAX_SCRUB_DEPTH):
        assert isinstance(node, dict)
        node = node["child"]
    assert node == "<redacted:max-depth-exceeded>"
