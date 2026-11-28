from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select a deterministic shard of pytest files.",
    )
    parser.add_argument("root", type=Path, help="Directory containing test files")
    parser.add_argument("--total", type=int, required=True, help="Total shard count")
    parser.add_argument("--index", type=int, required=True, help="Zero-based shard index")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.total < 1:
        raise SystemExit("--total must be >= 1")
    if args.index < 0 or args.index >= args.total:
        raise SystemExit("--index must be between 0 and --total - 1")

    files = sorted(
        {
            path
            for pattern in ("test_*.py", "*_test.py")
            for path in args.root.rglob(pattern)
            if path.is_file()
        }
    )
    shard = [path for offset, path in enumerate(files) if offset % args.total == args.index]
    if not shard:
        raise SystemExit(f"no test files selected for shard {args.index}/{args.total}")
    for path in shard:
        print(path)


if __name__ == "__main__":
    main()
