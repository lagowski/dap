"""The ``build`` section of a hub pipeline page: a minimal runner WITHOUT DAP (#1016 round 6).

A reader with no repository access rebuilds and runs the pipeline from the page alone:
``requirements`` (pinned), ``env`` (safe placeholders only, never a value), ``runner`` (one Python
file that wires the nodes in the bundle's edge order), ``steps`` (from an empty folder to a first
run) and ``dap`` (how to run the same bundle in DAP).

The runner mirrors what DAP does with each node, and nothing more:

- a prompt template is rendered with sandboxed Jinja (``StrictUndefined``) on the run state, the
  agent's ``role`` filled in, and projected on the agent's ``input_schema`` when it declares one;
- an ``api-call`` / ``agent`` node sends the rendered prompt to its provider's SDK
  (``call_model``); a ``bash`` node runs the ``<command>`` block of its rendered prompt with bash
  (``run_shell``); a ``github`` node makes its op's call with PyGithub (``github_call``);
- the JSON object in the reply's ``<output>`` block is merged into the state: a ``PipelineState``
  field at the top, any other key under ``extensions``;
- the next node is the first conditional edge whose condition holds, else the unconditional edge.

A runtime the runner can't reproduce on its own (a ``python-func`` callable, a coding-agent CLI)
becomes a function that stops with a message saying what to plug in. No endpoint is written into
the runner: the SDKs' default base URLs apply, and a provider without one reads its base URL from
an env var. The model's ``max_tokens`` is the runner's own default, as nothing else in
``runtime_config`` is read.
"""

from __future__ import annotations

import re
from typing import Any

from dap_types.state import PipelineState

MAX_RUNNER = 12000
RUNNER_FILE = "run_pipeline.py"
INPUT_FILE = "input.json"

# What the runner imports, pinned (Jinja2 / openai / anthropic as in DAP's own lock).
PINS: dict[str, str] = {
    "Jinja2": "3.1.6",
    "openai": "3.57.0",
    "anthropic": "2.11.0",
    "PyGithub": "2.6.1",
}

# Providers the runner calls through the OpenAI SDK, and the env var each one's key is read from.
OPENAI_FAMILY = frozenset({"openai", "glm", "openrouter", "openai-compat"})
KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "glm": "GLM_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai-compat": "MODEL_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
# Providers whose base URL is not the OpenAI SDK's default: the reader supplies it.
NEEDS_BASE_URL = frozenset({"glm", "openrouter", "openai-compat"})
BASE_URL_ENV = "MODEL_BASE_URL"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"

# The ``github`` runtime's ops: (params, state key the result is written to, the API call). The
# runner implements read_issue and comment; another op stops with a message naming it.
GITHUB_OPS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "read_issue": (("issue",), "github_issue", "GET /repos/{repo}/issues/{issue}"),
    "read_pr": (("pr",), "github_pr", "GET /repos/{repo}/pulls/{pr}"),
    "comment": (("issue", "body"), "github_comment", "POST /repos/{repo}/issues/{issue}/comments"),
    "update_issue_section": (
        ("issue", "section", "content"),
        "github_issue_section",
        "PATCH /repos/{repo}/issues/{issue}",
    ),
    "create_branch": (("branch", "base"), "github_branch", "POST /repos/{repo}/git/refs"),
    "open_pr": (
        ("head", "base", "title", "body", "draft"),
        "github_opened_pr",
        "POST /repos/{repo}/pulls",
    ),
    "merge_pr": (
        ("pr", "expected_head_sha", "method"),
        "github_merge",
        "PUT /repos/{repo}/pulls/{pr}/merge",
    ),
}

COMMAND = re.compile(r"<command>\s*(.*?)\s*</command>", re.S)
_GH_CLI = re.compile(r"(?:^|[\s;&|($])gh\s")


def command_of(agent: dict[str, Any]) -> str | None:
    """A bash node's command as the bundle holds it: ``runtime_config.command`` or ``<command>``."""
    raw = agent.get("runtime_config")
    cfg: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if isinstance(cfg.get("command"), str) and cfg["command"].strip():
        return str(cfg["command"]).strip()
    match = COMMAND.search(str(agent.get("prompt_template") or ""))
    return match.group(1) if match else None


def state_defaults() -> dict[str, Any]:
    """The run state's starting value: every ``PipelineState`` field at its default."""
    out: dict[str, Any] = {}
    for name, field in PipelineState.model_fields.items():
        out[name] = "" if field.is_required() else field.get_default(call_default_factory=True)
    out["run_id"] = "local"
    return out


def node_spec(node_id: str, agent: dict[str, Any], text: dict[str, str]) -> dict[str, Any]:
    """What the runner needs for one node. ``text`` holds its scrubbed templates."""
    runtime = str(agent.get("runtime_id") or "unknown")
    raw = agent.get("runtime_config")
    cfg: dict[str, Any] = raw if isinstance(raw, dict) else {}
    spec: dict[str, Any] = {"runtime": runtime, "role": str(agent.get("role") or "")}
    names = [f.get("name") if isinstance(f, dict) else f for f in agent.get("input_schema") or []]
    spec["inputs"] = [n for n in names if isinstance(n, str)]
    outs = [f.get("name") if isinstance(f, dict) else f for f in agent.get("output_schema") or []]
    spec["outputs"] = [n for n in outs if isinstance(n, str)]
    if runtime == "github":
        op = str(cfg.get("op") or "")
        spec["op"] = op
        spec["repo"] = text.get("repo", "{{ state.repo }}")
        spec["params"] = {k[6:]: v for k, v in text.items() if k.startswith("param:")}
        spec["outputs"] = [GITHUB_OPS[op][1]] if op in GITHUB_OPS else spec["outputs"]
    elif "prompt" in text:
        spec["prompt"] = text["prompt"]
    if "system" in text:
        spec["system"] = text["system"]
    if runtime in {"api-call", "agent"}:
        spec["provider"] = str(cfg.get("provider") or "")
        spec["model"] = str(cfg.get("model_id") or "")
    if runtime == "bash" and "command" in text:
        spec["command"] = text["command"]
    timeout = agent.get("timeout_ms")
    if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0:
        spec["timeout"] = timeout / 1000
    return spec


def _literal(text: str) -> str:
    """A string literal that keeps a prompt readable: raw triple quotes when it can."""
    if '"""' not in text and not text.endswith(("\\", '"')) and "\r" not in text:
        return 'r"""' + text + '"""'
    return repr(text)


def _compact(value: Any) -> str:
    """A dict of dicts, one entry per line (pprint would wrap and re-quote every value)."""
    rows = [f"    {key!r}: {item!r}," for key, item in value.items()]
    return "{\n" + "\n".join(rows) + "\n}"


def runner_source(
    name: str,
    entry: str,
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    gates: list[str],
) -> str:
    """The runner file, with only the parts its nodes use.

    Raises ValueError when it would exceed the hub's size limit.
    """
    moved = ("prompt", "system", "command")
    texts = {k: {f: n[f] for f in moved if f in n} for k, n in nodes.items()}
    slim = {k: {f: v for f, v in n.items() if f not in moved} for k, n in nodes.items()}
    prompt_rows = [
        f"    ({node!r}, {field!r}): {_literal(text)},"
        for node, fields in texts.items()
        for field, text in fields.items()
    ]
    providers = {str(n["provider"]) for n in nodes.values() if n.get("provider")}
    runtimes = {n["runtime"] for n in nodes.values()}
    used = {
        "anthropic": "anthropic" in providers,
        "openai": bool(providers & OPENAI_FAMILY),
        "github": "github" in runtimes,
        "bash": "bash" in runtimes,
        "conditions": any(e.get("condition") for e in edges),
        "gates": bool(gates),
    }
    lines: list[str] = []
    keep = [True]
    for line in _TEMPLATE.splitlines():
        if line.startswith("#{/"):
            keep.pop()
        elif line.startswith("#{"):
            keep.append(keep[-1] and used[line[2:-1]])
        elif keep[-1]:
            lines.append(line)
    body = "\n".join(lines) + "\n"
    for key, value in (
        ("__NAME__", name.replace('"', "'")),
        ("__ENTRY__", repr(entry)),
        ("__GATES__", repr(gates)),
        ("__KEY_ENV__", repr({p: KEY_ENV[p] for p in sorted(providers & set(KEY_ENV))})),
        ("__STATE__", repr(state_defaults())),
        ("__NODES__", _compact(slim)),
        ("__TEXTS__", "{\n" + "\n".join(prompt_rows) + "\n}"),
        ("__EDGES__", "[\n" + "\n".join(f"    {e!r}," for e in edges) + "\n]"),
    ):
        body = body.replace(key, value)
    if len(body) > MAX_RUNNER:
        raise ValueError(f"refused, build.runner.source: over {MAX_RUNNER} characters")
    return body


def requirements(nodes: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    want = ["Jinja2"]
    providers = {n.get("provider") for n in nodes.values()}
    if providers & OPENAI_FAMILY:
        want.append("openai")
    if "anthropic" in providers:
        want.append("anthropic")
    if any(n["runtime"] == "github" for n in nodes.values()):
        want.append("PyGithub")
    return [{"package": p, "version": PINS[p]} for p in want]


def env_vars(nodes: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    gh_cli = any(_GH_CLI.search(n.get("command", "") + n.get("prompt", "")) for n in nodes.values())
    if gh_cli or any(n["runtime"] == "github" for n in nodes.values()):
        out.append(
            {
                "name": GITHUB_TOKEN_ENV,
                "placeholder": "<your GitHub token>",
                "description": "A GitHub token that can read the repository and write to its "
                "issues, read by PyGithub and the gh CLI",
            }
        )
    providers = sorted({str(n["provider"]) for n in nodes.values() if n.get("provider")})
    for provider in providers:
        if provider in KEY_ENV:
            out.append(
                {
                    "name": KEY_ENV[provider],
                    "placeholder": f"<your {provider} API key>",
                    "description": f"The API key the {provider} model calls use",
                }
            )
    if set(providers) & NEEDS_BASE_URL:
        out.append(
            {
                "name": BASE_URL_ENV,
                "placeholder": "<your provider's OpenAI-compatible base URL>",
                "description": "The base URL of the model API, from the provider's documentation",
            }
        )
    return out


def build_steps(
    reqs: list[dict[str, str]],
    env: list[dict[str, str]],
    bash: bool,
    example_input: str | None,
    expected_output: str | None,
) -> list[dict[str, str]]:
    """From an empty folder to a first run; the example step only with a synthetic example."""
    pins = " ".join(f"{r['package']}=={r['version']}" for r in reqs)
    steps = [
        {
            "title": "Make an empty folder with a virtual environment",
            "body": "Python 3.11 or newer.",
            "command": "mkdir my-pipeline && cd my-pipeline && python3 -m venv .venv "
            "&& . .venv/bin/activate",
        },
        {
            "title": "Install the requirements",
            "body": "The pinned packages the runner imports.",
            "command": f"pip install {pins}",
        },
        {
            "title": "Save the runner",
            "body": f"Save the runner on this page as {RUNNER_FILE} in the folder.",
        },
    ]
    if bash:
        steps.append(
            {
                "title": "Install the command-line tools",
                "body": "The bash nodes run their commands with bash in the current folder: "
                "install every tool their code calls (see each node's code) and run the "
                "pipeline where those commands expect to run.",
            }
        )
    if env:
        steps.append(
            {
                "title": "Set the env vars",
                "body": "Replace each placeholder with your own value. Never commit it.",
                "command": "\n".join(f'export {e["name"]}="{e["placeholder"]}"' for e in env),
            }
        )
    run = {
        "title": "Run it",
        "body": f"The runner reads the initial state from {INPUT_FILE} and prints the "
        "fields the run wrote.",
        "command": f"python {RUNNER_FILE} {INPUT_FILE}",
    }
    if example_input is not None:
        steps.append(
            {
                "title": "Write the example input",
                "body": "The page's synthetic example input: the fields the first node reads.",
                "command": f"cat > {INPUT_FILE} <<'EOF'\n{example_input}\nEOF",
            }
        )
        if expected_output is not None:
            run["expected_output"] = expected_output
    else:
        steps.append(
            {
                "title": "Write the input",
                "body": f"Write the initial state as a JSON object in {INPUT_FILE}: the "
                "fields the first node reads.",
            }
        )
    steps.append(run)
    return steps


_TEMPLATE = """\"\"\"Runner for the __NAME__ pipeline, without DAP (made by the DAP hub export).

Usage: python run_pipeline.py input.json [--yes]   (--yes approves every human gate)
\"\"\"

import json
import os
import re
#{bash}
import subprocess
#{/bash}
import sys

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

ENTRY = __ENTRY__
#{gates}
GATES = __GATES__
#{/gates}
KEY_ENV = __KEY_ENV__
MAX_TOKENS = 4096
OUTPUT = re.compile(r"<output>\\s*(.*?)\\s*</output>", re.S)
FENCE = re.compile(r"```(?:json)?\\s*(.*?)\\s*```", re.S)
#{bash}
COMMAND = re.compile(r"<command>\\s*(.*?)\\s*</command>", re.S)
#{/bash}
JINJA = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
STATE = __STATE__
NODES = __NODES__
TEXTS = __TEXTS__
EDGES = __EDGES__


def render(template, context):
    return JINJA.from_string(template).render(**context)


def call_model(provider, model, system, prompt):
    \"\"\"One model call through the provider's SDK; returns the reply text.\"\"\"
#{anthropic}
    if provider == "anthropic":
        import anthropic

        extra = {"system": system} if system else {}
        reply = anthropic.Anthropic().messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            **extra,
        )
        return "".join(b.text for b in reply.content if b.type == "text")
#{/anthropic}
#{openai}
    if provider in KEY_ENV:
        import openai

        api = openai.OpenAI(
            api_key=os.environ[KEY_ENV[provider]], base_url=os.environ.get("MODEL_BASE_URL") or None
        )
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        reply = api.chat.completions.create(model=model, max_tokens=MAX_TOKENS, messages=messages)
        return reply.choices[0].message.content or ""
#{/openai}
    raise NotImplementedError(f"provider {provider}: call its SDK here")
#{github}


def github_call(op, repo, params):
    \"\"\"One GitHub op through PyGithub (its default API base URL).\"\"\"
    from github import Auth, Github

    gh = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))
    issue = gh.get_repo(repo).get_issue(int(params["issue"]))
    if op == "read_issue":
        labels = [label.name for label in issue.labels]
        return {"number": issue.number, "title": issue.title, "body": issue.body or "",
                "state": issue.state, "labels": labels, "is_pull_request": bool(issue.pull_request)}
    if op == "comment":
        return {"id": issue.create_comment(params["body"]).id, "issue": issue.number}
    raise NotImplementedError(f"github op {op}: make its API call here")
#{/github}
#{bash}


def run_shell(command, timeout):
    done = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"command exited with code {done.returncode}: {done.stderr[-500:]}")
    return done.stdout
#{/bash}


def parse_output(text):
    match = OUTPUT.search(text) or FENCE.search(text)
    value = json.loads(match.group(1)) if match else None
    if not isinstance(value, dict):
        raise ValueError("the reply holds no <output> block with a JSON object")
    return value


def run_node(node_id, state):
    \"\"\"Run one node; return the fields it writes.\"\"\"
    node, text = NODES[node_id], lambda key: TEXTS.get((node_id, key))
    runtime = node["runtime"]
#{github}
    if runtime == "github":
        params = {k: render(v, {"state": state}) for k, v in node["params"].items()}
        result = github_call(node["op"], render(node["repo"], {"state": state}), params)
        return {node["outputs"][0]: result}
#{/github}
    context = {k: state[k] for k in node["inputs"] if k in state} if node["inputs"] else dict(state)
    context.setdefault("role", node["role"])
    if runtime in ("api-call", "agent"):
        prompt = render(text("prompt") or "", context)
        reply = call_model(node["provider"], node["model"], text("system"), prompt)
#{bash}
    elif runtime == "bash":
        match = COMMAND.search(render(text("prompt") or "", context))
        command = render(text("command"), context) if text("command") else match and match.group(1)
        if not command:
            raise ValueError(f"node {node_id}: no <command> block in its prompt")
        reply = run_shell(command, node.get("timeout"))
#{/bash}
    else:
        raise NotImplementedError(f"node {node_id}: plug in what the {runtime} runtime does")
    out = parse_output(reply)
    return {k: v for k, v in out.items() if not node["outputs"] or k in node["outputs"]}
#{conditions}


def holds(cond, state):
    kind = cond.get("type", "comparison")
    if kind in ("and", "or"):
        return (all if kind == "and" else any)(holds(c, state) for c in cond["children"])
    if kind == "not":
        return not holds(cond["children"][0], state)
    actual = state
    for part in cond["field"].split("."):
        if not isinstance(actual, dict) or part not in actual:
            return False
        actual = actual[part]
    op = cond.get("operator") or cond.get("op")
    expected = cond.get("value")
    try:
        if op in ("==", "eq"):
            return actual == expected
        if op in ("!=", "ne", "neq"):
            return actual != expected
        if op == "in":
            return actual in expected
        return {"<": actual < expected, "<=": actual <= expected, ">": actual > expected,
                ">=": actual >= expected, "lt": actual < expected, "lte": actual <= expected,
                "gt": actual > expected, "gte": actual >= expected}[op]
    except TypeError:
        return False
#{/conditions}


def next_node(node_id, state):
    outgoing = [e for e in EDGES if e["from"] == node_id]
#{conditions}
    for edge in outgoing:
        if edge.get("condition") and holds(edge["condition"], state):
            return edge["to"]
#{/conditions}
    plain = [e["to"] for e in outgoing if not e.get("condition")]
    return plain[0] if plain else None


def run(initial, approve=False, max_steps=100):
    state = json.loads(json.dumps(STATE))
    for key, value in initial.items():
        if key == "extensions":
            state["extensions"].update(value)
        else:
            state[key] = value
    written, node_id = {}, ENTRY
    for _ in range(max_steps):
        if node_id in (None, "__end__"):
            return written
#{gates}
        if node_id in GATES and not approve and input(f"approve {node_id}? [y/N] ") != "y":
            raise SystemExit(f"rejected at the gate before {node_id}")
#{/gates}
        for key, value in run_node(node_id, state).items():
            (state if key in STATE else state["extensions"])[key] = value
            written[key] = value
        node_id = next_node(node_id, state)
    raise RuntimeError(f"stopped after {max_steps} nodes")


def main(argv):
    path = next((a for a in argv[1:] if not a.startswith("--")), "input.json")
    with open(path) as fh:
        initial = json.load(fh)
    print(json.dumps(run(initial, approve="--yes" in argv), indent=1, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv)
"""
