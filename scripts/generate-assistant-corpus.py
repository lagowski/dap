#!/usr/bin/env python3
"""Generate the assistant's ``DOCS_CORPUS`` from ``docs/assistant-corpus.md``.

The corpus is prompt-stuffed into the config assistant's system prompt. Keeping
the source as a reviewable markdown doc (alongside the rest of ``docs/``) and
generating the Python constant from it means editing the corpus is a normal
docs change — and the sync test (``tests/smoke/test_docs_corpus_sync.py``) fails
in CI if the generated module drifts from the source. Mirrors the
``gen:api`` / ``check:api`` pattern used for the dashboard's API types.

    python scripts/generate-assistant-corpus.py            # regenerate
    python scripts/generate-assistant-corpus.py --check    # exit 1 if out of date
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "docs" / "assistant-corpus.md"
TARGET = REPO / "apps" / "engine" / "src" / "dap_engine" / "assistant" / "docs_corpus.py"

_HEADER = '''"""Curated DAP knowledge for the config assistant (#689).

GENERATED from ``docs/assistant-corpus.md`` by
``scripts/generate-assistant-corpus.py`` — do not edit by hand. Edit the
markdown source and regenerate; the sync test fails in CI if they drift.

Prompt-stuffed into the assistant's system prompt so its config advice is
grounded in how DAP actually works rather than hallucinated.
"""

from __future__ import annotations

DOCS_CORPUS = """\\
'''


def corpus_body() -> str:
    """The corpus text — the markdown source minus a leading ``<!-- -->`` note."""
    md = SOURCE.read_text(encoding="utf-8").lstrip()
    if md.startswith("<!--"):
        end = md.find("-->")
        if end != -1:
            md = md[end + 3 :].lstrip()
    if '"""' in md:
        raise SystemExit("docs/assistant-corpus.md must not contain triple double-quotes")
    return md.rstrip("\n")


def render() -> str:
    return f'{_HEADER}{corpus_body()}\n"""\n'


def main() -> int:
    rendered = render()
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != rendered:
            print(
                "docs_corpus.py is out of date — run "
                "`python scripts/generate-assistant-corpus.py` and commit the result.",
                file=sys.stderr,
            )
            return 1
        return 0
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
