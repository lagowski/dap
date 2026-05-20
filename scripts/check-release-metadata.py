#!/usr/bin/env python3
"""Validate first-party package metadata before release.

This is intentionally lightweight and stdlib-only so it can run in CI before
building wheels. It checks the invariants that PyPI publishing depends on but
that local workspace sources can otherwise hide during development.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ReleasePackage:
    distribution: str
    path: Path
    import_name: str
    dependencies: tuple[str, ...] = ()


RELEASE_PACKAGES: tuple[ReleasePackage, ...] = (
    ReleasePackage("dap-schemas", Path("packages/types/pyproject.toml"), "dap_types"),
    ReleasePackage(
        "dap-runtimes",
        Path("packages/runtimes/pyproject.toml"),
        "dap_runtimes",
        dependencies=("dap-schemas",),
    ),
    ReleasePackage("dap-prompt-dsl", Path("packages/prompt-dsl/pyproject.toml"), "dap_prompt_dsl"),
    ReleasePackage(
        "dap-engine",
        Path("apps/engine/pyproject.toml"),
        "dap_engine",
        dependencies=("dap-schemas", "dap-runtimes[all]", "dap-prompt-dsl"),
    ),
    ReleasePackage(
        "dap-cli",
        Path("apps/cli/pyproject.toml"),
        "dap_cli",
        dependencies=("dap-engine",),
    ),
)

INTERNAL_PACKAGES: tuple[ReleasePackage, ...] = (
    ReleasePackage(
        "code-review-council",
        Path("packages/code-review-council/pyproject.toml"),
        "code_review_council",
    ),
)

VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-[0-9A-Za-z.-]+)?$")


def main() -> int:
    errors: list[str] = []
    root = _load_pyproject(Path("pyproject.toml"))
    root_version = _project(root).get("version")
    if not isinstance(root_version, str) or VERSION_RE.fullmatch(root_version) is None:
        errors.append(f"root pyproject.toml has invalid version: {root_version!r}")
        root_version = "0.0.0"

    expected_spec = _minor_compat_spec(root_version)
    seen_names: set[str] = set()

    for package in RELEASE_PACKAGES:
        _validate_release_package(
            package,
            root_version=root_version,
            expected_spec=expected_spec,
            seen_names=seen_names,
            errors=errors,
        )

    for package in INTERNAL_PACKAGES:
        _validate_internal_package(package, errors)

    if errors:
        print("Release metadata check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Release metadata OK for {root_version}")
    print(f"First-party compatibility spec: {expected_spec}")
    for package in RELEASE_PACKAGES:
        print(f"  - {package.distribution}")
    return 0


def _validate_release_package(
    package: ReleasePackage,
    *,
    root_version: str,
    expected_spec: str,
    seen_names: set[str],
    errors: list[str],
) -> None:
    data = _load_pyproject(package.path)
    project = _project(data)
    label = str(package.path)

    _validate_name(project, package, seen_names, errors)
    if project.get("version") != root_version:
        errors.append(
            f"{label}: version {project.get('version')!r} does not match root {root_version!r}"
        )

    for required in ("description", "readme", "requires-python"):
        if not project.get(required):
            errors.append(f"{label}: missing project.{required}")

    _validate_build_system(data, label, errors)
    _validate_first_party_dependencies(project, package, expected_spec, errors)
    _validate_wheel_package_path(data, package, errors)


def _validate_name(
    project: dict[str, Any],
    package: ReleasePackage,
    seen_names: set[str],
    errors: list[str],
) -> None:
    name = project.get("name")
    label = str(package.path)
    if name != package.distribution:
        errors.append(f"{label}: expected project.name {package.distribution!r}, got {name!r}")
    if name in seen_names:
        errors.append(f"{label}: duplicate distribution name {name!r}")
    if isinstance(name, str):
        seen_names.add(name)


def _validate_build_system(data: dict[str, Any], label: str, errors: list[str]) -> None:
    build_system = data.get("build-system", {})
    if build_system.get("build-backend") != "hatchling.build":
        errors.append(f"{label}: build-system.build-backend must be 'hatchling.build'")
    if "hatchling" not in build_system.get("requires", []):
        errors.append(f"{label}: build-system.requires must include 'hatchling'")


def _validate_first_party_dependencies(
    project: dict[str, Any],
    package: ReleasePackage,
    expected_spec: str,
    errors: list[str],
) -> None:
    label = str(package.path)
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list):
        errors.append(f"{label}: project.dependencies must be a list")
        dependencies = []
    for dependency in package.dependencies:
        expected = f"{dependency}{expected_spec}"
        if expected not in dependencies:
            errors.append(
                f"{label}: expected first-party dependency {expected!r}; "
                f"found {_matching_dependencies(dependencies, dependency)!r}"
            )


def _validate_wheel_package_path(
    data: dict[str, Any],
    package: ReleasePackage,
    errors: list[str],
) -> None:
    packages = _wheel_packages(data)
    expected_import_path = f"src/{package.import_name}"
    if expected_import_path not in packages:
        errors.append(f"{package.path}: wheel packages must include {expected_import_path!r}")


def _validate_internal_package(package: ReleasePackage, errors: list[str]) -> None:
    data = _load_pyproject(package.path)
    project = _project(data)
    if project.get("name") != package.distribution:
        errors.append(
            f"{package.path}: expected internal project.name {package.distribution!r}, "
            f"got {project.get('name')!r}"
        )


def _load_pyproject(path: Path) -> dict[str, Any]:
    with (REPO_ROOT / path).open("rb") as handle:
        return tomllib.load(handle)


def _project(data: dict[str, Any]) -> dict[str, Any]:
    project = data.get("project")
    if not isinstance(project, dict):
        return {}
    return project


def _minor_compat_spec(version: str) -> str:
    match = VERSION_RE.fullmatch(version)
    if match is None:
        return ">=0.0,<0.1"
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    lower = f"{major}.{minor}"
    return f">={lower},<{major}.{minor + 1}"


def _wheel_packages(data: dict[str, Any]) -> list[Any]:
    tool = _dict_value(data, "tool")
    hatch = _dict_value(tool, "hatch")
    build = _dict_value(hatch, "build")
    targets = _dict_value(build, "targets")
    wheel = _dict_value(targets, "wheel")
    packages = wheel.get("packages", [])
    return packages if isinstance(packages, list) else []


def _dict_value(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _matching_dependencies(dependencies: list[Any], package: str) -> list[str]:
    return [dep for dep in dependencies if isinstance(dep, str) and dep.startswith(package)]


if __name__ == "__main__":
    raise SystemExit(main())
