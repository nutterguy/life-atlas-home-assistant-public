import unittest

from connectors import (
    ConnectorCapabilities,
    ConnectorClient,
    ConnectorProtocolError,
    ConnectorRegistry,
    ConnectorState,
    ConnectorTimeout,
    ConnectorUnavailable,
    SourceLifecycle,
    UnsupportedCapability,
)


INFO = {
    "connector_id": "demo",
    "name": "Demo connector",
    "protocol_version": "1.0",
    "connector_version": "0.1.0",
    "upstream_name": "demo-upstream",
    "upstream_version": "2.0.0",
}

STATUS = {
    "state": "available",
    "authenticated": True,
    "last_attempted_sync": "2026-08-24T15:00:00Z",
    "last_successful_sync": "2026-08-24T15:00:00Z",
    "error": None,
}


class FakeTransport:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def request(self, operation, params=None):
        self.calls.append((operation, dict(params or {})))
        response = self.responses.get(operation)
        if callable(response):
            return response(operation, params or {})
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, list):
            if not response:
                raise AssertionError(f"No queued response for {operation}")
            result = response.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        if response is None:
            raise AssertionError(f"Unexpected operation: {operation}")
        return response


def client_with(extra=None):
    responses = {
        "info": dict(INFO),
        "status": dict(STATUS),
        "capabilities": {"capabilities": ["search"]},
    }
    if extra:
        responses.update(extra)
    transport = FakeTransport(responses)
    return ConnectorClient("demo", transport), transport


class ConnectorModelTests(unittest.TestCase):
    def test_info_status_and_capabilities_parse_source_neutral_metadata(self):
        client, _ = client_with()
        info = client.info()
        status = client.status()
        capabilities = client.capabilities()
        self.assertEqual(info.connector_id, "demo")
        self.assertEqual(info.protocol_major, 1)
        self.assertEqual(info.upstream_name, "demo-upstream")
        self.assertEqual(status.state, ConnectorState.AVAILABLE)
        self.assertTrue(status.authenticated)
        self.assertTrue(capabilities.supports("search"))

    def test_info_and_capabilities_are_cached_until_invalidated(self):
        client, transport = client_with()
        client.info()
        client.info()
        client.capabilities()
        client.capabilities()
        self.assertEqual([call[0] for call in transport.calls].count("info"), 1)
        self.assertEqual([call[0] for call in transport.calls].count("capabilities"), 1)
        client.invalidate_cache()
        client.info()
        client.capabilities()
        self.assertEqual([call[0] for call in transport.calls].count("info"), 2)
        self.assertEqual([call[0] for call in transport.calls].count("capabilities"), 2)

    def test_old_or_future_protocol_major_fails_closed(self):
        old = dict(INFO, protocol_version="0.9")
        client, _ = client_with({"info": old})
        with self.assertRaises(ConnectorProtocolError):
            client.info()

        future = dict(INFO, protocol_version="2.0")
        client, _ = client_with({"info": future})
        with self.assertRaises(ConnectorProtocolError):
            client.info()

    def test_connector_identity_mismatch_fails_closed(self):
        wrong = dict(INFO, connector_id="something-else")
        client, _ = client_with({"info": wrong})
        with self.assertRaises(ConnectorProtocolError):
            client.info()

    def test_malformed_info_and_capabilities_are_rejected(self):
        client, _ = client_with({"info": {"connector_id": "demo"}})
        with self.assertRaises(ConnectorProtocolError):
            client.info()

        client, _ = client_with({"capabilities": {"capabilities": "search"}})
        with self.assertRaises(ConnectorProtocolError):
            client.capabilities()

    def test_timeout_is_classified_without_crashing_registry(self):
        client, _ = client_with({"status": TimeoutError("slow")})
        with self.assertRaises(ConnectorTimeout):
            client.status()

        registry = ConnectorRegistry()
        registry.register(client)
        status = registry.statuses()["demo"]
        self.assertEqual(status.state, ConnectorState.DEGRADED)
        self.assertIn("timed out", status.error)

    def test_unavailable_connector_is_isolated(self):
        good, _ = client_with()
        bad_transport = FakeTransport({"info": ConnectionError("offline")})
        bad = ConnectorClient("offline", bad_transport)
        registry = ConnectorRegistry()
        registry.register(good)
        registry.register(bad)
        statuses = registry.statuses()
        self.assertEqual(statuses["demo"].state, ConnectorState.AVAILABLE)
        self.assertEqual(statuses["offline"].state, ConnectorState.UNAVAILABLE)

    def test_connector_can_recover_after_transient_unavailability(self):
        transport = FakeTransport({"info": [ConnectionError("offline"), dict(INFO)]})
        client = ConnectorClient("demo", transport)
        with self.assertRaises(ConnectorUnavailable):
            client.info()
        self.assertEqual(client.info().name, "Demo connector")

    def test_unsupported_capability_is_explicit(self):
        client, _ = client_with({"capabilities": {"capabilities": []}})
        with self.assertRaises(UnsupportedCapability):
            client.search("hello")

    def test_search_parses_source_items_without_source_specific_types(self):
        client, transport = client_with({
            "search": {
                "items": [{
                    "source_id": "message:1",
                    "item_type": "message",
                    "timestamp": "2026-08-24T12:00:00Z",
                    "lifecycle": "updated",
                    "text": "Flights booked",
                    "participants": [{
                        "source_id": "contact:7",
                        "kind": "contact",
                        "label": "Example person",
                    }],
                    "location": {"name": "Example place"},
                    "media": [{
                        "source_id": "media:9",
                        "mime_type": "image/jpeg",
                        "filename": "photo.jpg",
                        "size_bytes": 1234,
                    }],
                    "native_id": "native-1",
                    "content_hash": "sha256:abc",
                    "metadata": {"conversation_id": "chat:2"},
                }],
                "next_cursor": "cursor-2",
            }
        })
        page = client.search("flights", limit=25)
        self.assertEqual(len(page.items), 1)
        item = page.items[0]
        self.assertEqual(item.item_type, "message")
        self.assertEqual(item.lifecycle, SourceLifecycle.UPDATED)
        self.assertEqual(item.participants[0].source_id, "contact:7")
        self.assertEqual(item.media[0].filename, "photo.jpg")
        self.assertEqual(item.metadata["conversation_id"], "chat:2")
        self.assertEqual(page.next_cursor, "cursor-2")
        search_call = [call for call in transport.calls if call[0] == "search"][0]
        self.assertEqual(search_call[1]["query"], "flights")
        self.assertEqual(search_call[1]["limit"], 25)

    def test_malformed_search_payload_is_rejected(self):
        client, _ = client_with({"search": {"items": "not-a-list"}})
        with self.assertRaises(ConnectorProtocolError):
            client.search("test")

        client, _ = client_with({"search": {"items": [{"source_id": "x"}]}})
        with self.assertRaises(ConnectorProtocolError):
            client.search("test")

    def test_iter_search_handles_pagination(self):
        pages = [
            {
                "items": [{"source_id": "1", "item_type": "message", "text": "one"}],
                "next_cursor": "next",
            },
            {
                "items": [{"source_id": "2", "item_type": "photo", "title": "two"}],
                "next_cursor": None,
            },
        ]
        client, transport = client_with({"search": pages})
        items = list(client.iter_search("anything"))
        self.assertEqual([item.source_id for item in items], ["1", "2"])
        search_calls = [call for call in transport.calls if call[0] == "search"]
        self.assertEqual(len(search_calls), 2)
        self.assertNotIn("cursor", search_calls[0][1])
        self.assertEqual(search_calls[1][1]["cursor"], "next")

    def test_iter_search_rejects_repeated_cursor_loop(self):
        pages = [
            {"items": [], "next_cursor": "repeat"},
            {"items": [], "next_cursor": "repeat"},
        ]
        client, _ = client_with({"search": pages})
        with self.assertRaises(ConnectorProtocolError):
            list(client.iter_search("anything"))

    def test_registry_rejects_duplicate_connector_ids(self):
        first, _ = client_with()
        second, _ = client_with()
        registry = ConnectorRegistry()
        registry.register(first)
        with self.assertRaises(ValueError):
            registry.register(second)
        self.assertIs(registry.get("demo"), first)
        self.assertIs(registry.unregister("demo"), first)
        self.assertEqual(registry.all(), ())

    def test_capability_validation(self):
        capabilities = ConnectorCapabilities.from_value(["search", "media", "search"])
        self.assertEqual(capabilities.values, frozenset({"search", "media"}))
        with self.assertRaises(ConnectorProtocolError):
            ConnectorCapabilities.from_value(["search", 7])


if __name__ == "__main__":
    unittest.main()
