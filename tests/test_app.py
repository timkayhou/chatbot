import base64
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from workbench.app import create_server
from workbench.inference import GenerationUnavailable

try:
    import qrcode
    MEDIA_TESTS = True
except ImportError:
    MEDIA_TESTS = False


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "app.sqlite"
        self.server = create_server(self.database, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def test_page_declares_manual_mode(self):
        page = urlopen(self.base + "/").read().decode()
        self.assertIn("手动模式", page)
        self.assertIn("不会自动发送", page)
        self.assertIn("AI 生成草稿", page)

    def test_rejects_unexpected_host(self):
        request = Request(self.base + "/api/work", data=b"{}", method="POST", headers={"Host": "evil.test"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(403, caught.exception.code)
        caught.exception.close()

    def post(self, path, payload):
        request = Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": self.base},
        )
        with urlopen(request) as response:
            return json.load(response)

    def post_error(self, path, payload):
        request = Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": self.base},
        )
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def draft_count(self):
        connection = sqlite3.connect(self.database)
        try:
            return connection.execute("SELECT count(*) FROM drafts").fetchone()[0]
        finally:
            connection.close()

    def test_suggest_returns_draft_without_persisting(self):
        suggestion = {"draft": "您好，请先重启路由器。", "seconds": 1.2, "tokens": 20}
        with patch("workbench.app.generate_draft", return_value=suggestion) as mocked:
            result = self.post("/api/suggest", {"inbound": "上不了网", "language": "zh-CN"})
        self.assertEqual("suggested", result["mode"])
        self.assertFalse(result["sent"])
        self.assertEqual("您好，请先重启路由器。", result["draft"])
        self.assertEqual(0, self.draft_count())
        self.assertEqual("上不了网", mocked.call_args.args[2])
        self.assertEqual("zh-CN", mocked.call_args.args[3])

    def test_suggest_requires_inbound(self):
        status, _ = self.post_error("/api/suggest", {"language": "zh-CN"})
        self.assertEqual(400, status)

    def test_suggest_rejects_unknown_language(self):
        with patch("workbench.app.generate_draft", side_effect=ValueError("unsupported language")):
            status, body = self.post_error("/api/suggest", {"inbound": "hi", "language": "fr"})
        self.assertEqual(400, status)
        self.assertIn("language", body["error"])

    def test_suggest_reports_model_offline(self):
        with patch("workbench.app.generate_draft", side_effect=GenerationUnavailable("offline")):
            status, _ = self.post_error("/api/suggest", {"inbound": "hi", "language": "zh-CN"})
        self.assertEqual(503, status)

    def test_manual_workflow_never_reports_sent(self):
        created = self.post("/api/work", {"inbound": "需要帮助", "draft": "您好，我来协助您。"})
        draft_id = created["id"]
        self.assertEqual("manual", created["mode"])
        self.assertEqual("pass", self.post(f"/api/drafts/{draft_id}/check", {})["verdict"])
        self.assertTrue(self.post(f"/api/drafts/{draft_id}/approve", {"revision": 1})["approved"])
        copied = self.post(f"/api/drafts/{draft_id}/copy", {})
        self.assertFalse(copied["sent"])
        self.assertEqual("您好，我来协助您。", copied["body"])

    @unittest.skipUnless(MEDIA_TESTS, "media test extras are not installed")
    def test_real_qr_attachment_blocks_approval(self):
        created = self.post("/api/work", {"inbound": "question", "draft": "safe answer"})
        image = qrcode.make("https://blocked.example/path")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        media = self.post(
            f"/api/drafts/{created['id']}/media",
            {"filename": "code.png", "content": base64.b64encode(stream.getvalue()).decode()},
        )
        self.assertIn("https://blocked.example/path", media["qr_payloads"])
        checked = self.post(f"/api/drafts/{created['id']}/check", {})
        self.assertEqual("block", checked["verdict"])
        self.assertTrue(any(hit["rule"] == "media.qr" for hit in checked["hits"]))


if __name__ == "__main__":
    unittest.main()

