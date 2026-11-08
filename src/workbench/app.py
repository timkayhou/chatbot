"""Loopback-only workbench, simulated chat console and FAQ knowledge base."""

from __future__ import annotations

import argparse
import base64
import binascii
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import time
import threading
from urllib.parse import parse_qs, urlparse

from .adapters import STATE_LABELS, ConnectionManager
from .channels import load_channels
from .entities import missing_entities
from .guard import GuardPolicy, check_text
from .inference import GenerationUnavailable, generate_draft
from .maintenance import backup_age_hours, maybe_backup, rotate_logs
from .media import MediaUnavailable, analyze_image
from .policies import GENERIC, PlatformPolicy, guard_for, load_policies
from .store import Store
from .status import alerts, inference_health, inference_test, parse_soak_log, readiness


ROOT = Path(__file__).resolve().parent
PAGES = {"/": "index.html", "/chat": "chat.html", "/faq": "faq.html"}
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'"


class WorkbenchHandler(BaseHTTPRequestHandler):
    store: Store
    policy = GuardPolicy()
    inference_url = "http://127.0.0.1:8081"
    inference_api_key = ""
    inference_backend = "local"
    soak_log = Path("data/soak-sycl-15m.out.log")
    connection_manager = ConnectionManager()
    platform_policies: dict[str, PlatformPolicy] = {}
    channel_guards: dict[str, GuardPolicy] = {}

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _page(self, name: str) -> None:
        body = (ROOT / "static" / name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
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

    def _text(self, value: object, limit: int = 2000) -> str:
        text = str(value or "").strip()
        if len(text) > limit:
            raise ValueError("field is too long")
        return text

    def _draft(self, inbound: str, language: str, policy: GuardPolicy | None = None) -> dict:
        """FAQ first, model second; either way only a draft is returned."""
        faq = self.store.match_faq(inbound, language)
        reference: str | None = None
        if faq and faq.get("language_supported"):
            result: dict = {"draft": faq["answer"], "seconds": 0.0, "tokens": 0}
            source = "faq"
        else:
            reference = faq["answer"] if faq else None
            result = generate_draft(
                self.inference_url,
                self.inference_api_key,
                inbound,
                language,
                reference=reference,
            )
            source = "ai"
        checked = check_text(result["draft"], policy or self.policy)
        result["guard"] = {"verdict": checked.verdict, "hits": [hit.__dict__ for hit in checked.hits]}
        result["source"] = source
        result["faq"] = faq
        result["sent"] = False
        if reference:
            missing = missing_entities(reference, result["draft"])
            if missing:
                result["entity_warnings"] = missing
        return result

    # --- read-only API ---------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self._same_origin():
                self._json(HTTPStatus.FORBIDDEN, {"error": "origin rejected"})
                return
            try:
                self._get_api(parsed.path, parsed.query)
            except (KeyError, ValueError, TypeError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        page = PAGES.get(parsed.path)
        if page:
            self._page(page)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _backup_age_hours(self) -> float | None:
        return backup_age_hours("data/backups")

    def _get_api(self, path: str, query: str) -> None:
        if path == "/api/status":
            soak = parse_soak_log(self.soak_log)
            inference = inference_health(self.inference_url, self.inference_api_key)
            inference["backend"] = self.inference_backend
            faqs = self.store.list_faq()
            approved = sum(1 for faq in faqs if faq["reviewed_at"])
            channels = self.store.list_channels()
            pending = len(self.store.list_faq_candidates())
            self._json(
                HTTPStatus.OK,
                {
                    "readiness": readiness(
                        soak,
                        inference,
                        self.inference_backend,
                        approved_faq=approved,
                        mock_channels=len(channels),
                    ),
                    "soak": soak,
                    "inference": inference,
                    "alerts": alerts(
                        inference=inference,
                        soak=soak,
                        backup_age_hours=self._backup_age_hours(),
                        db_bytes=self.store.path.stat().st_size,
                        pending_candidates=pending,
                    ),
                },
            )
            return
        if path == "/api/channels":
            channels = self.store.list_channels()
            probed = self.connection_manager.probe()
            for channel in channels:
                status = probed.get(channel["id"])
                if status is not None:
                    channel["state"] = status.state
                    channel["detail"] = status.detail
                    channel["client_version"] = status.client_version or channel.get("client_version", "")
                    channel["adapter"] = status.adapter or channel.get("adapter", "")
                    channel["read_ok"] = status.read_ok
                    channel["last_read_at"] = status.last_read_at or channel.get("last_read_at")
                else:
                    simulated = channel["connection"] == "mock"
                    channel["state"] = "simulated" if simulated else channel["connection"]
                    channel["detail"] = "模拟环境，不连接真实账号" if simulated else ""
                    channel["read_ok"] = channel["read_capability"] == "read_ok"
                channel["state_label"] = STATE_LABELS.get(channel["state"], channel["state"])
                policy = self.platform_policies.get(channel.get("policy") or "", GENERIC)
                channel["policy_name"] = policy.name
                channel["policy_version"] = policy.version
            self._json(HTTPStatus.OK, {"channels": channels, "mode": "mock"})
            return
        if path == "/api/conversations":
            channel = (parse_qs(query).get("channel") or [None])[0]
            self._json(
                HTTPStatus.OK,
                {"conversations": self.store.list_conversations(channel), "mode": "mock"},
            )
            return
        if path.startswith("/api/conversations/"):
            parts = path.strip("/").split("/")
            if len(parts) != 3:
                raise ValueError("invalid conversation route")
            detail = self.store.conversation_detail(int(parts[2]))
            if detail is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "conversation not found"})
                return
            detail["mode"] = "mock"
            self._json(HTTPStatus.OK, detail)
            return
        if path == "/api/faq":
            text = (parse_qs(query).get("q") or [None])[0]
            items = self.store.list_faq(text)
            candidates = self.store.list_faq_candidates()
            self._json(
                HTTPStatus.OK,
                {
                    "items": items,
                    "candidates": candidates,
                    "stats": {
                        "total": len(items),
                        "approved": sum(1 for item in items if item["reviewed_at"]),
                        "pending": len(candidates),
                    },
                },
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "route not found"})

    # --- write API -------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        if not self._same_origin():
            self._json(HTTPStatus.FORBIDDEN, {"error": "origin rejected"})
            return
        try:
            data = self._payload()
            self._post_api(urlparse(self.path).path, data)
        except GenerationUnavailable as error:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
        except MediaUnavailable as error:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _post_api(self, path: str, data: dict) -> None:
        if path == "/api/work":
            inbound = self._text(data["inbound"])
            draft = self._text(data["draft"])
            if not inbound or not draft:
                raise ValueError("message and draft are required")
            key = sha256(inbound.encode()).hexdigest()
            draft_id = self.store.create_work(
                self._text(data.get("label", "手动会话"), 120) or "手动会话",
                self._text(data.get("language", "zh-CN"), 20) or "zh-CN",
                inbound,
                key,
                draft,
            )
            self._json(HTTPStatus.CREATED, {"id": draft_id, "mode": "manual"})
            return
        if path == "/api/system/inference-test":
            result = inference_test(self.inference_url, self.inference_api_key)
            self._json(HTTPStatus.OK if result["state"] == "passed" else HTTPStatus.SERVICE_UNAVAILABLE, result)
            return
        if path == "/api/suggest":
            inbound = self._text(data["inbound"], 4000)
            language = self._text(data.get("language", "zh-CN"), 20) or "zh-CN"
            result = self._draft(inbound, language)
            self._json(HTTPStatus.OK, {**result, "mode": "suggested"})
            return
        if path.startswith("/api/drafts/"):
            self._draft_action(path, data)
            return
        if path.startswith("/api/conversations/"):
            self._conversation_action(path, data)
            return
        if path == "/api/faq":
            self._create_faq(data)
            return
        if path == "/api/faq/candidates/build":
            created = self.store.build_faq_candidates()
            self._json(HTTPStatus.OK, {"created": created})
            return
        if path.startswith("/api/faq/candidates/"):
            self._candidate_action(path)
            return
        if path.startswith("/api/faq/"):
            self._faq_action(path, data)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "route not found"})

    def _conversation_action(self, path: str, data: dict) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 4:
            raise ValueError("invalid conversation route")
        conversation_id = int(parts[2])
        detail = self.store.conversation_detail(conversation_id)
        if detail is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "conversation not found"})
            return
        conversation = detail["conversation"]
        action = parts[3]
        if action == "messages":
            body = self._text(data.get("body"), 2000)
            if not body:
                raise ValueError("message is required")
            message = self.store.add_message(conversation_id, "out", body, "simulated")
            self._json(HTTPStatus.OK, {"message": message, "sent": False, "simulated": True})
            return
        if action == "inbound":
            body = self._text(data.get("body"), 2000)
            if not body:
                raise ValueError("message is required")
            message = self.store.add_message(conversation_id, "in", body, "mock")
            self._json(HTTPStatus.OK, {"message": message, "simulated": True})
            return
        if action == "read":
            self.store.mark_read(conversation_id)
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if action == "takeover":
            enabled = bool(data.get("enabled", True))
            self.store.set_takeover(conversation_id, enabled)
            self._json(HTTPStatus.OK, {"takeover": enabled})
            return
        if action == "suggest":
            inbound = ""
            for message in detail["messages"]:
                if message["direction"] == "in":
                    inbound = message["body"]
            if not inbound:
                raise ValueError("conversation has no inbound message")
            language = conversation.get("locale") or conversation.get("language") or "zh-CN"
            policy = self.channel_guards.get(conversation.get("channel_policy") or "", self.policy)
            result = self._draft(inbound, language, policy)
            self._json(HTTPStatus.OK, {**result, "mode": "suggested"})
            return
        raise ValueError("invalid conversation action")

    def _draft_action(self, path: str, data: dict) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 4:
            raise ValueError("invalid draft route")
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
                self._text(data.get("filename", "image"), 200) or "image",
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
        raise ValueError("invalid draft action")

    def _create_faq(self, data: dict) -> None:
        question = self._text(data["question"], 500)
        answer = self._text(data["answer"], 4000)
        if not question or not answer:
            raise ValueError("question and answer are required")
        faq_id = self.store.create_faq(
            question=question,
            answer=answer,
            category=self._text(data.get("category", "通用"), 60) or "通用",
            variants=[self._text(item, 200) for item in data.get("variants", []) if self._text(item, 200)],
            platforms=[self._text(item, 40) for item in data.get("platforms", []) if self._text(item, 40)],
            languages=[self._text(item, 20) for item in data.get("languages", ["zh-CN"]) if self._text(item, 20)],
            auto_send=bool(data.get("auto_send")),
            sensitive=bool(data.get("sensitive")),
            reviewed=bool(data.get("reviewed")),
        )
        self._json(HTTPStatus.CREATED, {"id": faq_id})

    def _faq_action(self, path: str, data: dict) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 4:
            raise ValueError("invalid faq route")
        faq_id = int(parts[2])
        action = parts[3]
        if self.store.get_faq(faq_id) is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "faq not found"})
            return
        if action == "update":
            updated = self.store.update_faq(
                faq_id,
                category=self._text(data["category"], 60) if "category" in data else None,
                question=self._text(data["question"], 500) if "question" in data else None,
                answer=self._text(data["answer"], 4000) if "answer" in data else None,
                variants=(
                    [self._text(item, 200) for item in data["variants"] if self._text(item, 200)]
                    if "variants" in data
                    else None
                ),
                platforms=(
                    [self._text(item, 40) for item in data["platforms"] if self._text(item, 40)]
                    if "platforms" in data
                    else None
                ),
                languages=(
                    [self._text(item, 20) for item in data["languages"] if self._text(item, 20)]
                    if "languages" in data
                    else None
                ),
                auto_send=bool(data["auto_send"]) if "auto_send" in data else None,
                sensitive=bool(data["sensitive"]) if "sensitive" in data else None,
                review=bool(data.get("review")),
            )
            self._json(HTTPStatus.OK, {"updated": updated})
            return
        if action == "approve":
            self._json(HTTPStatus.OK, {"approved": self.store.approve_faq(faq_id)})
            return
        if action == "delete":
            self._json(HTTPStatus.OK, {"deleted": self.store.delete_faq(faq_id)})
            return
        raise ValueError("invalid faq action")

    def _candidate_action(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 5:
            raise ValueError("invalid faq candidate route")
        candidate_id = int(parts[3])
        action = parts[4]
        if action == "accept":
            faq_id = self.store.accept_faq_candidate(candidate_id)
            if faq_id is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "candidate not found"})
                return
            self._json(HTTPStatus.OK, {"faq_id": faq_id})
            return
        if action == "reject":
            self._json(HTTPStatus.OK, {"rejected": self.store.reject_faq_candidate(candidate_id)})
            return
        raise ValueError("invalid faq candidate action")

    def log_message(self, format: str, *args: object) -> None:
        logging.getLogger("workbench.http").info("%s %s", self.address_string(), format % args)


def create_server(
    database: str | Path,
    port: int = 8765,
    *,
    inference_url: str = "http://127.0.0.1:8081",
    inference_api_key: str = "",
    inference_backend: str = "local",
    soak_log: str | Path = "data/soak-sycl-15m.out.log",
    channels: list[dict] | None = None,
    adapters: list | None = None,
    policies: dict[str, PlatformPolicy] | None = None,
) -> ThreadingHTTPServer:
    store = Store(database)
    store.sync_channels(channels if channels is not None else load_channels())
    loaded_policies = policies if policies is not None else load_policies()
    handler = type(
        "BoundWorkbenchHandler",
        (WorkbenchHandler,),
        {
            "store": store,
            "inference_url": inference_url,
            "inference_api_key": inference_api_key,
            "inference_backend": inference_backend,
            "soak_log": Path(soak_log),
            "connection_manager": ConnectionManager.from_list(adapters),
            "platform_policies": loaded_policies,
            "channel_guards": {pid: guard_for(policy) for pid, policy in loaded_policies.items()},
        },
    )
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def configure_logging() -> None:
    directory = Path("data/logs")
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "workbench.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def maintenance_tick() -> None:
    """Back up when stale and archive old logs; safe to call repeatedly."""
    logger = logging.getLogger("workbench.maintenance")
    try:
        created = maybe_backup()
        if created:
            logger.info("backup created %s", created["backup"])
        rotated = rotate_logs()
        if rotated["archived"]:
            logger.info("archived %s old log files", len(rotated["archived"]))
    except Exception:  # maintenance must never stop the server
        logger.exception("maintenance failed")


def maintenance_worker() -> None:
    while True:
        time.sleep(3600)
        maintenance_tick()


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local support workbench")
    parser.add_argument("--database", default="data/workbench.sqlite")
    parser.add_argument("--port", type=int, default=8765, choices=[8765])
    args = parser.parse_args()
    configure_logging()
    logging.getLogger("workbench").info("starting workbench database=%s", args.database)
    server = create_server(
        args.database,
        args.port,
        inference_url=os.environ.get("WORKBENCH_INFERENCE_URL", "http://127.0.0.1:8081"),
        inference_api_key=os.environ.get("WORKBENCH_INFERENCE_API_KEY", ""),
        inference_backend=os.environ.get("WORKBENCH_INFERENCE_BACKEND", "local"),
        soak_log=os.environ.get("WORKBENCH_SOAK_LOG", "data/soak-sycl-15m.out.log"),
    )
    maintenance_tick()
    threading.Thread(target=maintenance_worker, daemon=True).start()
    print("Local support workbench: http://127.0.0.1:8765")
    server.serve_forever()


if __name__ == "__main__":
    main()
