from __future__ import annotations

import importlib
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import app
from connector_http import ProtocolTestServer
from mock_connector import MockConnectorService


class RuntimeEntryTests(unittest.TestCase):
    def setUp(self):
        self.connector_service = MockConnectorService("healthy")
        self.connector_server = ProtocolTestServer(self.connector_service)
        self.connector_server.__enter__()
        self.addCleanup(self.connector_server.__exit__, None, None, None)

        os.environ["LIFE_ATLAS_REFERENCE_CONNECTOR_URL"] = self.connector_server.base_url
        os.environ["LIFE_ATLAS_VERSION"] = "0.6.0-test"
        import runtime_entry
        importlib.reload(runtime_entry)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        host, port = self.httpd.server_address[:2]
        self.base = f"http://{host}:{port}"

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        os.environ.pop("LIFE_ATLAS_REFERENCE_CONNECTOR_URL", None)
        os.environ.pop("LIFE_ATLAS_VERSION", None)

    def get_json(self, path):
        with urlopen(self.base + path, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_health_reports_running_version_without_connector_dependency(self):
        payload = self.get_json("/api/health")
        self.assertEqual(payload, {"status": "ok", "version": "0.6.0-test"})

    def test_reference_connector_is_reachable_from_life_atlas_runtime(self):
        payload = self.get_json("/api/connectors/reference?q=harbour")
        self.assertTrue(payload["available"])
        self.assertEqual(payload["connector_id"], "reference")
        self.assertEqual(payload["connector_version"], "0.1.0")
        source_ids = [item["source_id"] for item in payload["search"]["items"]]
        self.assertIn("photo-001", source_ids)


if __name__ == "__main__":
    unittest.main()
