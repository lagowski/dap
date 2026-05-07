"""Project profile management.

Profiles describe a target repository's language, framework, build/test
commands, and conventions. Each profile lives at:

    ~/.cortex/projects/<owner>-<name>/profile.yaml

A sibling ``repo/`` directory holds the shallow clone, and ``context.md``
holds the assembled agent prompt. This module owns the schema (Pydantic)
and the load/save/path helpers; cloning and scanning live in sibling
modules.

The location of ``~/.cortex`` can be overridden with the ``CORTEX_HOME``
environment variable, which is essential for test isolation.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

__all__ = [
    "Profile",
    "context_path",
    "cortex_home",
    "generate_context_md",
    "load_profile",
    "profile_dir",
    "profile_exists",
    "profile_path",
    "repo_clone_path",
    "save_profile",
]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class Profile(BaseModel):
    """Detected metadata about a target project.

    Stream C builds an instance of this from a ``ProjectScan`` (Stream A)
    plus the repo identifier, then calls :func:`save_profile` to persist
    it. Most string fields default to empty strings rather than ``None``
    so the YAML output stays uniform and human-friendly.
    """

    repo: str
    language: str = "unknown"
    framework: str = ""
    package_manager: str = ""
    test_framework: str = ""
    test_command: str = ""
    build_command: str = ""
    entry_point: str = ""
    key_directories: list[str] = Field(default_factory=list)
    conventions: list[str] = Field(default_factory=list)
    readme_summary: str = ""
    file_count: int = 0
    has_claude_md: bool = False
    last_scanned: datetime | None = None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def cortex_home() -> Path:
    """Return the root directory for cortex's per-user state.

    Honours the ``CORTEX_HOME`` environment variable when set (tests rely
    on this to redirect writes into ``tmp_path``); otherwise falls back
    to ``~/.cortex``.
    """
    override = os.environ.get("CORTEX_HOME")
    if override:
        return Path(override)
    return Path.home() / ".cortex"


def _repo_slug(repo: str) -> str:
    """Convert ``"owner/name"`` to a filesystem-safe ``"owner-name"``."""
    return repo.replace("/", "-")


def profile_dir(repo: str) -> Path:
    """Directory that holds a project's profile, clone, and context."""
    return cortex_home() / "projects" / _repo_slug(repo)


def profile_path(repo: str) -> Path:
    """Path to ``profile.yaml`` for the given repo."""
    return profile_dir(repo) / "profile.yaml"


def repo_clone_path(repo: str) -> Path:
    """Path to the shallow clone for the given repo."""
    return profile_dir(repo) / "repo"


def context_path(repo: str) -> Path:
    """Path to ``context.md`` for the given repo."""
    return profile_dir(repo) / "context.md"


def generate_context_md(clone_path: Path, repo: str) -> str:
    """Generate a context summary from the cloned repo for Phase 1 agents.

    Reads README.md, CLAUDE.md, top-level structure, build manifests, and
    recent git history. Returns a markdown string suitable for prepending
    to Phase 1 agent system prompts.
    """
    import subprocess as _sp

    parts: list[str] = [f"# Project context: {repo}\n"]

    def _read_file(path: Path, max_chars: int = 2000) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            return text[:max_chars] + ("…" if len(text) > max_chars else "")
        except OSError:
            return ""

    # README
    for name in ("README.md", "README.rst", "README"):
        readme = clone_path / name
        if readme.exists():
            content = _read_file(readme, 1500)
            if content:
                parts.append(f"## README\n{content}")
            break

    # CLAUDE.md — project conventions
    claude_md = clone_path / "CLAUDE.md"
    if claude_md.exists():
        content = _read_file(claude_md, 1500)
        if content:
            parts.append(f"## CLAUDE.md\n{content}")

    # Top-level structure
    try:
        entries = sorted(p.name for p in clone_path.iterdir() if not p.name.startswith("."))
        if entries:
            parts.append("## Top-level structure\n" + "\n".join(f"- {e}" for e in entries[:30]))
    except OSError:
        pass

    # Build/dep manifest (first match wins)
    for manifest in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", "requirements.txt"):
        mpath = clone_path / manifest
        if mpath.exists():
            content = _read_file(mpath, 600)
            if content:
                parts.append(f"## {manifest}\n```\n{content}\n```")
            break

    # Recent git history
    try:
        result = _sp.run(
            ["git", "log", "--oneline", "-10"],
            cwd=str(clone_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts.append("## Recent commits\n```\n" + result.stdout.strip() + "\n```")
    except Exception:
        pass

    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Existence / load / save
# ---------------------------------------------------------------------------


def profile_exists(repo: str) -> bool:
    """Return ``True`` when ``profile.yaml`` exists on disk for ``repo``."""
    return profile_path(repo).is_file()


def load_profile(repo: str) -> Profile | None:
    """Load and return the saved profile for ``repo``, or ``None``.

    Returns ``None`` when the profile file does not exist. Any YAML or
    validation errors are propagated to the caller — corrupt profiles
    are a real bug we want to surface, not silently swallow.
    """
    path = profile_path(repo)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Profile.model_validate(data)


def save_profile(profile: Profile) -> Path:
    """Persist ``profile`` to ``~/.cortex/projects/<repo>/profile.yaml``.

    Side-effects:
      * Sets ``profile.last_scanned`` to the current UTC time.
      * Creates parent directories as needed.

    Returns the path the profile was written to.
    """
    profile.last_scanned = datetime.now(UTC)

    target = profile_path(profile.repo)
    target.parent.mkdir(parents=True, exist_ok=True)

    # ``mode="json"`` ensures datetime serialises to an ISO-8601 string
    # rather than a native ``datetime`` object yaml can't render cleanly.
    payload = profile.model_dump(mode="json")

    with target.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            payload,
            fh,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    return target
