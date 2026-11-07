import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from workbench.inference import GenerationUnavailable, build_messages, generate_draft


class StubHandler(BaseHTTPRequestHandler):
    payloads: list[dict] = []
    status = 200
    body: dict = {}

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        type(self).payloads.append(json.loads(self.rfile.read(length)))
        encoded = json.dumps(type(self).body).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


class InferenceTests(unittest.TestCase):
    def setUp(self):
        StubHandler.payloads = []
        StubHandler.status = 200
        StubHandler.body = {"choices": [{"message": {"content": "  您好，请先检查无线开关。  "}}], "usage": {"completion_tokens": 12}}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_build_messages_uses_requested_language(self):
        messages = build_messages("Wi-Fi 连不上", "zh-TW")
        self.assertEqual("system", messages[0]["role"])
        self.assertIn("繁体", messages[1]["content"])
        self.assertIn("Wi-Fi 连不上", messages[1]["content"])

    def test_build_messages_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            build_messages("你好", "fr")
        with self.assertRaises(ValueError):
            build_messages("   ", "zh-CN")

    def test_generate_draft_disables_thinking_and_strips_text(self):
        result = generate_draft(self.base, "key", "无线网连不上", "zh-CN")
        self.assertEqual("您好，请先检查无线开关。", result["draft"])
        self.assertEqual(12, result["tokens"])
        payload = StubHandler.payloads[-1]
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(320, payload["max_tokens"])

    def test_generate_draft_removes_reasoning_block(self):
        StubHandler.body = {
            "choices": [{"message": {"content": "<think>先分析。</think>请重启路由器。"}}],
            "usage": {"completion_tokens": 8},
        }
        result = generate_draft(self.base, "key", "上不了网", "zh-CN")
        self.assertEqual("请重启路由器。", result["draft"])

    def test_server_error_raises_generation_unavailable(self):
        StubHandler.status = 500
        StubHandler.body = {"error": "boom"}
        with self.assertRaises(GenerationUnavailable):
            generate_draft(self.base, "key", "上不了网", "zh-CN")

    def test_empty_draft_raises_generation_unavailable(self):
        StubHandler.body = {"choices": [{"message": {"content": "<think>只有思考</think>"}}]}
        with self.assertRaises(GenerationUnavailable):
            generate_draft(self.base, "key", "上不了网", "zh-CN")

    def test_unreachable_server_raises_generation_unavailable(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        with self.assertRaises(GenerationUnavailable):
            generate_draft(self.base, "key", "上不了网", "zh-CN", timeout=2)


if __name__ == "__main__":
    unittest.main()
