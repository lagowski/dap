"""Pipeline bundle -> Labs hub docbundle (#1016).

The hub's ``/pipelines`` pages take a tutorial bundle (docbundle schema 1, ``kind: pipeline``)
plus a ``graph`` field for its read-only canvas::

    {"entry": "<node id>",
     "nodes": [{"id", "title", "type", "purpose"?, "inputs", "outputs", "x", "y"}],
     "edges": [{"from", "to", "label"?}]}

and the card fields the hub requires on a pipeline page: ``tagline5`` (3-6 words) and
``io_icons: {in, out}`` from the closed ``IO_ICONS`` vocabulary. A node's ``type`` is ``gate``
for a human approval gate, ``llm`` for an agent on an ``LLM_RUNTIMES`` runtime, otherwise its
runtime id.

Only public, structural facts leave the bundle: the pipeline name and description, node ids, agent
names, runtime ids, input/output field names, positions, edge conditions and the run defaults.
Prompts, ``runtime_config``, env var names and backend profiles are never read. ``scan`` is a last
guard: the export is refused if any text still holds a URL, IP address, email, absolute path, token
or private host name. A refusal names the rule, never the value.
"""

from __future__ import annotations

import json
import re
from collections import deque
from datetime import date
from pathlib import Path
from typing import Any

SLUG = re.compile(r"^(?!g\d+-)[a-z0-9][a-z0-9-]{2,62}$")
PSEUDO_NODES = {"__start__", "__end__"}
MAX_TITLE, MAX_SUMMARY, MAX_ITEM, MAX_BODY, MAX_STEPS, MAX_LABEL = 200, 800, 500, 4000, 40, 80
MAX_TAGLINE, MAX_PURPOSE = 60, 90
MIN_TAGLINE_WORDS, MAX_TAGLINE_WORDS = 3, 6

# The hub's closed icon vocabulary for a pipeline card's input and output.
IO_SIDES = ("in", "out")
IO_ICONS = frozenset(
    [
        "bulb",
        "scale",
        "repo",
        "issue",
        "merge",
        "docs",
        "post",
        "search",
        "report",
        "script",
        "clapperboard",
        "blocks",
        "pipeline",
        "list",
        "fanout",
        "comment",
        "agents",
        "agreement",
        "endpoint",
        "pr",
        "review",
        "terminal",
        "engine",
    ]
)

# Runtimes whose node is a model call, from docs/runtimes.md and the adapter registry. A node's
# hub type is never guessed from its title.
LLM_RUNTIMES = frozenset({"api-call", "agent", "claude-code", "codex", "gemini-cli", "aider"})

_MARKUP = re.compile(r"[<>*_`\[\]{}|#~]")
_DOMAIN = re.compile(r"\b[\w-]+\.(?:com|org|net|io|dev|es|ai|app|md|json|ya?ml|txt|py|sh)\b", re.I)
_PATH = re.compile(r"\w/\w|(?<![\w.])\.{0,2}/\w")

RUNTIME_NEEDS: dict[str, str] = {
    "bash": "A shell on the engine host with the command-line tools the bash nodes call",
    "api-call": "An API key for an LLM provider, stored as an env var in DAP (never in the bundle)",
    "github": "A GitHub token that can read the repository and write to its issues, "
    "stored as an env var in DAP",
    "claude-code": "A coding-agent CLI installed on the engine host, and a git "
    "repository to work in",
    "codex": "A coding-agent CLI installed on the engine host, and a git repository to work in",
    "python-func": "A Python package with the node functions, installed in the engine's "
    "environment",
    "human": "Someone to approve or reject the human gates in the dashboard",
}

_OPS = {
    "eq": "==",
    "ne": "!=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "in": "in",
}

GUARD: dict[str, re.Pattern[str]] = {
    "url": re.compile(r"://|\bwww\.", re.I),
    "ip address": re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "absolute path": re.compile(
        r"(?<![\w.])(?:/home/|/Users/|/root/|/mnt/|/etc/|/var/|/tmp/|~/)|\b[A-Za-z]:\\"
    ),
    "token": re.compile(
        r"\b(?:ghp_|gho_|ghs_|github_pat_)[A-Za-z0-9_]{10,}"
        r"|\bsk-[A-Za-z0-9_-]{16,}|\bxox[abp]-|\bAKIA[0-9A-Z]{16}\b"
    ),
    "private host": re.compile(
        r"\b[\w-]+\.(?:internal|local|lan|corp|home\.arpa)\b|lagowski|dixter|rafeekpro", re.I
    ),
    "secret marker": re.compile(r"SOURCE-OF-TRUTH|VAULT_SYNC_SECRET|BEGIN [A-Z ]*PRIVATE KEY"),
}


class Unsafe(ValueError):
    """The generated bundle still holds something that must not be published."""


def check_tagline5(text: str) -> str:
    """Return the trimmed card tagline, or raise ValueError naming the broken rule."""
    tagline = text.strip()
    if "\n" in tagline or "\r" in tagline:
        raise ValueError("bad --tagline5: one line only")
    if not MIN_TAGLINE_WORDS <= len(tagline.split()) <= MAX_TAGLINE_WORDS:
        raise ValueError("bad --tagline5: 3-6 words")
    if len(tagline) > MAX_TAGLINE:
        raise ValueError(f"bad --tagline5: at most {MAX_TAGLINE} characters")
    if "..." in tagline or "…" in tagline:
        raise ValueError("bad --tagline5: no ellipsis")
    if _MARKUP.search(tagline):
        raise ValueError("bad --tagline5: plain text, no markup")
    if GUARD["url"].search(tagline) or _DOMAIN.search(tagline) or _PATH.search(tagline):
        raise ValueError("bad --tagline5: no link or path")
    return tagline


def parse_io(spec: str) -> dict[str, str]:
    """``"<in>:<out>"`` -> ``{"in", "out"}``; refuse a name outside ``IO_ICONS``."""
    parts = [p.strip() for p in spec.split(":")]
    if len(parts) != len(IO_SIDES) or not all(parts):
        raise ValueError("bad --io: expected <in>:<out>")
    for side, name in zip(IO_SIDES, parts, strict=True):
        if name not in IO_ICONS:
            raise ValueError(f"bad --io: unknown {side} icon; one of: {' '.join(sorted(IO_ICONS))}")
    return dict(zip(IO_SIDES, parts, strict=True))


def _purpose(agent: dict[str, Any]) -> str | None:
    """One line from the agent's description, or None (no description, or a link/path in it)."""
    raw = agent.get("description")
    if not isinstance(raw, str):
        return None
    line = next((ln for ln in raw.splitlines() if ln.strip()), "")
    text = _plain(line)
    if not text:
        return None
    if any(rx.search(text) for rx in GUARD.values()) or _DOMAIN.search(text) or _PATH.search(text):
        return None
    return _clip(text, MAX_PURPOSE)


def _strings(value: Any, where: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(where, value)]
    if isinstance(value, dict):
        return [
            s for k, v in value.items() for s in _strings(v, f"{where}.{k}" if where else str(k))
        ]
    if isinstance(value, list):
        return [s for i, v in enumerate(value) for s in _strings(v, f"{where}[{i}]")]
    return []


def scan(page: Any) -> list[str]:
    """Return ``"<field>: <rule>"`` per guard hit, empty when clean. Never echoes the value."""
    return [
        f"{where}: {rule}"
        for where, text in _strings(page, "")
        for rule, rx in GUARD.items()
        if rx.search(text)
    ]


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def _plain(text: str) -> str:
    """Drop repository issue references such as ``(#919)``: they mean nothing on the hub."""
    # Parenthesised references (also lists such as "(#1, #2)") go first, whole: stripping
    # the bare "#919" first would leave an empty "()" behind.
    text = re.sub(r"\s*\(\s*#\d+(?:\s*,\s*#\d+)*\s*\)", "", text)
    return re.sub(r"\s*#\d+\b", "", text).strip()


def _label(edge: dict[str, Any]) -> str | None:
    if edge.get("label"):
        return _clip(str(edge["label"]), MAX_LABEL)
    cond = edge.get("condition")
    if not isinstance(cond, dict) or "field" not in cond:
        return None
    field = str(cond["field"]).removeprefix("extensions.").removeprefix("state.")
    op = str(cond.get("operator") or cond.get("op") or "==")
    return _clip(f"{field} {_OPS.get(op, op)} {json.dumps(cond.get('value'))}", MAX_LABEL)


def _names(fields: Any) -> list[str]:
    out: list[str] = []
    for f in fields or []:
        name = f.get("name") if isinstance(f, dict) else f
        if isinstance(name, str) and name:
            out.append(name)
    return out


def _agent(node: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
    """The bundled agent a node runs, or ``{}`` (one lookup for the title, type and purpose)."""
    agent = agents.get(str(node.get("agent_id")))
    return agent if isinstance(agent, dict) else {}


def _runtime(node: dict[str, Any], agents: dict[str, Any]) -> str:
    return str(_agent(node, agents).get("runtime_id") or "unknown")


def node_type(node: dict[str, Any], agents: dict[str, Any], gates: set[str]) -> str:
    """``gate`` for a human approval gate, ``llm`` for a model call, else the runtime id."""
    if node["id"] in gates:
        return "gate"
    runtime = _runtime(node, agents)
    return "llm" if runtime in LLM_RUNTIMES else runtime


def build_graph(pipeline: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
    gates = {str(g) for g in (pipeline.get("defaults") or {}).get("approval_required_nodes") or []}
    nodes = []
    for node in pipeline["nodes"]:
        agent = _agent(node, agents)
        pos = node.get("position") or {}
        out: dict[str, Any] = {
            "id": node["id"],
            "title": _clip(_plain(agent.get("name") or node["id"]), MAX_TITLE),
            "type": node_type(node, agents, gates),
        }
        if (purpose := _purpose(agent)) is not None:
            out["purpose"] = purpose
        out |= {
            "inputs": _names(agent.get("input_schema")),
            "outputs": _names(agent.get("output_schema")),
            "x": int(pos.get("x", 0)),
            "y": int(pos.get("y", 0)),
        }
        nodes.append(out)
    edges = []
    for edge in pipeline["edges"]:
        if edge["source"] in PSEUDO_NODES or edge["target"] in PSEUDO_NODES:
            continue
        out = {"from": edge["source"], "to": edge["target"]}
        if (label := _label(edge)) is not None:
            out["label"] = label
        edges.append(out)
    return {"entry": pipeline["entry_point"], "nodes": nodes, "edges": edges}


def _walk(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Nodes in breadth-first order from the entry, then any unreachable ones in bundle order."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    seen: list[str] = []
    queue = deque([graph["entry"]])
    while queue:
        nid = queue.popleft()
        if nid in seen or nid not in by_id:
            continue
        seen.append(nid)
        queue.extend(e["to"] for e in graph["edges"] if e["from"] == nid)
    seen += [n["id"] for n in graph["nodes"] if n["id"] not in seen]
    return [by_id[i] for i in seen]


def _fields(names: list[str], empty: str) -> str:
    return ", ".join(f"`{n}`" for n in names) if names else empty


def _node_step(
    node: dict[str, Any], graph: dict[str, Any], titles: dict[str, str], runtime: str
) -> dict[str, str]:
    nexts = [
        titles.get(e["to"], e["to"]) + (f" (when {e['label']})" if "label" in e else "")
        for e in graph["edges"]
        if e["from"] == node["id"]
    ]
    body = (
        f"Runtime `{runtime}`. Reads "
        f"{_fields(node['inputs'], 'the shared run state')}. "
        f"Writes {_fields(node['outputs'], 'no named outputs')}. "
        f"Next: {'; '.join(nexts) if nexts else 'the end of the run'}."
    )
    return {
        "title": _clip(f"Node {node['id']}: {node['title']}", MAX_TITLE),
        "body": _clip(body, MAX_BODY),
    }


def _pitfalls(pipeline: dict[str, Any], graph: dict[str, Any]) -> list[str]:
    defaults = pipeline.get("defaults") or {}
    out = [
        "The providers and models in the bundle are examples: change them on each "
        "agent's page after importing.",
        "Keep tokens and API keys in DAP's env vars. Never paste them into the bundle or a prompt.",
    ]
    gates = [str(g) for g in defaults.get("approval_required_nodes") or []]
    if gates:
        out.append(
            f"The run pauses at the human approval gates {', '.join(gates)}: approve or "
            f"reject each one in the dashboard."
        )
    budget = defaults.get("budget_limit_usd")
    if isinstance(budget, int | float) and budget > 0:
        out.append(
            f"The run stops once it has spent {budget:g} USD on model calls "
            f"(budget_limit_usd in the pipeline defaults)."
        )
    attempts = defaults.get("max_attempts")
    if isinstance(attempts, int) and attempts > 1:
        out.append(f"A failing node is retried up to {attempts} times before the run fails.")
    if any("label" in e for e in graph["edges"]):
        out.append(
            "The next node is chosen from state fields, never by the model: when a run "
            "takes an unexpected path, "
            "check the fields on its branches."
        )
    return [_clip(p, MAX_ITEM) for p in out]


def to_hub_bundle(
    bundle: dict[str, Any],
    slug: str,
    *,
    tagline5: str,
    io: str,
    generated: str | None = None,
) -> dict[str, Any]:
    """Build the hub page for one pipeline bundle.

    ``tagline5`` is the card's 3-6 word line, ``io`` the card's ``<in>:<out>`` icons.
    Raises ValueError on a bad slug, tagline or icon, and Unsafe on a guard hit.
    """
    if not SLUG.match(slug):
        raise ValueError(
            "bad hub slug: lowercase letters, digits and dashes, 3-63 characters, not g<number>-"
        )
    tagline = check_tagline5(tagline5)
    io_icons = parse_io(io)
    pipeline = bundle["pipeline"]
    agents = bundle.get("bundled_agents") or {}
    graph = build_graph(pipeline, agents)
    runtime_of = {n["id"]: _runtime(n, agents) for n in pipeline["nodes"]}
    order = _walk(graph)
    titles = {n["id"]: n["title"] for n in graph["nodes"]}
    name = _clip(_plain(pipeline.get("name") or slug), MAX_TITLE)
    description = _plain(pipeline.get("description") or "") or f"The {name} pipeline for DAP."
    entry = titles.get(graph["entry"], graph["entry"])
    first_inputs = order[0]["inputs"] if order else []

    runtimes = sorted(set(runtime_of.values()))
    needs = ["A running DAP engine and dashboard (self-hosted, open source)"]
    needs += [
        RUNTIME_NEEDS.get(rt, f"The `{rt}` runtime available on the engine") for rt in runtimes
    ]

    steps = [
        {
            "title": "Get the bundle",
            "body": f"Download `{slug}.pipeline-bundle.json` from the example pipelines "
            "in DAP's public repository. "
            "It holds the pipeline and every agent it needs.",
        },
        {
            "title": "Import it into DAP",
            "body": "In the dashboard open Pipelines and click Import JSON, then pick "
            "the file. DAP creates the agents "
            "first, links each node to its new agent and opens the pipeline in the designer.",
        },
    ]
    node_steps = [_node_step(n, graph, titles, runtime_of[n["id"]]) for n in order]
    room = MAX_STEPS - len(steps) - 1
    if len(node_steps) > room:
        rest = node_steps[room - 1 :]
        node_steps = [
            *node_steps[: room - 1],
            {
                "title": "The remaining nodes",
                "body": _clip(" ".join(s["title"] + ". " + s["body"] for s in rest), MAX_BODY),
            },
        ]
    steps += node_steps
    steps.append(
        {
            "title": "Run it",
            "body": "Start a run from the pipeline's page and follow each node on the canvas. "
            + (
                f"The initial state must carry the fields the first node reads: "
                f"{_fields(first_inputs, '')}."
                if first_inputs
                else "The run ends when the last node finishes."
            ),
        }
    )

    page: dict[str, Any] = {
        "schema": 1,
        "kind": "pipeline",
        "slug": slug,
        "lang": "en",
        "title": name,
        "tagline5": tagline,
        "io_icons": io_icons,
        "summary": _clip(description, MAX_SUMMARY),
        "goal": _clip(
            f"Import the {name} pipeline into DAP and run it: {len(graph['nodes'])} "
            f"nodes, starting at {entry}.",
            MAX_SUMMARY,
        ),
        "needs": [_clip(n, MAX_ITEM) for n in needs][:20],
        "steps": steps,
        "pitfalls": _pitfalls(pipeline, graph)[:20],
        "sources": ["DAP example pipeline bundles"],
        "source_note": "A public example pipeline of DAP, converted by its hub exporter",
        "generated": generated or date.today().isoformat(),
        "graph": graph,
    }
    if hits := scan(page):
        raise Unsafe("refused, private material in: " + "; ".join(hits))
    return page


def write_hub_bundle(
    src: Path,
    slug: str,
    out_dir: Path,
    *,
    tagline5: str,
    io: str,
    generated: str | None = None,
) -> Path:
    """Write ``<out_dir>/<slug>/page/page.json``. Nothing is written when the export is refused."""
    page = to_hub_bundle(
        json.loads(Path(src).read_text()), slug, tagline5=tagline5, io=io, generated=generated
    )
    target = Path(out_dir) / slug / "page" / "page.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(page, ensure_ascii=False, indent=1) + "\n")
    return target
