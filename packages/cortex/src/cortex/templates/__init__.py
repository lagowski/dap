"""Issue template parsing and section manipulation utilities.

Every enrichment agent reads the GitHub issue body, updates its section,
and writes it back. These helpers make that read-modify-write cycle simple.
"""

from __future__ import annotations

import re
from pathlib import Path

# Regex: match a line starting with "## " (markdown H2 header)
_SECTION_HEADER_RE = re.compile(r"^## ", re.MULTILINE)

TEMPLATE_PATH = Path(__file__).parent / "issue_template.md"


def parse_issue_sections(body: str) -> dict[str, str]:
    """Parse a markdown issue body into a dict of section_name -> content.

    Only splits on ``## `` (H2) headers. Sub-headers (``###``, ``####``)
    are preserved as part of their parent section's content.

    Returns:
        Dict mapping section names to their content (whitespace-stripped).
        Empty dict if the body has no ``## `` headers.
    """
    if not body or not body.strip():
        return {}

    # If there are no ## headers at all, there are no sections
    if not _SECTION_HEADER_RE.search(body):
        return {}

    # Split the body at every "## " boundary.
    # The first element is everything before the first ## header (preamble).
    parts = _SECTION_HEADER_RE.split(body)

    sections: dict[str, str] = {}
    for i, part in enumerate(parts):
        # First chunk is the preamble (before first ## header) — skip it
        if i == 0:
            continue

        if not part.strip():
            continue

        # Each remaining chunk starts with "SectionName\n..."
        lines = part.split("\n", 1)
        name = lines[0].strip()
        content = lines[1].strip() if len(lines) > 1 else ""

        if name:
            sections[name] = content

    return sections


def get_issue_section(body: str, section_name: str) -> str:
    """Extract one section's content from an issue body.

    Args:
        body: Full markdown issue body.
        section_name: Section header text (case-insensitive).

    Returns:
        Section content, or empty string if not found.
    """
    sections = parse_issue_sections(body)
    # Case-insensitive lookup
    lower_name = section_name.lower()
    for name, content in sections.items():
        if name.lower() == lower_name:
            return content
    return ""


def update_issue_section(body: str, section_name: str, new_content: str) -> str:
    """Replace a section's content in the issue body. Appends if missing.

    Args:
        body: Full markdown issue body.
        section_name: Section header text (case-insensitive match).
        new_content: Replacement content for the section.

    Returns:
        Updated issue body with the section replaced.
    """
    sections = parse_issue_sections(body)

    # Find the canonical name (preserving original case)
    canonical_name: str | None = None
    lower_name = section_name.lower()
    for name in sections:
        if name.lower() == lower_name:
            canonical_name = name
            break

    if canonical_name is not None:
        # Replace existing section content
        sections[canonical_name] = new_content
    else:
        # Append new section
        sections[section_name] = new_content

    # Reconstruct: preserve any content before the first ## header
    preamble = ""
    first_header = _SECTION_HEADER_RE.search(body)
    if first_header and first_header.start() > 0:
        preamble = body[: first_header.start()]

    # Rebuild body from sections in original order
    # (new sections appended at end)
    parts = [preamble.rstrip()] if preamble.strip() else []
    for name, content in sections.items():
        parts.append(f"## {name}\n{content}")

    return "\n\n".join(parts) + "\n"


def parse_target_files(section_content: str) -> list[tuple[str, int | None]]:
    """Extract (path, line_or_none) pairs from a Target Files section.

    Matches markdown bullet lines containing backtick-wrapped paths like:
      - `cortex/cli.py:10` — description
      - `cortex/cli.py` — description

    Returns:
        List of (file_path, line_number_or_None) tuples.
    """
    pattern = re.compile(r"`([^`]+?\.[\w]+)(?::(\d+))?`")
    results: list[tuple[str, int | None]] = []
    for line in section_content.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        match = pattern.search(stripped)
        if match:
            path = match.group(1)
            line_num = int(match.group(2)) if match.group(2) else None
            results.append((path, line_num))
    return results


def load_template() -> str:
    """Load the issue template from disk.

    Returns:
        Template content as a string.
    """
    return TEMPLATE_PATH.read_text()
