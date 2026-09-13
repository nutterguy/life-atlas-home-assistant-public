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
        self.overview = []
        self.lids = {}
        self.contacts = {}

    def session(self, name):
        return {"name": name, "status": "WORKING"}

    def chats_overview(self, name, *, limit, offset):
        return [] if offset else list(self.overview)

    def lid_phone_numbers(self, name):
        return dict(self.lids)

    def contact_names(self, name):
        return dict(self.contacts)

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

    def test_contact_names_skip_numeric_name_and_use_pushname(self):
        client = RecordingClient(
            self.api,
            self.webhook,
            [[{
                "id": "447700900123@c.us",
                "number": "447700900123",
                "name": "+44 7700 900123",
                "shortName": "447700900123",
                "pushname": "Alex Example",
            }]],
        )

        names = client.contact_names("life-atlas")

        self.assertEqual(names["447700900123@c.us"], "Alex Example")
        self.assertEqual(names["447700900123"], "Alex Example")

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
        self.assertEqual(webhook["url"], "http://127.0.0.1:8099/internal/waha-events")
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

    def _named_chats(self, client, archive):
        sync = HistorySynchronizer(client, archive, "life-atlas", page_size=50)
        sync.run(full=True)
        chats, _ = archive.list_chat_policies(limit=100, offset=0)
        return {chat["chat_id"]: chat["name"] for chat in chats}

    def test_a_lid_chat_is_named_from_the_address_book(self):
        """WhatsApp addresses a direct chat by an opaque LID, not a phone number."""
        archive = Archive(Path(self.directory.name) / "lid.sqlite3")
        client = FakeHistoryClient()
        client.list_chats = lambda name, *, limit, offset: (
            [] if offset else [{"id": "12099090665628@lid", "name": None, "archived": False}]
        )
        client.lids = {"12099090665628@lid": "447700900123"}
        client.contacts = {"447700900123@c.us": "Sam Okonkwo"}

        self.assertEqual(self._named_chats(client, archive)["12099090665628@lid"], "Sam Okonkwo")

    def test_numeric_chat_name_is_replaced_by_address_book_name(self):
        client = FakeHistoryClient()
        archive = Archive(Path(self.directory.name) / "numeric-name.sqlite3")
        client.list_chats = lambda name, *, limit, offset: (
            [] if offset else [{"id": "447700900123@c.us", "name": "+44 7700 900123", "archived": False}]
        )
        client.overview = [{"id": "447700900123@c.us", "name": "+44 7700 900123"}]
        client.contacts = {"447700900123@c.us": "Sam Okonkwo"}

        HistorySynchronizer(client, archive, "life-atlas")._sync_chats()

        self.assertEqual(self._named_chats(client, archive)["447700900123@c.us"], "Sam Okonkwo")

    def test_lid_keyed_contact_names_the_corresponding_phone_chat(self):
        client = FakeHistoryClient()
        archive = Archive(Path(self.directory.name) / "lid-phone.sqlite3")
        client.list_chats = lambda name, *, limit, offset: (
            [] if offset else [{"id": "447700900123@c.us", "name": "+447700900123", "archived": False}]
        )
        client.lids = {"12099090665628@lid": "447700900123"}
        client.contacts = {"12099090665628@lid": "Sam Okonkwo"}

        HistorySynchronizer(client, archive, "life-atlas")._sync_chats()

        self.assertEqual(self._named_chats(client, archive)["447700900123@c.us"], "Sam Okonkwo")

    def test_a_bare_lid_mapping_still_matches_a_suffixed_chat_id(self):
        """WAHA may give either side bare or suffixed; the chat id carries @lid."""
        archive = Archive(Path(self.directory.name) / "bare.sqlite3")
        client = FakeHistoryClient()
        client.list_chats = lambda name, *, limit, offset: (
            [] if offset else [{"id": "12099090665628@lid", "name": None, "archived": False}]
        )
        client.lids = {"12099090665628": "447700900123"}
        client.contacts = {"447700900123": "Sam Okonkwo"}

        self.assertEqual(self._named_chats(client, archive)["12099090665628@lid"], "Sam Okonkwo")

    def test_a_lid_with_no_contact_falls_back_to_the_phone_number(self):
        archive = Archive(Path(self.directory.name) / "lid2.sqlite3")
        client = FakeHistoryClient()
        client.list_chats = lambda name, *, limit, offset: (
            [] if offset else [{"id": "12099090665628@lid", "name": None, "archived": False}]
        )
        client.lids = {"12099090665628@lid": "447700900123"}

        self.assertEqual(self._named_chats(client, archive)["12099090665628@lid"], "+447700900123")

    def test_the_overview_name_is_preferred_over_a_derived_one(self):
        archive = Archive(Path(self.directory.name) / "overview.sqlite3")
        client = FakeHistoryClient()
        client.list_chats = lambda name, *, limit, offset: (
            [] if offset else [{"id": "12099090665628@lid", "name": None, "archived": False}]
        )
        client.overview = [{"id": "12099090665628@lid", "name": "Book club"}]
        client.lids = {"12099090665628@lid": "447700900123"}
        client.contacts = {"447700900123@c.us": "Sam Okonkwo"}

        self.assertEqual(self._named_chats(client, archive)["12099090665628@lid"], "Book club")

    def test_an_unresolvable_phone_chat_still_shows_its_number(self):
        archive = Archive(Path(self.directory.name) / "phone.sqlite3")
        client = FakeHistoryClient()
        client.list_chats = lambda name, *, limit, offset: (
            [] if offset else [{"id": "447700900555@s.whatsapp.net", "name": None, "archived": False}]
        )

        self.assertEqual(
            self._named_chats(client, archive)["447700900555@s.whatsapp.net"], "+447700900555"
        )

    def test_a_name_that_cannot_be_resolved_is_left_alone(self):
        archive = Archive(Path(self.directory.name) / "unknown.sqlite3")
        client = FakeHistoryClient()
        client.list_chats = lambda name, *, limit, offset: (
            [] if offset else [{"id": "0@c.us", "name": None, "archived": False}]
        )

        self.assertIsNone(self._named_chats(client, archive)["0@c.us"])

    def test_public_client_surface_has_no_message_mutation_method(self):
        public = {name for name in dir(WahaClient) if not name.startswith("_")}
        forbidden = {"send_message", "send_text", "send_image", "reply", "react", "forward", "delete_message", "edit_message", "mark_seen", "start_typing"}
        self.assertTrue(public.isdisjoint(forbidden))
        self.assertTrue({"list_messages", "list_chats", "session", "qr"}.issubset(public))


if __name__ == "__main__":
    unittest.main()
