from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from workbench.local_loop import (
    RepoSnapshot,
    changed_paths,
    evaluate,
    load_state,
    render_report,
    run_once,
    save_state,
)


def sample(head: str = "a" * 40, status: tuple[str, ...] = ()) -> RepoSnapshot:
    return RepoSnapshot(
        path="D:/repo",
        branch="main",
        head=head,
        status=status,
        last_subject="test commit",
        ahead=2,
    )


class LocalLoopTests(unittest.TestCase):
    def test_digest_changes_with_status(self) -> None:
        self.assertNotEqual(sample().digest(), sample(status=(" M file.txt",)).digest())

    def test_render_report_contains_repo_and_head(self) -> None:
        report = render_report([sample()], ["D:/repo"], "2026-11-08T12:00:00+00:00")
        self.assertIn("D:/repo", report)
        self.assertIn("aaaaaaaaaaaa", report)
        self.assertIn("Read-only check", report)

    def test_evaluate_reports_first_run_and_skips_unchanged(self) -> None:
        snapshot = sample()
        should_report, changed = evaluate([snapshot], {})
        self.assertTrue(should_report)
        self.assertEqual(changed, ["D:/repo"])

        save_state(Path(tempfile.gettempdir()) / "local-loop-test-state.json", [snapshot])
        should_report, changed = evaluate([snapshot], {"D:/repo": snapshot.digest()})
        self.assertFalse(should_report)
        self.assertEqual(changed, [])

    def test_evaluate_force_and_error(self) -> None:
        error = RepoSnapshot("D:/missing", "unknown", "unknown", (), "", None, "not a repo")
        should_report, changed = evaluate([sample()], {"D:/repo": sample().digest()}, force=True)
        self.assertTrue(should_report)
        self.assertEqual(changed, [])
        should_report, _ = evaluate([error], {"D:/missing": error.digest()})
        self.assertTrue(should_report)

    def test_changed_paths_detects_added_and_removed(self) -> None:
        self.assertEqual(
            changed_paths({"a": "1", "b": "2"}, [sample()]),
            ["D:/repo", "a", "b"],
        )

    def test_run_once_writes_report_and_state_then_skips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            reports = root / "reports"
            with mock.patch("workbench.local_loop.snapshot_repo", return_value=sample()):
                first = run_once(["D:/repo"], state, reports)
                second = run_once(["D:/repo"], state, reports)
            self.assertTrue(first["reported"])
            self.assertTrue(Path(first["markdown"]).exists())
            self.assertTrue(Path(first["json"]).exists())
            self.assertFalse(second["reported"])
            payload = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(payload["repos"]["D:/repo"], sample().digest())
            self.assertEqual(load_state(state)["D:/repo"], sample().digest())


if __name__ == "__main__":
    unittest.main()
