"""Detect project metadata from a local repository directory.

The scanner is pure: given a Path, it inspects files on disk and returns a
ProjectScan describing what kind of project lives there. It does not clone,
fetch, or write anything.

Stream A of issue #31 (cortex-multi-project epic). Streams B and C consume
ProjectScan to build a profile.yaml and to expose a `cortex init` command.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# README is summarised down to this many characters.
_README_SUMMARY_MAX = 500

# Candidate entry-point files for Python projects, in priority order.
_PYTHON_ENTRY_CANDIDATES = (
    "src/main.py",
    "src/app.py",
    "src/cli.py",
    "main.py",
    "app.py",
    "cli.py",
)

# Candidate top-level directories to flag as "key directories".
_KEY_DIR_CANDIDATES = ("src", "lib", "app", "tests", "test", "docs")


@dataclass
class ProjectScan:
    """Detected metadata for a project living at some local path."""

    repo: str = ""  # "owner/name" — caller-supplied; scanner never invents it.
    language: str = "unknown"
    framework: str = ""
    package_manager: str = ""
    test_framework: str = ""
    test_command: str = ""
    build_command: str = ""
    entry_point: str = ""
    key_directories: list[str] = field(default_factory=list)
    readme_summary: str = ""
    file_count: int = 0
    has_claude_md: bool = False
    has_dockerfile: bool = False
    has_github_actions: bool = False


# --------------------------------------------------------------------------- #
# Top-level orchestrator                                                      #
# --------------------------------------------------------------------------- #

def scan_project(path: Path, repo: str = "") -> ProjectScan:
    """Inspect ``path`` and produce a ProjectScan.

    The scan never raises on missing optional files (e.g. README, lockfiles);
    those simply leave the corresponding fields empty / default.
    """
    path = Path(path)

    language = detect_language(path)
    package_manager = detect_package_manager(path, language)
    test_framework = detect_test_framework(path, language)
    framework = detect_framework(path, language)

    return ProjectScan(
        repo=repo,
        language=language,
        framework=framework,
        package_manager=package_manager,
        test_framework=test_framework,
        test_command=detect_test_command(language, package_manager, test_framework),
        build_command=detect_build_command(language, package_manager),
        entry_point=detect_entry_point(path, language),
        key_directories=detect_key_directories(path),
        readme_summary=read_readme_summary(path),
        file_count=count_files(path),
        has_claude_md=(path / "CLAUDE.md").is_file(),
        has_dockerfile=(path / "Dockerfile").is_file(),
        has_github_actions=_has_github_actions(path),
    )


# --------------------------------------------------------------------------- #
# Detectors                                                                   #
# --------------------------------------------------------------------------- #

def detect_language(path: Path) -> str:
    """Identify the primary language by package-manifest presence."""
    if (path / "pyproject.toml").is_file() or (path / "setup.py").is_file():
        return "python"
    if (path / "package.json").is_file():
        return "node"
    if (path / "go.mod").is_file():
        return "go"
    if (path / "Cargo.toml").is_file():
        return "rust"
    if (path / "Gemfile").is_file():
        return "ruby"
    return "unknown"


def detect_package_manager(path: Path, language: str) -> str:
    """Identify the package/dependency manager.

    Lockfiles take priority — they're unambiguous. Falls back to language
    defaults (e.g. python without any lockfile -> "pip").
    """
    if (path / "uv.lock").is_file():
        return "uv"
    if (path / "poetry.lock").is_file():
        return "poetry"
    if (path / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (path / "yarn.lock").is_file():
        return "yarn"
    if (path / "package-lock.json").is_file():
        return "npm"
    if (path / "Cargo.lock").is_file():
        return "cargo"
    if (path / "Gemfile.lock").is_file():
        return "bundler"

    # No lockfile — use language defaults.
    if language == "python":
        if (path / "pyproject.toml").is_file():
            return "pip"
        if (path / "setup.py").is_file():
            return "pip"
    if language == "node":
        if (path / "package.json").is_file():
            return "npm"
    if language == "go":
        return "go-mod"
    if language == "rust":
        return "cargo"
    if language == "ruby":
        return "bundler"
    return ""


def detect_test_framework(path: Path, language: str) -> str:
    """Identify the test framework via config files or dependency lists."""
    if language == "python":
        return _detect_python_test_framework(path)
    if language == "node":
        return _detect_node_test_framework(path)
    if language == "go":
        return "go-test"
    if language == "ruby":
        if (path / ".rspec").is_file():
            return "rspec"
    if language == "rust":
        return "cargo-test"
    return ""


def detect_framework(path: Path, language: str) -> str:
    """Identify the application framework (fastapi, express, ...)."""
    if language == "python":
        return _detect_python_framework(path)
    if language == "node":
        return _detect_node_framework(path)
    return ""


def detect_test_command(language: str, package_manager: str, test_framework: str) -> str:
    """Build a sensible default test command from language + tooling."""
    if language == "python" and test_framework == "pytest":
        if package_manager == "uv":
            return "uv run pytest"
        if package_manager == "poetry":
            return "poetry run pytest"
        return "pytest"
    if language == "node" and test_framework in {"jest", "mocha", "vitest"}:
        # Always go through `npm test` style — the script field handles which runner.
        if package_manager == "pnpm":
            return "pnpm test"
        if package_manager == "yarn":
            return "yarn test"
        return "npm test"
    if language == "go":
        return "go test ./..."
    if language == "ruby" and test_framework == "rspec":
        return "bundle exec rspec"
    if language == "rust":
        return "cargo test"
    return ""


def detect_build_command(language: str, package_manager: str) -> str:
    """Build the dependency-install / build command."""
    if language == "python":
        if package_manager == "uv":
            return "uv sync"
        if package_manager == "poetry":
            return "poetry install"
        if package_manager == "pip":
            return "pip install -e ."
    if language == "node":
        if package_manager == "pnpm":
            return "pnpm install"
        if package_manager == "yarn":
            return "yarn install"
        return "npm install"
    if language == "go":
        return "go build ./..."
    if language == "rust":
        return "cargo build"
    if language == "ruby":
        return "bundle install"
    return ""


def detect_entry_point(path: Path, language: str) -> str:
    """Locate the most plausible entry-point file, returned as a relative path."""
    if language == "python":
        for candidate in _PYTHON_ENTRY_CANDIDATES:
            if (path / candidate).is_file():
                return candidate
        return ""
    if language == "node":
        pkg = _read_package_json(path)
        main = pkg.get("main")
        if isinstance(main, str) and main:
            return main
        # Fallbacks
        for candidate in ("index.js", "src/index.js", "server.js"):
            if (path / candidate).is_file():
                return candidate
    if language == "go":
        if (path / "main.go").is_file():
            return "main.go"
    return ""


def detect_key_directories(path: Path) -> list[str]:
    """Return top-level directories that look like meaningful project areas."""
    return [name for name in _KEY_DIR_CANDIDATES if (path / name).is_dir()]


def read_readme_summary(path: Path) -> str:
    """Read the first chunk of README.md, if any."""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        readme = path / name
        if readme.is_file():
            try:
                text = readme.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
            return text[:_README_SUMMARY_MAX].strip()
    return ""


def count_files(path: Path) -> int:
    """Count files under ``path``, ignoring anything inside .git/."""
    count = 0
    for entry in path.rglob("*"):
        if not entry.is_file():
            continue
        if ".git" in entry.parts:
            continue
        count += 1
    return count


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _has_github_actions(path: Path) -> bool:
    workflows = path / ".github" / "workflows"
    if not workflows.is_dir():
        return False
    return any(workflows.iterdir())


def _read_pyproject(path: Path) -> dict[str, Any]:
    """Parse pyproject.toml; return {} on any failure."""
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_package_json(path: Path) -> dict[str, Any]:
    """Parse package.json; return {} on any failure."""
    pkg_json = path / "package.json"
    if not pkg_json.is_file():
        return {}
    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _detect_python_test_framework(path: Path) -> str:
    # Explicit config files first.
    if (path / "pytest.ini").is_file() or (path / "conftest.py").is_file():
        return "pytest"

    pyproject = _read_pyproject(path)
    tool = pyproject.get("tool", {})
    if isinstance(tool, dict) and "pytest" in tool:
        return "pytest"

    # Fall back to scanning declared dependencies.
    for dep in _iter_python_dependencies(pyproject):
        name = _strip_dep_name(dep)
        if name == "pytest":
            return "pytest"
    return ""


def _detect_python_framework(path: Path) -> str:
    pyproject = _read_pyproject(path)
    known = {"fastapi", "flask", "django", "starlette", "aiohttp", "tornado"}
    for dep in _iter_python_dependencies(pyproject):
        name = _strip_dep_name(dep)
        if name in known:
            return name
    return ""


def _iter_python_dependencies(pyproject: dict[str, Any]) -> Iterator[str]:
    """Yield raw dependency strings from [project.dependencies] and dev groups."""
    project = pyproject.get("project", {})
    if isinstance(project, dict):
        for dep in project.get("dependencies", []) or []:
            if isinstance(dep, str):
                yield dep
        opt_deps = project.get("optional-dependencies", {}) or {}
        if isinstance(opt_deps, dict):
            for group_deps in opt_deps.values():
                for dep in group_deps or []:
                    if isinstance(dep, str):
                        yield dep

    # PEP 735 dependency groups (e.g. uv / pip dev groups).
    groups = pyproject.get("dependency-groups", {})
    if isinstance(groups, dict):
        for group_deps in groups.values():
            for dep in group_deps or []:
                if isinstance(dep, str):
                    yield dep


def _strip_dep_name(dep: str) -> str:
    """Extract just the package name from a PEP 508 / requirement string."""
    # Stop at the first version operator, whitespace, or extras bracket.
    name = dep.strip().lower()
    for sep in ("[", ">", "<", "=", "!", "~", ";", " "):
        idx = name.find(sep)
        if idx != -1:
            name = name[:idx]
    return name.strip()


def _detect_node_test_framework(path: Path) -> str:
    pkg = _read_package_json(path)
    dev = pkg.get("devDependencies", {}) or {}
    deps = pkg.get("dependencies", {}) or {}
    for runner in ("jest", "vitest", "mocha", "ava", "tap"):
        if runner in dev or runner in deps:
            return runner
    # Fallback: a "test" script that obviously names a runner.
    scripts = pkg.get("scripts", {}) or {}
    test_script = scripts.get("test", "") if isinstance(scripts, dict) else ""
    for runner in ("jest", "vitest", "mocha", "ava"):
        if runner in test_script:
            return runner
    return ""


def _detect_node_framework(path: Path) -> str:
    pkg = _read_package_json(path)
    deps = pkg.get("dependencies", {}) or {}
    known = ("next", "express", "fastify", "koa", "nestjs", "@nestjs/core", "react", "vue")
    for name in known:
        if name in deps:
            # Normalise scoped nestjs to "nestjs".
            if name == "@nestjs/core":
                return "nestjs"
            return name
    return ""
