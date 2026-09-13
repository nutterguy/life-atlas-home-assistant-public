from __future__ import annotations

import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import connector_registry as registry
from connector_http import ProtocolTestServer, make_protocol_handler
from mock_connector import MockConnectorService


class ConnectorRegistryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.data = Path(self.dir.name)
        os.environ.pop("LIFE_ATLAS_REFERENCE_CONNECTOR_URL", None)
        registry.initialise(self.data)

    def serve(self, mode="healthy"):
        server = ProtocolTestServer(MockConnectorService(mode))
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        return server.base_url

    def serve_keyed(self, expected_key):
        """A Connector Protocol server that answers 401 without the right key."""
        service = MockConnectorService("healthy")

        class KeyedHandler(make_protocol_handler(service)):
            def do_GET(self):
                if self.headers.get("X-Life-Atlas-Connector-Key") != expected_key:
                    return self._json_error(401, "unauthorised", "Connector key rejected")
                return super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), KeyedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        host, port = server.server_address[:2]
        return f"http://{host}:{port}/"

    def register(self, base_url, connector_id="reference", **overrides):
        payload = {"connector_id": connector_id, "name": "Reference", "kind": "source",
                   "base_url": base_url, "enabled": True}
        payload.update(overrides)
        return registry.save_connector(self.data, payload)

    def test_registry_lives_outside_the_canonical_database(self):
        """Machine addresses and connector keys must not travel with the life record."""
        self.assertTrue(registry.registry_path(self.data).exists())
        self.assertFalse((self.data / "life_atlas.sqlite3").exists())

    def test_known_connectors_are_seeded_switched_off(self):
        entries = {c["connector_id"]: c for c in registry.list_connectors(self.data)}
        self.assertEqual(set(entries), {"reference", "whatsapp_archive", "chatgpt_bridge"})
        for entry in entries.values():
            self.assertFalse(entry["enabled"])
            self.assertEqual(entry["state"], "unknown")
        self.assertTrue(entries["whatsapp_archive"]["reads_into_life_atlas"])
        self.assertFalse(entries["whatsapp_archive"]["reads_from_life_atlas"])

    def test_a_removed_connector_is_not_seeded_back(self):
        registry.delete_connector(self.data, "whatsapp_archive")
        registry.initialise(self.data)
        ids = [c["connector_id"] for c in registry.list_connectors(self.data)]
        self.assertNotIn("whatsapp_archive", ids)

    def test_a_connector_can_read_in_read_out_or_both(self):
        entry = registry.save_connector(self.data, {
            "connector_id": "chatgpt_bridge", "name": "ChatGPT bridge",
            "kind": "bidirectional", "base_url": "http://local-bridge:8099",
        })
        self.assertTrue(entry["reads_into_life_atlas"])
        self.assertTrue(entry["reads_from_life_atlas"])

    def test_an_outbound_connector_needs_no_address(self):
        entry = registry.save_connector(self.data, {
            "connector_id": "chatgpt_bridge", "name": "ChatGPT bridge", "kind": "consumer",
        })
        self.assertEqual(entry["base_url"], "")
        self.assertTrue(entry["reads_from_life_atlas"])

    def test_an_inbound_connector_without_an_address_is_refused(self):
        with self.assertRaises(registry.RegistryError):
            registry.save_connector(self.data, {"connector_id": "x", "name": "X", "kind": "source"})

    def test_an_address_must_be_http(self):
        with self.assertRaises(registry.RegistryError):
            self.register("file:///etc/passwd")

    def test_probe_records_identity_capabilities_and_state(self):
        self.register(self.serve())
        entry = registry.probe(self.data, "reference")
        self.assertEqual(entry["state"], "available")
        self.assertEqual(entry["info"]["protocol_version"], "1.0")
        self.assertEqual(entry["info"]["connector_version"], "0.1.0")
        self.assertIn("search", entry["capabilities"])
        self.assertIsNotNone(entry["last_checked_at"])

    def test_a_broken_connector_becomes_its_own_state_not_an_exception(self):
        self.register("http://127.0.0.1:9/")
        entry = registry.probe(self.data, "reference")
        self.assertEqual(entry["state"], "unavailable")
        self.assertTrue(entry["error"])

    def test_an_offline_source_is_reported_as_unavailable(self):
        self.register(self.serve("offline"))
        self.assertEqual(registry.probe(self.data, "reference")["state"], "unavailable")

    def test_identity_mismatch_fails_closed_as_incompatible(self):
        self.register(self.serve(), connector_id="not_the_reference")
        entry = registry.probe(self.data, "not_the_reference")
        self.assertEqual(entry["state"], "incompatible")

    def test_a_switched_off_connector_is_never_contacted(self):
        self.register("http://127.0.0.1:9/", enabled=False)
        self.assertEqual(registry.probe(self.data, "reference")["state"], "disabled")

    def test_switching_off_clears_the_stale_status(self):
        self.register(self.serve())
        registry.probe(self.data, "reference")
        entry = registry.set_enabled(self.data, "reference", False)
        self.assertEqual(entry["state"], "disabled")
        self.assertIsNone(entry["last_checked_at"])

    def test_the_connector_key_is_stored_but_never_returned(self):
        entry = self.register(self.serve(), auth_key="secret-key")
        self.assertTrue(entry["has_key"])
        self.assertNotIn("auth_key", entry)
        self.assertNotIn("secret-key", str(registry.list_connectors(self.data)))

    def test_saving_without_a_key_field_keeps_the_stored_key(self):
        self.register(self.serve(), auth_key="secret-key")
        entry = registry.save_connector(self.data, {
            "connector_id": "reference", "name": "Renamed", "kind": "source",
            "base_url": "http://example.invalid:8098", "enabled": True,
        })
        self.assertTrue(entry["has_key"])
        self.assertEqual(entry["name"], "Renamed")

    def test_an_empty_key_field_clears_the_stored_key(self):
        self.register(self.serve(), auth_key="secret-key")
        entry = self.register(self.serve(), auth_key="")
        self.assertFalse(entry["has_key"])

    def test_reconfiguring_discards_the_status_of_the_old_configuration(self):
        self.register(self.serve())
        registry.probe(self.data, "reference")
        entry = self.register("http://127.0.0.1:9/")
        self.assertEqual(entry["state"], "unknown")
        self.assertIsNone(entry["last_checked_at"])

    def test_the_key_is_sent_to_the_connector_as_a_header(self):
        """A connector on the private app network may still demand its own key."""
        base_url = self.serve_keyed("expected-key")
        self.register(base_url, auth_key="expected-key")
        self.assertEqual(registry.probe(self.data, "reference")["state"], "available")

    def test_a_rejected_key_is_reported_as_needing_authorisation(self):
        base_url = self.serve_keyed("expected-key")
        self.register(base_url, auth_key="wrong-key")
        entry = registry.probe(self.data, "reference")
        self.assertEqual(entry["state"], "auth_required")
        self.assertTrue(entry["error"])

    def test_a_missing_key_is_reported_as_needing_authorisation(self):
        self.register(self.serve_keyed("expected-key"))
        self.assertEqual(registry.probe(self.data, "reference")["state"], "auth_required")

    def test_an_outbound_connector_reports_whether_its_credential_exists(self):
        registry.save_connector(self.data, {
            "connector_id": "chatgpt_bridge", "name": "ChatGPT bridge",
            "kind": "consumer", "enabled": True,
        })
        os.environ.pop("LIFE_ATLAS_AGENT_API_KEY", None)
        self.assertEqual(registry.probe(self.data, "chatgpt_bridge")["state"], "auth_required")

        os.environ["LIFE_ATLAS_AGENT_API_KEY"] = "machine-key"
        self.addCleanup(os.environ.pop, "LIFE_ATLAS_AGENT_API_KEY", None)
        entry = registry.probe(self.data, "chatgpt_bridge")
        self.assertEqual(entry["state"], "available")
        self.assertIn("agent_api", entry["capabilities"])

    def test_inspecting_an_archive_returns_source_items_and_promotes_nothing(self):
        self.register(self.serve())
        result = registry.search(self.data, "reference", "harbour")
        self.assertIn("photo-001", [item["source_id"] for item in result["items"]])
        self.assertFalse((self.data / "life_atlas.sqlite3").exists())

    def test_a_switched_off_connector_cannot_be_searched(self):
        self.register(self.serve(), enabled=False)
        with self.assertRaises(registry.RegistryError):
            registry.search(self.data, "reference", "harbour")

    def test_an_outbound_connector_cannot_be_searched(self):
        registry.save_connector(self.data, {
            "connector_id": "chatgpt_bridge", "name": "Bridge", "kind": "consumer", "enabled": True,
        })
        with self.assertRaises(registry.RegistryError):
            registry.search(self.data, "chatgpt_bridge", "harbour")

    def test_an_unknown_connector_is_refused(self):
        for call in (lambda: registry.get_connector(self.data, "nope"),
                     lambda: registry.probe(self.data, "nope"),
                     lambda: registry.delete_connector(self.data, "nope")):
            with self.assertRaises(registry.RegistryError):
                call()

    def test_an_identifier_cannot_escape_its_own_row(self):
        for bad in ("", "../etc", "has space", "a" * 65):
            with self.assertRaises(registry.RegistryError):
                registry.save_connector(self.data, {"connector_id": bad, "kind": "consumer"})

    def test_probe_all_leaves_every_connector_with_a_state(self):
        self.register(self.serve())
        entries = registry.probe_all(self.data)
        self.assertEqual(len(entries), 3)
        self.assertNotIn("unknown", {entry["state"] for entry in entries})


if __name__ == "__main__":
    unittest.main()
