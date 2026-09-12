from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ADDON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON))

import adapter  # noqa: E402
from archive import Archive  # noqa: E402


class FakeWaha:
    @staticmethod
    def qr(name):
        return b"\x89PNG\r\n\x1a\nfixture"


class FakeService:
    def __init__(self, root: Path):
        self.session_name = "life-atlas"
        self.archive = Archive(root / "archive.sqlite3")
        self.webhook_key_file = root / "webhook-key"
        self.connector_key_file = root / "connector-key"
        self.webhook_key_file.write_text("w" * 48, encoding="ascii")
        self.connector_key_file.write_text("c" * 48, encoding="ascii")
        self.waha = FakeWaha()
        self.actions = []

    def connector_key_matches(self, supplied):
        return supplied == "c" * 48

    @staticmethod
    def safe_session():
        return {"name": "life-atlas", "status": "WORKING", "engine": "NOWEB", "paired": True}

    def management_status(self):
        return {
            "session": self.safe_session(),
            "archive": self.archive.stats(),
            "sync": {"status": "idle"},
            "read_only_boundary": True,
            "waha_version": adapter.WAHA_VERSION,
            "connector_version": adapter.CONNECTOR_VERSION,
        }

    def start_session(self):
        self.actions.append("start")
        return {"status": "STARTING", "name": self.session_name}

    def restart_session(self):
        self.actions.append("restart")
        return {"status": "STARTING", "name": self.session_name}

    def trigger_sync(self, *, full):
        self.actions.append("full" if full else "incremental")
        return {"status": "running", "sync_type": "full" if full else "incremental"}

    def chat_inventory(self, *, limit, offset):
        chats, total = self.archive.list_chat_policies(limit=limit, offset=offset)
        next_offset = offset + len(chats)
        return {
            "chats": chats,
            "total": total,
            "next_offset": next_offset if next_offset < total else None,
            "selection": self.archive.selection_summary(),
        }

    def set_chat_policy(self, chat_id, mode):
        return self.archive.set_chat_policy(chat_id, mode)

    def confirm_chat_selection(self):
        self.archive.confirm_selection()
        return {"selection": self.archive.selection_summary(), "sync": self.trigger_sync(full=True)}


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.service = FakeService(Path(cls.directory.name))
        adapter.SERVICE = cls.service
        cls.connector = ThreadingHTTPServer(("127.0.0.1", 0), adapter.ConnectorHandler)
        cls.management = ThreadingHTTPServer(("127.0.0.1", 0), adapter.ManagementHandler)
        cls.threads = [
            threading.Thread(target=cls.connector.serve_forever, daemon=True),
            threading.Thread(target=cls.management.serve_forever, daemon=True),
        ]
        for thread in cls.threads:
            thread.start()
        cls.connector_url = f"http://127.0.0.1:{cls.connector.server_address[1]}"
        cls.management_url = f"http://127.0.0.1:{cls.management.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        for server in (cls.connector, cls.management):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(timeout=2)
        cls.directory.cleanup()

    @staticmethod
    def json_request(url, *, body=None, headers=None, method=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        merged = dict(headers or {})
        if body is not None:
            merged.setdefault("Content-Type", "application/json")
        request = Request(url, data=data, headers=merged, method=method)
        with urlopen(request, timeout=3) as response:
            return response, json.loads(response.read().decode("utf-8"))

    def test_connector_requires_key_and_reports_protocol(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.connector_url + "/v1/info", timeout=3)
        self.assertEqual(caught.exception.code, 401)

        response, payload = self.json_request(
            self.connector_url + "/v1/info",
            headers={"X-Life-Atlas-Connector-Key": "c" * 48},
        )
        self.assertEqual(response.headers["X-Life-Atlas-Connector-Protocol"], "1")
        self.assertEqual(payload["connector_id"], "whatsapp")

    def test_chat_inventory_policy_and_confirmation_endpoints(self):
        self.service.archive.upsert_chat(
            {"id": "archived@c.us", "name": "Noisy archive", "archived": True}
        )
        self.service.archive.replace_inventory_metrics(
            {"archived@c.us": (42, 1_600_000_000, 1_700_000_000)}
        )
        _, inventory = self.json_request(self.management_url + "/manage/chats?limit=10&offset=0")
        chat = next(item for item in inventory["chats"] if item["chat_id"] == "archived@c.us")
        self.assertFalse(chat["effective_selected"])
        self.assertEqual(chat["observed_message_count"], 42)

        action_headers = {"X-Life-Atlas-Action": "1"}
        _, changed = self.json_request(
            self.management_url + "/manage/chats/policy",
            body={"chat_id": "archived@c.us", "mode": "include"},
            headers=action_headers,
            method="POST",
        )
        self.assertTrue(changed["effective_selected"])
        _, confirmed = self.json_request(
            self.management_url + "/manage/chats/confirm",
            body={},
            headers=action_headers,
            method="POST",
        )
        self.assertTrue(confirmed["selection"]["confirmed"])

    def test_connector_search_changes_and_item(self):
        event = {
            "id": "false_chat@c.us_HTTP1",
            "from": "chat@c.us",
            "chatId": "chat@c.us",
            "timestamp": 1_700_000_000,
            "body": "Dinner booked at seven",
            "type": "chat",
        }
        self.service.archive.upsert_message(event)
        headers = {"Authorization": "Bearer " + "c" * 48}
        _, search = self.json_request(
            self.connector_url + "/v1/search", body={"query": "Dinner booked"}, headers=headers, method="POST"
        )
        self.assertEqual(search["items"][0]["source_id"], event["id"])
        _, changes = self.json_request(
            self.connector_url + "/v1/changes", body={"cursor": "0"}, headers=headers, method="POST"
        )
        self.assertGreaterEqual(len(changes["items"]), 1)
        _, item = self.json_request(
            self.connector_url + "/v1/item", body={"source_id": event["id"]}, headers=headers, method="POST"
        )
        self.assertEqual(item["item"]["text"], "Dinner booked at seven")

    def test_hmac_webhook_accepts_once_and_rejects_tampering(self):
        self.service.archive.upsert_chat({"id": "chat@c.us", "name": "Live", "archived": False})
        self.service.archive.replace_inventory_metrics({"chat@c.us": (1, 1_700_000_001, 1_700_000_001)})
        self.service.archive.confirm_selection()
        event = {
            "event": "message.any",
            "payload": {
                "id": "false_chat@c.us_LIVE1",
                "from": "chat@c.us",
                "chatId": "chat@c.us",
                "timestamp": 1_700_000_001,
                "body": "Live incoming message",
                "type": "chat",
            },
        }
        raw = json.dumps(event, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(("w" * 48).encode("ascii"), raw, hashlib.sha512).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Request-Id": "live-request-1",
            "X-Webhook-Timestamp": str(int(time.time() * 1000)),
            "X-Webhook-Hmac": signature,
            "X-Webhook-Hmac-Algorithm": "sha512",
        }
        request = Request(self.management_url + "/internal/waha-events", data=raw, headers=headers, method="POST")
        with urlopen(request, timeout=3) as response:
            self.assertTrue(json.loads(response.read())["accepted"])
        with urlopen(request, timeout=3) as response:
            self.assertFalse(json.loads(response.read())["accepted"])
        self.assertIsNotNone(self.service.archive.get_item("false_chat@c.us_LIVE1"))

        headers["X-Webhook-Request-Id"] = "live-request-2"
        headers["X-Webhook-Hmac"] = "0" * 128
        bad = Request(self.management_url + "/internal/waha-events", data=raw, headers=headers, method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(bad, timeout=3)
        self.assertEqual(caught.exception.code, 401)

    def test_management_mutations_require_ingress_action_header(self):
        request = Request(
            self.management_url + "/manage/sync/full",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 403)

        request.add_header("X-Life-Atlas-Action", "1")
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload, {"status": "running", "sync_type": "full"})


if __name__ == "__main__":
    unittest.main()

