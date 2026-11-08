"""Loopback mock platform used to develop and test read-only adapters.

It only serves synthetic messages, requires a bearer token and rejects writes,
so it can stand in for a real platform during adapter development.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


FIXTURES = (
    {
        "message_id": "m-1001",
        "external_id": "demo-1",
        "customer_name": "演示客户甲",
        "body": "你好，笔记本开不了机，想预约检测。",
        "occurred_at": "2026-11-08T01:00:00+09:00",
        "locale": "zh-CN",
    },
    {
        "message_id": "m-1002",
        "external_id": "demo-1",
        "customer_name": "演示客户甲",
        "body": "型号是 ThinkPad X1 Carbon，电源灯不亮。",
        "occurred_at": "2026-11-08T01:03:00+09:00",
        "locale": "zh-CN",
    },
    {
        "message_id": "m-1003",
        "external_id": "demo-2",
        "customer_name": "演示客户乙",
        "body": "筆電風扇很大聲，可以到店檢查嗎？",
        "occurred_at": "2026-11-08T02:10:00+09:00",
        "locale": "zh-TW",
    },
    {
        "message_id": "m-1004",
        "external_id": "demo-3",
        "customer_name": "Demo Customer",
        "body": "Do you check laptops with a broken charging port?",
        "occurred_at": "2026-11-08T03:20:00+09:00",
        "locale": "en",
    },
)


class MockPlatformHandler(BaseHTTPRequestHandler):
    token = "test-token"
    fixtures = FIXTURES

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {self.token}"

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        if parsed.path == "/session":
            self._json(
                200,
                {
                    "state": "read_ok",
                    "detail": "mock platform ready",
                    "client_version": "mock-1.0",
                    "account": "demo",
                },
            )
            return
        if parsed.path == "/messages":
            query = parse_qs(parsed.query)
            since = max(0, int((query.get("since") or ["0"])[0]))
            limit = min(50, max(1, int((query.get("limit") or ["20"])[0])))
            window = list(self.fixtures[since : since + limit])
            self._json(200, {"messages": window, "cursor": since + len(window)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        self._json(405, {"error": "mock platform is read-only"})

    def log_message(self, format: str, *args: object) -> None:
        return


def create_mock_server(port: int = 0, token: str = "test-token") -> ThreadingHTTPServer:
    handler = type("BoundMockHandler", (MockPlatformHandler,), {"token": token})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
