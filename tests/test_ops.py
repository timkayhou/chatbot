import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from workbench.maintenance import backup_database as backup
from workbench.maintenance import maybe_backup
from workbench.maintenance import rotate_logs as rotate


class BackupTests(unittest.TestCase):
    def test_backup_copies_rows_and_keeps_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "workbench.sqlite"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
                connection.execute("INSERT INTO items(name) VALUES('a'),('b')")
                connection.commit()

            target = Path(directory) / "backups"
            first = backup(database, target, keep=2)
            self.assertEqual("ok", first["integrity"])
            copy = Path(first["backup"])
            with closing(sqlite3.connect(copy)) as connection:
                self.assertEqual(2, connection.execute("SELECT count(*) FROM items").fetchone()[0])

            for name in ("older.sqlite", "oldest.sqlite"):
                path = target / f"workbench-{name}"
                path.write_bytes(b"x")
                os.utime(path, (time.time() - 600, time.time() - 600))
            result = backup(database, target, keep=2)
            remaining = sorted(path.name for path in target.glob("workbench-*.sqlite"))
            self.assertEqual(2, len(remaining))
            self.assertIn(Path(result["backup"]).name, remaining)

    def test_maybe_backup_only_when_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "workbench.sqlite"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
                connection.commit()
            target = Path(directory) / "backups"
            self.assertIsNotNone(maybe_backup(database, target, max_age_hours=24))
            self.assertIsNone(maybe_backup(database, target, max_age_hours=24))


class RotateTests(unittest.TestCase):
    def test_old_logs_are_gzipped_and_kept_logs_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            names = [f"soak-{index}.out.log" for index in range(6)]
            old = time.time() - 40 * 86400
            for index, name in enumerate(names):
                path = data / name
                path.write_text('{"event": "heartbeat"}\n', encoding="utf-8")
                os.utime(path, (old - index, old - index))
            result = rotate(data, days=30, keep_newest=2)
            archived = [Path(path).name for path in result["archived"]]
            self.assertEqual(4, len(archived))
            self.assertEqual(2, len(list(data.glob("*.log"))))
            self.assertTrue(all(name.endswith(".gz") for name in archived))


if __name__ == "__main__":
    unittest.main()
