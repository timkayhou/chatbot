import json
import tempfile
import unittest
from pathlib import Path

from workbench.status import alerts, parse_soak_log, readiness


class StatusTests(unittest.TestCase):
    def test_parses_latest_soak_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "soak.log"
            path.write_text(
                '\n'.join((
                    json.dumps({"event": "heartbeat", "elapsed_seconds": 60, "runs": 20, "failures": 0}),
                    json.dumps({"event": "heartbeat", "elapsed_seconds": 120, "runs": 42, "failures": 1, "last_seconds": 2.4}),
                )),
                encoding="utf-8",
            )
            result = parse_soak_log(path)
            self.assertEqual("failed", result["state"])
            self.assertEqual(42, result["runs"])
            self.assertEqual(1, result["failures"])

    def test_readiness_never_claims_formal_operation(self):
        result = readiness(
            {"state": "passed", "runs": 100, "failures": 0},
            {"state": "ready", "detail": "ok"},
        )
        self.assertFalse(result["formal_ready"])
        self.assertTrue(any(item["state"] == "todo" for item in result["items"]))

    def test_alerts_list_local_risks(self):
        items = alerts(
            inference={"state": "offline"},
            soak={"state": "failed"},
            backup_age_hours=None,
            db_bytes=2_000_000_000,
            pending_candidates=3,
        )
        texts = " ".join(item["text"] for item in items)
        self.assertIn("AI 服务不可用", texts)
        self.assertIn("稳定性测试出现失败", texts)
        self.assertIn("尚未创建数据库备份", texts)
        self.assertIn("1 GB", texts)
        self.assertIn("3 条 FAQ 候选", texts)
        self.assertTrue(all(item["level"] in {"danger", "warning", "info"} for item in items))

    def test_alerts_are_empty_when_healthy(self):
        items = alerts(
            inference={"state": "ready"},
            soak={"state": "passed"},
            backup_age_hours=2,
            db_bytes=1_000_000,
        )
        self.assertEqual([], items)

    def test_long_soak_unlocks_release_gate(self):
        short = readiness(
            {"state": "passed", "runs": 139, "failures": 0, "elapsed_seconds": 904},
            {"state": "ready", "detail": "ok"},
        )
        gate = next(item for item in short["items"] if item["id"] == "release_soak")
        self.assertEqual("todo", gate["state"])

        long = readiness(
            {"state": "passed", "runs": 800, "failures": 0, "elapsed_seconds": 7200},
            {"state": "ready", "detail": "ok"},
        )
        gate = next(item for item in long["items"] if item["id"] == "release_soak")
        self.assertEqual("passed", gate["state"])
        self.assertEqual(6, long["ready_count"])

    def test_drafting_readiness_follows_inference(self):
        ready = readiness(
            {"state": "passed", "runs": 100, "failures": 0},
            {"state": "ready", "detail": "ok"},
        )
        drafting = next(item for item in ready["items"] if item["id"] == "drafting")
        self.assertEqual("ready", drafting["state"])

        offline = readiness(
            {"state": "not_started", "runs": 0, "failures": 0},
            {"state": "offline", "detail": "connection refused"},
        )
        drafting = next(item for item in offline["items"] if item["id"] == "drafting")
        self.assertEqual("attention", drafting["state"])

    def test_operations_reports_implemented_but_unverified_state(self):
        result = readiness(
            {"state": "passed", "runs": 800, "failures": 0, "elapsed_seconds": 7200},
            {"state": "ready", "detail": "ok"},
        )
        operations = next(item for item in result["items"] if item["id"] == "operations")
        self.assertEqual("attention", operations["state"])
        self.assertIn("已实现", operations["detail"])
        self.assertIn("待完成", operations["detail"])

    def test_failure_after_heartbeat_marks_soak_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "soak.log"
            path.write_text(
                '\n'.join((
                    json.dumps({"event": "heartbeat", "elapsed_seconds": 60, "runs": 20, "failures": 0}),
                    json.dumps({"event": "failure", "run": 21, "error": "server stopped"}),
                )),
                encoding="utf-8",
            )
            result = parse_soak_log(path)
            self.assertEqual("failed", result["state"])
            self.assertEqual(21, result["runs"])
            self.assertEqual(1, result["failures"])


if __name__ == "__main__":
    unittest.main()
