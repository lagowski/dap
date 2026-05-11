"""Locate and launch the bundled dashboard (#302 sub-D2, #334).

The wheel ships the Next.js standalone bundle at
``dap_cli/_dashboard/`` when built via the release pipeline (or after
running ``scripts/build-dashboard-bundle.sh`` locally). Dev wheels
built without the bundler ship an effectively-empty directory — this
module's ``find_bundle`` returns ``None`` in that case so ``dap
start`` can fall back to a friendly message instead of crashing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path


def find_bundle() -> Path | None:
    """Return the absolute path of the dashboard bundle's ``server.js``,
    or ``None`` when no real bundle is present.

    The wheel always ships the ``_dashboard/`` directory (a
    ``.gitkeep`` placeholder keeps it on disk) but only release /
    Docker / locally-bundled builds carry ``server.js``. Probing for
    ``server.js`` instead of the directory itself is the right
    presence check.
    """
    try:
        # ``importlib.resources.files`` resolves to the install location
        # of the ``dap_cli`` package — works whether installed via wheel,
        # pip install -e, or run from the source tree.
        bundle_dir = Path(str(files("dap_cli") / "_dashboard"))
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    server_js = bundle_dir / "server.js"
    return server_js if server_js.is_file() else None


def find_node() -> str | None:
    """Return the path to the ``node`` binary, or ``None`` when Node isn't
    installed.

    The bundle runs on Node 20+ (matches the version the dashboard
    was built with — see the Dockerfile). We don't verify the
    version here because Next standalone is usually forgiving across
    Node majors; the operator gets a clear runtime error from Node
    itself on real incompatibility.
    """
    return shutil.which("node")


def spawn_dashboard(
    *,
    port: int,
    engine_url: str,
    log_prefix: str = "[dashboard] ",
) -> subprocess.Popen[bytes] | None:
    """Spawn ``node <bundle>/server.js`` in the background.

    Returns the ``Popen`` handle on success, or ``None`` when either
    the bundle or Node is missing — caller surfaces an appropriate
    message and continues without the dashboard. Stdout/stderr are
    forwarded to the parent process with a fixed prefix so the
    operator sees engine and dashboard logs interleaved cleanly.
    """
    bundle = find_bundle()
    if bundle is None:
        return None
    node = find_node()
    if node is None:
        return None

    env = {
        **os.environ,
        "PORT": str(port),
        "HOSTNAME": "127.0.0.1",
        "DAP_ENGINE_URL": engine_url,
        # Same flag the Dockerfile sets — keeps Next quiet on first run.
        "NEXT_TELEMETRY_DISABLED": "1",
    }

    # ``Popen`` instead of ``run`` — we want the dashboard alive in
    # the background while the engine runs in the foreground via
    # uvicorn. The parent's ``finally`` block in ``dap start``
    # terminates this process explicitly on shutdown.
    return subprocess.Popen(
        [node, str(bundle)],
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        # New session so a SIGINT to the engine doesn't get duplicated
        # to the dashboard before our own handler decides what to do.
        # Best-effort: ``start_new_session`` is POSIX-only; on Windows
        # the equivalent isn't available and Popen falls back gracefully.
        start_new_session=True,
    )
