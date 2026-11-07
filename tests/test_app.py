import base64
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from workbench.app import create_server

try:
    import qrcode
    MEDIA_TESTS = True
except ImportError:
    MEDIA_TESTS = False


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server(Path(self.temp.name) / "app.sqlite", 0)
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

