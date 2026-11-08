"""Run the read-only local snapshot loop once or at a low-frequency interval."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workbench.local_loop import run_once


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Read-only repository snapshot loop")
    parser.add_argument("--repo", action="append", dest="repos", help="Repository path; repeatable")
    parser.add_argument("--state", default="data/local-loop-state.json")
    parser.add_argument("--report-dir", default="data/local-loop")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between checks when looping")
    parser.add_argument("--once", action="store_true", help="Check once and exit")
    parser.add_argument("--force", action="store_true", help="Write a report even when unchanged")
    args = parser.parse_args()

    repos = args.repos or [str(root), str(root.parent / "my-site")]
    while True:
        result = run_once(repos, args.state, args.report_dir, force=args.force)
        print(json.dumps(result, ensure_ascii=False))
        if args.once or result["errors"]:
            raise SystemExit(1 if result["errors"] else 0)
        args.force = False
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    main()
