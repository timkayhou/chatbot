"""SQLite persistence and the single-delivery state machine."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import sqlite3
from pathlib import Path
from typing import Iterator


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
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

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

    def set_takeover(self, conversation_id: int) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE conversations SET takeover=1 WHERE id=?", (conversation_id,))
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

