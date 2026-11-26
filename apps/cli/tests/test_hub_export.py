"""Tests for ``dap pipeline export --hub`` (#1016): bundle -> Labs hub docbundle + graph."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from dap_cli.__main__ import app
from dap_cli.hub_export import Unsafe, scan, to_hub_bundle, write_hub_bundle
from typer.testing import CliRunner

runner = CliRunner()

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = sorted((REPO / "examples" / "pipelines").glob("*.pipeline-bundle.json"))
FIRST_HUB_SLUGS = ("github-read-issue-comment", "github-issue-triage", "team-collaboration")
GENERATED = "2026-11-26"

# The hub's docbundle schema 1 rules, plus the ``graph`` key this adapter adds.
HUB_KEYS = {
    "schema",
    "kind",
    "slug",
    "lang",
    "title",
    "summary",
    "goal",
    "needs",
    "steps",
    "pitfalls",
    "sources",
    "generated",
    "curated_on",
    "source_note",
    "graph",
}
HUB_REQUIRED = {
    "schema",
    "kind",
    "slug",
    "lang",
    "title",
    "summary",
    "goal",
    "needs",
    "steps",
    "pitfalls",
    "generated",
}
HUB_SLUG = re.compile(r"^(?!g\d+-)[a-z0-9][a-z0-9-]{2,62}$")


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _example(name: str) -> dict[str, Any]:
    return _load(REPO / "examples" / "pipelines" / f"{name}.pipeline-bundle.json")


def assert_hub_valid(page: dict[str, Any]) -> None:
    """Mirror of the hub's validator, so a bundle this exporter writes imports there unchanged."""
    assert set(page) <= HUB_KEYS, set(page) - HUB_KEYS
    assert set(page) >= HUB_REQUIRED, HUB_REQUIRED - set(page)
    assert page["schema"] == 1 and page["kind"] == "pipeline" and page["lang"] == "en"
    assert HUB_SLUG.match(page["slug"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", page["generated"])
    for key, limit in (("title", 200), ("summary", 800), ("goal", 800)):
        assert isinstance(page[key], str) and page[key].strip() and len(page[key]) <= limit, key
    for key, limit in (("needs", 20), ("pitfalls", 20)):
        assert len(page[key]) <= limit
        assert all(isinstance(x, str) and x.strip() and len(x) <= 500 for x in page[key])
    assert 1 <= len(page["steps"]) <= 40
    for step in page["steps"]:
        assert set(step) <= {"title", "body", "code"} and {"title", "body"} <= set(step)
        assert 0 < len(step["title"]) <= 200 and 0 < len(step["body"]) <= 4000
    for name in page.get("sources", []):
        assert len(name) <= 100 and not re.search(
            r"[/\\]|://|^[A-Za-z]:|\.(?:md|txt|json|ya?ml)$", name
        )
    note = page.get("source_note", "")
    assert len(note) <= 160
    assert not re.search(
        r"://|\bwww\.|[/\\]|\b[\w-]+\.(?:com|es|org|net|io|dev|md|json|ya?ml|txt|py|sh)\b", note
    )


# ---------------------------------------------------------------------------
# Every example converts into a valid, clean hub bundle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name.split(".")[0])
def test_every_example_exports_valid_and_clean(path: Path) -> None:
    slug = path.name.split(".")[0]
    page = to_hub_bundle(_load(path), slug, generated=GENERATED)
    assert_hub_valid(page)
    assert scan(page) == []
    assert page["slug"] == slug


def test_graph_mirrors_the_pipeline_without_start_and_end() -> None:
    bundle = _example("github-issue-triage")
    graph = to_hub_bundle(bundle, "github-issue-triage", generated=GENERATED)["graph"]
    assert graph["entry"] == "fetch_issues"
    assert [n["id"] for n in graph["nodes"]] == [
        "fetch_issues",
        "select_issue",
        "load_prd",
        "enrich",
        "create_branch",
    ]
    first = graph["nodes"][0]
    assert first == {
        "id": "fetch_issues",
        "title": "GH Issue Fetcher",
        "type": "bash",
        "inputs": [],
        "outputs": ["available_issues"],
        "x": 0,
        "y": 0,
    }
    assert {"from": "fetch_issues", "to": "select_issue"} in graph["edges"]
    assert all(
        "__start__" not in (e["from"], e["to"]) and "__end__" not in (e["from"], e["to"])
        for e in graph["edges"]
    )
    assert len(graph["edges"]) == 4


def test_conditional_edge_keeps_its_label_or_derives_one() -> None:
    labelled = to_hub_bundle(
        _example("cortex-github-issue"), "cortex-github-issue", generated=GENERATED
    )["graph"]
    assert {"from": "finalize", "to": "phase1_gate", "label": "READY"} in labelled["edges"]
    derived = to_hub_bundle(
        _example("project-bootstrap"), "project-bootstrap", generated=GENERATED
    )["graph"]
    assert {
        "from": "gate_params",
        "to": "scope_mvp",
        "label": "gate_params_approved == true",
    } in derived["edges"]


def test_no_prompt_or_runtime_config_reaches_the_bundle() -> None:
    bundle = _example("github-issue-triage")
    text = json.dumps(to_hub_bundle(bundle, "github-issue-triage", generated=GENERATED))
    for agent in bundle["bundled_agents"].values():
        assert agent["prompt_template"][:60] not in text
        for value in agent.get("runtime_config", {}).values():
            if isinstance(value, str) and len(value) > 3:
                assert value not in text, value
    assert "gh issue list" not in text
    assert "glm-4.5" not in text


def test_issue_references_are_dropped_from_text() -> None:
    page = to_hub_bundle(
        _example("github-read-issue-comment"), "github-read-issue-comment", generated=GENERATED
    )
    assert "#919" not in page["summary"]


def test_steps_walk_the_nodes_and_explain_import_and_run() -> None:
    page = to_hub_bundle(_example("team-collaboration"), "team-collaboration", generated=GENERATED)
    titles = [s["title"] for s in page["steps"]]
    assert titles[0].lower().startswith("get the bundle")
    assert titles[1].lower().startswith("import")
    assert titles[-1].lower().startswith("run")
    assert [t.split(": ", 1)[1] for t in titles[2:-1]] == [
        "Scope Extractor",
        "Brief Drafter",
        "GitHub Issue Formatter",
    ]
    assert "team-collaboration.pipeline-bundle.json" in page["steps"][0]["body"]


def test_needs_cover_every_runtime_used() -> None:
    page = to_hub_bundle(
        _example("github-issue-triage"), "github-issue-triage", generated=GENERATED
    )
    needs = " ".join(page["needs"]).lower()
    assert "dap" in needs
    assert "shell" in needs  # bash
    assert "llm provider" in needs  # api-call


def test_approval_gates_and_budget_become_pitfalls() -> None:
    page = to_hub_bundle(_example("project-bootstrap"), "project-bootstrap", generated=GENERATED)
    pitfalls = " ".join(page["pitfalls"])
    assert "gate_params" in pitfalls
    assert "25" in pitfalls  # budget_limit_usd


def test_bad_slug_is_refused() -> None:
    with pytest.raises(ValueError):
        to_hub_bundle(_example("team-collaboration"), "g300-truco", generated=GENERATED)
    with pytest.raises(ValueError):
        to_hub_bundle(_example("team-collaboration"), "Bad Slug", generated=GENERATED)


# ---------------------------------------------------------------------------
# The guard refuses anything private
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "leak",
    [
        "see https://example.com/x",
        "host 192.168.1.100",
        "mail ops@example.com",
        "file /home/someone/secret.txt",
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "key sk-abcdefghijklmnopqrstuvwx",
        "the box at db.internal",
    ],
)
def test_scan_flags_private_material(leak: str) -> None:
    assert scan({"summary": leak}) != []


def test_scan_reports_the_rule_not_the_value() -> None:
    hits = scan({"steps": [{"body": "token ghp_abcdefghijklmnopqrstuvwxyz0123456789"}]})
    assert hits and "ghp_" not in " ".join(hits)


def test_export_refuses_a_leaky_bundle() -> None:
    bundle = copy.deepcopy(_example("team-collaboration"))
    bundle["pipeline"]["description"] = "Ask ops at 10.1.2.3 first."
    with pytest.raises(Unsafe):
        to_hub_bundle(bundle, "team-collaboration", generated=GENERATED)


# ---------------------------------------------------------------------------
# Writing and the CLI
# ---------------------------------------------------------------------------


def test_write_hub_bundle_layout(tmp_path: Path) -> None:
    src = REPO / "examples" / "pipelines" / "team-collaboration.pipeline-bundle.json"
    out = write_hub_bundle(src, "team-collaboration", tmp_path, generated=GENERATED)
    assert out == tmp_path / "team-collaboration" / "page" / "page.json"
    assert_hub_valid(_load(out))


def test_write_leaves_nothing_on_refusal(tmp_path: Path) -> None:
    bundle = _example("team-collaboration")
    bundle["pipeline"]["description"] = "see https://example.com"
    src = tmp_path / "leaky.pipeline-bundle.json"
    src.write_text(json.dumps(bundle))
    with pytest.raises(Unsafe):
        write_hub_bundle(src, "leaky", tmp_path / "out", generated=GENERATED)
    assert not (tmp_path / "out").exists()


def test_cli_export_writes_the_first_hub_bundles(tmp_path: Path) -> None:
    for slug in FIRST_HUB_SLUGS:
        src = REPO / "examples" / "pipelines" / f"{slug}.pipeline-bundle.json"
        result = runner.invoke(
            app, ["pipeline", "export", str(src), "--hub", slug, "--out", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert_hub_valid(_load(tmp_path / slug / "page" / "page.json"))


def test_cli_export_refusal_exits_nonzero(tmp_path: Path) -> None:
    bundle = _example("team-collaboration")
    bundle["pipeline"]["description"] = "host 192.168.1.100"
    src = tmp_path / "leaky.pipeline-bundle.json"
    src.write_text(json.dumps(bundle))
    result = runner.invoke(
        app, ["pipeline", "export", str(src), "--hub", "leaky", "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == 2
    assert "192.168" not in result.output
