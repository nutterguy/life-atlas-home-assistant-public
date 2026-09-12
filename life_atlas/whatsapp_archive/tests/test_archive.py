from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON))

from archive import Archive  # noqa: E402


def message(*, body: str = "Flights booked for the island trip", timestamp: int = 1_700_000_000):
    return {
        "id": "false_447700900123@c.us_ABC123",
        "from": "447700900123@c.us",
        "fromMe": False,
        "chatId": "447700900123@c.us",
        "pushName": "Alex",
        "timestamp": timestamp,
        "type": "chat",
        "body": body,
        "hasMedia": False,
        "_data": {"key": {"id": "ABC123"}},
    }


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.directory.name) / "archive.sqlite3")

    def tearDown(self):
        self.directory.cleanup()

    def approve_chat(self, chat_id="447700900123@c.us", *, archived=False, mode="auto"):
        self.archive.upsert_chat({"id": chat_id, "name": "Approved", "archived": archived})
        self.archive.replace_inventory_metrics({chat_id: (1, 1_700_000_000, 1_700_000_000)})
        if mode != "auto":
            self.archive.set_chat_policy(chat_id, mode)
        self.archive.confirm_selection()

    def test_insert_replay_edit_and_revoke_preserve_revision_history(self):
        self.approve_chat()
        self.assertTrue(self.archive.upsert_message(message()))
        self.assertFalse(self.archive.upsert_message(message()))

        edited = {
            "event": "message.edited",
            "payload": {"editedMessageId": "ABC123", "body": "Flights moved to Friday"},
        }
        self.assertTrue(self.archive.record_webhook("req-edit", "hash-edit", edited))
        self.assertFalse(self.archive.record_webhook("req-edit", "hash-edit", edited))

        revoked = {
            "event": "message.revoked",
            "payload": {"revokedMessageId": "ABC123"},
        }
        self.assertTrue(self.archive.record_webhook("req-revoke", "hash-revoke", revoked))

        item = self.archive.get_item("false_447700900123@c.us_ABC123")
        self.assertIsNotNone(item)
        self.assertEqual(item["lifecycle"], "deleted")
        self.assertEqual(item["text"], "Flights moved to Friday")
        self.assertEqual(item["metadata"]["version"], 3)
        self.assertEqual(self.archive.stats()["counts"]["message_versions"], 3)

        replay = message()
        replay["body"] = "Flights moved to Friday"
        self.assertFalse(self.archive.ingest_message(replay))
        item = self.archive.get_item("false_447700900123@c.us_ABC123")
        self.assertEqual(item["lifecycle"], "deleted")
        self.assertEqual(item["metadata"]["version"], 3)

        changes, cursor, has_more = self.archive.changes(0, limit=2)
        self.assertEqual([entry["lifecycle"] for entry in changes], ["created", "updated"])
        self.assertEqual(cursor, 2)
        self.assertTrue(has_more)

    def test_search_item_shape_coverage_and_integrity(self):
        self.archive.upsert_chat({"id": "447700900123@c.us", "name": "Trip planning"})
        self.archive.upsert_message(message())
        results = self.archive.search("island trip", limit=10, offset=0)
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item["item_type"], "message")
        self.assertEqual(item["metadata"]["conversation_name"], "Trip planning")
        self.assertEqual(item["participants"][0]["label"], "Alex")
        self.assertEqual(item["timestamp"], "2023-11-14T22:13:20Z")
        self.assertEqual(self.archive.validate(), {"integrity": "ok", "foreign_key_violations": 0})

    def test_failed_webhook_processing_can_be_retried(self):
        self.approve_chat()
        bad = {
            "event": "message.any",
            "payload": {"chatId": "447700900123@c.us", "body": "no id"},
        }
        with self.assertRaises(ValueError):
            self.archive.record_webhook("retry-me", "bad", bad)
        good = {"event": "message.any", "payload": message()}
        self.assertTrue(self.archive.record_webhook("retry-me", "good", good))
        self.assertIsNotNone(self.archive.get_item("false_447700900123@c.us_ABC123"))

    def test_raw_webhook_and_message_are_retained_locally(self):
        self.approve_chat()
        event = {"event": "message.any", "payload": message()}
        self.archive.record_webhook("raw-1", "body-hash", event)
        with self.archive.connect() as con:
            raw_event = con.execute("SELECT raw_json FROM source_events").fetchone()[0]
            raw_message = con.execute("SELECT raw_json FROM messages").fetchone()[0]
        self.assertEqual(json.loads(raw_event), event)
        self.assertEqual(json.loads(raw_message)["body"], message()["body"])

    def test_completed_full_sync_timestamp_survives_restart(self):
        sync_id = self.archive.start_sync("full")
        self.archive.finish_sync(sync_id, status="completed", pages=1, seen=1, changed=1)
        timestamp = Archive(self.archive.database).latest_successful_sync_timestamp("full")
        self.assertIsInstance(timestamp, float)
        self.assertGreater(timestamp, 0)

    def test_inventory_defaults_archived_chats_to_excluded_and_gates_content(self):
        active = "active@c.us"
        archived = "old@c.us"
        self.archive.upsert_chat({"id": active, "name": "Active", "archived": False})
        self.archive.upsert_chat({"id": archived, "name": "Old", "archived": True})
        self.archive.replace_inventory_metrics(
            {
                active: (10, 100, 200),
                archived: (20, 50, 150),
            }
        )
        summary = self.archive.selection_summary()
        self.assertFalse(summary["confirmed"])
        self.assertEqual((summary["included"], summary["excluded"]), (1, 1))
        self.assertEqual((summary["included_messages"], summary["excluded_messages"]), (10, 20))

        gated = {**message(), "id": "active-1", "chatId": active, "from": active}
        self.assertFalse(self.archive.ingest_message(gated))
        self.assertIsNone(self.archive.get_item("active-1"))

        self.archive.confirm_selection()
        self.assertTrue(self.archive.ingest_message(gated))
        excluded = {**message(), "id": "old-1", "chatId": archived, "from": archived}
        self.assertFalse(self.archive.ingest_message(excluded))

    def test_explicit_include_and_exclude_override_archived_default_and_purge(self):
        chat_id = "archive@c.us"
        self.approve_chat(chat_id, archived=True, mode="include")
        payload = {**message(), "id": "archive-1", "chatId": chat_id, "from": chat_id}
        self.assertTrue(self.archive.ingest_message(payload))
        result = self.archive.set_chat_policy(chat_id, "exclude")
        self.assertFalse(result["effective_selected"])
        self.assertEqual(result["purged_messages"], 1)
        self.assertIsNone(self.archive.get_item("archive-1"))

    def test_unapproved_webhook_retains_audit_but_redacts_message_body(self):
        event = {"event": "message.any", "payload": message(body="private body")}
        self.assertTrue(self.archive.record_webhook("unapproved", "hash", event))
        with self.archive.connect() as con:
            raw = con.execute("SELECT raw_json FROM source_events").fetchone()[0]
        self.assertNotIn("private body", raw)
        self.assertTrue(json.loads(raw)["excluded_by_chat_policy"])

    def test_chat_inventory_persists_only_allowlisted_metadata(self):
        chat_id = "metadata-only@c.us"
        self.archive.upsert_chat(
            {
                "id": chat_id,
                "name": "Metadata only",
                "archived": True,
                "lastMessage": {"body": "must not enter the durable archive"},
                "_chat": {"engineSecret": "also excluded"},
            }
        )
        with self.archive.connect() as con:
            raw = con.execute(
                "SELECT raw_json FROM chats WHERE chat_id=?", (chat_id,)
            ).fetchone()[0]
        self.assertNotIn("must not enter", raw)
        self.assertNotIn("engineSecret", raw)
        self.assertEqual(json.loads(raw)["id"], chat_id)

    def test_archiving_an_auto_policy_chat_purges_its_durable_messages(self):
        chat_id = "later-archived@c.us"
        self.approve_chat(chat_id)
        payload = {**message(), "id": "later-archived-1", "chatId": chat_id, "from": chat_id}
        self.assertTrue(self.archive.ingest_message(payload))
        event = {
            "event": "chat.archive",
            "payload": {
                "id": chat_id,
                "archived": True,
                "timestamp": 1_700_000_010,
                "lastMessage": {"body": "must be discarded with the chat"},
            },
        }
        self.assertTrue(self.archive.record_webhook("archive-chat", "hash", event))
        self.assertIsNone(self.archive.get_item("later-archived-1"))
        policy = self.archive.get_chat_policy(chat_id)
        self.assertTrue(policy["archived"])
        self.assertFalse(policy["effective_selected"])
        with self.archive.connect() as con:
            retained = " ".join(
                row[0]
                for row in con.execute(
                    "SELECT raw_json FROM source_events UNION ALL SELECT raw_json FROM chats"
                )
            )
        self.assertNotIn("must be discarded", retained)

    def test_schema_v1_is_migrated_without_losing_chat_inventory(self):
        legacy = Path(self.directory.name) / "legacy.sqlite3"
        con = sqlite3.connect(legacy)
        try:
            con.executescript(
                """
                CREATE TABLE chats(
                  chat_id TEXT PRIMARY KEY,name TEXT,chat_type TEXT,last_message_ts INTEGER,
                  raw_json TEXT NOT NULL DEFAULT '{}',first_seen TEXT NOT NULL,last_seen TEXT NOT NULL
                );
                CREATE TABLE source_events(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT,event_type TEXT NOT NULL,
                  target_native_id TEXT,raw_json TEXT NOT NULL,received_at TEXT NOT NULL
                );
                INSERT INTO chats VALUES('old@c.us','Old chat','direct',123,'{}','now','now');
                """
            )
            con.commit()
        finally:
            con.close()
        migrated = Archive(legacy)
        chat = migrated.get_chat_policy("old@c.us")
        self.assertIsNotNone(chat)
        self.assertFalse(chat["archived"])
        self.assertTrue(chat["effective_selected"])
        with migrated.connect() as con:
            columns = {row["name"] for row in con.execute("PRAGMA table_info(source_events)")}
        self.assertIn("chat_id", columns)


if __name__ == "__main__":
    unittest.main()
