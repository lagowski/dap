#!/usr/bin/env python3
"""Export the engine FastAPI OpenAPI schema as deterministic JSON."""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Path to write openapi.json")
    args = parser.parse_args()

    warnings.simplefilter("ignore")

    from dap_engine.app import create_app  # noqa: PLC0415

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
