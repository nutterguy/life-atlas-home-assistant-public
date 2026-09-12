from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = 2
MAX_RAW_ITEM_BYTES = 2 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def finite_timestamp(value: object) -> int | None:
    try:
        timestamp = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    if timestamp < 0 or timestamp > 32_503_680_000:
        return None
    return timestamp


def timestamp_iso(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (OSError, OverflowError, ValueError):
        return None


def nested(mapping: Mapping[str, Any], *path: str) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def message_chat_id(payload: Mapping[str, Any]) -> str | None:
    from_me = bool(payload.get("fromMe"))
    value = (
        payload.get("chatId")
        or nested(payload, "_data", "Info", "Chat")
        or (payload.get("to") if from_me else payload.get("from"))
    )
    chat_id = str(value).strip() if value else ""
    return chat_id if chat_id and len(chat_id) <= 512 else None


class Archive:
    def __init__(self, database: Path | str) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.initialise()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.database, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        try:
            with con:
                yield con
        finally:
            con.close()

    def initialise(self) -> None:
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS archive_meta (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chats (
                  chat_id TEXT PRIMARY KEY,
                  name TEXT,
                  chat_type TEXT,
                  archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
                  selection_override INTEGER CHECK(selection_override IN (0,1)),
                  observed_message_count INTEGER NOT NULL DEFAULT 0,
                  observed_earliest_ts INTEGER,
                  observed_latest_ts INTEGER,
                  last_message_ts INTEGER,
                  raw_json TEXT NOT NULL DEFAULT '{}',
                  first_seen TEXT NOT NULL,
                  last_seen TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS identities (
                  source_id TEXT PRIMARY KEY,
                  label TEXT,
                  kind TEXT,
                  raw_json TEXT NOT NULL DEFAULT '{}',
                  first_seen TEXT NOT NULL,
                  last_seen TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                  source_id TEXT PRIMARY KEY,
                  native_message_id TEXT,
                  chat_id TEXT,
                  sender_id TEXT,
                  sender_label TEXT,
                  from_me INTEGER NOT NULL DEFAULT 0 CHECK(from_me IN (0,1)),
                  timestamp INTEGER,
                  timestamp_iso TEXT,
                  message_type TEXT NOT NULL DEFAULT 'message',
                  body TEXT NOT NULL DEFAULT '',
                  has_media INTEGER NOT NULL DEFAULT 0 CHECK(has_media IN (0,1)),
                  media_json TEXT NOT NULL DEFAULT 'null',
                  reply_to_id TEXT,
                  lifecycle TEXT NOT NULL DEFAULT 'created'
                    CHECK(lifecycle IN ('created','updated','deleted','unavailable')),
                  content_hash TEXT NOT NULL,
                  version INTEGER NOT NULL DEFAULT 1,
                  raw_json TEXT NOT NULL,
                  first_seen TEXT NOT NULL,
                  last_seen TEXT NOT NULL,
                  FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE SET NULL,
                  FOREIGN KEY(sender_id) REFERENCES identities(source_id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS message_versions (
                  source_id TEXT NOT NULL REFERENCES messages(source_id) ON DELETE CASCADE,
                  version INTEGER NOT NULL,
                  content_hash TEXT NOT NULL,
                  lifecycle TEXT NOT NULL,
                  body TEXT NOT NULL,
                  raw_json TEXT NOT NULL,
                  captured_at TEXT NOT NULL,
                  PRIMARY KEY(source_id, version)
                );
                CREATE TABLE IF NOT EXISTS change_log (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source_id TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  lifecycle TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  changed_at TEXT NOT NULL,
                  UNIQUE(source_id, version),
                  FOREIGN KEY(source_id, version)
                    REFERENCES message_versions(source_id, version) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS sync_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  sync_type TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  status TEXT NOT NULL DEFAULT 'running',
                  pages INTEGER NOT NULL DEFAULT 0,
                  messages_seen INTEGER NOT NULL DEFAULT 0,
                  messages_changed INTEGER NOT NULL DEFAULT 0,
                  error TEXT
                );
                CREATE TABLE IF NOT EXISTS webhook_requests (
                  request_id TEXT PRIMARY KEY,
                  body_hash TEXT NOT NULL,
                  received_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  request_id TEXT,
                  event_type TEXT NOT NULL,
                  target_native_id TEXT,
                  chat_id TEXT,
                  raw_json TEXT NOT NULL,
                  received_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp
                  ON messages(timestamp, source_id);
                CREATE INDEX IF NOT EXISTS idx_messages_chat_timestamp
                  ON messages(chat_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_messages_native
                  ON messages(native_message_id);
                CREATE INDEX IF NOT EXISTS idx_changes_id ON change_log(id);
                CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs(started_at);
                """
            )
            chat_columns = {
                row["name"] for row in con.execute("PRAGMA table_info(chats)").fetchall()
            }
            chat_migrations = {
                "archived": "ALTER TABLE chats ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1))",
                "selection_override": "ALTER TABLE chats ADD COLUMN selection_override INTEGER CHECK(selection_override IN (0,1))",
                "observed_message_count": "ALTER TABLE chats ADD COLUMN observed_message_count INTEGER NOT NULL DEFAULT 0",
                "observed_earliest_ts": "ALTER TABLE chats ADD COLUMN observed_earliest_ts INTEGER",
                "observed_latest_ts": "ALTER TABLE chats ADD COLUMN observed_latest_ts INTEGER",
            }
            for column, statement in chat_migrations.items():
                if column not in chat_columns:
                    con.execute(statement)
            event_columns = {
                row["name"]
                for row in con.execute("PRAGMA table_info(source_events)").fetchall()
            }
            if "chat_id" not in event_columns:
                con.execute("ALTER TABLE source_events ADD COLUMN chat_id TEXT")
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_source_events_chat ON source_events(chat_id)"
            )
            try:
                con.execute(
                    """CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
                    source_id UNINDEXED, body, sender_label, chat_name)"""
                )
                fts = "fts5"
            except sqlite3.OperationalError:
                con.execute(
                    """CREATE TABLE IF NOT EXISTS message_fts(
                    source_id TEXT PRIMARY KEY, body TEXT, sender_label TEXT, chat_name TEXT)"""
                )
                fts = "like"
            con.execute(
                "INSERT OR REPLACE INTO archive_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
            con.execute(
                "INSERT OR REPLACE INTO archive_meta(key,value) VALUES('search_backend',?)",
                (fts,),
            )
            con.execute(
                "INSERT OR IGNORE INTO archive_meta(key,value) VALUES('selection_confirmed','false')"
            )

    def upsert_chat(self, value: Mapping[str, Any]) -> None:
        chat_id = str(value.get("id") or value.get("chatId") or "").strip()
        if not chat_id or len(chat_id) > 512:
            return
        now = utc_now()
        name = value.get("name") or value.get("pushName")
        name = str(name)[:512] if name else None
        timestamp = finite_timestamp(
            value.get("conversationTimestamp")
            or value.get("timestamp")
            or value.get("messageTimestamp")
        )
        if "@g.us" in chat_id:
            chat_type = "group"
        elif "@newsletter" in chat_id:
            chat_type = "channel"
        elif "@broadcast" in chat_id:
            chat_type = "broadcast"
        else:
            chat_type = "direct"
        archived_value = value.get("archived")
        if archived_value is None:
            archived_value = value.get("archive", False)
        archived = int(bool(archived_value))
        # Chat API responses are not a stable metadata-only contract across
        # WAHA engines. Persist an explicit allowlist so last-message previews
        # or engine internals can never bypass the per-chat content gate.
        raw = compact_json(
            {
                "id": chat_id,
                "name": name,
                "chat_type": chat_type,
                "archived": bool(archived),
                "last_message_timestamp": timestamp,
            }
        )
        with self.connect() as con:
            con.execute(
                """INSERT INTO chats(
                chat_id,name,chat_type,archived,last_message_ts,raw_json,first_seen,last_seen)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                  name=COALESCE(excluded.name,chats.name),
                  chat_type=excluded.chat_type,
                  archived=excluded.archived,
                  last_message_ts=COALESCE(excluded.last_message_ts,chats.last_message_ts),
                  raw_json=excluded.raw_json,last_seen=excluded.last_seen""",
                (chat_id, name, chat_type, archived, timestamp, raw, now, now),
            )

    def selection_confirmed(self) -> bool:
        with self.connect() as con:
            row = con.execute(
                "SELECT value FROM archive_meta WHERE key='selection_confirmed'"
            ).fetchone()
        return bool(row and row["value"] == "true")

    def inventory_available(self) -> bool:
        with self.connect() as con:
            row = con.execute(
                "SELECT value FROM archive_meta WHERE key='inventory_updated_at'"
            ).fetchone()
        return bool(row and row["value"])

    def confirm_selection(self) -> None:
        if not self.inventory_available():
            raise ValueError("Run a chat inventory scan before confirming selection")
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO archive_meta(key,value) VALUES('selection_confirmed','true')"
            )

    @staticmethod
    def _effective_selection_sql(alias: str = "chats") -> str:
        return (
            f"COALESCE({alias}.selection_override,"
            f"CASE WHEN {alias}.archived=1 THEN 0 ELSE 1 END)"
        )

    def should_archive_chat(self, chat_id: str | None) -> bool:
        if not chat_id or not self.selection_confirmed():
            return False
        expression = self._effective_selection_sql("chats")
        with self.connect() as con:
            row = con.execute(
                f"SELECT {expression} selected FROM chats WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        return bool(row and row["selected"])

    def effective_chat_selections(self) -> dict[str, bool]:
        expression = self._effective_selection_sql("chats")
        with self.connect() as con:
            rows = con.execute(
                f"SELECT chat_id,{expression} selected FROM chats"
            ).fetchall()
        return {row["chat_id"]: bool(row["selected"]) for row in rows}

    def set_chat_policy(self, chat_id: str, mode: str) -> dict[str, Any]:
        if not chat_id or len(chat_id) > 512:
            raise ValueError("chat_id is invalid")
        overrides = {"auto": None, "include": 1, "exclude": 0}
        if mode not in overrides:
            raise ValueError("mode must be auto, include, or exclude")
        with self.connect() as con:
            updated = con.execute(
                "UPDATE chats SET selection_override=?,last_seen=? WHERE chat_id=?",
                (overrides[mode], utc_now(), chat_id),
            ).rowcount
        if not updated:
            raise ValueError("chat was not found in the current inventory")
        purged = 0
        if self.selection_confirmed() and not self.should_archive_chat(chat_id):
            purged = self.purge_chat(chat_id)
        chat = self.get_chat_policy(chat_id)
        assert chat is not None
        return {**chat, "purged_messages": purged}

    def get_chat_policy(self, chat_id: str) -> dict[str, Any] | None:
        expression = self._effective_selection_sql("c")
        with self.connect() as con:
            row = con.execute(
                f"""SELECT c.*, {expression} effective_selected,
                (SELECT COUNT(*) FROM messages m WHERE m.chat_id=c.chat_id) durable_message_count
                FROM chats c WHERE c.chat_id=?""",
                (chat_id,),
            ).fetchone()
        return self._chat_policy_item(row) if row else None

    def list_chat_policies(
        self, *, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        expression = self._effective_selection_sql("c")
        with self.connect() as con:
            total = con.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
            rows = con.execute(
                f"""SELECT c.*, {expression} effective_selected,
                (SELECT COUNT(*) FROM messages m WHERE m.chat_id=c.chat_id) durable_message_count
                FROM chats c
                ORDER BY c.archived ASC,COALESCE(c.name,c.chat_id) COLLATE NOCASE,c.chat_id
                LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [self._chat_policy_item(row) for row in rows], int(total)

    def _chat_policy_item(self, row: Mapping[str, Any]) -> dict[str, Any]:
        override = row["selection_override"]
        return {
            "chat_id": row["chat_id"],
            "name": row["name"],
            "chat_type": row["chat_type"],
            "archived": bool(row["archived"]),
            "mode": "auto" if override is None else ("include" if override else "exclude"),
            "effective_selected": bool(row["effective_selected"]),
            "observed_message_count": int(row["observed_message_count"] or 0),
            "observed_earliest": timestamp_iso(row["observed_earliest_ts"]),
            "observed_latest": timestamp_iso(row["observed_latest_ts"]),
            "durable_message_count": int(row["durable_message_count"] or 0),
        }

    def replace_inventory_metrics(
        self, metrics: Mapping[str, tuple[int, int | None, int | None]]
    ) -> None:
        now = utc_now()
        with self.connect() as con:
            con.execute(
                """UPDATE chats SET observed_message_count=0,
                observed_earliest_ts=NULL,observed_latest_ts=NULL"""
            )
            for chat_id, (count, earliest, latest) in metrics.items():
                con.execute(
                    """INSERT OR IGNORE INTO chats(
                    chat_id,name,chat_type,raw_json,first_seen,last_seen)
                    VALUES(?,?,?,?,?,?)""",
                    (
                        chat_id,
                        None,
                        "group" if "@g.us" in chat_id else "direct",
                        "{}",
                        now,
                        now,
                    ),
                )
                con.execute(
                    """UPDATE chats SET observed_message_count=?,
                    observed_earliest_ts=?,observed_latest_ts=?,
                    last_message_ts=COALESCE(?,last_message_ts),last_seen=? WHERE chat_id=?""",
                    (count, earliest, latest, latest, now, chat_id),
                )
            con.execute(
                "INSERT OR REPLACE INTO archive_meta(key,value) VALUES('inventory_updated_at',?)",
                (now,),
            )
            if metrics:
                latest_upstream = max(
                    (latest for _, _, latest in metrics.values() if latest is not None),
                    default=None,
                )
                if latest_upstream is not None:
                    con.execute(
                        "INSERT OR REPLACE INTO archive_meta(key,value) VALUES('upstream_latest_timestamp',?)",
                        (str(latest_upstream),),
                    )

    def upstream_latest_timestamp(self) -> int | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT value FROM archive_meta WHERE key='upstream_latest_timestamp'"
            ).fetchone()
        try:
            return int(row["value"]) if row else None
        except (TypeError, ValueError):
            return None

    def record_upstream_latest_timestamp(self, value: int | None) -> None:
        if value is None:
            return
        current = self.upstream_latest_timestamp()
        if current is not None and current >= value:
            return
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO archive_meta(key,value) VALUES('upstream_latest_timestamp',?)",
                (str(value),),
            )

    def selection_summary(self) -> dict[str, Any]:
        expression = self._effective_selection_sql("chats")
        with self.connect() as con:
            row = con.execute(
                f"""SELECT COUNT(*) discovered,
                SUM(CASE WHEN archived=1 THEN 1 ELSE 0 END) archived,
                SUM(CASE WHEN {expression}=1 THEN 1 ELSE 0 END) included,
                SUM(CASE WHEN {expression}=0 THEN 1 ELSE 0 END) excluded,
                SUM(CASE WHEN {expression}=1 THEN observed_message_count ELSE 0 END) included_messages,
                SUM(CASE WHEN {expression}=0 THEN observed_message_count ELSE 0 END) excluded_messages
                FROM chats"""
            ).fetchone()
            updated = con.execute(
                "SELECT value FROM archive_meta WHERE key='inventory_updated_at'"
            ).fetchone()
        return {
            "confirmed": self.selection_confirmed(),
            "inventory_updated_at": updated["value"] if updated else None,
            "discovered": int(row["discovered"] or 0),
            "archived": int(row["archived"] or 0),
            "included": int(row["included"] or 0),
            "excluded": int(row["excluded"] or 0),
            "included_messages": int(row["included_messages"] or 0),
            "excluded_messages": int(row["excluded_messages"] or 0),
        }

    def purge_chat(self, chat_id: str) -> int:
        with self.connect() as con:
            source_ids = [
                row["source_id"]
                for row in con.execute(
                    "SELECT source_id FROM messages WHERE chat_id=?", (chat_id,)
                ).fetchall()
            ]
            for source_id in source_ids:
                con.execute("DELETE FROM message_fts WHERE source_id=?", (source_id,))
            request_ids = [
                row["request_id"]
                for row in con.execute(
                    "SELECT request_id FROM source_events WHERE chat_id=? AND request_id IS NOT NULL",
                    (chat_id,),
                ).fetchall()
            ]
            con.execute("DELETE FROM source_events WHERE chat_id=?", (chat_id,))
            for request_id in request_ids:
                con.execute("DELETE FROM webhook_requests WHERE request_id=?", (request_id,))
            con.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
            con.execute(
                """DELETE FROM identities WHERE source_id!='owner' AND NOT EXISTS(
                SELECT 1 FROM messages WHERE messages.sender_id=identities.source_id)"""
            )
        return len(source_ids)

    def purge_excluded_chats(self) -> int:
        if not self.selection_confirmed():
            return 0
        expression = self._effective_selection_sql("chats")
        with self.connect() as con:
            chat_ids = [
                row["chat_id"]
                for row in con.execute(
                    f"SELECT chat_id FROM chats WHERE {expression}=0"
                ).fetchall()
            ]
        return sum(self.purge_chat(chat_id) for chat_id in chat_ids)

    def _upsert_identity(
        self, con: sqlite3.Connection, source_id: str | None, label: str | None
    ) -> None:
        if not source_id or len(source_id) > 512:
            return
        now = utc_now()
        kind = "owner" if source_id == "owner" else "contact"
        con.execute(
            """INSERT INTO identities(source_id,label,kind,raw_json,first_seen,last_seen)
            VALUES(?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
              label=COALESCE(excluded.label,identities.label),last_seen=excluded.last_seen""",
            (source_id, label, kind, "{}", now, now),
        )

    def _normalise_message(
        self, payload: Mapping[str, Any], lifecycle: str | None = None
    ) -> dict[str, Any]:
        source_id = str(payload.get("id") or "").strip()
        if not source_id or len(source_id) > 512:
            raise ValueError("WAHA message has no bounded stable id")
        raw = compact_json(dict(payload))
        if len(raw.encode("utf-8")) > MAX_RAW_ITEM_BYTES:
            raise ValueError("WAHA message payload exceeds archive item limit")
        from_me = bool(payload.get("fromMe"))
        chat_id = message_chat_id(payload)
        sender_id = payload.get("participant") or (
            "owner" if from_me else payload.get("from")
        )
        sender_id = str(sender_id).strip() if sender_id else None
        sender_label = payload.get("pushName") or payload.get("notifyName")
        body = payload.get("body")
        body = body if isinstance(body, str) else ""
        timestamp = finite_timestamp(payload.get("timestamp"))
        media = payload.get("media")
        reply = payload.get("replyTo")
        reply_id = reply.get("id") if isinstance(reply, Mapping) else None
        native = nested(payload, "_data", "key", "id") or nested(
            payload, "_data", "Info", "ID"
        )
        native = str(native) if native else source_id
        state = lifecycle or str(payload.get("lifecycle") or "created")
        if state not in {"created", "updated", "deleted", "unavailable"}:
            state = "updated"
        message_type = str(payload.get("type") or "message")[:100]
        semantic = {
            "source_id": source_id,
            "native_message_id": native,
            "chat_id": chat_id,
            "sender_id": sender_id,
            "from_me": from_me,
            "timestamp": timestamp,
            "message_type": message_type,
            "body": body,
            "has_media": bool(payload.get("hasMedia")),
            "media": media,
            "reply_to_id": reply_id,
            "lifecycle": state,
        }
        semantic["content_hash"] = hashlib.sha256(
            compact_json(semantic).encode("utf-8")
        ).hexdigest()
        semantic.update(
            {
                "sender_label": str(sender_label) if sender_label else None,
                "timestamp_iso": timestamp_iso(timestamp),
                "media_json": compact_json(media),
                "raw_json": raw,
            }
        )
        return semantic

    def upsert_message(
        self, payload: Mapping[str, Any], lifecycle: str | None = None
    ) -> bool:
        item = self._normalise_message(payload, lifecycle)
        now = utc_now()
        chat_name: str | None = None
        with self.connect() as con:
            if item["chat_id"]:
                con.execute(
                    """INSERT OR IGNORE INTO chats(
                    chat_id,name,chat_type,raw_json,first_seen,last_seen)
                    VALUES(?,?,?,?,?,?)""",
                    (
                        item["chat_id"],
                        None,
                        "group" if "@g.us" in item["chat_id"] else "direct",
                        "{}",
                        now,
                        now,
                    ),
                )
                chat = con.execute(
                    "SELECT name FROM chats WHERE chat_id=?", (item["chat_id"],)
                ).fetchone()
                chat_name = chat["name"] if chat else None
            self._upsert_identity(con, item["sender_id"], item["sender_label"])
            existing = con.execute(
                "SELECT content_hash,version,lifecycle FROM messages WHERE source_id=?",
                (item["source_id"],),
            ).fetchone()
            if lifecycle is None and payload.get("lifecycle") is None and existing is not None:
                item = self._normalise_message(payload, str(existing["lifecycle"]))
            changed = existing is None or existing["content_hash"] != item["content_hash"]
            version = 1 if existing is None else int(existing["version"]) + (1 if changed else 0)
            con.execute(
                """INSERT INTO messages(
                source_id,native_message_id,chat_id,sender_id,sender_label,from_me,
                timestamp,timestamp_iso,message_type,body,has_media,media_json,
                reply_to_id,lifecycle,content_hash,version,raw_json,first_seen,last_seen)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_id) DO UPDATE SET
                  native_message_id=excluded.native_message_id,
                  chat_id=COALESCE(excluded.chat_id,messages.chat_id),
                  sender_id=COALESCE(excluded.sender_id,messages.sender_id),
                  sender_label=COALESCE(excluded.sender_label,messages.sender_label),
                  from_me=excluded.from_me,timestamp=COALESCE(excluded.timestamp,messages.timestamp),
                  timestamp_iso=COALESCE(excluded.timestamp_iso,messages.timestamp_iso),
                  message_type=excluded.message_type,body=excluded.body,
                  has_media=excluded.has_media,media_json=excluded.media_json,
                  reply_to_id=excluded.reply_to_id,lifecycle=excluded.lifecycle,
                  content_hash=excluded.content_hash,version=excluded.version,
                  raw_json=excluded.raw_json,last_seen=excluded.last_seen""",
                (
                    item["source_id"], item["native_message_id"], item["chat_id"],
                    item["sender_id"], item["sender_label"], int(item["from_me"]),
                    item["timestamp"], item["timestamp_iso"], item["message_type"],
                    item["body"], int(item["has_media"]), item["media_json"],
                    item["reply_to_id"], item["lifecycle"], item["content_hash"],
                    version, item["raw_json"], now, now,
                ),
            )
            if changed:
                con.execute(
                    """INSERT INTO message_versions(
                    source_id,version,content_hash,lifecycle,body,raw_json,captured_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (
                        item["source_id"], version, item["content_hash"],
                        item["lifecycle"], item["body"], item["raw_json"], now,
                    ),
                )
                con.execute(
                    """INSERT INTO change_log(
                    source_id,version,lifecycle,content_hash,changed_at)
                    VALUES(?,?,?,?,?)""",
                    (
                        item["source_id"], version, item["lifecycle"],
                        item["content_hash"], now,
                    ),
                )
                con.execute("DELETE FROM message_fts WHERE source_id=?", (item["source_id"],))
                con.execute(
                    "INSERT INTO message_fts(source_id,body,sender_label,chat_name) VALUES(?,?,?,?)",
                    (item["source_id"], item["body"], item["sender_label"], chat_name),
                )
            return changed

    def ingest_message(
        self, payload: Mapping[str, Any], lifecycle: str | None = None
    ) -> bool:
        if not self.should_archive_chat(message_chat_id(payload)):
            return False
        return self.upsert_message(payload, lifecycle=lifecycle)

    def _update_by_native(
        self, native_id: str, *, body: str | None, lifecycle: str, event: Mapping[str, Any]
    ) -> bool:
        with self.connect() as con:
            row = con.execute(
                """SELECT raw_json,source_id,chat_id FROM messages
                WHERE native_message_id=? ORDER BY timestamp DESC LIMIT 1""",
                (native_id,),
            ).fetchone()
        if not row or not self.should_archive_chat(row["chat_id"]):
            return False
        try:
            payload = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            payload = {"id": row["source_id"]}
        payload["id"] = row["source_id"]
        if body is not None:
            payload["body"] = body
        payload["archiveEvent"] = dict(event)
        return self.ingest_message(payload, lifecycle=lifecycle)

    def record_webhook(
        self, request_id: str, body_hash: str, event: Mapping[str, Any]
    ) -> bool:
        now = utc_now()
        full_raw = compact_json(dict(event))
        event_type = str(event.get("event") or "unknown")[:100]
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        target = payload.get("editedMessageId") or payload.get("revokedMessageId")
        chat_id = message_chat_id(payload)
        if event_type == "chat.archive":
            archive_chat_id = str(payload.get("id") or "").strip()
            chat_id = archive_chat_id if archive_chat_id else None
        selected = self.should_archive_chat(chat_id)
        if target and not chat_id:
            with self.connect() as con:
                target_row = con.execute(
                    """SELECT chat_id FROM messages WHERE native_message_id=?
                    ORDER BY timestamp DESC LIMIT 1""",
                    (str(target),),
                ).fetchone()
            chat_id = target_row["chat_id"] if target_row else None
            selected = self.should_archive_chat(chat_id)
        content_event = event_type in {
            "message",
            "message.any",
            "message.edited",
            "message.revoked",
        }
        if event_type == "chat.archive":
            raw = compact_json(
                {
                    "event": event_type,
                    "payload": {
                        "id": chat_id,
                        "archived": bool(payload.get("archived")),
                        "timestamp": finite_timestamp(payload.get("timestamp")),
                    },
                }
            )
        else:
            raw = (
                full_raw
                if selected or not content_event
                else compact_json({"event": event_type, "excluded_by_chat_policy": True})
            )
        if len(raw.encode("utf-8")) > MAX_RAW_ITEM_BYTES:
            raise ValueError("WAHA webhook payload exceeds archive item limit")
        with self.connect() as con:
            try:
                con.execute(
                    "INSERT INTO webhook_requests(request_id,body_hash,received_at) VALUES(?,?,?)",
                    (request_id, body_hash, now),
                )
            except sqlite3.IntegrityError:
                return False
            con.execute(
                """INSERT INTO source_events(
                request_id,event_type,target_native_id,chat_id,raw_json,received_at)
                VALUES(?,?,?,?,?,?)""",
                (request_id, event_type, target, chat_id, raw, now),
            )
        try:
            if event_type in {"message", "message.any"}:
                self.ingest_message(payload)
            elif event_type == "message.edited" and target:
                self._update_by_native(
                    str(target),
                    body=str(payload.get("body") or ""),
                    lifecycle="updated",
                    event=payload,
                )
            elif event_type == "message.revoked" and target:
                self._update_by_native(
                    str(target), body=None, lifecycle="deleted", event=payload
                )
            elif event_type == "chat.archive" and chat_id:
                self.upsert_chat(payload)
                if self.selection_confirmed() and not self.should_archive_chat(chat_id):
                    self.purge_chat(chat_id)
        except Exception:
            # WAHA retries non-2xx deliveries. Remove the idempotency marker so a
            # corrected/retried delivery can be processed instead of being lost.
            with self.connect() as con:
                con.execute("DELETE FROM source_events WHERE request_id=?", (request_id,))
                con.execute("DELETE FROM webhook_requests WHERE request_id=?", (request_id,))
            raise
        return True

    def start_sync(self, sync_type: str) -> int:
        with self.connect() as con:
            return con.execute(
                "INSERT INTO sync_runs(sync_type,started_at) VALUES(?,?)",
                (sync_type, utc_now()),
            ).lastrowid

    def finish_sync(
        self,
        sync_id: int,
        *,
        status: str,
        pages: int,
        seen: int,
        changed: int,
        error: str | None = None,
    ) -> None:
        with self.connect() as con:
            con.execute(
                """UPDATE sync_runs SET finished_at=?,status=?,pages=?,messages_seen=?,
                messages_changed=?,error=? WHERE id=?""",
                (utc_now(), status, pages, seen, changed, error, sync_id),
            )

    def latest_timestamp(self) -> int | None:
        with self.connect() as con:
            row = con.execute("SELECT MAX(timestamp) value FROM messages").fetchone()
            return row["value"] if row else None

    def latest_successful_sync_timestamp(self, sync_type: str) -> float | None:
        with self.connect() as con:
            row = con.execute(
                """SELECT finished_at FROM sync_runs
                WHERE sync_type=? AND status='completed' AND finished_at IS NOT NULL
                ORDER BY id DESC LIMIT 1""",
                (sync_type,),
            ).fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return None

    def stats(self) -> dict[str, Any]:
        with self.connect() as con:
            counts = {
                table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("messages", "message_versions", "chats", "identities", "change_log")
            }
            coverage = con.execute(
                "SELECT MIN(timestamp_iso) earliest,MAX(timestamp_iso) latest FROM messages"
            ).fetchone()
            sync = con.execute(
                """SELECT id,sync_type,started_at,finished_at,status,pages,
                messages_seen,messages_changed,error FROM sync_runs ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            return {
                "counts": counts,
                "coverage": dict(coverage) if coverage else {"earliest": None, "latest": None},
                "last_sync": dict(sync) if sync else None,
                "selection": self.selection_summary(),
            }

    def _source_item(self, row: Mapping[str, Any]) -> dict[str, Any]:
        sender_id = row.get("sender_id") if hasattr(row, "get") else row["sender_id"]
        sender_label = row.get("sender_label") if hasattr(row, "get") else row["sender_label"]
        participants = []
        if sender_id:
            participants.append(
                {
                    "source_id": sender_id,
                    "kind": "owner" if sender_id == "owner" else "contact",
                    "label": sender_label,
                    "metadata": {},
                }
            )
        return {
            "source_id": row["source_id"],
            "native_id": row["native_message_id"],
            "item_type": "message",
            "timestamp": row["timestamp_iso"],
            "modified_at": row["last_seen"] if "last_seen" in row.keys() else None,
            "lifecycle": row["lifecycle"],
            "title": None,
            "text": row["body"],
            "participants": participants,
            "location": None,
            "media": [],
            "content_hash": row["content_hash"],
            "metadata": {
                "conversation_id": row["chat_id"],
                "conversation_name": row["chat_name"] if "chat_name" in row.keys() else None,
                "from_me": bool(row["from_me"]),
                "message_type": row["message_type"],
                "has_media": bool(row["has_media"]),
                "reply_to_id": row["reply_to_id"],
                "version": row["version"],
            },
        }

    def search(self, query: str, *, limit: int, offset: int) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be empty")
        with self.connect() as con:
            backend = con.execute(
                "SELECT value FROM archive_meta WHERE key='search_backend'"
            ).fetchone()[0]
            if backend == "fts5":
                phrase = '"' + query.replace('"', '""') + '"'
                rows = con.execute(
                    """SELECT m.*,c.name chat_name FROM message_fts f
                    JOIN messages m ON m.source_id=f.source_id
                    LEFT JOIN chats c ON c.chat_id=m.chat_id
                    WHERE message_fts MATCH ? ORDER BY m.timestamp DESC,m.source_id
                    LIMIT ? OFFSET ?""",
                    (phrase, limit, offset),
                ).fetchall()
            else:
                pattern = f"%{query.casefold()}%"
                rows = con.execute(
                    """SELECT m.*,c.name chat_name FROM messages m
                    LEFT JOIN chats c ON c.chat_id=m.chat_id
                    WHERE lower(m.body) LIKE ? OR lower(m.sender_label) LIKE ? OR lower(c.name) LIKE ?
                    ORDER BY m.timestamp DESC,m.source_id LIMIT ? OFFSET ?""",
                    (pattern, pattern, pattern, limit, offset),
                ).fetchall()
            return [self._source_item(row) for row in rows]

    def changes(self, cursor: int, *, limit: int) -> tuple[list[dict[str, Any]], int, bool]:
        with self.connect() as con:
            rows = con.execute(
                """SELECT cl.id change_id,cl.source_id,cl.lifecycle,cl.content_hash,cl.version,
                mv.body,m.native_message_id,m.chat_id,m.sender_id,m.sender_label,m.from_me,
                m.timestamp_iso,m.message_type,m.has_media,m.reply_to_id,
                c.name chat_name,cl.changed_at last_seen
                FROM change_log cl
                JOIN message_versions mv ON mv.source_id=cl.source_id AND mv.version=cl.version
                JOIN messages m ON m.source_id=cl.source_id
                LEFT JOIN chats c ON c.chat_id=m.chat_id
                WHERE cl.id>? ORDER BY cl.id LIMIT ?""",
                (cursor, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = page[-1]["change_id"] if page else cursor
        return [self._source_item(row) for row in page], next_cursor, has_more

    def get_item(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                """SELECT m.*,c.name chat_name FROM messages m
                LEFT JOIN chats c ON c.chat_id=m.chat_id WHERE m.source_id=?""",
                (source_id,),
            ).fetchone()
            return self._source_item(row) if row else None

    def validate(self) -> dict[str, Any]:
        with self.connect() as con:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = con.execute("PRAGMA foreign_key_check").fetchall()
            return {"integrity": integrity, "foreign_key_violations": len(foreign_keys)}
