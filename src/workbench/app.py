"""Loopback-only HTTP workbench with no automatic delivery path."""

from __future__ import annotations

import argparse
import base64
import binascii
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from .guard import GuardPolicy, check_text
from .inference import GenerationUnavailable, generate_draft
from .media import MediaUnavailable, analyze_image
from .store import Store
from .status import inference_health, inference_test, parse_soak_log, readiness


ROOT = Path(__file__).resolve().parent


class WorkbenchHandler(BaseHTTPRequestHandler):
    store: Store
    policy = GuardPolicy()
    inference_url = "http://127.0.0.1:8081"
    inference_api_key = ""
    inference_backend = "local"
    soak_log = Path("data/soak-sycl-15m.out.log")

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _same_origin(self) -> bool:
        host = self.headers.get("Host", "")
        port = self.server.server_port
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 14_000_000:
            raise ValueError("request is too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _check_draft(self, draft_id: int, body: str):
        extracted_text, qr_payloads = self.store.media_inputs(draft_id)
        return check_text(body, self.policy, extracted_text=extracted_text, qr_payloads=qr_payloads)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/status":
            if not self._same_origin():
                self._json(HTTPStatus.FORBIDDEN, {"error": "origin rejected"})
                return
            soak = parse_soak_log(self.soak_log)
            inference = inference_health(self.inference_url, self.inference_api_key)
            inference["backend"] = self.inference_backend
            self._json(HTTPStatus.OK, {"readiness": readiness(soak, inference, self.inference_backend), "soak": soak, "inference": inference})
            return
        if self.path == "/":
            body = (ROOT / "static" / "index.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not self._same_origin():
            self._json(HTTPStatus.FORBIDDEN, {"error": "origin rejected"})
            return
        try:
            data = self._payload()
            if self.path == "/api/work":
                inbound = str(data["inbound"]).strip()
                draft = str(data["draft"]).strip()
                if not inbound or not draft:
                    raise ValueError("message and draft are required")
                key = sha256(inbound.encode()).hexdigest()
                draft_id = self.store.create_work(str(data.get("label", "手动会话")), str(data.get("language", "zh-CN")), inbound, key, draft)
                self._json(HTTPStatus.CREATED, {"id": draft_id, "mode": "manual"})
                return
            if self.path == "/api/system/inference-test":
                result = inference_test(self.inference_url, self.inference_api_key)
                self._json(HTTPStatus.OK if result["state"] == "passed" else HTTPStatus.SERVICE_UNAVAILABLE, result)
                return
            if self.path == "/api/suggest":
                inbound = str(data["inbound"]).strip()
                language = str(data.get("language", "zh-CN"))
                try:
                    result = generate_draft(self.inference_url, self.inference_api_key, inbound, language)
                except GenerationUnavailable as error:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
                    return
                self._json(HTTPStatus.OK, {**result, "mode": "suggested", "sent": False})
                return
            if self.path.startswith("/api/drafts/"):
                parts = urlparse(self.path).path.strip("/").split("/")
                draft_id = int(parts[2])
                action = parts[3]
                row = self.store.get_draft(draft_id)
                if row is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "draft not found"})
                    return
                if action == "check":
                    result = self._check_draft(draft_id, row["body"])
                    hits = [hit.__dict__ for hit in result.hits]
                    self.store.save_guard(draft_id, result.verdict, hits)
                    self._json(HTTPStatus.OK, {"verdict": result.verdict, "hits": hits})
                    return
                if action == "approve":
                    result = self._check_draft(draft_id, row["body"])
                    if result.verdict != "pass":
                        self._json(HTTPStatus.CONFLICT, {"error": "guard blocked draft"})
                        return
                    ok = self.store.approve(draft_id, int(data.get("revision", row["revision"])))
                    self._json(HTTPStatus.OK if ok else HTTPStatus.CONFLICT, {"approved": ok})
                    return
                if action == "media":
                    try:
                        content = base64.b64decode(str(data["content"]), validate=True)
                    except (KeyError, binascii.Error) as error:
                        raise ValueError("invalid image encoding") from error
                    analysis = analyze_image(content)
                    digest = sha256(content).hexdigest()
                    self.store.add_media_analysis(
                        draft_id,
                        str(data.get("filename", "image"))[:200],
                        digest,
                        analysis.extracted_text,
                        analysis.qr_payloads,
                        analysis.width,
                        analysis.height,
                    )
                    self._json(
                        HTTPStatus.OK,
                        {
                            "sha256": digest,
                            "extracted_text": analysis.extracted_text,
                            "qr_payloads": analysis.qr_payloads,
                            "width": analysis.width,
                            "height": analysis.height,
                        },
                    )
                    return
                if action == "copy":
                    body = self.store.mark_copied(draft_id)
                    self._json(HTTPStatus.OK if body is not None else HTTPStatus.CONFLICT, {"body": body, "sent": False})
                    return
            self._json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
        except MediaUnavailable as error:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(
    database: str | Path,
    port: int = 8765,
    *,
    inference_url: str = "http://127.0.0.1:8081",
    inference_api_key: str = "",
    inference_backend: str = "local",
    soak_log: str | Path = "data/soak-sycl-15m.out.log",
) -> ThreadingHTTPServer:
    handler = type(
        "BoundWorkbenchHandler",
        (WorkbenchHandler,),
        {
            "store": Store(database),
            "inference_url": inference_url,
            "inference_api_key": inference_api_key,
            "inference_backend": inference_backend,
            "soak_log": Path(soak_log),
        },
    )
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local manual-send workbench")
    parser.add_argument("--database", default="data/workbench.sqlite")
    parser.add_argument("--port", type=int, default=8765, choices=[8765])
    args = parser.parse_args()
    server = create_server(
        args.database,
        args.port,
        inference_url=os.environ.get("WORKBENCH_INFERENCE_URL", "http://127.0.0.1:8081"),
        inference_api_key=os.environ.get("WORKBENCH_INFERENCE_API_KEY", ""),
        inference_backend=os.environ.get("WORKBENCH_INFERENCE_BACKEND", "local"),
        soak_log=os.environ.get("WORKBENCH_SOAK_LOG", "data/soak-sycl-15m.out.log"),
    )
    print("Manual-send workbench: http://127.0.0.1:8765")
    server.serve_forever()


if __name__ == "__main__":
    main()

