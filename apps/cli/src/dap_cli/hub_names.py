"""Client-name scrubber for the Labs hub export (#1016).

Nothing that might name a client may leave a bundle: every capitalised proper noun (one word or
a capitalised sequence such as ``Acme Logistics``) that is NOT on the public-names allowlist is
replaced with ``client``. The rules mirror the hub's own import gate, so a page this exporter
writes passes it unchanged:

- not candidates: Jinja (``{{ ... }}``, ``{% ... %}``, ``{# ... #}``), code spans and blocks,
  URLs, ``<placeholders>``, identifier-like tokens (a digit, ``_``, ``/``, ``@`` or an inner
  ``.``), command-line options (``-Rs``) and the word ``client`` itself;
- a plain capitalised word at a sentence start is an ordinary word (``COMMON``, or the same word
  written in lower case elsewhere in the page) unless there is evidence it is a name: the page
  uses it as a name elsewhere, or the next word is one (``Acme Logistics ships``);
- ALL-CAPS is emphasis when the word is ordinary (``ONLY``), otherwise an acronym and a name;
- a capitalised word with inner capitals (``ShopRite``) is a name; a lower-case-initial one
  (``updatedAt``, ``macOS``) is an identifier, as in the hub gate.

The allowlist is ``BUILTIN_PUBLIC_NAMES`` (public technology: products, platforms, languages,
formats, plus the title-case words of DAP node names), extendable with a ``public-names.yaml``
in the hub's format (a mapping of group -> list of names). Matching is case-insensitive and
whole-word; a multi-word entry matches as a phrase.

When the scrubber can't classify a text (an unclosed template tag, or a name still found after
the replacement) it raises ``Unclear``; the exporter then refuses the export. It never echoes
the value.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Iterable
from pathlib import Path

CLIENT = "client"

# Public names: a small built-in set of technology names and the title-case words DAP's node
# names use. Never a client, customer, partner or person.
BUILTIN_PUBLIC_NAMES: dict[str, list[str]] = {
    "dap": ["DAP", "Deterministic Agent Pipeline", "LangGraph", "Jinja", "Cortex"],
    "ai": [
        "Anthropic",
        "Claude",
        "Claude Code",
        "OpenAI",
        "ChatGPT",
        "GPT",
        "Gemini",
        "Copilot",
        "GitHub Copilot",
        "Ollama",
        "Codex",
        "Aider",
        "GLM",
        "LLM",
        "AI",
        "MCP",
        "SDK",
        "RAG",
    ],
    "dev_tools": [
        "GitHub",
        "GitHub Actions",
        "GH",
        "GitLab",
        "Git",
        "HEAD",
        "CODEOWNERS",
        "README",
        "PR",
        "CI",
        "CD",
        "IDE",
        "CLI",
        "VS Code",
        "Docker",
        "Kubernetes",
        "Helm",
        "Bash",
        "Linux",
        "Ubuntu",
        "WSL",
        "Windows",
        "macOS",
        "PyPI",
        "npm",
        "Ruff",
        "FastAPI",
        "SQLAlchemy",
        "Alembic",
        "PostgreSQL",
        "Postgres",
        "SQLite",
        "Redis",
        "GraphQL",
        "Next.js",
        "Playwright",
        "Markdown",
    ],
    "languages": ["Python", "JavaScript", "TypeScript", "Node", "Go", "Rust"],
    "standards": [
        "API",
        "HTTP",
        "HTTPS",
        "URL",
        "JSON",
        "YAML",
        "XML",
        "HTML",
        "CSS",
        "CSV",
        "SVG",
        "PDF",
        "PNG",
        "UTC",
        "CPU",
        "GPU",
        "USD",
        "SSH",
        "MIT",
        "PRD",
        "MVP",
        "SQL",
        "ID",
        "UI",
    ],
    "platforms": ["Reddit", "YouTube", "LinkedIn"],
    "ordinary_words": [
        "Engine",
        "Review",
        "Pipelines",
        "Import",
        "Processor",
        "Selector",
        "Enricher",
        "Creator",
        "Fetcher",
        "Extractor",
        "Drafter",
        "Formatter",
        "Loader",
        "Triage",
        "Issue",
        "Scope",
        "Brief",
        "Team",
        "Collaboration",
        "Acceptance",
        "Context",
        "Goal",
        "Risks",
        "Priority",
        "Recency",
    ],
}

# Ordinary English words that may start a sentence in title case (the hub gate's list, English
# part). Anything else at a sentence start passes only if the page also writes it in lower case.
_COMMON_WORDS = """
a an the this that these those it its is are was were be been do does did done not no yes if
when then else so but and or nor for of in on at to by with without from into onto over under
about after before during while until since as than why what which who whom whose where how all
any each every some many most more less few other another such same only also just still even
very too here there now today use using used run running read reading write pick install
installing installed check checking open add get set make keep stop start see show find search
list build test tests tested try compare download upload create update remove delete send save
copy move change choose follow ask approve reject review import export clone fork apply our your
their his her my we you they he she i me us them one two three four five first second third
last next new old free open-source note step steps result results output input what's it's
don't can't won't isn't doesn't didn't capture captures expected measured claim claims nothing
everything everyone someone none public private warning ok unproven tip tips example examples
summary goal needs pitfalls sources limits caveats
"""
COMMON = frozenset(_COMMON_WORDS.split())

_CLIENT_WORDS = frozenset({"client", "clients"})
_TEMPLATE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
_OPEN_TAG = re.compile(r"\{\{|\}\}|\{%|%\}|\{#|#\}")
_CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.S)
_CODE_SPAN = re.compile(r"``[^\n]*?``|`[^`\n]*`")  # RST ``literal`` and markdown `code`
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+")
_PLACEHOLDER = re.compile(r"<[^<>\n]{1,60}>")
# a word: a letter, then letters/digits with inner apostrophes, hyphens, dots or underscores
_TOKEN = re.compile(r"[^\W\d_](?:[\w'’.+#/@-]*[\w+#])?")  # noqa: RUF001
_SENTENCE_END = re.compile(
    r"(?:^|[.!?:;\n¿¡]|\d\)|\s[-–—•*]\s|^[-–—•*]\s|(?:^|\n)[ \t]*(?:#{1,6}|>)"  # noqa: RUF001
    r"|(?:^|\s)[-*]\s+\[[ xX]\])"  # a markdown task item: - [ ] Each ...
    r"[\s\"'“”«»(\[]*$"
)
_MIN_WORD = 2
_SUFFIXES = ("'s", "’s", "s", "es")  # noqa: RUF001


class Unclear(ValueError):
    """The scrubber can't classify a text; the export must be refused."""


def _canon(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip().lower()  # noqa: RUF001


class PublicNames:
    """The allowlist: every phrase lower-cased, plus the single-word ones."""

    def __init__(self, groups: dict[str, list[str]]) -> None:
        names: set[str] = set()
        for group, items in groups.items():
            if not isinstance(items, list) or not all(
                isinstance(x, str) and x.strip() for x in items
            ):
                raise ValueError(f"bad public names: group {group!r} must be a list of names")
            names |= {_canon(x) for x in items}
        self.phrases = frozenset(names)
        self.singles = frozenset(n for n in names if " " not in n)

    def extend(self, groups: dict[str, list[str]]) -> PublicNames:
        extra = PublicNames(groups)  # validates; an error names the caller's group
        return PublicNames({"base": sorted(self.phrases), "extra": sorted(extra.phrases)})

    def word(self, tok: str) -> bool:
        w = _canon(tok)
        if w in self.singles:
            return True
        if "-" in w:  # "GitHub-issue-friendly": an allowlisted word with a lower-case tail
            head, tail = tok.split("-", 1)
            return tail == tail.lower() and self.word(head)
        return any(
            w.endswith(sfx) and w[: -len(sfx)] in self.singles and len(w) - len(sfx) >= _MIN_WORD
            for sfx in _SUFFIXES
        )


BUILTIN = PublicNames(BUILTIN_PUBLIC_NAMES)


def load_public_names(path: Path, base: PublicNames = BUILTIN) -> PublicNames:
    """The built-in allowlist extended with a ``public-names.yaml`` (group -> list of names)."""
    # PyYAML ships with uvicorn[standard], a CLI dependency; loaded only when a file is given
    yaml = importlib.import_module("yaml")

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("bad --public-names: not a readable YAML file") from exc
    if not isinstance(data, dict):
        raise ValueError("bad --public-names: must map a group to a list of names")
    try:
        return base.extend(data)
    except ValueError as exc:
        raise ValueError(f"bad --public-names: {exc}".replace("bad public names: ", "")) from exc


def _blank(text: str) -> str:
    """Template tags, code, URLs and placeholders become spaces (offsets and newlines stay)."""

    def spaces(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    for rx in (_TEMPLATE, _CODE_BLOCK, _CODE_SPAN, _URL, _PLACEHOLDER):
        text = rx.sub(spaces, text)
    return text


def _identifier(tok: str) -> bool:
    return bool(re.search(r"[\d_/@]|\w\.\w", tok))


Token = tuple[str, bool, int, int]  # text, at a sentence start, start, end


def _runs(text: str) -> list[list[Token]]:
    """Runs of adjacent capitalised words (joined by one space or " & ")."""
    clean = _blank(text)
    runs: list[list[Token]] = []
    prev_end: int | None = None
    for m in _TOKEN.finditer(clean):
        tok = m.group(0).rstrip(".'’-")  # noqa: RUF001
        gap = clean[prev_end : m.start()] if prev_end is not None else None
        prev_end = m.end()
        flag = m.start() > 0 and clean[m.start() - 1] == "-"  # a command-line option: jq -Rs
        if (
            flag
            or len(tok) < _MIN_WORD
            or not tok[0].isupper()
            or _identifier(tok)
            or _canon(tok) in _CLIENT_WORDS
        ):
            runs.append([])
            continue
        item = (
            tok,
            bool(_SENTENCE_END.search(clean[: m.start()])),
            m.start(),
            m.start() + len(tok),
        )
        if runs and runs[-1] and gap in (" ", " & "):
            runs[-1].append(item)
        else:
            runs.append([item])
    return [r for r in runs if r]


def _lower_words(texts: Iterable[str]) -> set[str]:
    return {t.lower() for text in texts for t in _TOKEN.findall(_blank(text)) if t[0].islower()}


class Scrubber:
    """Replace client names in texts, with page-wide evidence from ``context``."""

    def __init__(self, context: Iterable[str], public: PublicNames = BUILTIN) -> None:
        self.context = list(context)
        self.public = public
        self.lowered = _lower_words(self.context)
        self.proper = {
            run[k][0].lower()
            for text in self.context
            for run in _runs(text)
            for k, s in enumerate(self._status(run))
            if s == "cand"
        }

    def _status(self, run: list[Token]) -> list[str]:
        """Per token: allow (allowlisted), ord (ordinary), cand (a name) or maybe (see names)."""
        st: list[str] = []
        i = 0
        while i < len(run):
            for j in range(len(run), i, -1):
                phrase = re.sub(r"['’]s$", "", " ".join(t[0] for t in run[i:j]))  # noqa: RUF001
                if (j - i > 1 and _canon(phrase) in self.public.phrases) or (
                    j - i == 1 and self.public.word(phrase)
                ):
                    st += ["allow"] * (j - i)
                    i = j
                    break
            else:
                tok, initial = run[i][0], run[i][1]
                low = _canon(tok)
                ordinary = low in COMMON or low in self.lowered
                if tok.isupper():
                    st.append("ord" if ordinary else "cand")
                elif any(c.isupper() for c in tok[1:]) or not initial:
                    st.append("cand")
                else:
                    st.append("ord" if ordinary else "maybe")
                i += 1
        return st

    def spans(self, text: str) -> list[tuple[int, int]]:
        """(start, end) of every name in ``text``; adjacent names are one span."""
        out: list[tuple[int, int]] = []
        for run in _runs(text):
            st = self._status(run)
            for k in range(len(st) - 1, -1, -1):
                if st[k] == "maybe":
                    name = run[k][0].lower() in self.proper or (
                        k + 1 < len(st) and st[k + 1] == "cand"
                    )
                    st[k] = "cand" if name else "ord"
            cur: list[Token] = []
            for tok, s in [*zip(run, st, strict=True), (None, "end")]:
                if s == "cand" and tok is not None:
                    cur.append(tok)
                elif cur:
                    out.append((cur[0][2], cur[-1][3]))
                    cur = []
        return out

    def scrub(self, text: str) -> str:
        """``text`` with every name replaced by ``client``; ``Unclear`` when it can't tell."""
        if _OPEN_TAG.search(_TEMPLATE.sub("", text)):
            raise Unclear("unclosed template tag")
        out = text
        for start, end in sorted(self.spans(text), reverse=True):
            out = out[:start] + CLIENT + out[end:]
        if Scrubber([*self.context, out], self.public).spans(out):
            raise Unclear("a name is still there after scrubbing")
        return out
