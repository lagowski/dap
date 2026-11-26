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

For the canvas's node detail panel a node may also carry these OPTIONAL keys, each written only
when the bundle really holds the value (never a guess or an empty placeholder)::

    "agent": {"name", "model"?, "provider"?}      "prompt": {"system"?, "user"?} (<= 8000 each)
    "tools": [name] (<= 30, <= 80 chars)
    "output_fields": [{"name", "type"?, "description"?}]
    "gate_rule": str (gate nodes, <= 300)         "retries": 0-20      "max_loops": 0-50

Structural facts leave the bundle as they are: the pipeline name and description, node ids, agent
names, runtime ids, input/output field names, positions, edge conditions and the run defaults.
Prompts are now read too: the agent's ``prompt_template`` (the ``user`` prompt, Jinja left as it
is) and, for the ``agent`` runtime, its ``system_prompt``. Only these ``runtime_config`` keys are
ever read: ``model_id``, ``provider``, a ``github`` runtime's ``op`` and the ``agent`` runtime's
``system_prompt`` and tool names; never ``token_env``, ``shell``, ``repo``, ``body`` or any other.
Env var names and backend profiles are never read.

Every exported detail text (agent name, prompts, tool names, output descriptions, gate rule)
goes through the client-name scrubber (``hub_names``: a name not on the public allowlist becomes
``client``; a text it can't classify refuses the export) and then ``scan``, the last guard: the
export is refused if any text still holds a URL, IP address, email, absolute path, token or
private host name. A refusal names the field and the rule, never the value.
"""

from __future__ import annotations

import json
import re
from collections import deque
from datetime import date
from pathlib import Path
from typing import Any

from dap_cli.hub_names import BUILTIN, PublicNames, Scrubber, Unclear, load_public_names

SLUG = re.compile(r"^(?!g\d+-)[a-z0-9][a-z0-9-]{2,62}$")
PSEUDO_NODES = {"__start__", "__end__"}
MAX_TITLE, MAX_SUMMARY, MAX_ITEM, MAX_BODY, MAX_STEPS, MAX_LABEL = 200, 800, 500, 4000, 40, 80
MAX_TAGLINE, MAX_PURPOSE = 60, 90
MIN_TAGLINE_WORDS, MAX_TAGLINE_WORDS = 3, 6
# The hub importer's limits on the node detail keys.
MAX_PROMPT, MAX_TOOLS, MAX_TOOL, MAX_FIELDS, MAX_FIELD_DESC, MAX_GATE_RULE = (
    8000,
    30,
    80,
    40,
    200,
    300,
)
MAX_NAME, MAX_RETRIES, MAX_LOOPS = 200, 20, 50

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
# Page fields that hold machine data, not prose (ids, enums, dates, field names).
_MACHINE = re.compile(
    r"(?:^|\.)(?:schema|kind|slug|lang|generated|id|type|from|to|entry|inputs|outputs|io_icons"
    r"|name|model|provider|tools)(?:\[\d+\])*$"
)

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


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _config(agent: dict[str, Any]) -> dict[str, Any]:
    cfg = agent.get("runtime_config")
    return cfg if isinstance(cfg, dict) else {}


def _agent_detail(agent: dict[str, Any]) -> dict[str, str] | None:
    """Name, model and provider. ``model_id`` and ``provider`` are the only config keys read."""
    if (name := _str(agent.get("name"))) is None:
        return None
    out = {"name": name}
    cfg = _config(agent)
    for key, src in (("model", "model_id"), ("provider", "provider")):
        if (value := _str(cfg.get(src))) is not None and len(value) <= MAX_NAME:
            out[key] = value
    return out


def _prompt(agent: dict[str, Any]) -> dict[str, str] | None:
    """The one ``prompt_template`` as ``user``; ``system`` only for an agent runtime's own one."""
    out = {}
    if agent.get("runtime_id") == "agent" and (system := _str(_config(agent).get("system_prompt"))):
        out["system"] = system
    if (user := _str(agent.get("prompt_template"))) is not None:
        out["user"] = user
    return out or None


def _tools(agent: dict[str, Any]) -> list[str]:
    """Declared tool names: a ``github`` runtime's ``op``, an ``agent`` runtime's tool list."""
    cfg = _config(agent)
    runtime = agent.get("runtime_id")
    names: list[str | None] = []
    if runtime == "github":
        names = [_str(cfg.get("op"))]
    elif runtime == "agent" and isinstance(cfg.get("tools"), list):
        names = [
            _str(t.get("name")) or _str(t.get("runtime_id"))
            for t in cfg["tools"]
            if isinstance(t, dict)
        ]
    out: list[str] = []
    for name in names:  # an over-long name is dropped, not clipped into a name it isn't
        if name and len(name) <= MAX_TOOL and name not in out:
            out.append(name)
    return out[:MAX_TOOLS]


def _output_fields(agent: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in agent.get("output_schema") or []:
        field = {"name": raw} if isinstance(raw, str) else raw
        if not isinstance(field, dict):
            continue
        name = _str(field.get("name")) or _str(field.get("key"))
        if name is None or len(name) > MAX_NAME:
            continue
        item = {"name": name}
        if (ftype := _str(field.get("type"))) is not None and len(ftype) <= MAX_NAME:
            item["type"] = ftype
        if (desc := _str(field.get("description"))) is not None:
            item["description"] = desc  # scrubbed, then clipped, in _scrub_details
        out.append(item)
    return out[:MAX_FIELDS]


def _gate_rule(defaults: dict[str, Any]) -> str:
    """From ``approval_required_nodes`` (the engine pauses before the node) and its timeout."""
    rule = "The run pauses before this node until a person approves or rejects it in the dashboard."
    timeout = _count(defaults.get("gate_timeout_seconds"))
    if timeout is not None and timeout > 0:
        rule += f" With no answer within {timeout} seconds the run fails."
    return rule


def node_details(
    node: dict[str, Any], agent: dict[str, Any], defaults: dict[str, Any], gate: bool
) -> dict[str, Any]:
    """The detail-panel keys the bundle really holds (raw text: ``_scrub_details`` cleans it)."""
    out: dict[str, Any] = {}
    if (detail := _agent_detail(agent)) is not None:
        out["agent"] = detail
    if (prompt := _prompt(agent)) is not None:
        out["prompt"] = prompt
    if tools := _tools(agent):
        out["tools"] = tools
    if fields := _output_fields(agent):
        out["output_fields"] = fields
    if gate:
        out["gate_rule"] = _gate_rule(defaults)
    if (attempts := _count(defaults.get("max_attempts"))) is not None and attempts >= 1:
        out["retries"] = min(attempts - 1, MAX_RETRIES)
    retry = node.get("retry")
    if isinstance(retry, dict) and (loops := _count(retry.get("max_retries"))) is not None:
        out["max_loops"] = max(0, min(loops, MAX_LOOPS))
    return out


def _clip_lines(text: str, limit: int) -> str:
    """Clip a prompt keeping its lines (``_clip`` would fold it into one)."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# (path inside a node, limit, keep lines) of every detail text the scrubber cleans
_DETAIL_TEXTS: tuple[tuple[tuple[str, ...], int, bool], ...] = (
    (("agent", "name"), MAX_NAME, False),
    (("prompt", "system"), MAX_PROMPT, True),
    (("prompt", "user"), MAX_PROMPT, True),
    (("gate_rule",), MAX_GATE_RULE, False),
)


def _scrub_details(graph: dict[str, Any], scrubber: Scrubber) -> None:
    """Replace client names in every node's detail texts, then clip them to the hub limits."""

    def clean(text: str, where: str, limit: int, lines: bool) -> str:
        try:
            text = scrubber.scrub(text)
        except Unclear as exc:
            raise Unsafe(f"refused, {where}: client-name scrubber: {exc}") from exc
        return _clip_lines(text, limit) if lines else _clip(text, limit)

    for i, node in enumerate(graph["nodes"]):
        at = f"graph.nodes[{i}]"
        for path, limit, lines in _DETAIL_TEXTS:
            parent = node.get(path[0]) if len(path) > 1 else node
            key = path[-1]
            if isinstance(parent, dict) and isinstance(parent.get(key), str):
                parent[key] = clean(parent[key], f"{at}.{'.'.join(path)}", limit, lines)
        if "tools" in node:
            tools = [
                clean(t, f"{at}.tools[{k}]", MAX_TOOL, False) for k, t in enumerate(node["tools"])
            ]
            node["tools"] = list(dict.fromkeys(tools))
        for k, field in enumerate(node.get("output_fields", [])):
            if "description" in field:
                where = f"{at}.output_fields[{k}].description"
                field["description"] = clean(field["description"], where, MAX_FIELD_DESC, False)


def build_graph(pipeline: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
    defaults = pipeline.get("defaults") or {}
    gates = {str(g) for g in defaults.get("approval_required_nodes") or []}
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
        out |= node_details(node, agent, defaults, out["type"] == "gate")
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
    public_names: PublicNames | None = None,
) -> dict[str, Any]:
    """Build the hub page for one pipeline bundle.

    ``tagline5`` is the card's 3-6 word line, ``io`` the card's ``<in>:<out>`` icons,
    ``public_names`` the scrubber's allowlist (default: the built-in one).
    Raises ValueError on a bad slug, tagline or icon, and Unsafe on a guard hit or on a
    detail text the client-name scrubber can't classify.
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
    # The page's prose is the scrubber's evidence (a word in lower case elsewhere is ordinary).
    prose = [text for where, text in _strings(page, "") if not _MACHINE.search(where)]
    _scrub_details(graph, Scrubber(prose, public_names or BUILTIN))
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
    public_names: Path | None = None,
) -> Path:
    """Write ``<out_dir>/<slug>/page/page.json``. Nothing is written when the export is refused.

    ``public_names`` is a ``public-names.yaml`` that extends the built-in allowlist.
    """
    public = load_public_names(public_names) if public_names is not None else None
    page = to_hub_bundle(
        json.loads(Path(src).read_text()),
        slug,
        tagline5=tagline5,
        io=io,
        generated=generated,
        public_names=public,
    )
    target = Path(out_dir) / slug / "page" / "page.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(page, ensure_ascii=False, indent=1) + "\n")
    return target
