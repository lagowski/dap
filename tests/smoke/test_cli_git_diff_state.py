"""Workspace diff into state after a CLI code run (#933, part of #811).

A code runtime run with a ``branch`` reports three top-level ``structured`` keys, which
``_merge_structured_into_state`` copies into the matching ``PipelineState`` fields:

- ``changed_files``: ``git diff --name-only <start>..<branch tip>`` (this run only),
- ``commits_since_base``: ``git log <base>..<branch tip>`` as ``{sha, subject, author}``,
- ``diff_stats``: ``{additions, deletions, files}`` from ``git diff --shortstat``.

The runtime half uses the real-git fixtures of test_cli_git_workspace.py (nothing
mocked). The engine half runs the real codex adapter against the same repos inside a
pipeline, so a downstream prompt and an edge condition see what the run changed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import AgentORM, AgentVersionORM
from dap_runtimes import RuntimeRegistry
from dap_types import PipelineState
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.smoke._git_repo import _cli, _commit_body, _git, _run
from tests.smoke.test_pipeline_runner import StubAdapter, _run_pipeline, _seed_pipeline

pytestmark = pytest.mark.usefixtures("openai_key")

DIFF_KEYS = ("changed_files", "commits_since_base", "diff_stats")

# Two commits: a new one-line file, then README "seed" -> "changed".
TWO_FILES = (
    "echo one > a.txt\n"
    "git add a.txt\n"
    'git commit -q -m "add a"\n'
    "echo changed > README\n"
    'git commit -q -am "edit readme"\n'
)


def _structured(result: Any) -> dict[str, Any]:
    assert result.success, result.errors
    assert result.structured is not None
    structured: dict[str, Any] = result.structured
    return structured


# --------------------------------------------------------------------------- runtime


async def test_a_run_that_commits_two_files_reports_all_three_fields(
    git_repo: dict[str, Path],
) -> None:
    work = git_repo["work"]
    result = await _run(work, _cli(git_repo["tmp"], TWO_FILES), branch="feat/two")
    structured = _structured(result)

    assert structured["changed_files"] == ["README", "a.txt"]
    assert structured["diff_stats"] == {"additions": 2, "deletions": 1, "files": 2}
    shas = _git(work, "rev-list", "refs/remotes/origin/develop..refs/heads/feat/two").split()
    assert structured["commits_since_base"] == [
        {"sha": shas[0], "subject": "edit readme", "author": "Coder"},
        {"sha": shas[1], "subject": "add a", "author": "Coder"},
    ]


async def test_a_noop_run_reports_empty_fields(git_repo: dict[str, Path]) -> None:
    result = await _run(git_repo["work"], _cli(git_repo["tmp"], ""), branch="feat/noop")
    structured = _structured(result)

    assert structured["changed_files"] == []
    assert structured["commits_since_base"] == []
    assert structured["diff_stats"] == {"additions": 0, "deletions": 0, "files": 0}


async def test_changed_files_is_this_run_and_commits_reach_back_to_base(
    git_repo: dict[str, Path],
) -> None:
    work, tmp = git_repo["work"], git_repo["tmp"]
    first = await _run(work, _cli(tmp, _commit_body("first.txt"), "first-cli"), branch="feat/again")
    assert first.success, first.errors

    second = await _run(
        work, _cli(tmp, _commit_body("second.txt"), "second-cli"), branch="feat/again"
    )
    structured = _structured(second)

    assert structured["changed_files"] == ["second.txt"]
    assert structured["diff_stats"]["files"] == 1
    assert [c["subject"] for c in structured["commits_since_base"]] == [
        "cli: second.txt",
        "cli: first.txt",
    ]


async def test_fields_read_the_branch_not_head(git_repo: dict[str, Path]) -> None:
    # The CLI commits, then ends its turn on develop (cortex #815).
    body = _commit_body("work.txt") + "git checkout -q develop\n"
    result = await _run(git_repo["work"], _cli(git_repo["tmp"], body), branch="feat/wander")
    structured = _structured(result)

    assert structured["changed_files"] == ["work.txt"]
    assert len(structured["commits_since_base"]) == 1


async def test_pushed_run_reports_the_fields_too(git_repo: dict[str, Path]) -> None:
    result = await _run(
        git_repo["work"], _cli(git_repo["tmp"], _commit_body()), branch="feat/pushed", push=True
    )
    structured = _structured(result)

    assert structured["git"]["pushed"] is True
    assert structured["changed_files"] == ["change.txt"]


async def test_a_run_without_branch_reports_no_fields(git_repo: dict[str, Path]) -> None:
    # A `workspace` alone selects the directory but does no git work.
    result = await _run(
        git_repo["tmp"], _cli(git_repo["tmp"], _commit_body()), workspace=str(git_repo["work"])
    )
    structured = _structured(result)

    assert not set(DIFF_KEYS) & set(structured)


async def test_a_run_without_git_config_reports_no_fields(git_repo: dict[str, Path]) -> None:
    result = await _run(git_repo["work"], _cli(git_repo["tmp"], _commit_body()))
    structured = _structured(result)

    assert not set(DIFF_KEYS) & set(structured)


def test_old_snapshots_without_the_fields_still_load() -> None:
    state = PipelineState.model_validate({"run_id": "r", "repo": "x", "branch": "main"})

    assert state.changed_files == []
    assert state.commits_since_base == []
    assert state.diff_stats == {}


# --------------------------------------------------------------------------- engine


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[tuple[sessionmaker[Session], RuntimeRegistry]]:
    config = EngineConfig(db_path=str(tmp_path / "state.db"))
    app = create_app(config)
    with TestClient(app):
        yield app.state.session_factory, app.state.runtime_registry


def _seed(
    session: Session, agent_id: str, runtime_id: str, config: dict[str, Any], tpl: str
) -> str:
    now = datetime.now(UTC)
    session.add(
        AgentORM(
            id=agent_id,
            name=agent_id,
            role="task_selector",
            current_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        AgentVersionORM(
            id=f"{agent_id}-v1",
            agent_id=agent_id,
            version=1,
            name=agent_id,
            runtime_id=runtime_id,
            runtime_config=config,
            prompt_template=tpl,
            input_schema=[],
            output_schema=[],
            constraints=[],
            budget_limit_usd=None,
            timeout_ms=30_000,
            created_at=now,
        )
    )
    session.commit()
    return agent_id


async def _pipeline(
    engine: tuple[sessionmaker[Session], RuntimeRegistry],
    coder_config: dict[str, Any],
) -> tuple[PipelineState, StubAdapter]:
    """coder → (diff_stats.files > 0 → reviewer, else → END); reviewer → END."""
    factory, registry = engine
    reviewer = StubAdapter(adapter_id="reviewer-stub")
    registry.register(reviewer)
    with factory() as session:
        coder = _seed(
            session,
            "coder",
            "codex",
            coder_config,
            "<agent_prompt><role>code</role></agent_prompt>",
        )
        review = _seed(
            session,
            "reviewer",
            "reviewer-stub",
            {},
            '<agent_prompt><role>{{ changed_files | join(",") }}</role></agent_prompt>',
        )
        pipeline, version = _seed_pipeline(
            session,
            nodes=[
                {"id": "coder", "agent_id": coder, "position": {"x": 0, "y": 0}},
                {"id": "reviewer", "agent_id": review, "position": {"x": 100, "y": 0}},
            ],
            edges=[
                {
                    "id": "changed",
                    "source": "coder",
                    "target": "reviewer",
                    "condition": {
                        "type": "comparison",
                        "field": "diff_stats.files",
                        "operator": ">",
                        "value": 0,
                    },
                },
                {"id": "unchanged", "source": "coder", "target": "__end__"},
                {"id": "done", "source": "reviewer", "target": "__end__"},
            ],
            entry_point="coder",
        )
    state = await _run_pipeline(factory, registry, pipeline, version)
    return state, reviewer


def _codex(g: dict[str, Path], body: str, **git: Any) -> dict[str, Any]:
    return {
        "model_id": "gpt-5-codex",
        "binary_path": _cli(g["tmp"], body),
        "workspace": str(g["work"]),
        **git,
    }


async def test_a_downstream_prompt_and_edge_see_what_the_run_changed(
    engine: tuple[sessionmaker[Session], RuntimeRegistry], git_repo: dict[str, Path]
) -> None:
    state, reviewer = await _pipeline(engine, _codex(git_repo, TWO_FILES, branch="feat/eng"))

    assert state.final_status != "failed", state.verification_reason
    assert state.changed_files == ["README", "a.txt"]
    assert state.diff_stats == {"additions": 2, "deletions": 1, "files": 2}
    assert [c["subject"] for c in state.commits_since_base] == ["edit readme", "add a"]
    assert len(reviewer.calls) == 1
    assert "<role>README,a.txt</role>" in reviewer.calls[0].prompt_xml


async def test_a_noop_run_routes_past_the_downstream_node(
    engine: tuple[sessionmaker[Session], RuntimeRegistry], git_repo: dict[str, Path]
) -> None:
    state, reviewer = await _pipeline(engine, _codex(git_repo, "", branch="feat/eng-noop"))

    assert state.final_status != "failed", state.verification_reason
    assert state.diff_stats == {"additions": 0, "deletions": 0, "files": 0}
    assert reviewer.calls == []


async def test_a_coder_without_branch_leaves_the_fields_untouched(
    engine: tuple[sessionmaker[Session], RuntimeRegistry], git_repo: dict[str, Path]
) -> None:
    state, reviewer = await _pipeline(engine, _codex(git_repo, _commit_body()))

    assert state.final_status != "failed", state.verification_reason
    assert state.changed_files == []
    assert state.commits_since_base == []
    assert state.diff_stats == {}
    assert reviewer.calls == []


async def test_role_output_parsing_still_works_alongside_the_fields(
    engine: tuple[sessionmaker[Session], RuntimeRegistry], git_repo: dict[str, Path]
) -> None:
    # The CLI reports a task_selector role output as well as committing.
    payload = git_repo["tmp"] / "payload.json"
    payload.write_text(
        json.dumps({"output_text": '<output>{"selected_issue_ids": [7, 9]}</output>', "usage": {}})
    )
    body = _commit_body() + f"cat {payload}\nexit 0\n"
    state, _ = await _pipeline(engine, _codex(git_repo, body, branch="feat/eng-role"))

    assert state.final_status != "failed", state.verification_reason
    assert state.selected_issue_ids == [7, 9]
    assert state.changed_files == ["change.txt"]
