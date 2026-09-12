from __future__ import annotations

import unittest

from connectors import (
    ConnectorClient,
    ConnectorProtocolError,
    ConnectorState,
    ConnectorTimeout,
    ConnectorUnavailable,
    UnsupportedCapability,
)
from mock_connector import MockConnectorService, MockConnectorTransport


class MockConnectorTests(unittest.TestCase):
    def client(self, scenario: str = "healthy") -> tuple[ConnectorClient, MockConnectorService]:
        service = MockConnectorService(scenario)
        client = ConnectorClient(service.connector_id, MockConnectorTransport(service))
        return client, service

    def test_reference_connector_reports_identity_version_and_capabilities(self):
        client, _ = self.client()
        info = client.info()
        self.assertEqual(info.connector_id, "reference")
        self.assertEqual(info.protocol_version, "1.0")
        self.assertEqual(info.connector_version, "0.1.0")
        self.assertTrue(client.capabilities().supports("search"))
        self.assertTrue(client.capabilities().supports("media"))

    def test_reference_connector_status_is_deterministic(self):
        client, _ = self.client()
        status = client.status()
        self.assertEqual(status.state, ConnectorState.AVAILABLE)
        self.assertTrue(status.authenticated)
        self.assertEqual(status.last_successful_sync, "2026-08-24T12:00:00Z")

    def test_search_returns_mixed_source_types(self):
        client, _ = self.client()
        items = list(client.iter_search("harbour"))
        self.assertEqual([item.source_id for item in items], ["photo-001", "journal-001"])
        self.assertEqual(items[0].item_type, "photo")
        self.assertEqual(items[1].item_type, "journal_entry")
        self.assertEqual(items[0].location["name"], "Fixture Harbour")
        self.assertEqual(items[0].media[0].filename, "harbour.jpg")

    def test_search_matches_identity_labels(self):
        client, _ = self.client()
        items = list(client.iter_search("Alex"))
        self.assertEqual(
            [item.source_id for item in items],
            ["message-001", "journal-001", "message-002"],
        )

    def test_pagination_is_stable_and_complete(self):
        client, service = self.client()
        items = list(client.iter_search("alex", limit=1))
        self.assertEqual(len(items), 3)
        search_calls = [call for call in service.calls if call[0] == "search"]
        self.assertEqual([params.get("cursor") for _, params in search_calls], [None, "1", "2"])

    def test_no_search_capability_fails_before_search_operation(self):
        client, service = self.client("no_search")
        with self.assertRaises(UnsupportedCapability):
            client.search("anything")
        self.assertNotIn("search", [operation for operation, _ in service.calls])

    def test_offline_reference_connector_is_classified(self):
        client, _ = self.client("offline")
        with self.assertRaises(ConnectorUnavailable):
            client.info()

    def test_timeout_reference_connector_is_classified(self):
        client, _ = self.client("timeout")
        with self.assertRaises(ConnectorTimeout):
            client.info()

    def test_future_protocol_fails_closed(self):
        client, _ = self.client("future_protocol")
        with self.assertRaises(ConnectorProtocolError):
            client.info()

    def test_wrong_identity_fails_closed(self):
        client, _ = self.client("wrong_identity")
        with self.assertRaises(ConnectorProtocolError):
            client.info()

    def test_malformed_connector_responses_are_rejected(self):
        client, _ = self.client("malformed_info")
        with self.assertRaises(ConnectorProtocolError):
            client.info()

        client, _ = self.client("malformed_capabilities")
        with self.assertRaises(ConnectorProtocolError):
            client.capabilities()

        client, _ = self.client("malformed_search")
        with self.assertRaises(ConnectorProtocolError):
            client.search("fixture")

    def test_auth_required_and_degraded_are_valid_connector_states(self):
        client, _ = self.client("auth_required")
        status = client.status()
        self.assertEqual(status.state, ConnectorState.AUTH_REQUIRED)
        self.assertFalse(status.authenticated)

        client, _ = self.client("degraded")
        status = client.status()
        self.assertEqual(status.state, ConnectorState.DEGRADED)
        self.assertIn("partially unavailable", status.error)

    def test_fixture_contains_updated_item_and_durable_provenance_fields(self):
        client, _ = self.client()
        item = client.search("moved to Saturday").items[0]
        self.assertEqual(item.lifecycle.value, "updated")
        self.assertEqual(item.native_id, "native-message-002")
        self.assertEqual(item.content_hash, "fixture-hash-message-002-v2")
        self.assertEqual(item.metadata["conversation_id"], "chat-alex")

    def test_repeated_cursor_scenario_is_detected(self):
        client, _ = self.client("repeated_cursor")
        with self.assertRaises(ConnectorProtocolError):
            list(client.iter_search("anything", limit=1))


if __name__ == "__main__":
    unittest.main()
