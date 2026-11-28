"""Tests for ``dap pipeline export --hub`` (#1016): bundle -> Labs hub docbundle + graph."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from dap_cli.__main__ import app
from dap_cli.hub_export import GUARD, Unsafe, _plain, scan, to_hub_bundle, write_hub_bundle
from typer.testing import CliRunner

runner = CliRunner()

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = sorted((REPO / "examples" / "pipelines").glob("*.pipeline-bundle.json"))
FIRST_HUB_SLUGS = ("github-read-issue-comment", "github-issue-triage", "team-collaboration")
GENERATED = "2026-11-26"
CARD: dict[str, Any] = {"tagline5": "Run this example pipeline", "io": "pipeline:report"}
# The card values for the first three hub bundles (issue #1016).
FIRST_HUB_CARDS = {
    "github-read-issue-comment": ("Read an issue, then comment", "issue:comment"),
    "github-issue-triage": ("Pick and enrich the next issue", "list:issue"),
    "team-collaboration": ("Turn a problem into a brief", "comment:report"),
}
# A deliberate independent copy of the vocabulary from issue #1016, so a change to
# IO_ICONS in hub_export.py fails this spec check instead of passing silently.
IO_VOCAB = set(
    [
        "bulb",
        "scale",
        "repo",
        "issue",
        "merge",
        "docs",
        "post",
        "search",
        "report",
        "script",
        "clapperboard",
        "blocks",
        "pipeline",
        "list",
        "fanout",
        "comment",
        "agents",
        "agreement",
        "endpoint",
        "pr",
        "review",
        "terminal",
        "engine",
    ]
)

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
    "tagline5",
    "io_icons",
    "build",
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
    "tagline5",
    "io_icons",
}
HUB_SLUG = re.compile(r"^(?!g\d+-)[a-z0-9][a-z0-9-]{2,62}$")
# The hub importer's node keys: the canvas ones plus the optional detail-panel ones (round 5).
DETAIL_KEYS = {
    "agent",
    "prompt",
    "tools",
    "input_fields",
    "output_fields",
    "example",
    "code",
    "failure_modes",
    "gate_rule",
    "retries",
    "max_loops",
}
NODE_KEYS = {"id", "title", "type", "purpose", "inputs", "outputs", "x", "y"} | DETAIL_KEYS


def _text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit


def assert_node_detail_valid(node: dict[str, Any]) -> None:  # noqa: PLR0912
    """The hub's limits on the optional detail keys; a present key is never an empty placeholder."""
    if "agent" in node:
        agent = node["agent"]
        assert set(agent) <= {"name", "model", "provider"} and "name" in agent
        assert all(_text(v, 200) for v in agent.values()), agent
    if "prompt" in node:
        prompt = node["prompt"]
        assert prompt and set(prompt) <= {"system", "user"}
        assert all(_text(v, 8000) for v in prompt.values())
    if "tools" in node:
        assert 0 < len(node["tools"]) <= 30
        for tool in node["tools"]:
            if isinstance(tool, dict):
                assert set(tool) <= {"name", "signature", "description"} and _text(tool["name"], 80)
                assert all(_text(v, 300) for v in tool.values()), tool
            else:
                assert _text(tool, 80)
    for key in ("input_fields", "output_fields"):
        if key in node:
            fields = node[key]
            assert 0 < len(fields) <= 40
            for field in fields:
                assert set(field) <= {"name", "type", "description"} and _text(field["name"], 200)
                assert "description" not in field or _text(field["description"], 200)
                assert "type" not in field or _text(field["type"], 200)
    if "example" in node:
        example = node["example"]
        assert set(example) == {"input", "output", "example_source"}
        assert example["example_source"] == "synthetic"
        for side in ("input", "output"):
            assert len(json.dumps(example[side])) <= 4000
    if "code" in node:
        assert node["type"] != "llm" and set(node["code"]) == {"language", "source"}
        assert _text(node["code"]["language"], 40) and _text(node["code"]["source"], 6000)
    if "failure_modes" in node:
        modes = node["failure_modes"]
        assert 0 < len(modes) <= 20 and all(_text(m, 300) for m in modes)
    if "gate_rule" in node:
        assert node["type"] == "gate" and _text(node["gate_rule"], 300)
    if "retries" in node:
        assert isinstance(node["retries"], int) and 0 <= node["retries"] <= 20
    if "max_loops" in node:
        assert isinstance(node["max_loops"], int) and 0 <= node["max_loops"] <= 50


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
    assert 3 <= len(page["tagline5"].split()) <= 6 and len(page["tagline5"]) <= 60
    assert page["tagline5"] == page["tagline5"].strip()
    assert set(page["io_icons"]) == {"in", "out"} and set(page["io_icons"].values()) <= IO_VOCAB
    for node in page["graph"]["nodes"]:
        assert set(node) <= NODE_KEYS, set(node) - NODE_KEYS
        if "purpose" in node:
            assert 0 < len(node["purpose"]) <= 90 and "\n" not in node["purpose"]
        assert_node_detail_valid(node)
    if "build" in page:
        assert_build_valid(page["build"])
    note = page.get("source_note", "")
    assert len(note) <= 160
    assert not re.search(
        r"://|\bwww\.|[/\\]|\b[\w-]+\.(?:com|es|org|net|io|dev|md|json|ya?ml|txt|py|sh)\b", note
    )


ALLOWED_DOMAIN = re.compile(r"^(?:[\w-]+\.)*(?:example\.(?:com|org|net)|[\w-]+\.(?:example|test))$")
ANY_DOMAIN = re.compile(r"\b(?:[\w-]+\.)+(?:com|org|net|io|dev|ai|app|co|cn|uk|de|fr|es|eu|me)\b")


def assert_build_valid(build: dict[str, Any]) -> None:
    """The hub's rules on the ``build`` section (#1016 round 6)."""
    assert set(build) <= {"requirements", "env", "runner", "steps", "dap", "example_source"}
    assert build.get("example_source", "synthetic") == "synthetic"
    for req in build.get("requirements", []):
        assert set(req) <= {"package", "version"} and _text(req["package"], 100)
        assert re.fullmatch(r"\d+(?:\.\d+)*", req["version"]), req  # pinned
    for env in build.get("env", []):
        assert set(env) <= {"name", "placeholder", "description"}
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", env["name"])
        assert re.fullmatch(r"<[^<>]+>", env["placeholder"]), env  # a placeholder, never a value
    if "runner" in build:
        assert build["runner"]["language"] == "python"
        assert _text(build["runner"]["source"], 12000)
    for step in build.get("steps", []):
        assert set(step) <= {"title", "body", "command", "expected_output"}
        assert _text(step["title"], 200) and _text(step["body"], 4000)
        if "expected_output" in step:
            assert build["example_source"] == "synthetic"
    assert "dap" not in build or _text(build["dap"], 4000)
    for _, text in _walk_strings(build):
        for domain in ANY_DOMAIN.findall(text):
            assert ALLOWED_DOMAIN.match(domain), domain


def _walk_strings(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [("", value)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _walk_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _walk_strings(v)]
    return []


# ---------------------------------------------------------------------------
# Every example converts into a valid, clean hub bundle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name.split(".")[0])
def test_every_example_exports_valid_and_clean(path: Path) -> None:
    slug = path.name.split(".")[0]
    page = to_hub_bundle(_load(path), slug, **CARD, generated=GENERATED)
    assert_hub_valid(page)
    assert scan(page) == []
    assert page["slug"] == slug


def test_graph_mirrors_the_pipeline_without_start_and_end() -> None:
    bundle = _example("github-issue-triage")
    graph = to_hub_bundle(bundle, "github-issue-triage", **CARD, generated=GENERATED)["graph"]
    assert graph["entry"] == "fetch_issues"
    assert [n["id"] for n in graph["nodes"]] == [
        "fetch_issues",
        "select_issue",
        "load_prd",
        "enrich",
        "create_branch",
    ]
    first = graph["nodes"][0]
    assert set(first) - DETAIL_KEYS == {"id", "title", "type", "inputs", "outputs", "x", "y"}
    assert {k: v for k, v in first.items() if k not in DETAIL_KEYS} == {
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
        _example("cortex-github-issue"), "cortex-github-issue", **CARD, generated=GENERATED
    )["graph"]
    assert {"from": "finalize", "to": "phase1_gate", "label": "READY"} in labelled["edges"]
    derived = to_hub_bundle(
        _example("project-bootstrap"), "project-bootstrap", **CARD, generated=GENERATED
    )["graph"]
    assert {
        "from": "gate_params",
        "to": "scope_mvp",
        "label": "gate_params_approved == true",
    } in derived["edges"]


def test_only_model_and_provider_are_read_from_runtime_config() -> None:
    bundle = _example("github-issue-triage")
    text = json.dumps(to_hub_bundle(bundle, "github-issue-triage", **CARD, generated=GENERATED))
    for agent in bundle["bundled_agents"].values():
        for key, value in agent.get("runtime_config", {}).items():
            if key not in {"model_id", "provider"} and isinstance(value, str) and len(value) > 3:
                assert value not in text, key
    # the bash node's command is its prompt_template, exported as prompt.user (#1016 round 5)
    assert "gh issue list" in _node(json.loads(text), "fetch_issues")["prompt"]["user"]


def _node(page: dict[str, Any], node_id: str) -> dict[str, Any]:
    node: dict[str, Any] = next(n for n in page["graph"]["nodes"] if n["id"] == node_id)
    return node


def _one_agent_bundle(**agent: Any) -> dict[str, Any]:
    """A one-node bundle whose agent carries ``agent`` (on top of a minimal api-call agent)."""
    base = {
        "name": "Brief Drafter",
        "role": "custom",
        "runtime_id": "api-call",
        "runtime_config": {"provider": "anthropic", "model_id": "claude-sonnet-4-5"},
        "prompt_template": "Summarise {{ issue_title }}.",
        "input_schema": ["issue_title"],
        "output_schema": ["summary"],
    }
    return {
        "schema_version": 1,
        "bundled_agents": {"a1": base | agent},
        "pipeline": {
            "name": "One node",
            "description": "One agent.",
            "entry_point": "write",
            "nodes": [{"id": "write", "agent_id": "a1", "position": {"x": 0, "y": 0}}],
            "edges": [],
        },
    }


def _export(bundle: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return to_hub_bundle(bundle, "one-node", **CARD, generated=GENERATED, **kw)


def test_a_planted_client_name_in_a_prompt_becomes_client() -> None:
    bundle = _one_agent_bundle(prompt_template="Summarise the issue for Acme Logistics")
    prompt = _node(_export(bundle), "write")["prompt"]
    assert prompt == {"user": "Summarise the issue for client"}


def test_allowlisted_names_and_jinja_survive_in_a_prompt() -> None:
    text = "Read the GitHub issue {{ issue_title }} and ask Claude for JSON."
    bundle = _one_agent_bundle(prompt_template=text)
    assert _node(_export(bundle), "write")["prompt"]["user"] == text


@pytest.mark.parametrize(
    "leak",
    ["See https://example.com/a first.", "Mail ops@example.com.", "Read /home/someone/notes."],
)
def test_a_prompt_with_a_url_email_or_path_refuses_the_export(leak: str) -> None:
    with pytest.raises(Unsafe, match=r"prompt\.user"):
        _export(_one_agent_bundle(prompt_template=leak))


def test_an_unclear_prompt_refuses_the_export_naming_the_field() -> None:
    with pytest.raises(Unsafe, match=r"prompt\.user: client-name scrubber") as exc:
        _export(_one_agent_bundle(prompt_template="Hello {{ Initech"))
    assert "Initech" not in str(exc.value)


def test_unknown_runtime_config_keys_never_reach_the_page() -> None:
    config = {
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5",
        "token_env": "SECRETISH_TOKEN_NAME",
        "shell": "cat secret-notes-file",
        "max_tokens": 777123,
    }
    page = _export(_one_agent_bundle(runtime_config=config))
    text = json.dumps(page)
    assert "SECRETISH_TOKEN_NAME" not in text and "secret-notes-file" not in text
    assert "777123" not in text
    agent = _node(page, "write")["agent"]
    assert agent == {"name": "Brief Drafter", "model": "claude-sonnet-4-5", "provider": "anthropic"}


def test_the_agent_system_prompt_and_tools_are_exported() -> None:
    config = {
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5",
        "system_prompt": "You review code for Initech.",
        "tools": [
            {"runtime_id": "github", "config": {"repo": "o/r"}},
            {"runtime_id": "bash", "name": "run_tests"},
        ],
    }
    node = _node(_export(_one_agent_bundle(runtime_id="agent", runtime_config=config)), "write")
    assert node["prompt"] == {
        "system": "You review code for client.",
        "user": "Summarise {{ issue_title }}.",
    }
    assert node["tools"] == ["github", "run_tests"]
    assert "o/r" not in json.dumps(node)


def test_a_github_op_is_its_one_tool() -> None:
    slug = "github-read-issue-comment"
    page = to_hub_bundle(_example(slug), slug, **CARD, generated=GENERATED)
    tools = {n["id"]: n.get("tools") for n in page["graph"]["nodes"]}
    # round 6: the op with its parameters as the signature
    assert tools["read_issue"] == [{"name": "read_issue", "signature": "read_issue(repo, issue)"}]
    assert tools["comment"] == [{"name": "comment", "signature": "comment(repo, issue, body)"}]


def test_new_keys_are_absent_when_the_bundle_does_not_hold_them() -> None:
    bundle = _one_agent_bundle(runtime_config={}, prompt_template="  ", output_schema=[])
    node = _node(_export(bundle), "write")
    assert node["agent"] == {"name": "Brief Drafter"}
    for key in ("prompt", "tools", "output_fields", "gate_rule", "retries", "max_loops"):
        assert key not in node, key


def test_new_keys_respect_their_limits() -> None:
    bundle = _one_agent_bundle(
        prompt_template="word " * 3000,
        output_schema=[
            {"key": f"f{i}", "type": "string", "description": "a long field " * 40}
            for i in range(60)
        ],
        runtime_id="agent",
        runtime_config={
            "tools": [{"runtime_id": "bash", "name": "t" * 120}]
            + [{"runtime_id": "bash", "name": f"tool_{i}"} for i in range(50)]
        },
    )
    bundle["pipeline"]["defaults"] = {"max_attempts": 99, "approval_required_nodes": ["write"]}
    bundle["pipeline"]["nodes"][0]["retry"] = {"max_retries": 80, "retry_to": "write"}
    page = _export(bundle)
    assert_hub_valid(page)
    node = _node(page, "write")
    assert len(node["prompt"]["user"]) <= 8000
    assert len(node["output_fields"]) == 40
    assert node["tools"] == [f"tool_{i}" for i in range(30)]  # an over-long name is dropped
    assert node["retries"] == 20 and node["max_loops"] == 50
    assert node["type"] == "gate" and 0 < len(node["gate_rule"]) <= 300


def test_retries_come_from_the_pipeline_max_attempts_and_loops_from_the_node_retry() -> None:
    page = to_hub_bundle(
        _example("tester-coder-retry"), "tester-coder-retry", **CARD, generated=GENERATED
    )
    by_id = {n["id"]: n for n in page["graph"]["nodes"]}
    assert all(n["retries"] == 0 for n in by_id.values())  # defaults.max_attempts == 1
    looped = [n for n in by_id.values() if "max_loops" in n]
    assert [n["max_loops"] for n in looped] == [3]


def test_gate_rule_is_written_on_gate_nodes_only() -> None:
    page = to_hub_bundle(
        _example("project-bootstrap"), "project-bootstrap", **CARD, generated=GENERATED
    )
    for node in page["graph"]["nodes"]:
        assert ("gate_rule" in node) == (node["type"] == "gate"), node["id"]


def test_output_fields_keep_names_types_and_descriptions() -> None:
    schema = ["summary", {"key": "score", "type": "number", "description": "Rank for Acme."}]
    node = _node(_export(_one_agent_bundle(output_schema=schema)), "write")
    assert node["output_fields"] == [
        {"name": "summary"},
        {"name": "score", "type": "number", "description": "Rank for client."},
    ]


def test_public_names_extend_the_allowlist() -> None:
    from dap_cli.hub_names import BUILTIN

    bundle = _one_agent_bundle(prompt_template="Summarise the issue for Acme Logistics")
    public = BUILTIN.extend({"partners": ["Acme Logistics"]})
    prompt = _node(_export(bundle, public_names=public), "write")["prompt"]
    assert prompt == {"user": "Summarise the issue for Acme Logistics"}


@pytest.mark.parametrize("slug", FIRST_HUB_SLUGS)
def test_first_hub_bundles_export_the_detail_keys(slug: str) -> None:
    page = to_hub_bundle(_example(slug), slug, **CARD, generated=GENERATED)
    assert_hub_valid(page)
    assert scan(page) == []
    for node in page["graph"]["nodes"]:
        assert {"agent", "prompt", "output_fields", "retries"} <= set(node), node["id"]
    assert "client" not in json.dumps(page)  # nothing in the examples is a client


def test_issue_references_are_dropped_from_text() -> None:
    page = to_hub_bundle(
        _example("github-read-issue-comment"),
        "github-read-issue-comment",
        **CARD,
        generated=GENERATED,
    )
    assert "#919" not in page["summary"]


def test_steps_walk_the_nodes_and_explain_import_and_run() -> None:
    page = to_hub_bundle(
        _example("team-collaboration"), "team-collaboration", **CARD, generated=GENERATED
    )
    titles = [s["title"] for s in page["steps"]]
    assert [t.split(": ", 1)[1] for t in titles] == [
        "Scope Extractor",
        "Brief Drafter",
        "GitHub Issue Formatter",
    ]
    # round 6: importing into DAP and running it there moved to build.dap
    dap = page["build"]["dap"]
    assert "team-collaboration.pipeline-bundle.json" in dap and "Import JSON" in dap


def test_needs_cover_every_runtime_used() -> None:
    page = to_hub_bundle(
        _example("github-issue-triage"), "github-issue-triage", **CARD, generated=GENERATED
    )
    needs = " ".join(page["needs"]).lower()
    assert "dap" in needs
    assert "shell" in needs  # bash
    assert "llm provider" in needs  # api-call


def test_approval_gates_and_budget_become_pitfalls() -> None:
    page = to_hub_bundle(
        _example("project-bootstrap"), "project-bootstrap", **CARD, generated=GENERATED
    )
    pitfalls = " ".join(page["pitfalls"])
    assert "gate_params" in pitfalls
    assert "25" in pitfalls  # budget_limit_usd


def test_bad_slug_is_refused() -> None:
    with pytest.raises(ValueError):
        to_hub_bundle(_example("team-collaboration"), "g300-truco", **CARD, generated=GENERATED)
    with pytest.raises(ValueError):
        to_hub_bundle(_example("team-collaboration"), "Bad Slug", **CARD, generated=GENERATED)


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


@pytest.mark.parametrize(
    "leak",
    [
        "api_key=os.environ[X]",
        "client(API_KEY = os.environ[KEY_ENV[provider]])",
        "token: abcdefgh12",
        "password=hunter2hunter2",
        "passwd:s3cr3t-value",
        "Secret = some-long-value",
    ],
)
def test_scan_flags_a_credential_assignment(leak: str) -> None:
    hits = scan({"build": {"runner": leak}})
    assert hits == ["build.runner: credential assignment"]
    assert re.split(r"[=:]", leak, maxsplit=1)[1].strip() not in " ".join(hits)


@pytest.mark.parametrize(
    "fine",
    ["key = os.environ[X]", "token=short", "max_tokens=MAX_TOKENS", "a secret: is kept"],
)
def test_scan_allows_no_credential_assignment(fine: str) -> None:
    assert scan({"build": {"runner": fine}}) == []


def test_generated_runner_has_no_credential_assignment() -> None:
    from dap_cli import hub_runner

    source = Path(hub_runner.__file__).read_text(encoding="utf-8")
    assert not GUARD["credential assignment"].search(source)


def test_export_refuses_a_leaky_bundle() -> None:
    bundle = copy.deepcopy(_example("team-collaboration"))
    bundle["pipeline"]["description"] = "Ask ops at 10.1.2.3 first."
    with pytest.raises(Unsafe):
        to_hub_bundle(bundle, "team-collaboration", **CARD, generated=GENERATED)


# ---------------------------------------------------------------------------
# Writing and the CLI
# ---------------------------------------------------------------------------


def test_write_hub_bundle_layout(tmp_path: Path) -> None:
    src = REPO / "examples" / "pipelines" / "team-collaboration.pipeline-bundle.json"
    out = write_hub_bundle(src, "team-collaboration", tmp_path, **CARD, generated=GENERATED)
    assert out == tmp_path / "team-collaboration" / "page" / "page.json"
    assert_hub_valid(_load(out))


def test_write_leaves_nothing_on_refusal(tmp_path: Path) -> None:
    bundle = _example("team-collaboration")
    bundle["pipeline"]["description"] = "see https://example.com"
    src = tmp_path / "leaky.pipeline-bundle.json"
    src.write_text(json.dumps(bundle))
    with pytest.raises(Unsafe):
        write_hub_bundle(src, "leaky", tmp_path / "out", **CARD, generated=GENERATED)
    assert not (tmp_path / "out").exists()


def _card_args(tagline: str = CARD["tagline5"], io: str = CARD["io"]) -> list[str]:
    return ["--tagline5", tagline, "--io", io]


def test_cli_export_writes_the_first_hub_bundles(tmp_path: Path) -> None:
    for slug in FIRST_HUB_SLUGS:
        tagline, io = FIRST_HUB_CARDS[slug]
        src = REPO / "examples" / "pipelines" / f"{slug}.pipeline-bundle.json"
        result = runner.invoke(
            app,
            [
                "pipeline",
                "export",
                str(src),
                "--hub",
                slug,
                "--tagline5",
                tagline,
                "--io",
                io,
                "--out",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        page = _load(tmp_path / slug / "page" / "page.json")
        assert_hub_valid(page)
        assert page["tagline5"] == tagline
        assert page["io_icons"] == dict(zip(("in", "out"), io.split(":"), strict=True))


def test_cli_export_refusal_exits_nonzero(tmp_path: Path) -> None:
    bundle = _example("team-collaboration")
    bundle["pipeline"]["description"] = "host 192.168.1.100"
    src = tmp_path / "leaky.pipeline-bundle.json"
    src.write_text(json.dumps(bundle))
    result = runner.invoke(
        app,
        [
            "pipeline",
            "export",
            str(src),
            "--hub",
            "leaky",
            *_card_args(),
            "--out",
            str(tmp_path / "o"),
        ],
    )
    assert result.exit_code == 2
    assert "192.168" not in result.output


def test_plain_removes_a_trailing_parenthesised_reference_whole() -> None:
    assert _plain("x through state (#919).") == "x through state."


def test_plain_removes_a_parenthesised_reference_list_whole() -> None:
    assert _plain("Reads issues (#1, #2) and comments.") == "Reads issues and comments."


def test_plain_still_removes_a_bare_reference() -> None:
    assert _plain("Built on #12 for good.") == "Built on for good."


# ---------------------------------------------------------------------------
# Hub card fields: tagline5, io_icons, node type and purpose
# ---------------------------------------------------------------------------


def _card(tagline5: str = CARD["tagline5"], io: str = CARD["io"]) -> dict[str, Any]:
    return to_hub_bundle(
        _example("team-collaboration"),
        "team-collaboration",
        tagline5=tagline5,
        io=io,
        generated=GENERATED,
    )


def test_card_fields_reach_the_page() -> None:
    page = _card("  Turn a problem into a brief ", "comment:report")
    assert page["tagline5"] == "Turn a problem into a brief"
    assert page["io_icons"] == {"in": "comment", "out": "report"}


@pytest.mark.parametrize(
    ("tagline", "rule"),
    [
        ("Too short", "3-6 words"),
        ("one two three four five six seven", "3-6 words"),
        (
            "Extraordinarily unbelievably lengthy wording surpassing sixtycharacters",
            "60 characters",
        ),
        ("Read an issue...", "ellipsis"),
        ("Read an issue…", "ellipsis"),
        ("Read an **issue** now", "markup"),
        ("Read <b>an</b> issue", "markup"),
        ("Read [an issue](x) now", "markup"),
        ("See https example.com today", "link"),
        ("Read the docs/guide now", "link"),
        ("Read an\nissue then comment", "one line"),
    ],
)
def test_bad_tagline5_is_refused(tagline: str, rule: str) -> None:
    with pytest.raises(ValueError, match=rule):
        _card(tagline5=tagline)


@pytest.mark.parametrize("io", ["issue", "issue:", ":comment", "issue:comment:post", "issue:tweet"])
def test_bad_io_is_refused(io: str) -> None:
    with pytest.raises(ValueError, match="bad --io"):
        _card(io=io)


def test_every_io_icon_in_the_vocabulary_is_accepted() -> None:
    for name in sorted(IO_VOCAB):
        assert _card(io=f"{name}:{name}")["io_icons"] == {"in": name, "out": name}


@pytest.mark.parametrize(
    "args",
    [
        ["--tagline5", "Too short", "--io", "issue:comment"],
        ["--tagline5", "Read an issue, then comment", "--io", "issue:tweet"],
        ["--io", "issue:comment"],
        ["--tagline5", "Read an issue, then comment"],
    ],
    ids=["bad-tagline", "bad-io", "no-tagline", "no-io"],
)
def test_cli_refuses_bad_or_missing_card_fields(tmp_path: Path, args: list[str]) -> None:
    src = REPO / "examples" / "pipelines" / "github-read-issue-comment.pipeline-bundle.json"
    result = runner.invoke(
        app,
        ["pipeline", "export", str(src), "--hub", "x-slug", *args, "--out", str(tmp_path / "o")],
    )
    assert result.exit_code == 2, result.output
    assert not (tmp_path / "o").exists()


def _types(name: str) -> dict[str, str]:
    page = to_hub_bundle(_example(name), name, **CARD, generated=GENERATED)
    return {n["id"]: n["type"] for n in page["graph"]["nodes"]}


def test_approval_gates_are_typed_gate() -> None:
    bundle = _example("project-bootstrap")
    gates = set(bundle["pipeline"]["defaults"]["approval_required_nodes"])
    types = _types("project-bootstrap")
    assert {nid for nid, t in types.items() if t == "gate"} == gates
    # The other nodes keep their (non-LLM) runtime id.
    assert {t for nid, t in types.items() if nid not in gates} == {"python-func"}


def test_llm_runtimes_are_typed_llm_and_the_rest_keep_their_runtime() -> None:
    bundle = _example("github-issue-triage")
    agents = bundle["bundled_agents"]
    runtime = {n["id"]: agents[n["agent_id"]]["runtime_id"] for n in bundle["pipeline"]["nodes"]}
    for nid, t in _types("github-issue-triage").items():
        assert t == ("llm" if runtime[nid] == "api-call" else runtime[nid])
    assert set(_types("team-collaboration").values()) == {"llm"}
    assert set(_types("github-read-issue-comment").values()) == {"github"}
    tester = _types("tester-coder-retry")
    assert "llm" in tester.values() and "bash" in tester.values()


def test_llm_typing_comes_from_the_runtime_not_the_title() -> None:
    bundle = copy.deepcopy(_example("github-read-issue-comment"))
    for agent in bundle["bundled_agents"].values():
        agent["name"] = "LLM model call"
    page = to_hub_bundle(bundle, "x-slug", **CARD, generated=GENERATED)
    assert {n["type"] for n in page["graph"]["nodes"]} == {"github"}


def test_needs_and_steps_still_name_the_runtime_behind_llm_and_gate() -> None:
    page = to_hub_bundle(
        _example("github-issue-triage"), "github-issue-triage", **CARD, generated=GENERATED
    )
    assert "llm provider" in " ".join(page["needs"]).lower()
    assert "`llm`" not in " ".join(s["body"] for s in page["steps"])
    assert "Runtime `api-call`" in " ".join(s["body"] for s in page["steps"])


def test_purpose_is_omitted_without_a_description() -> None:
    page = to_hub_bundle(
        _example("team-collaboration"), "team-collaboration", **CARD, generated=GENERATED
    )
    assert all("purpose" not in n for n in page["graph"]["nodes"])


def test_purpose_is_one_short_line_from_the_description() -> None:
    bundle = copy.deepcopy(_example("team-collaboration"))
    agents = bundle["bundled_agents"]
    first, second, third = list(agents)
    agents[first]["description"] = "Drafts the brief from the problem (#12).\nSecond line."
    agents[second]["description"] = "word " * 40
    agents[third]["description"] = "Reads the docs/guide.md file first"
    page = to_hub_bundle(bundle, "team-collaboration", **CARD, generated=GENERATED)
    by_agent = {n["agent_id"]: n["id"] for n in bundle["pipeline"]["nodes"] if n.get("agent_id")}
    nodes = {n["id"]: n for n in page["graph"]["nodes"]}
    assert nodes[by_agent[first]]["purpose"] == "Drafts the brief from the problem."
    long = nodes[by_agent[second]]["purpose"]
    assert len(long) <= 90 and "\n" not in long
    assert "purpose" not in nodes[by_agent[third]]  # a path never reaches the card


# ---------------------------------------------------------------------------
# Round 6: buildable from the page alone (per-node example/code/fields, page ``build``)
# ---------------------------------------------------------------------------


def _hub_examples(name: str) -> dict[str, Any]:
    return _load(REPO / "examples" / "pipelines" / f"{name}.hub-examples.json")


def _page6(slug: str, examples: dict[str, Any] | None = None) -> dict[str, Any]:
    tagline, io = FIRST_HUB_CARDS[slug]
    return to_hub_bundle(
        _example(slug),
        slug,
        tagline5=tagline,
        io=io,
        generated=GENERATED,
        examples=_hub_examples(slug) if examples is None else examples,
    )


@pytest.mark.parametrize("slug", FIRST_HUB_SLUGS)
def test_a_first_hub_page_holds_every_build_section(slug: str) -> None:
    page = _page6(slug)
    assert_hub_valid(page)
    assert scan(page) == []
    build = page["build"]
    assert {"requirements", "env", "runner", "steps", "dap", "example_source"} <= set(build)
    assert build["example_source"] == "synthetic"
    assert any("expected_output" in s for s in build["steps"])
    for node in page["graph"]["nodes"]:
        assert node["example"]["example_source"] == "synthetic", node["id"]
        assert node["example"] == {**_hub_examples(slug)[node["id"]]}, node["id"]
        assert node["failure_modes"], node["id"]
        if node["type"] != "llm":
            assert node["code"]["source"], node["id"]
        else:
            assert "code" not in node


@pytest.mark.parametrize("marker", [None, "real", "Synthetic", True])
def test_an_example_without_the_synthetic_marker_refuses(marker: Any) -> None:
    examples = copy.deepcopy(_hub_examples("team-collaboration"))
    if marker is None:
        del examples["draft_brief"]["example_source"]
    else:
        examples["draft_brief"]["example_source"] = marker
    with pytest.raises(ValueError, match="draft_brief") as info:
        _page6("team-collaboration", examples)
    assert "synthetic" in str(info.value)


def test_an_example_for_an_unknown_node_or_with_extra_keys_refuses() -> None:
    examples = copy.deepcopy(_hub_examples("team-collaboration"))
    examples["no_such_node"] = examples["draft_brief"]
    with pytest.raises(ValueError, match="no_such_node"):
        _page6("team-collaboration", examples)
    examples = copy.deepcopy(_hub_examples("team-collaboration"))
    examples["draft_brief"]["note"] = "x"
    with pytest.raises(ValueError, match="draft_brief"):
        _page6("team-collaboration", examples)


def test_an_example_over_the_size_limit_refuses() -> None:
    examples = copy.deepcopy(_hub_examples("team-collaboration"))
    examples["draft_brief"]["output"] = {"implementation_notes": "word " * 1000}
    with pytest.raises(ValueError, match=r"draft_brief.*4000"):
        _page6("team-collaboration", examples)


@pytest.mark.parametrize("domain", ["tracker.acme-corp.io", "api.realclient.com", "a.b.dev"])
def test_a_real_domain_in_an_example_refuses_naming_the_field(domain: str) -> None:
    examples = copy.deepcopy(_hub_examples("team-collaboration"))
    examples["extract_scope"]["input"]["description"] = f"Mirror the reports to {domain} daily."
    with pytest.raises(Unsafe) as info:
        _page6("team-collaboration", examples)
    assert "extract_scope" in str(info.value) and "domain" in str(info.value)
    assert domain not in str(info.value)


def test_example_domains_are_allowed() -> None:
    examples = copy.deepcopy(_hub_examples("team-collaboration"))
    text = "Send the file to reports.example.com, files.example.org and api.shop.test."
    examples["extract_scope"]["input"]["description"] = text
    page = _page6("team-collaboration", examples)
    assert _node(page, "extract_scope")["example"]["input"]["description"] == text


def test_a_client_name_in_an_example_is_scrubbed() -> None:
    examples = copy.deepcopy(_hub_examples("team-collaboration"))
    examples["extract_scope"]["input"]["description"] = "Readers at Acme Logistics want CSV."
    page = _page6("team-collaboration", examples)
    assert "Acme" not in json.dumps(page)


def test_without_examples_there_is_no_example_and_no_expected_output() -> None:
    page = _page6("team-collaboration", {})
    assert all("example" not in n for n in page["graph"]["nodes"])
    assert page["build"]["example_source"] == "synthetic"
    assert all("expected_output" not in s for s in page["build"]["steps"])
    assert_hub_valid(page)


def test_requirements_are_pinned_and_follow_the_runtimes() -> None:
    def reqs(slug: str) -> dict[str, str]:
        return {r["package"]: r["version"] for r in _page6(slug)["build"]["requirements"]}

    assert reqs("github-read-issue-comment") == {"Jinja2": "3.1.6", "PyGithub": "2.6.1"}
    assert reqs("team-collaboration") == {"Jinja2": "3.1.6", "openai": "3.57.0"}
    assert set(reqs("github-issue-triage")) == {"Jinja2", "openai"}


def test_env_holds_placeholders_only() -> None:
    env = _page6("github-issue-triage")["build"]["env"]
    assert [e["name"] for e in env] == ["GITHUB_TOKEN", "GLM_API_KEY", "MODEL_BASE_URL"]
    assert all(e["placeholder"].startswith("<") and e["placeholder"].endswith(">") for e in env)
    env = _page6("github-read-issue-comment")["build"]["env"]
    assert env == [
        {
            "name": "GITHUB_TOKEN",
            "placeholder": "<your GitHub token>",
            "description": env[0]["description"],
        }
    ]


def test_code_is_the_bundles_command_or_the_github_call() -> None:
    page = _page6("github-issue-triage")
    fetch = _node(page, "fetch_issues")["code"]
    assert fetch["language"] == "bash" and "gh issue list --state open" in fetch["source"]
    assert "<command>" not in fetch["source"]
    comment = _node(_page6("github-read-issue-comment"), "comment")["code"]
    assert "POST /repos/{repo}/issues/{issue}/comments" in comment["source"]
    # a python-func bundle carries only the callable path: no code
    cortex = to_hub_bundle(
        _example("cortex-github-issue"), "cortex-github-issue", **CARD, generated=GENERATED
    )
    assert all("code" not in n for n in cortex["graph"]["nodes"] if n["type"] == "python-func")


def test_input_fields_come_from_the_schema_or_the_template() -> None:
    team = _node(_page6("team-collaboration"), "draft_brief")["input_fields"]
    assert team == [{"name": "implementation_notes", "type": "str | None"}]
    read = _node(_page6("github-read-issue-comment"), "read_issue")["input_fields"]
    assert [f["name"] for f in read] == ["repo", "extensions.issue_number"]
    select = _node(_page6("github-issue-triage"), "select_issue")["input_fields"]
    assert {"name": "available_issues", "type": "list[dict[str, Any]]"} in select


def test_failure_modes_are_derived_from_the_settings() -> None:
    page = _page6("github-issue-triage")
    fetch = " ".join(_node(page, "fetch_issues")["failure_modes"])
    assert "30 seconds" in fetch and "non-zero" in fetch and "<output>" in fetch
    select = " ".join(_node(page, "select_issue")["failure_modes"])
    assert "0.2 USD" in select and "60 seconds" in select
    comment = " ".join(_node(_page6("github-read-issue-comment"), "comment")["failure_modes"])
    assert "GitHub" in comment


def test_the_runner_writes_no_endpoint_or_shell_path() -> None:
    for slug in FIRST_HUB_SLUGS:
        source = _page6(slug)["build"]["runner"]["source"]
        assert "://" not in source and "/bin/bash" not in source
        assert "api.github.com" not in source
        compile(source, "run_pipeline.py", "exec")


def test_the_runner_parts_follow_the_nodes() -> None:
    read = _page6("github-read-issue-comment")["build"]["runner"]["source"]
    team = _page6("team-collaboration")["build"]["runner"]["source"]
    assert "def github_call" in read and "def run_shell" not in read and "import openai" not in read
    assert "def github_call" not in team and "import openai" in team


def test_a_runner_over_the_limit_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    from dap_cli import hub_runner

    monkeypatch.setattr(hub_runner, "MAX_RUNNER", 100)
    build = _page6("team-collaboration")["build"]
    assert set(build) == {"dap", "example_source"}
    assert "Import JSON" in build["dap"]


def _load_runner(page: dict[str, Any], tmp_path: Path) -> Any:
    import importlib.util

    path = tmp_path / "run_pipeline.py"
    path.write_text(page["build"]["runner"]["source"])
    spec = importlib.util.spec_from_file_location(f"hub_runner_{page['slug']}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step(page: dict[str, Any], prefix: str) -> dict[str, str]:
    step: dict[str, str] = next(s for s in page["build"]["steps"] if s["title"].startswith(prefix))
    return step


@pytest.mark.parametrize("slug", FIRST_HUB_SLUGS)
def test_the_runner_runs_end_to_end_on_the_example_input(
    slug: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = _page6(slug)
    examples = _hub_examples(slug)
    module = _load_runner(page, tmp_path)
    # The example input as the page writes it (heredoc of the "Write the example input" step).
    command = _step(page, "Write the example input")["command"]
    (tmp_path / "input.json").write_text(command.split("<<'EOF'\n", 1)[1].rsplit("\nEOF", 1)[0])
    replies = iter(
        examples[n["id"]]["output"] for n in page["graph"]["nodes"] if n["type"] == "llm"
    )
    prompts: list[str] = []

    def fake_model(provider: str, model: str, system: str | None, prompt: str) -> str:
        prompts.append(prompt)
        return f"Done.\n<output>{json.dumps(next(replies))}</output>"

    calls: list[tuple[str, str, dict[str, str]]] = []

    def fake_github(op: str, repo: str, params: dict[str, str]) -> Any:
        calls.append((op, repo, params))
        key = {"read_issue": "github_issue", "comment": "github_comment"}[op]
        return examples[op]["output"][key]

    monkeypatch.setattr(module, "call_model", fake_model)
    monkeypatch.setattr(module, "github_call", fake_github, raising=False)
    # The bash nodes' tools: gh prints the example issues, git does nothing.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    issues = json.dumps(examples.get("fetch_issues", {}).get("output", {}).get("available_issues"))
    (bin_dir / "gh").write_text(f"#!/usr/bin/env bash\ncat <<'JSON'\n{issues}\nJSON\n")
    (bin_dir / "git").write_text("#!/usr/bin/env bash\nexit 0\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()
    module.main(["run_pipeline.py", "input.json"])
    out = capsys.readouterr().out
    assert json.loads(out) == json.loads(_step(page, "Run it")["expected_output"])
    if slug == "github-read-issue-comment":
        assert calls[0] == ("read_issue", "client-a/example-app", {"issue": "42"})
        assert calls[1][0] == "comment" and "Add a dark mode toggle" in calls[1][2]["body"]
    if slug == "team-collaboration":
        assert "saved reports" in prompts[0]
    if slug == "github-issue-triage":
        assert "Fix the login redirect" in prompts[0] and len(prompts) == 2


def test_the_cli_reads_an_examples_file(tmp_path: Path) -> None:
    slug = "team-collaboration"
    tagline, io = FIRST_HUB_CARDS[slug]
    src = REPO / "examples" / "pipelines" / f"{slug}.pipeline-bundle.json"
    examples = REPO / "examples" / "pipelines" / f"{slug}.hub-examples.json"
    args = ["pipeline", "export", str(src), "--hub", slug, "--tagline5", tagline, "--io", io]
    result = runner.invoke(app, [*args, "--examples", str(examples), "--out", str(tmp_path / "o")])
    assert result.exit_code == 0, result.output
    page = json.loads((tmp_path / "o" / slug / "page" / "page.json").read_text())
    assert _node(page, "draft_brief")["example"]["example_source"] == "synthetic"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"draft_brief": {"input": {}, "output": {}}}))
    result = runner.invoke(app, [*args, "--examples", str(bad), "--out", str(tmp_path / "p")])
    assert result.exit_code == 2 and "draft_brief" in result.output
    assert not (tmp_path / "p").exists()
