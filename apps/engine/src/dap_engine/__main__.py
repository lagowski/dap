"""Standalone entrypoint: `uv run dap-engine` lub `python -m dap_engine`."""

from __future__ import annotations

import logging
import os

import uvicorn

from dap_engine.app import EngineConfig, create_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    config = EngineConfig(
        db_path=os.environ.get("DAP_DB_PATH", "./.dap/state.db"),
        host=os.environ.get("DAP_ENGINE_HOST", "127.0.0.1"),
        port=int(os.environ.get("DAP_ENGINE_PORT", "7333")),
        dry_run_budget_usd=float(os.environ.get("DAP_DRY_RUN_BUDGET_USD", "0.50")),
    )

    app = create_app(config)

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
