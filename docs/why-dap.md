# Why DAP

Design notes: the problem DAP was built for, the idea behind it, and the decisions that
shaped it. Every date below is the commit date of the change on `develop` (check it with
`git log --date=short`), and every `#N` is the pull request that made it. Written
2026-11-27.

## Problem

In spring 2026, putting a coding agent to work on a real repository meant handing it
control of the whole job. Three things went wrong over and over:

- **The agent chose its own path.** The same task could plan, edit, test and review in a
  different order on every run, or skip a step without saying so. Nobody could say ahead
  of time which steps a run would take.
- **Cost had no upper bound.** An agent that decides when it is done can also decide it is
  never done. A loop of retries or tool calls kept spending until someone noticed.
- **There was no replay.** When a run went wrong, the evidence was a chat transcript. There
  was no record of which step ran, with which prompt, producing which output, and no way
  to resume from the step that failed.

## Idea

> A pipeline is a versioned DAG of agents. The state machine decides what runs next,
> never the model.

The model still does the work inside each step: it writes the code, the spec or the
review. What it does not get to choose is the order of the steps, how many times one
repeats, or whether a human has to look before the next one starts. Those are edges and
conditions in a graph that is written down, versioned, and the same on every run.

In practice:

- Each node is one agent: a Jinja template compiled to an XML prompt, sent to one runtime
  adapter (an LLM SDK, a coding-agent CLI, a shell command, a Python function, HTTP).
- LangGraph walks the graph. Retries are bounded by `max_attempts`; a run that hits the
  bound fails, it does not loop.
- Every node's prompt, output, tokens and cost are stored, and runs are checkpointed, so a
  run can be paused, resumed, retried from a node or aborted.
- An approval gate stops the run before a node until a person approves it.

## Timeline

| Date | What | Proof |
|---|---|---|
| 2026-04-24 | First commit: the monorepo skeleton. | `965f363` |
| 2026-04-25 | Stack moves from TypeScript/Node to Python + uv. | #2 |
| 2026-04-25 | Jinja → XML prompt compiler; first runtime adapter (Anthropic SDK); LangGraph pipeline execution; first dashboard. | #14, #16, #18, #20 |
| 2026-04-25 | Pause and resume runs through LangGraph checkpoints. | #28 |
| 2026-04-27 | Coding-agent CLIs as runtimes: claude-code, gemini-cli, codex. | #73, #74, #78 |
| 2026-04-28 | `POST /agents/dry-run`: run one agent without persisting it, capped by a server-side budget. | #106 |
| 2026-05-04 | PostgreSQL backend next to SQLite; the in-process `python-func` runtime. | #150, #149 |
| 2026-05-05 | Human approval gates as LangGraph interrupts, with an approve endpoint. | #165 |
| 2026-05-10 | Multi-user: password login, GitHub/Google OAuth, API tokens. | #304, #305, #306 |
| 2026-05-12 | v0.3.0, the first multi-user release (PyPI, container image, GitHub Release). | tag `v0.3.0`, `CHANGELOG.md` |
| 2026-05-16 | The code review council: several narrow reviewer agents instead of one broad one. | #447, #448 |
| 2026-11-22 | The agent tool loop: one node may call other runtimes as tools, bounded by turns and budget. | #1007 |
| 2026-11-23 – 2026-11-25 | The tool loop on OpenAI, OpenRouter and Gemini. | #1012, #1015 |
| 2026-11-26 – 2026-11-27 | Tool loop hardening: protected input keys refused, closed tool schemas, plain HTTP body. | #1023, #1029, #1039 |

## Design decisions

**Python + uv, one day in (#2).** The first commit was a TypeScript/Node skeleton. The
next day the stack moved to Python: LangGraph, the provider SDKs and most agent tooling
are Python first. The dashboard stayed in TypeScript (Next.js) and talks to the engine over
REST, with its API types generated from the engine's OpenAPI schema.

**The graph decides, LangGraph runs it (#18, #28).** DAP does not write its own scheduler.
LangGraph supplies the state machine and the checkpoints; DAP's job is to compile a
versioned pipeline into a graph and keep the model out of the routing.

**Runtime adapters, not one model client (#16, #73, #74, #78).** A node does not call "the
LLM"; it calls a runtime. Coding-agent CLIs were in from the first week, because most of
the real work is done by those tools and DAP's value is the discipline around them. The
README lists the current set.

**Humans at fixed points (#165).** The pipeline names the nodes that need approval
(`approval_required_nodes`); the model cannot ask for a gate or skip one. The run stops there, waits, and fails if nobody approves within the gate
timeout.

**Bounded cost.** Retries stop at `max_attempts`. The agent tool loop stops at `max_turns`
and `budget_usd` and reports which limit it hit (#1007). The dry-run endpoint refuses an
agent whose budget is over a server cap (#106). Cost is recorded per node.

**Self-hosted and multi-user (#150, #304–#306, v0.3.0).** DAP runs where the code is.
SQLite by default, PostgreSQL when `DAP_DATABASE_URL` is set; accounts, OAuth and API
tokens so a team can share one instance.

**A review council instead of one reviewer (#447, #448).** One reviewer pushed to find
problems invents some outside its focus. The council gives each agent one narrow scope
(security, correctness and so on) and filters findings before reporting them
(`packages/code-review-council`).

**Tools inside a node, still bounded (#1007, #1023, #1029, #1039).** Some steps need an
agent that calls tools in a loop. The agent runtime allows that inside one node: the model
picks tools, but only those the node lists, with keys it may not override, and within a
turn and budget limit. The graph around it stays deterministic.

## Scope

DAP is maintained with a focused scope:

- **In scope:** a deterministic runner around coding agents (versioned pipelines,
  runtime adapters, approval gates, bounded retries and cost, a record of every node),
  the dashboard and CLI to drive it, and the code review council.
- **Out of scope:** a generic agent framework, and a visual workflow builder that competes
  with n8n or Dify. The dashboard's pipeline designer edits DAP pipelines; it is not
  meant to become a general automation tool.

A small, finished-looking feature set is deliberate. Changes that keep the runner
deterministic and the record complete are welcome; see [CONTRIBUTING.md](../CONTRIBUTING.md).
