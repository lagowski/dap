"""Project onboarding: scan target repos and build profiles for Cortex."""

from cortex.init.profile import Profile, save_profile
from cortex.init.scanner import ProjectScan, scan_project


def scan_to_profile(scan: ProjectScan) -> Profile:
    """Convert a scanner result into a Profile model.

    The two are deliberately separate types: ProjectScan describes what's on
    disk right now (Stream A), Profile is the persisted YAML schema (Stream B).
    This is the seam where Cortex translates one to the other.
    """
    return Profile(
        repo=scan.repo,
        language=scan.language,
        framework=scan.framework,
        package_manager=scan.package_manager,
        test_framework=scan.test_framework,
        test_command=scan.test_command,
        build_command=scan.build_command,
        entry_point=scan.entry_point,
        key_directories=list(scan.key_directories),
        readme_summary=scan.readme_summary,
        file_count=scan.file_count,
        has_claude_md=scan.has_claude_md,
    )


__all__ = [
    "Profile",
    "ProjectScan",
    "save_profile",
    "scan_project",
    "scan_to_profile",
]
