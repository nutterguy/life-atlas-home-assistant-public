from __future__ import annotations

import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from connector_http import HTTPConnectorTransport, ProtocolTestServer
from connectors import ConnectorClient, ConnectorProtocolError, ConnectorState, ConnectorTimeout, ConnectorUnavailable
from mock_connector import MockConnectorService


class ConnectorHTTPTests(unittest.TestCase):
    def client(self, scenario: str = "healthy"):
        service = MockConnectorService(scenario)
        server = ProtocolTestServer(service)
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        client = ConnectorClient(service.connector_id, HTTPConnectorTransport(server.base_url, timeout=2))
        return client, service, server

    def test_info_status_capabilities_and_search_cross_real_http_socket(self):
        client, service, _ = self.client()
        info = client.info()
        self.assertEqual(info.connector_id, "reference")
        self.assertEqual(info.protocol_version, "1.0")
        self.assertEqual(client.status().state, ConnectorState.AVAILABLE)
        self.assertTrue(client.capabilities().supports("search"))
        items = list(client.iter_search("alex", limit=1))
        self.assertEqual([item.source_id for item in items], ["message-001", "journal-001", "message-002"])
        self.assertGreaterEqual(len(service.calls), 6)

    def test_http_search_preserves_source_item_shape_and_provenance(self):
        client, _, _ = self.client()
        item = client.search("harbour", limit=1).items[0]
        self.assertEqual(item.item_type, "photo")
        self.assertEqual(item.native_id, "native-photo-001")
        self.assertEqual(item.content_hash, "fixture-hash-photo-001")
        self.assertEqual(item.location["name"], "Fixture Harbour")
        self.assertEqual(item.media[0].mime_type, "image/jpeg")

    def test_wrong_identity_and_future_protocol_fail_closed_over_http(self):
        client, _, _ = self.client("wrong_identity")
        with self.assertRaises(ConnectorProtocolError):
            client.info()

        client, _, _ = self.client("future_protocol")
        with self.assertRaises(ConnectorProtocolError):
            client.info()

    def test_source_unavailable_isolated_over_http(self):
        client, _, _ = self.client("offline")
        with self.assertRaises(ConnectorUnavailable):
            client.info()

    def test_source_timeout_remains_timeout_over_http(self):
        client, _, _ = self.client("timeout")
        with self.assertRaises(ConnectorTimeout):
            client.info()

    def test_malformed_connector_payload_fails_closed_over_http(self):
        client, _, _ = self.client("malformed_search")
        with self.assertRaises(ConnectorProtocolError):
            client.search("fixture")

    def test_repeated_cursor_is_rejected_over_http(self):
        client, _, _ = self.client("repeated_cursor")
        with self.assertRaises(ConnectorProtocolError):
            list(client.iter_search("anything", limit=1))

    def test_unknown_endpoint_returns_json_404(self):
        _, _, server = self.client()
        req = Request(server.base_url + "v1/does-not-exist", headers={"Accept": "application/json"})
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req, timeout=2)
        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(ctx.exception.headers.get_content_type(), "application/json")
        payload = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_search_rejects_non_json_body(self):
        _, _, server = self.client()
        req = Request(
            server.base_url + "v1/search",
            data=b"query=alex",
            headers={"Content-Type": "text/plain", "Accept": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req, timeout=2)
        self.assertEqual(ctx.exception.code, 415)
        payload = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(payload["error"]["code"], "unsupported_media_type")

    def test_search_rejects_invalid_json_and_non_object_json(self):
        _, _, server = self.client()
        for raw, expected_code in [(b"{broken", "invalid_json"), (b"[]", "invalid_json")]:
            req = Request(
                server.base_url + "v1/search",
                data=raw,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req, timeout=2)
            self.assertEqual(ctx.exception.code, 400)
            payload = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertEqual(payload["error"]["code"], expected_code)

    def test_protocol_responses_are_json_no_store_and_version_tagged(self):
        _, _, server = self.client()
        req = Request(server.base_url + "v1/info", headers={"Accept": "application/json"})
        with urlopen(req, timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "application/json")
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            self.assertEqual(response.headers.get("X-Life-Atlas-Connector-Protocol"), "1")


if __name__ == "__main__":
    unittest.main()
