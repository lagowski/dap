"""Tests for the ``code-review-council`` CLI and the ``review/`` composite action.

No network: the provider is a fake injected through ``provider_factory``,
and the ``--base/--head`` path runs ``git diff`` against a throwaway repo.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, TypeVar

import pytest
import yaml  # type: ignore[import-untyped]
from code_review_council import AgentReport, Finding
from code_review_council.cli import EXIT_FINDINGS, EXIT_USAGE, main
from code_review_council.providers.base import BaseProvider
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

PKG_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PKG_ROOT.parents[1]
ACTION = REPO_ROOT / "review" / "action.yml"

DIFF = """\
diff --git a/app/auth.py b/app/auth.py
--- a/app/auth.py
+++ b/app/auth.py
@@ -1,2 +1,3 @@
 def login(user, pw):
+    query = f"SELECT * FROM users WHERE name = '{user}'"
     return check(user, pw)
"""


class FakeProvider:
    """Canned reports keyed by agent name; records how it was built."""

    name = "fake-model"

    def __init__(self, responses: dict[str, AgentReport]) -> None:
        self._responses = responses
        self.calls: list[str] = []
        self.contents: list[str] = []

    def run_structured(
        self,
        *,
        system_instruction: str,
        user_content: str,
        response_schema: type[T],
    ) -> T:
        self.contents.append(user_content)
        for agent, report in self._responses.items():
            if f"**{agent}** reviewer" in system_instruction:
                self.calls.append(agent)
                return response_schema.model_validate(report.model_dump())
        return response_schema.model_validate(AgentReport(summary="clean").model_dump())


def _finding(severity: str, confidence: str = "HIGH", issue: str = "SQL injection") -> Finding:
    return Finding(
        severity=severity,
        confidence=confidence,
        file="app/auth.py",
        line=2,
        issue=issue,
        suggestion="Use a parameterised query.",
        evidence="query = f\"SELECT * FROM users WHERE name = '{user}'\"",
    )


def _factory(responses: dict[str, AgentReport], seen: list[dict[str, Any]] | None = None) -> Any:
    def build(provider: str, api_key: str, model: str | None) -> BaseProvider:
        if seen is not None:
            seen.append({"provider": provider, "api_key": api_key, "model": model})
        return FakeProvider(responses)

    return build


@pytest.fixture
def diff_file(tmp_path: Path) -> Path:
    path = tmp_path / "some.diff"
    path.write_text(DIFF)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_prints_arbitrated_findings_as_json_and_markdown(
    diff_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    responses = {
        "Security": AgentReport(
            summary="one injection",
            findings=[
                _finding("HIGH"),
                # Dropped by the arbiter: low confidence.
                _finding("MEDIUM", confidence="LOW", issue="What if the user is an admin?"),
            ],
        ),
        # Same file + line + severity as Security's: the arbiter dedups it.
        "Correctness": AgentReport(summary="same bug", findings=[_finding("HIGH")]),
    }
    seen: list[dict[str, Any]] = []

    rc = main(
        ["--diff", str(diff_file), "--provider", "gemini"],
        provider_factory=_factory(responses, seen),
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert seen == [{"provider": "gemini", "api_key": "test-key-not-real", "model": None}]
    json_part, _, md_part = out.partition("\n\n## Code review council")
    data = json.loads(json_part)
    assert data["verdict"] == "request_changes"
    assert len(data["findings"]) == 1
    assert data["findings"][0]["severity"] == "HIGH"
    assert "What if the user is an admin" not in out
    assert md_part, "markdown section missing"
    assert "SQL injection" in md_part
    assert "app/auth.py:2" in md_part
    assert "test-key-not-real" not in out


def test_missing_api_key_is_a_clear_error_with_exit_2(
    diff_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    rc = main(["--diff", str(diff_file), "--provider", "gemini"], provider_factory=_factory({}))

    captured = capsys.readouterr()
    assert rc == EXIT_USAGE == 2
    assert "GEMINI_API_KEY" in captured.err
    assert "not set" in captured.err
    assert captured.out == ""


def test_openrouter_reads_its_own_env_var_and_model(
    diff_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    seen: list[dict[str, Any]] = []

    rc = main(
        [
            "--diff",
            str(diff_file),
            "--provider",
            "openrouter",
            "--model",
            "some/model",
            "--format",
            "json",
        ],
        provider_factory=_factory({}, seen),
    )

    assert rc == 0
    assert seen == [{"provider": "openrouter", "api_key": "or-key", "model": "some/model"}]
    assert json.loads(capsys.readouterr().out)["verdict"] == "comment"


def test_api_key_env_overrides_the_variable_name(
    diff_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("MY_KEY", "k")
    rc = main(
        ["--diff", str(diff_file), "--api-key-env", "MY_KEY", "--format", "json"],
        provider_factory=_factory({}),
    )
    assert rc == 0
    capsys.readouterr()


def test_reads_diff_from_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(sys, "stdin", io.StringIO(DIFF))
    responses = {"Security": AgentReport(summary="x", findings=[_finding("MEDIUM")])}

    rc = main(["--diff", "-", "--format", "json"], provider_factory=_factory(responses))

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["findings"][0]["severity"] == "MEDIUM"


def test_reads_diff_from_git_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / "auth.py").write_text("def login():\n    pass\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    (repo / "auth.py").write_text("def login():\n    return eval(input())\n")
    git("commit", "-q", "-am", "head")
    head = git("rev-parse", "HEAD")

    monkeypatch.chdir(repo)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    provider = FakeProvider({})

    def build(provider_name: str, api_key: str, model: str | None) -> BaseProvider:
        return provider

    rc = main(["--base", base, "--head", head, "--format", "json"], provider_factory=build)

    assert rc == 0
    assert provider.contents, "no agent ran on the git diff"
    assert all("eval(input())" in c for c in provider.contents)
    capsys.readouterr()


def test_base_without_head_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    rc = main(["--base", "main"], provider_factory=_factory({}))
    assert rc == EXIT_USAGE
    assert "--head" in capsys.readouterr().err


def test_fail_on_high_exits_non_zero_when_a_high_survives(
    diff_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    high = {"Security": AgentReport(summary="x", findings=[_finding("HIGH")])}
    medium = {"Security": AgentReport(summary="x", findings=[_finding("MEDIUM")])}
    args = ["--diff", str(diff_file), "--format", "json", "--fail-on"]

    assert main([*args, "high"], provider_factory=_factory(high)) == EXIT_FINDINGS
    assert main([*args, "none"], provider_factory=_factory(high)) == 0
    assert main([*args, "high"], provider_factory=_factory(medium)) == 0
    # A HIGH the arbiter drops (low confidence) does not fail the run.
    dropped = {"Security": AgentReport(summary="x", findings=[_finding("HIGH", confidence="LOW")])}
    assert main([*args, "high"], provider_factory=_factory(dropped)) == 0
    capsys.readouterr()


def test_writes_output_files_and_reads_context_file(
    diff_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    context = tmp_path / "review-context.toml"
    context.write_text(
        'stack = ["Python 3.13", "FastAPI"]\n'
        'in_scope = ["auth"]\n'
        'out_of_scope = ["i18n"]\n'
        'notes = "Internal tool."\n'
    )
    json_out = tmp_path / "out" / "review.json"
    md_out = tmp_path / "out" / "review.md"
    seen_prompts: list[str] = []

    class Recording(FakeProvider):
        def run_structured(
            self, *, system_instruction: str, user_content: str, response_schema: type[T]
        ) -> T:
            seen_prompts.append(system_instruction)
            return super().run_structured(
                system_instruction=system_instruction,
                user_content=user_content,
                response_schema=response_schema,
            )

    def build(provider: str, api_key: str, model: str | None) -> BaseProvider:
        return Recording({})

    rc = main(
        [
            "--diff",
            str(diff_file),
            "--context-file",
            str(context),
            "--json-out",
            str(json_out),
            "--markdown-out",
            str(md_out),
            "--format",
            "none",
        ],
        provider_factory=build,
    )

    assert rc == 0
    assert capsys.readouterr().out == ""
    assert json.loads(json_out.read_text())["verdict"] == "comment"
    assert md_out.read_text().startswith("## Code review council")
    assert seen_prompts and all("FastAPI" in p for p in seen_prompts)
    assert all("i18n" in p for p in seen_prompts)


def test_context_file_as_json_and_plain_text(tmp_path: Path) -> None:
    from code_review_council.cli import load_context

    js = tmp_path / "ctx.json"
    js.write_text(json.dumps({"stack": ["Go"], "out_of_scope": ["docs"]}))
    assert load_context(js).stack == ["Go"]
    txt = tmp_path / "ctx.md"
    txt.write_text("A small Go service; ignore docs.")
    assert load_context(txt).notes == "A small Go service; ignore docs."


def test_bad_context_file_is_a_usage_error(
    diff_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    bad = tmp_path / "ctx.toml"
    bad.write_text("stack = [unterminated\n")
    rc = main(["--diff", str(diff_file), "--context-file", str(bad)], provider_factory=_factory({}))
    assert rc == EXIT_USAGE
    assert "context file" in capsys.readouterr().err


def test_python_dash_m_entry_point_exists() -> None:
    """``python -m code_review_council --help`` works in a fresh interpreter."""
    proc = subprocess.run(
        [sys.executable, "-m", "code_review_council", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--provider" in proc.stdout
    assert "--fail-on" in proc.stdout


def test_console_script_is_declared() -> None:
    meta = tomllib.loads((PKG_ROOT / "pyproject.toml").read_text())
    assert meta["project"]["scripts"]["code-review-council"] == "code_review_council.cli:run"


# ---------------------------------------------------------------------------
# review/action.yml
# ---------------------------------------------------------------------------


def _action() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(ACTION.read_text())
    assert isinstance(data, dict)
    return data


def test_action_is_a_composite_action_with_documented_inputs() -> None:
    action = _action()
    assert action["name"]
    assert action["description"]
    assert action["runs"]["using"] == "composite"
    inputs = action["inputs"]
    for name in ("provider", "model", "api-key", "context-file", "fail-on"):
        assert name in inputs, name
        assert inputs[name]["description"]
    assert inputs["api-key"]["required"] is True
    assert inputs["fail-on"]["default"] == "none"
    steps = action["runs"]["steps"]
    assert steps
    for step in steps:
        assert ("run" in step) != ("uses" in step), step
        if "run" in step:
            # A composite `run` step must name its shell.
            assert step["shell"] == "bash", step


def test_action_never_echoes_the_api_key() -> None:
    """The key reaches the CLI only through a step `env:` entry, never a script."""
    action = _action()
    text = ACTION.read_text()
    key_refs = re.findall(r"\$\{\{\s*inputs\.api-key\s*\}\}", text)
    assert key_refs, "action does not use the api-key input"
    env_refs = 0
    for step in action["runs"]["steps"]:
        script = step.get("run", "")
        # No `${{ ... }}` expression is spliced into a shell script at all
        # (script injection and secret echo share this root cause).
        assert "${{" not in script, step.get("name")
        assert "set -x" not in script, step.get("name")
        for value in (step.get("env") or {}).values():
            if re.fullmatch(r"\$\{\{\s*inputs\.api-key\s*\}\}", str(value)):
                env_refs += 1
        for value in (step.get("with") or {}).values():
            assert "inputs.api-key" not in str(value), step.get("name")
    assert env_refs == len(key_refs)


def test_action_honours_fail_on_and_posts_one_review() -> None:
    steps = _action()["runs"]["steps"]
    scripts = "\n".join(step.get("run", "") for step in steps)
    assert "python -m code_review_council" in scripts
    assert "--fail-on" in scripts
    assert scripts.count("gh pr review") == 1
    # The final step re-raises the council's exit code after the review is posted.
    last = steps[-1]
    assert "exit" in last["run"]


def test_nothing_under_dot_github_names_the_action() -> None:
    """The action lives at the repo root, not under the canon-rendered `.github/`."""
    assert ACTION.is_file()
    assert not (REPO_ROOT / ".github" / "review").exists()


def _step(name: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = _action()["runs"]["steps"]
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step {name!r}")


@pytest.mark.parametrize(("council_rc", "want_rc"), [(0, 0), (1, 1)])
def test_action_scripts_post_then_apply_fail_on(
    tmp_path: Path, council_rc: int, want_rc: int
) -> None:
    """Run the action's own bash with a stub ``python`` standing in for the council.

    The council step must swallow exit 1 (so the review still gets posted),
    record it, and the last step must re-raise it. The key never reaches stdout.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_python = sys.executable
    stub = bin_dir / "python"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-m" ]; then\n'
        '  printf \'%s\' \'{"verdict": "request_changes", "findings": [{}]}\''
        ' > "$RUNNER_TEMP/council.json"\n'
        f"  exit {council_rc}\n"
        "fi\n"
        f'exec "{real_python}" "$@"\n'
    )
    stub.chmod(0o755)
    output_file = tmp_path / "github_output"
    output_file.write_text("")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output_file),
        "COUNCIL_API_KEY": "sk-secret-value",
        "COUNCIL_PROVIDER": "gemini",
        "COUNCIL_MODEL": "",
        "COUNCIL_CONTEXT_FILE": "",
        "COUNCIL_FAIL_ON": "high",
        "PR_TITLE": "t",
        "PR_BODY": "b",
    }
    run = subprocess.run(
        ["bash", "-c", _step("Run the council")["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    outputs = dict(
        line.split("=", 1) for line in output_file.read_text().splitlines() if "=" in line
    )
    assert outputs["exit-code"] == str(council_rc)
    assert outputs["verdict"] == "request_changes"
    assert outputs["findings"] == "1"
    assert "sk-secret-value" not in run.stdout + run.stderr

    apply = subprocess.run(
        ["bash", "-c", _step("Apply fail-on")["run"]],
        env={"PATH": "/usr/bin:/bin", "COUNCIL_EXIT": outputs["exit-code"]},
        capture_output=True,
        text=True,
        check=False,
    )
    assert apply.returncode == want_rc


def test_action_council_step_stops_on_a_config_error(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "python"
    stub.write_text("#!/bin/sh\nexit 2\n")
    stub.chmod(0o755)
    output_file = tmp_path / "out"
    output_file.write_text("")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output_file),
        "COUNCIL_API_KEY": "",
        "COUNCIL_PROVIDER": "gemini",
        "COUNCIL_MODEL": "",
        "COUNCIL_CONTEXT_FILE": "",
        "COUNCIL_FAIL_ON": "none",
        "PR_TITLE": "",
        "PR_BODY": "",
    }
    run = subprocess.run(
        ["bash", "-c", _step("Run the council")["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 2
    assert output_file.read_text() == ""


def test_review_readme_has_a_complete_workflow_and_readme_links_it() -> None:
    readme = (REPO_ROOT / "review" / "README.md").read_text()
    block = re.search(r"```yaml\n(name: .*?)```", readme, re.S)
    assert block, "no copy-paste workflow in review/README.md"
    workflow = yaml.safe_load(block.group(1))
    # PyYAML reads the bare `on:` key as boolean True.
    assert "pull_request" in workflow[True]
    assert workflow["permissions"]["pull-requests"] == "write"
    steps = workflow["jobs"]["review"]["steps"]
    council = [s for s in steps if str(s.get("uses", "")).startswith("lagowski/dap/review@")]
    assert len(council) == 1
    declared = set(_action()["inputs"])
    assert set(council[0]["with"]) <= declared
    assert "api-key" in council[0]["with"]
    assert "(review/README.md)" in (REPO_ROOT / "README.md").read_text()
