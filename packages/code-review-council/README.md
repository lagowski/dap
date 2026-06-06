# code-review-council

Multi-agent code review with anti-hallucination filtering.

## Why

Single-agent LLM code reviewers tend to hallucinate when forced to find
issues outside their natural focus. Push a Gemini reviewer hard enough
and it'll flag "what if you ever add i18n", "what about CSP nonces",
"missing AAA accessibility" — concerns that don't apply to the project
under review.

Council fixes this by narrowing each agent's scope:

- **SecurityAgent** — auth, injection, secrets, XSS, CSRF, data exposure.
  Will never flag i18n or component naming, because that's outside its
  scope.
- **CorrectnessAgent** — logic bugs, race conditions, null handling,
  off-by-one, error handling. Will never flag style or perf.

Each agent runs against the diff with a narrow system prompt; findings
go through a **FinalArbiter** that deduplicates and drops low-confidence
items before the result reaches the PR.

The whole pipeline takes a ``ProjectContext`` so out-of-scope concerns
(this project doesn't use i18n / CSP / etc.) are baked into every agent's
prompt — the model never gets to flag them in the first place.

## Anti-hallucination knobs

1. **Confidence on every finding** — HIGH (verified in this code) /
   MEDIUM (likely) / LOW (hypothetical). LOW-confidence findings are
   filtered out unless severity is also NIT.
2. **Project context injection** — explicit "in scope" + "out of scope"
   lists per project. Out-of-scope findings are dropped at the agent
   level (the agent is instructed not to produce them) and again at the
   arbiter level (belt-and-braces).
3. **No "minimum N findings" rule** — empty findings is permitted. The
   prompt that previously forced agents to "find at least 3 per file"
   was the chief source of hallucination.
4. **review_complete signal** — agents and arbiter explicitly signal
   convergence when remaining concerns would be hypothetical.

## Usage

```python
import asyncio

from code_review_council import Council, ProjectContext
from code_review_council.providers import GeminiProvider

context = ProjectContext(
    stack=["Next.js 14 App Router", "TypeScript", "Python 3.13 / FastAPI"],
    in_scope=["auth", "data correctness", "real perf", "type safety"],
    out_of_scope=["i18n", "CSP", "AAA a11y", "RTL"],
    notes="shadcn defaults are fine; admin endpoints return 404 not 403.",
)

provider = GeminiProvider(api_key="...", model="gemini-2.5-flash")
council = Council(provider=provider, context=context)

with open("pr.diff") as f:
    diff = f.read()

verdict = asyncio.run(council.review(diff=diff, title="...", body="..."))
print(verdict.verdict, len(verdict.findings))
```

## MVP scope

Today's MVP ships:

- 2 agents (Security, Correctness)
- FinalArbiter with dedup + low-confidence filter
- GeminiProvider
- Sequential agent execution (parallel is a follow-up)
- Pydantic models for structured Gemini output

Out of scope for MVP:

- Performance / Architecture / TypeScript-specific agents
- Multi-provider (Claude / OpenAI) — current scaffolding accepts a
  ``BaseProvider`` so adding them is mechanical
- Async parallelism (``asyncio.gather``) — for two agents the latency
  improvement is small; do this once we have 5+ agents
- Per-agent prompt customization from disk

## Opt-in: Code Style specialist (spike, #633)

An experimental line-by-line **Code Style** specialist (naming clarity,
dead/duplicated code, readability, lint smells the auto-formatter won't
catch — all cited as file:line, held to LOW/NIT severity) is registered
in the package but **disabled by default**. The default roster is the
unchanged 5 specialists (Security / Correctness / Database / Performance
/ Frontend).

Enable it for evaluation by setting an env var at roster-build time:

```bash
COUNCIL_ENABLE_CODE_STYLE=1   # truthy: 1 / true / yes / on
```

When unset (or falsy), `default_agents()` returns the original roster
byte-for-byte — merging the spike does not add a 6th reviewer to any PR.
This is a spike pending live evaluation; do not enable it in CI until a
maintainer has confirmed the specialist produces useful citations.

## Integration

DAP's ``.github/workflows/gemini-review.yml`` uses this package via
``.github/scripts/gemini_review.py`` — the script becomes a thin
adapter that builds the ``ProjectContext`` and ``Council``, runs it,
and posts the result back to the PR.
