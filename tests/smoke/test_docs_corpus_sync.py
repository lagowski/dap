"""The assistant's DOCS_CORPUS stays in sync with its markdown source (#689).

``docs/assistant-corpus.md`` is the source of truth; ``docs_corpus.py`` is
generated from it by ``scripts/generate-assistant-corpus.py``. This is the
CI guard (the ``check:api`` equivalent): it fails if either was edited without
regenerating the other, so the prompt corpus can't silently drift.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from dap_engine.assistant.docs_corpus import DOCS_CORPUS

REPO = Path(__file__).resolve().parents[2]


def _load_generator() -> object:
    spec = importlib.util.spec_from_file_location(
        "_gen_assistant_corpus", REPO / "scripts" / "generate-assistant-corpus.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_docs_corpus_matches_markdown_source() -> None:
    gen = _load_generator()
    # The shipped constant equals the markdown source (minus the leading note),
    # so editing one without regenerating fails here.
    assert DOCS_CORPUS.strip() == gen.corpus_body().strip()  # type: ignore[attr-defined]


def test_generated_module_is_up_to_date() -> None:
    gen = _load_generator()
    target = REPO / "apps/engine/src/dap_engine/assistant/docs_corpus.py"
    assert target.read_text(encoding="utf-8") == gen.render()  # type: ignore[attr-defined]


def test_corpus_still_grounds_the_key_surface() -> None:
    # Cheap content tripwire: the corpus must still describe the load-bearing
    # config concepts the assistant grounds on.
    for token in ("runtime_id", "api-call", "python-func", "approval_required_nodes"):
        assert token in DOCS_CORPUS
