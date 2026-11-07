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


TEST_CHANNELS = [
    {"id": "test-channel", "name": "测试渠道", "accent": "#123456", "skin": "plain", "sort_order": 1},
]


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "app.sqlite"
        self.server = create_server(self.database, 0, channels=TEST_CHANNELS)
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

    def get(self, path):
        with urlopen(self.base + path) as response:
            return json.load(response)

    def store(self):
        return self.server.RequestHandlerClass.store

    def make_conversation(self, inbound="预约需要提前多久？"):
        store = self.store()
        customer = store.upsert_customer("test-channel", "u-1", "测试客户", tags=("新客户",), locale="zh-CN")
        conversation = store.create_chat_conversation("test-channel", customer, "测试客户", "zh-CN")
        store.add_message(conversation, "in", inbound, "mock")
        return conversation

    def test_channels_report_connection_state(self):
        channels = self.get("/api/channels")["channels"]
        self.assertEqual("simulated", channels[0]["state"])
        self.assertEqual("模拟", channels[0]["state_label"])
        self.assertTrue(channels[0]["detail"])

    def test_status_includes_alerts(self):
        data = self.get("/api/status")
        self.assertIn("alerts", data)
        self.assertIn("readiness", data)

    def test_chat_and_faq_pages_render(self):
        chat = urlopen(self.base + "/chat").read().decode()
        self.assertIn("模拟环境", chat)
        self.assertIn("AI 生成草稿", chat)
        faq = urlopen(self.base + "/faq").read().decode()
        self.assertIn("FAQ 知识库", faq)
        self.assertIn("从历史聊天生成候选", faq)

    def test_conversation_flow_is_simulated(self):
        conversation = self.make_conversation()
        channels = self.get("/api/channels")
        self.assertEqual("test-channel", channels["channels"][0]["id"])
        listing = self.get("/api/conversations?channel=test-channel")
        self.assertEqual(1, len(listing["conversations"]))
        self.assertEqual(1, listing["conversations"][0]["unread"])

        detail = self.get(f"/api/conversations/{conversation}")
        self.assertEqual("测试客户", detail["conversation"]["display_name"])
        self.assertEqual("in", detail["messages"][0]["direction"])

        reply = self.post(f"/api/conversations/{conversation}/messages", {"body": "您好，请稍等。"})
        self.assertFalse(reply["sent"])
        self.assertTrue(reply["simulated"])
        self.assertEqual("simulated", reply["message"]["source"])

        inbound = self.post(f"/api/conversations/{conversation}/inbound", {"body": "那明天呢？"})
        self.assertTrue(inbound["simulated"])
        self.assertEqual("mock", inbound["message"]["source"])

        self.post(f"/api/conversations/{conversation}/read", {})
        self.assertEqual(0, self.get("/api/conversations?channel=test-channel")["conversations"][0]["unread"])

        self.post(f"/api/conversations/{conversation}/takeover", {"enabled": True})
        detail = self.get(f"/api/conversations/{conversation}")
        self.assertEqual(1, detail["conversation"]["takeover"])

    def test_conversation_suggest_prefers_approved_faq(self):
        conversation = self.make_conversation("预约需要提前多久？")
        faq_id = self.store().create_faq(
            question="预约需要提前多久？",
            answer="一般提前一天预约即可。",
            reviewed=True,
        )
        with patch("workbench.app.generate_draft", side_effect=AssertionError("model must not run")):
            result = self.post(f"/api/conversations/{conversation}/suggest", {})
        self.assertEqual("faq", result["source"])
        self.assertEqual(faq_id, result["faq"]["id"])
        self.assertEqual("一般提前一天预约即可。", result["draft"])
        self.assertEqual("pass", result["guard"]["verdict"])

    def test_conversation_suggest_falls_back_to_model(self):
        conversation = self.make_conversation("笔记本很卡怎么办？")
        suggestion = {"draft": "建议先检查启动项和磁盘占用。", "seconds": 0.5, "tokens": 18}
        with patch("workbench.app.generate_draft", return_value=suggestion):
            result = self.post(f"/api/conversations/{conversation}/suggest", {})
        self.assertEqual("ai", result["source"])
        self.assertEqual("建议先检查启动项和磁盘占用。", result["draft"])

    def test_conversation_suggest_warns_on_changed_entities(self):
        conversation = self.make_conversation("电池更换报价是多少？")
        self.store().create_faq(
            question="电池更换报价是多少？",
            answer="电池更换费用为 ¥299。",
            languages=["en"],
            reviewed=True,
        )
        suggestion = {"draft": "电池更换费用为 299 元。", "seconds": 0.4, "tokens": 12}
        with patch("workbench.app.generate_draft", return_value=suggestion):
            result = self.post(f"/api/conversations/{conversation}/suggest", {})
        self.assertEqual("ai", result["source"])
        self.assertIn("¥299", result["entity_warnings"])

    def test_faq_crud_and_candidates(self):
        created = self.post("/api/faq", {"question": "到店要带什么？", "answer": "带设备和电源。", "languages": ["zh-CN"]})
        faq_id = created["id"]
        listing = self.get("/api/faq")
        self.assertEqual(1, listing["stats"]["total"])
        self.assertEqual(0, listing["stats"]["approved"])

        self.post(f"/api/faq/{faq_id}/approve", {})
        self.assertEqual(1, self.get("/api/faq")["stats"]["approved"])

        updated = self.post(
            f"/api/faq/{faq_id}/update",
            {"answer": "带设备、电源和保修卡。", "review": True},
        )
        self.assertTrue(updated["updated"])

        self.post(f"/api/faq/candidates/build", {})
        self.post(f"/api/faq/{faq_id}/delete", {})
        self.assertEqual(0, self.get("/api/faq")["stats"]["total"])

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
        self.assertEqual("pass", result["guard"]["verdict"])
        self.assertEqual(0, self.draft_count())
        self.assertEqual("上不了网", mocked.call_args.args[2])
        self.assertEqual("zh-CN", mocked.call_args.args[3])

    def test_suggest_prechecks_generated_text(self):
        suggestion = {"draft": "请访问 https://bad.example/path", "seconds": 1.0, "tokens": 10}
        with patch("workbench.app.generate_draft", return_value=suggestion):
            result = self.post("/api/suggest", {"inbound": "怎么联系", "language": "zh-CN"})
        self.assertEqual("block", result["guard"]["verdict"])
        self.assertIn("external.url", [hit["rule"] for hit in result["guard"]["hits"]])
        self.assertEqual(0, self.draft_count())

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

