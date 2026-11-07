"""SQLite persistence and the single-delivery state machine."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import sqlite3
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .faq import match as match_faq_row, parse_row as parse_faq_row, similarity
from .guard import compact


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL,
  language TEXT NOT NULL,
  takeover INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  direction TEXT NOT NULL CHECK(direction IN ('in','out')),
  body TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(conversation_id, dedupe_key)
);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  based_on_message_id INTEGER NOT NULL REFERENCES messages(id),
  body TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected','stale','copied')),
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS guard_results (
  id INTEGER PRIMARY KEY,
  draft_id INTEGER NOT NULL REFERENCES drafts(id),
  verdict TEXT NOT NULL,
  hits_json TEXT NOT NULL,
  checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS draft_media (
  id INTEGER PRIMARY KEY,
  draft_id INTEGER NOT NULL REFERENCES drafts(id),
  filename TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  extracted_text_json TEXT NOT NULL,
  qr_payloads_json TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  analyzed_at TEXT NOT NULL,
  UNIQUE(draft_id, sha256)
);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY,
  draft_id INTEGER NOT NULL UNIQUE REFERENCES drafts(id),
  account_key TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('approved','leased','sending','sent','failed','unknown','cancelled')),
  lease_owner TEXT,
  lease_expires_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_sending_per_account
  ON outbox(account_key) WHERE state = 'sending';
CREATE TABLE IF NOT EXISTS channels (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  accent TEXT NOT NULL,
  skin TEXT NOT NULL DEFAULT 'plain',
  sort_order INTEGER NOT NULL DEFAULT 0,
  connection TEXT NOT NULL DEFAULT 'mock',
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY,
  channel_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  avatar TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  locale TEXT NOT NULL DEFAULT 'zh-CN',
  profile_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(channel_id, external_id)
);
CREATE TABLE IF NOT EXISTS faq (
  id INTEGER PRIMARY KEY,
  category TEXT NOT NULL DEFAULT '通用',
  question TEXT NOT NULL,
  variants_json TEXT NOT NULL DEFAULT '[]',
  answer TEXT NOT NULL,
  platforms_json TEXT NOT NULL DEFAULT '[]',
  languages_json TEXT NOT NULL DEFAULT '["zh-CN"]',
  auto_send INTEGER NOT NULL DEFAULT 0,
  sensitive INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'manual',
  reviewed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS faq_candidates (
  id INTEGER PRIMARY KEY,
  question TEXT NOT NULL,
  suggested_answer TEXT NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 1,
  source_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
  created_at TEXT NOT NULL,
  UNIQUE(question, suggested_answer)
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)

    def _migrate(self, connection: sqlite3.Connection) -> None:
        """Add columns introduced after the first local database was created."""
        conversation_columns = {row[1] for row in connection.execute("PRAGMA table_info(conversations)")}
        additions = {
            "channel_id": "TEXT NOT NULL DEFAULT 'manual'",
            "customer_id": "INTEGER",
            "unread": "INTEGER NOT NULL DEFAULT 0",
            "updated_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in conversation_columns:
                connection.execute(f"ALTER TABLE conversations ADD COLUMN {name} {definition}")
        connection.execute("UPDATE conversations SET updated_at=COALESCE(updated_at, created_at) WHERE updated_at IS NULL")
        message_columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
        if "source" not in message_columns:
            connection.execute("ALTER TABLE messages ADD COLUMN source TEXT NOT NULL DEFAULT 'inbound'")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def create_work(self, label: str, language: str, inbound: str, dedupe_key: str, draft: str) -> int:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            conversation = db.execute(
                "INSERT INTO conversations(label,language,created_at) VALUES(?,?,?) RETURNING id",
                (label, language, now),
            ).fetchone()[0]
            message = db.execute(
                "INSERT INTO messages(conversation_id,direction,body,dedupe_key,created_at) VALUES(?,'in',?,?,?) RETURNING id",
                (conversation, inbound, dedupe_key, now),
            ).fetchone()[0]
            draft_id = db.execute(
                "INSERT INTO drafts(conversation_id,based_on_message_id,body,state,created_at,updated_at) VALUES(?,?,?,'pending',?,?) RETURNING id",
                (conversation, message, draft, now, now),
            ).fetchone()[0]
            db.commit()
            return draft_id

    def save_guard(self, draft_id: int, verdict: str, hits: list[dict[str, str]]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO guard_results(draft_id,verdict,hits_json,checked_at) VALUES(?,?,?,?)",
                (draft_id, verdict, json.dumps(hits, ensure_ascii=False), utc_now()),
            )

    def add_media_analysis(
        self,
        draft_id: int,
        filename: str,
        digest: str,
        extracted_text: tuple[str, ...],
        qr_payloads: tuple[str, ...],
        width: int,
        height: int,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO draft_media(draft_id,filename,sha256,extracted_text_json,qr_payloads_json,width,height,analyzed_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(draft_id,sha256) DO UPDATE SET "
                "filename=excluded.filename,extracted_text_json=excluded.extracted_text_json,"
                "qr_payloads_json=excluded.qr_payloads_json,width=excluded.width,height=excluded.height,analyzed_at=excluded.analyzed_at",
                (
                    draft_id,
                    filename,
                    digest,
                    json.dumps(extracted_text, ensure_ascii=False),
                    json.dumps(qr_payloads, ensure_ascii=False),
                    width,
                    height,
                    utc_now(),
                ),
            )

    def media_inputs(self, draft_id: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
        text: list[str] = []
        qr: list[str] = []
        with self.connect() as db:
            rows = db.execute(
                "SELECT extracted_text_json,qr_payloads_json FROM draft_media WHERE draft_id=? ORDER BY id",
                (draft_id,),
            ).fetchall()
        for row in rows:
            text.extend(json.loads(row["extracted_text_json"]))
            qr.extend(json.loads(row["qr_payloads_json"]))
        return tuple(text), tuple(qr)

    def approve(self, draft_id: int, expected_revision: int, account_key: str = "manual") -> bool:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE drafts SET state='approved',updated_at=? WHERE id=? AND revision=? AND state='pending'",
                (now, draft_id, expected_revision),
            ).rowcount
            if changed:
                db.execute(
                    "INSERT INTO outbox(draft_id,account_key,state,updated_at) VALUES(?,?,'approved',?) ON CONFLICT(draft_id) DO NOTHING",
                    (draft_id, account_key, now),
                )
            db.commit()
            return bool(changed)

    def expire_draft(self, draft_id: int) -> bool:
        """Invalidate a draft when newer context makes it unsafe to use."""
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE drafts SET state='stale',revision=revision+1,updated_at=? "
                "WHERE id=? AND state IN ('pending','approved')",
                (now, draft_id),
            ).rowcount
            db.execute(
                "UPDATE outbox SET state='cancelled',updated_at=? WHERE draft_id=? AND state IN ('approved','leased')",
                (now, draft_id),
            )
            db.commit()
            return bool(changed)

    def claim(self, account_key: str, owner: str, lease_seconds: int = 30) -> sqlite3.Row | None:
        now = datetime.now(UTC)
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE outbox SET state='approved',lease_owner=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE state='leased' AND lease_expires_at < ?",
                (now.isoformat(), now.isoformat()),
            )
            row = db.execute(
                "SELECT id FROM outbox WHERE account_key=? AND state='approved' ORDER BY id LIMIT 1",
                (account_key,),
            ).fetchone()
            if row is None:
                db.commit()
                return None
            changed = db.execute(
                "UPDATE outbox SET state='leased',lease_owner=?,lease_expires_at=?,attempts=attempts+1,updated_at=? "
                "WHERE id=? AND state='approved'",
                (owner, expires, now.isoformat(), row[0]),
            ).rowcount
            result = db.execute("SELECT * FROM outbox WHERE id=?", (row[0],)).fetchone() if changed else None
            db.commit()
            return result

    def start_sending(self, outbox_id: int, owner: str) -> bool:
        """Move a valid lease to sending while preserving the per-account lock."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE outbox SET state='sending',updated_at=? WHERE id=? AND state='leased' "
                "AND lease_owner=? AND lease_expires_at>=?",
                (utc_now(), outbox_id, owner, utc_now()),
            ).rowcount
            db.commit()
            return bool(changed)

    def finish_sending(self, outbox_id: int, owner: str, result: str) -> bool:
        """Persist a terminal result. Unknown and failed results are never retried automatically."""
        if result not in {"sent", "failed", "unknown"}:
            raise ValueError("invalid delivery result")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE outbox SET state=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE id=? AND state='sending' AND lease_owner=?",
                (result, utc_now(), outbox_id, owner),
            ).rowcount
            db.commit()
            return bool(changed)

    def mark_copied(self, draft_id: int) -> str | None:
        """Record manual handoff idempotently; this does not claim the message was sent."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body,state FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None or row["state"] not in ("approved", "copied"):
                db.commit()
                return None
            db.execute("UPDATE drafts SET state='copied',updated_at=? WHERE id=?", (utc_now(), draft_id))
            db.execute("UPDATE outbox SET state='cancelled',updated_at=? WHERE draft_id=? AND state='approved'", (utc_now(), draft_id))
            db.commit()
            return str(row["body"])

    def set_takeover(self, conversation_id: int, enabled: bool = True) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE conversations SET takeover=?,updated_at=? WHERE id=?",
                (1 if enabled else 0, utc_now(), conversation_id),
            )
            if not enabled:
                db.commit()
                return
            db.execute(
                "UPDATE drafts SET state='stale',updated_at=? WHERE conversation_id=? AND state IN ('pending','approved')",
                (utc_now(), conversation_id),
            )
            db.execute(
                "UPDATE outbox SET state='cancelled',updated_at=? WHERE draft_id IN "
                "(SELECT id FROM drafts WHERE conversation_id=?) AND state IN ('approved','leased')",
                (utc_now(), conversation_id),
            )
            db.commit()

    def get_draft(self, draft_id: int) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute(
                "SELECT d.*, c.label, c.language, c.takeover, m.body AS inbound FROM drafts d "
                "JOIN conversations c ON c.id=d.conversation_id "
                "JOIN messages m ON m.id=d.based_on_message_id WHERE d.id=?",
                (draft_id,),
            ).fetchone()

    # --- channels, customers and simulated conversations -----------------

    def sync_channels(self, channels: list[dict]) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for channel in channels:
                db.execute(
                    "INSERT INTO channels(id,name,accent,skin,sort_order) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name,accent=excluded.accent,"
                    "skin=excluded.skin,sort_order=excluded.sort_order",
                    (
                        channel["id"],
                        channel["name"],
                        channel["accent"],
                        channel.get("skin", "plain"),
                        int(channel.get("sort_order", 0)),
                    ),
                )
            db.commit()

    def list_channels(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT ch.id,ch.name,ch.accent,ch.skin,ch.sort_order,ch.connection,"
                "(SELECT COUNT(*) FROM conversations c WHERE c.channel_id=ch.id) AS conversations,"
                "(SELECT COALESCE(SUM(c.unread),0) FROM conversations c WHERE c.channel_id=ch.id) AS unread "
                "FROM channels ch WHERE ch.enabled=1 ORDER BY ch.sort_order,ch.id"
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_customer(
        self,
        channel_id: str,
        external_id: str,
        display_name: str,
        *,
        avatar: str = "",
        note: str = "",
        tags: tuple[str, ...] | list[str] = (),
        locale: str = "zh-CN",
        profile: dict | None = None,
    ) -> int:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO customers(channel_id,external_id,display_name,avatar,note,tags_json,locale,profile_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(channel_id,external_id) DO UPDATE SET "
                "display_name=excluded.display_name,avatar=excluded.avatar,note=excluded.note,"
                "tags_json=excluded.tags_json,locale=excluded.locale,profile_json=excluded.profile_json",
                (
                    channel_id,
                    external_id,
                    display_name,
                    avatar,
                    note,
                    json.dumps(list(tags), ensure_ascii=False),
                    locale,
                    json.dumps(profile or {}, ensure_ascii=False),
                    now,
                ),
            )
            row = db.execute(
                "SELECT id FROM customers WHERE channel_id=? AND external_id=?",
                (channel_id, external_id),
            ).fetchone()
            db.commit()
            return int(row[0])

    def create_chat_conversation(self, channel_id: str, customer_id: int, label: str, language: str) -> int:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "INSERT INTO conversations(label,language,channel_id,customer_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?) RETURNING id",
                (label, language, channel_id, customer_id, now, now),
            ).fetchone()
            db.commit()
            return int(row[0])

    def add_message(
        self,
        conversation_id: int,
        direction: str,
        body: str,
        source: str = "mock",
        *,
        created_at: str | None = None,
    ) -> dict:
        if direction not in {"in", "out"}:
            raise ValueError("invalid direction")
        now = created_at or utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "INSERT INTO messages(conversation_id,direction,body,dedupe_key,created_at,source) "
                "VALUES(?,?,?,?,?,?) RETURNING id",
                (conversation_id, direction, body, uuid4().hex, now, source),
            ).fetchone()
            db.execute(
                "UPDATE conversations SET updated_at=?,unread=CASE WHEN ?='in' THEN unread+1 ELSE unread END WHERE id=?",
                (now, direction, conversation_id),
            )
            db.commit()
        return {
            "id": int(row[0]),
            "conversation_id": conversation_id,
            "direction": direction,
            "body": body,
            "source": source,
            "created_at": now,
        }

    def list_conversations(self, channel_id: str | None = None) -> list[dict]:
        query = (
            "SELECT c.id,c.label,c.language,c.takeover,c.unread,c.channel_id,c.updated_at,"
            "cu.id AS customer_id,cu.external_id,cu.display_name,cu.avatar,cu.locale,cu.tags_json,"
            "(SELECT m.body FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS preview,"
            "(SELECT m.created_at FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_at "
            "FROM conversations c LEFT JOIN customers cu ON cu.id=c.customer_id "
        )
        params: tuple = ()
        if channel_id:
            query += "WHERE c.channel_id=? "
            params = (channel_id,)
        query += "ORDER BY COALESCE(last_at,c.updated_at) DESC, c.id DESC"
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            result.append(item)
        return result

    def conversation_detail(self, conversation_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT c.id,c.label,c.language,c.takeover,c.unread,c.channel_id,c.created_at,c.updated_at,"
                "ch.name AS channel_name,ch.accent AS channel_accent,ch.skin AS channel_skin,"
                "cu.id AS customer_id,cu.external_id,cu.display_name,cu.avatar,cu.note AS customer_note,"
                "cu.tags_json,cu.locale,cu.profile_json "
                "FROM conversations c LEFT JOIN channels ch ON ch.id=c.channel_id "
                "LEFT JOIN customers cu ON cu.id=c.customer_id WHERE c.id=?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            messages = [
                dict(message)
                for message in db.execute(
                    "SELECT id,direction,body,source,created_at FROM messages WHERE conversation_id=? ORDER BY id",
                    (conversation_id,),
                ).fetchall()
            ]
        conversation = dict(row)
        conversation["tags"] = json.loads(conversation.pop("tags_json") or "[]")
        conversation["profile"] = json.loads(conversation.pop("profile_json") or "{}")
        return {"conversation": conversation, "messages": messages}

    def mark_read(self, conversation_id: int) -> None:
        with self.connect() as db:
            db.execute("UPDATE conversations SET unread=0 WHERE id=?", (conversation_id,))

    def set_unread(self, conversation_id: int, count: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE conversations SET unread=? WHERE id=?",
                (max(0, int(count)), conversation_id),
            )

    # --- FAQ knowledge base ----------------------------------------------

    def create_faq(
        self,
        *,
        question: str,
        answer: str,
        category: str = "通用",
        variants: tuple[str, ...] | list[str] = (),
        platforms: tuple[str, ...] | list[str] = (),
        languages: tuple[str, ...] | list[str] = ("zh-CN",),
        auto_send: bool = False,
        sensitive: bool = False,
        source: str = "manual",
        reviewed: bool = False,
    ) -> int:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "INSERT INTO faq(category,question,variants_json,answer,platforms_json,languages_json,"
                "auto_send,sensitive,source,reviewed_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
                (
                    category,
                    question,
                    json.dumps(list(variants), ensure_ascii=False),
                    answer,
                    json.dumps(list(platforms), ensure_ascii=False),
                    json.dumps(list(languages), ensure_ascii=False),
                    1 if auto_send else 0,
                    1 if sensitive else 0,
                    source,
                    now if reviewed else None,
                    now,
                    now,
                ),
            ).fetchone()
            db.commit()
            return int(row[0])

    def update_faq(
        self,
        faq_id: int,
        *,
        category: str | None = None,
        question: str | None = None,
        answer: str | None = None,
        variants: list[str] | None = None,
        platforms: list[str] | None = None,
        languages: list[str] | None = None,
        auto_send: bool | None = None,
        sensitive: bool | None = None,
        review: bool = False,
    ) -> bool:
        assignments: list[str] = []
        params: list[object] = []
        content_changed = False
        for column, value in (
            ("category", category),
            ("question", question),
            ("answer", answer),
        ):
            if value is not None:
                assignments.append(f"{column}=?")
                params.append(value)
                content_changed = content_changed or column != "category"
        for column, values in (
            ("variants_json", variants),
            ("platforms_json", platforms),
            ("languages_json", languages),
        ):
            if values is not None:
                assignments.append(f"{column}=?")
                params.append(json.dumps(list(values), ensure_ascii=False))
                content_changed = True
        for column, flag in (("auto_send", auto_send), ("sensitive", sensitive)):
            if flag is not None:
                assignments.append(f"{column}=?")
                params.append(1 if flag else 0)
        if content_changed:
            assignments.append("reviewed_at=?")
            params.append(utc_now() if review else None)
        elif review:
            assignments.append("reviewed_at=?")
            params.append(utc_now())
        if not assignments:
            return False
        assignments.append("updated_at=?")
        params.append(utc_now())
        params.append(faq_id)
        with self.connect() as db:
            changed = db.execute(f"UPDATE faq SET {','.join(assignments)} WHERE id=?", params).rowcount
        return bool(changed)

    def approve_faq(self, faq_id: int) -> bool:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE faq SET reviewed_at=?,updated_at=? WHERE id=?",
                (utc_now(), utc_now(), faq_id),
            ).rowcount
        return bool(changed)

    def delete_faq(self, faq_id: int) -> bool:
        with self.connect() as db:
            return bool(db.execute("DELETE FROM faq WHERE id=?", (faq_id,)).rowcount)

    def list_faq(self, query: str | None = None) -> list[dict]:
        sql = "SELECT * FROM faq"
        params: tuple = ()
        if query and query.strip():
            like = f"%{query.strip()}%"
            sql += " WHERE question LIKE ? OR answer LIKE ? OR variants_json LIKE ?"
            params = (like, like, like)
        sql += " ORDER BY id DESC"
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [parse_faq_row(row) for row in rows]

    def get_faq(self, faq_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM faq WHERE id=?", (faq_id,)).fetchone()
        return parse_faq_row(row) if row else None

    def match_faq(self, inbound: str, language: str | None = None, threshold: float = 0.3) -> dict | None:
        hit = match_faq_row(inbound, self.list_faq(), threshold)
        if hit is None:
            return None
        faq, score = hit
        supported = language in faq["languages"] if language else True
        return {**faq, "score": score, "language_supported": supported}

    def build_faq_candidates(self, minimum: int = 2, limit: int = 100) -> int:
        """Group repeated inbound messages and the human reply that followed them."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT conversation_id,direction,body FROM messages ORDER BY conversation_id,id"
            ).fetchall()
        groups: dict[str, dict] = {}
        pending: sqlite3.Row | None = None
        for row in rows:
            if row["direction"] == "in":
                pending = row
                continue
            if pending is None:
                continue
            question = pending["body"].strip()[:200]
            answer = row["body"].strip()
            if question and answer:
                entry = groups.setdefault(question, {"answers": {}, "sources": set(), "count": 0})
                entry["answers"][answer] = entry["answers"].get(answer, 0) + 1
                entry["sources"].add(pending["conversation_id"])
                entry["count"] += 1
            pending = None
        existing = self.list_faq()
        created = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for question, entry in sorted(groups.items(), key=lambda item: item[1]["count"], reverse=True)[:limit]:
                if entry["count"] < minimum:
                    continue
                if any(similarity(question, faq["question"], faq["variants"]) >= 0.7 for faq in existing):
                    continue
                answer = max(entry["answers"], key=entry["answers"].get)
                db.execute(
                    "INSERT INTO faq_candidates(question,suggested_answer,occurrences,source_json,created_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(question,suggested_answer) DO UPDATE SET "
                    "occurrences=excluded.occurrences,source_json=excluded.source_json,created_at=excluded.created_at "
                    "WHERE faq_candidates.status='pending'",
                    (
                        question,
                        answer,
                        entry["count"],
                        json.dumps(sorted(entry["sources"]), ensure_ascii=False),
                        utc_now(),
                    ),
                )
                created += 1
            db.commit()
        return created

    def list_faq_candidates(self, status: str = "pending") -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM faq_candidates WHERE status=? ORDER BY occurrences DESC,id DESC",
                (status,),
            ).fetchall()
        return [{**dict(row), "sources": json.loads(row["source_json"])} for row in rows]

    def accept_faq_candidate(self, candidate_id: int) -> int | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM faq_candidates WHERE id=? AND status='pending'",
                (candidate_id,),
            ).fetchone()
        if row is None:
            return None
        faq_id = self.create_faq(
            category="历史归纳",
            question=row["question"],
            answer=row["suggested_answer"],
            source="history",
        )
        with self.connect() as db:
            db.execute("UPDATE faq_candidates SET status='accepted' WHERE id=?", (candidate_id,))
        return faq_id

    def reject_faq_candidate(self, candidate_id: int) -> bool:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE faq_candidates SET status='rejected' WHERE id=? AND status='pending'",
                (candidate_id,),
            ).rowcount
        return bool(changed)

