"""Verify a SQLite backup by restoring it into a disposable directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workbench.maintenance import verify_backup_restore


def newest_backup(directory: str | Path) -> Path:
    backups = sorted(Path(directory).glob("*.sqlite"), key=lambda path: path.stat().st_mtime)
    if not backups:
        raise FileNotFoundError(f"no SQLite backups found in: {directory}")
    return backups[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore and verify a workbench backup in a temporary directory")
    parser.add_argument("backup", nargs="?", help="backup file; defaults to the newest file in --directory")
    parser.add_argument("--directory", default="data/backups")
    args = parser.parse_args()
    try:
        backup = Path(args.backup) if args.backup else newest_backup(args.directory)
        result = verify_backup_restore(backup)
    except Exception as error:  # local CLI: report the cause and exit non-zero
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False))
    if result["integrity"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
