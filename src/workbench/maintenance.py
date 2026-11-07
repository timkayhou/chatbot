"""Backup, log rotation and periodic local housekeeping."""

from __future__ import annotations

import gzip
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3


def backup_age_hours(target_dir: str | Path = "data/backups") -> float | None:
    directory = Path(target_dir)
    if not directory.exists():
        return None
    backups = sorted(
        (path for path in directory.glob("*.sqlite") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )
    if not backups:
        return None
    return (datetime.now().timestamp() - backups[-1].stat().st_mtime) / 3600


def backup_database(
    database: str | Path = "data/workbench.sqlite",
    target_dir: str | Path = "data/backups",
    keep: int = 14,
) -> dict:
    source = Path(database)
    if not source.exists():
        raise FileNotFoundError(f"database not found: {source}")
    destination_dir = Path(target_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{source.stem}-{datetime.now():%Y%m%d-%H%M%S}.sqlite"

    with closing(sqlite3.connect(source)) as source_db, closing(sqlite3.connect(destination)) as target_db:
        source_db.backup(target_db)
        integrity = target_db.execute("PRAGMA integrity_check").fetchone()[0]

    backups = sorted(
        destination_dir.glob(f"{source.stem}-*.sqlite"),
        key=lambda path: path.stat().st_mtime,
    )
    removed = []
    while len(backups) > max(1, keep):
        oldest = backups.pop(0)
        removed.append(oldest.name)
        oldest.unlink(missing_ok=True)

    return {
        "backup": str(destination),
        "bytes": destination.stat().st_size,
        "integrity": integrity,
        "removed": removed,
    }


def rotate_logs(directory: str | Path = "data", days: int = 30, keep_newest: int = 4) -> dict:
    data_dir = Path(directory)
    archive_dir = data_dir / "logs" / "archive"
    cutoff = datetime.now().timestamp() - days * 86400
    archived: list[str] = []
    skipped: list[str] = []
    candidates = sorted(
        (path for path in data_dir.glob("*.log") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates[keep_newest:]:
        if path.stat().st_mtime >= cutoff:
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        destination = archive_dir / f"{path.name}.{datetime.fromtimestamp(path.stat().st_mtime):%Y%m%d}.gz"
        try:
            with path.open("rb") as source, gzip.open(destination, "wb") as target:
                target.write(source.read())
            path.unlink()
            archived.append(str(destination))
        except OSError:
            skipped.append(str(path))
    return {"archived": archived, "skipped": skipped}


def maybe_backup(
    database: str | Path = "data/workbench.sqlite",
    target_dir: str | Path = "data/backups",
    keep: int = 14,
    max_age_hours: float = 24,
) -> dict | None:
    """Create a backup only when the newest one is missing or stale."""
    age = backup_age_hours(target_dir)
    if age is not None and age < max_age_hours:
        return None
    return backup_database(database, target_dir, keep)
