import json
import tempfile
import unittest
from pathlib import Path

from workbench.status import parse_soak_log, readiness


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
