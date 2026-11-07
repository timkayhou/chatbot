"""Archive old local log files without touching open handles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workbench.maintenance import rotate_logs


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate old local logs")
    parser.add_argument("--directory", default="data")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--keep-newest", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(rotate_logs(args.directory, args.days, args.keep_newest), ensure_ascii=False))


if __name__ == "__main__":
    main()
