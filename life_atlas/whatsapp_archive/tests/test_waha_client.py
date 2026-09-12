from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON))

from archive import Archive  # noqa: E402
from waha_client import HistorySynchronizer, WahaClient, WahaError  # noqa: E402


class RecordingClient(WahaClient):
    def __init__(self, api_key_file, webhook_key_file, responses):
        super().__init__(api_key_file, webhook_key_file)
        self.responses = list(responses)
        self.calls = []

    def _json(self, method, path, body=None, *, admin=False):
        self.calls.append((method, path, body, admin))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeHistoryClient:
    def __init__(self):
        self.message_calls = []
        self.archived = False

    def session(self, name):
        return {"name": name, "status": "WORKING"}

    def list_chats(self, name, *, limit, offset):
        if offset:
            return []
        return [{"id": "chat@c.us", "name": "History", "archived": self.archived}]

    def list_messages(self, name, *, limit, offset, timestamp_gte=None):
        self.message_calls.append((offset, timestamp_gte))
        if offset:
            return []
        return [
            {
                "id": "false_chat@c.us_HISTORY1",
                "from": "chat@c.us",
                "chatId": "chat@c.us",
                "timestamp": 1_700_000_100,
                "body": "Historical hello",
                "type": "chat",
            }
        ]


class WahaClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.api = root / "api"
        self.webhook = root / "webhook"
        self.api.write_text("a" * 43, encoding="ascii")
        self.webhook.write_text("h" * 43, encoding="ascii")

    def tearDown(self):
        self.directory.cleanup()

    def test_new_session_enables_noweb_full_sync_without_online_presence(self):
        client = RecordingClient(
            self.api,
            self.webhook,
            [
                WahaError("WAHA GET /api/sessions/life-atlas returned HTTP 404"),
                {"name": "life-atlas", "status": "STARTING"},
                {
                    "key": "key_" + "r" * 40,
                    "actions": {"read": True, "send": False, "control": True},
                },
            ],
        )
        result = client.ensure_session("life-atlas")
        self.assertEqual(result["status"], "STARTING")
        method, path, body, admin = client.calls[1]
        self.assertEqual((method, path, admin), ("POST", "/api/sessions", True))
        noweb = body["config"]["noweb"]
        self.assertEqual(noweb["store"], {"enabled": True, "fullSync": True})
        self.assertFalse(noweb["markOnline"])
        webhook = body["config"]["webhooks"][0]
        self.assertEqual(webhook["url"], "http://127.0.0.1:8110/internal/waha-events")
        self.assertEqual(webhook["hmac"]["key"], "h" * 43)
        self.assertEqual(
            webhook["events"],
            [
                "session.status",
                "message.any",
                "message.edited",
                "message.revoked",
                "chat.archive",
            ],
        )
        _, key_path, key_body, key_admin = client.calls[2]
        self.assertEqual((key_path, key_admin), ("/api/keys/", True))
        self.assertEqual(
            key_body["actions"],
            {"read": True, "send": False, "control": True, "setting": False, "app": False, "delete": False},
        )
        self.assertFalse(self.api.exists())
        self.assertEqual(client.scoped_api_key_file.read_text(encoding="ascii"), "key_" + "r" * 40)

    def test_full_and_incremental_history_are_idempotent(self):
        archive = Archive(Path(self.directory.name) / "archive.sqlite3")
        client = FakeHistoryClient()
        sync = HistorySynchronizer(client, archive, "life-atlas", page_size=50)
        first = sync.run(full=True)
        self.assertEqual(first["messages_changed"], 0)
        self.assertTrue(archive.inventory_available())
        archive.confirm_selection()
        second = sync.run(full=True)
        third = sync.run(full=False)
        self.assertEqual(second["messages_changed"], 1)
        self.assertEqual(third["messages_changed"], 0)
        self.assertIsNone(client.message_calls[0][1])
        self.assertEqual(client.message_calls[4][1], 1_699_999_800)
        self.assertEqual(archive.stats()["counts"]["messages"], 1)

    def test_incremental_reconciliation_refreshes_archive_state_and_purges(self):
        archive = Archive(Path(self.directory.name) / "archive.sqlite3")
        client = FakeHistoryClient()
        sync = HistorySynchronizer(client, archive, "life-atlas", page_size=50)
        sync.run(full=True)
        archive.confirm_selection()
        sync.run(full=True)
        self.assertEqual(archive.stats()["counts"]["messages"], 1)

        client.archived = True
        sync.run(full=False)
        policy = archive.get_chat_policy("chat@c.us")
        self.assertTrue(policy["archived"])
        self.assertFalse(policy["effective_selected"])
        self.assertEqual(archive.stats()["counts"]["messages"], 0)

        client.archived = False
        result = sync.run(full=False)
        self.assertTrue(result["backfill_triggered"])
        self.assertIsNone(client.message_calls[-2][1])
        self.assertEqual(archive.stats()["counts"]["messages"], 1)

    def test_public_client_surface_has_no_message_mutation_method(self):
        public = {name for name in dir(WahaClient) if not name.startswith("_")}
        forbidden = {"send_message", "send_text", "send_image", "reply", "react", "forward", "delete_message", "edit_message", "mark_seen", "start_typing"}
        self.assertTrue(public.isdisjoint(forbidden))
        self.assertTrue({"list_messages", "list_chats", "session", "qr"}.issubset(public))


if __name__ == "__main__":
    unittest.main()
