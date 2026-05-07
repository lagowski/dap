"""Plugin loader — auto-discovery and sentinel resolution for cortex/plugins/.

Discovers enabled plugins under ``cortex/plugins/*/plugin.yaml``, merges their
agent configs into the core config, resolves the ``__PLUGIN_ENTRY__`` and
``runs_to_plugin_exit`` sentinels against real attachment-point agents, and
splices the graph edges so traffic routes through plugin agents.

Multiple plugins on the same ``attach_after`` are chained in alphabetical
order by plugin name for deterministic ordering.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

_PLUGIN_ENTRY_SENTINEL = "__PLUGIN_ENTRY__"


def load_plugins(
    base_configs: dict[str, dict],
    plugins_dir: Path | None = None,
) -> dict[str, dict]:
    """Discover and merge all enabled plugins into base_configs.

    Args:
        base_configs: Core agent config dict (from agents.yaml).
        plugins_dir: Override for testing. Defaults to cortex/plugins/.

    Returns:
        Merged config dict with plugin agents inserted and attachment
        points spliced. base_configs is NOT mutated.
    """
    if plugins_dir is None:
        plugins_dir = Path(__file__).parent

    configs = copy.deepcopy(base_configs)

    # Discover plugins — sorted alphabetically for deterministic chaining
    plugins: list[tuple[str, dict, dict]] = []  # (name, meta, agents)
    for plugin_dir in sorted(plugins_dir.iterdir()):
        if not plugin_dir.is_dir():
            continue
        plugin_yaml = plugin_dir / "plugin.yaml"
        if not plugin_yaml.exists():
            continue
        with open(plugin_yaml) as f:
            meta = yaml.safe_load(f)
        if not meta.get("enabled", False):
            continue

        # Standalone plugins have their own graph and CLI command — they
        # don't attach to the main pipeline, so skip them here.
        if meta.get("standalone", False):
            continue

        agents_yaml = plugin_dir / "agents.yaml"
        if not agents_yaml.exists():
            continue
        with open(agents_yaml) as f:
            plugin_agents = yaml.safe_load(f)
        if not isinstance(plugin_agents, dict):
            continue

        plugins.append((meta["name"], meta, plugin_agents))

    if not plugins:
        return configs

    # Group plugins by attach_after for chaining
    by_attach: dict[str, list[tuple[str, dict, dict]]] = {}
    for name, meta, agents in plugins:
        attach_after = meta["attach_after"]
        by_attach.setdefault(attach_after, []).append((name, meta, agents))

    for attach_after, group in by_attach.items():
        # Validate attach_after exists in base_configs
        if attach_after not in configs:
            plugin_names = ", ".join(n for n, _, _ in group)
            raise ValueError(
                f"Plugin(s) [{plugin_names}] declare attach_after={attach_after!r} "
                f"but no agent named {attach_after!r} exists in the base config"
            )

        # Chain plugins: attach_after → plugin_A → plugin_B → ... → attach_before
        prev_last_agent: str | None = None

        for i, (name, meta, plugin_agents) in enumerate(group):
            attach_before = meta["attach_before"]
            if attach_before not in configs and attach_before not in plugin_agents:
                # Check if it will be added by a later plugin in the same group
                found = False
                for _, _, future_agents in group[i + 1 :]:
                    if attach_before in future_agents:
                        found = True
                        break
                if not found:
                    raise ValueError(
                        f"Plugin {name!r} declares attach_before={attach_before!r} "
                        f"but no agent named {attach_before!r} exists in the base config"
                    )

            # Detect duplicate agent names
            for agent_name in plugin_agents:
                if agent_name in configs:
                    raise ValueError(
                        f"Plugin {name!r} defines agent {agent_name!r} which "
                        f"already exists in the config"
                    )

            # Determine the ordered list of agents in this plugin
            agent_names = list(plugin_agents.keys())
            first_agent = agent_names[0]
            last_agent = agent_names[-1]

            # Resolve __PLUGIN_ENTRY__ sentinel on the first agent
            entry_source = prev_last_agent if prev_last_agent else attach_after
            agent_cfg = plugin_agents[first_agent]
            if "runs_after" in agent_cfg:
                agent_cfg["runs_after"] = [
                    entry_source if v == _PLUGIN_ENTRY_SENTINEL else v
                    for v in agent_cfg["runs_after"]
                ]

            # Resolve runs_to_plugin_exit sentinel on the last agent
            last_cfg = plugin_agents[last_agent]
            if last_cfg.get("runs_to_plugin_exit"):
                del last_cfg["runs_to_plugin_exit"]
                # Only add attach_before edge if this is the last plugin in the chain
                is_last_in_chain = i == len(group) - 1
                if is_last_in_chain:
                    # The attach_before edge is set via splice below
                    pass

            # Merge plugin agents into configs
            configs.update(plugin_agents)
            prev_last_agent = last_agent

        # Splice: update attach_before's runs_after to point to the last plugin agent
        # instead of the original attach_after
        final_last_agent = prev_last_agent
        attach_before = group[-1][1]["attach_before"]
        if attach_before in configs:
            before_cfg = configs[attach_before]
            if "runs_after" in before_cfg:
                before_cfg["runs_after"] = [
                    final_last_agent if v == attach_after else v
                    for v in before_cfg["runs_after"]
                ]

    return configs
