"""Create a consistent online backup of the local SQLite database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workbench.maintenance import backup_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up the workbench database")
    parser.add_argument("--database", default="data/workbench.sqlite")
    parser.add_argument("--target", default="data/backups")
    parser.add_argument("--keep", type=int, default=14)
    args = parser.parse_args()
    try:
        result = backup_database(args.database, args.target, args.keep)
    except Exception as error:  # local CLI: report the cause and exit non-zero
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False))
    if result["integrity"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
