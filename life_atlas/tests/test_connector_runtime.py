from __future__ import annotations

import unittest

from connector_http import ProtocolTestServer
from connector_runtime import reference_connector_diagnostic
from mock_connector import MockConnectorService


class ConnectorRuntimeTests(unittest.TestCase):
    def test_reference_diagnostic_crosses_real_http_boundary(self):
        service = MockConnectorService("healthy")
        with ProtocolTestServer(service) as server:
            result = reference_connector_diagnostic(base_url=server.base_url, query="harbour", timeout=2)

        self.assertTrue(result["available"])
        self.assertEqual(result["connector_id"], "reference")
        self.assertEqual(result["protocol_version"], "1.0")
        self.assertIn("search", result["capabilities"])
        source_ids = [item["source_id"] for item in result["search"]["items"]]
        self.assertIn("photo-001", source_ids)
        self.assertGreaterEqual(result["latency_ms"], 0)

    def test_connector_failure_is_contained_as_status(self):
        service = MockConnectorService("offline")
        with ProtocolTestServer(service) as server:
            result = reference_connector_diagnostic(base_url=server.base_url, timeout=2)

        self.assertFalse(result["available"])
        self.assertEqual(result["state"], "unavailable")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
