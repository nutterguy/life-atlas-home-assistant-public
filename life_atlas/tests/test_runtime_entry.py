from __future__ import annotations

import importlib
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from connector_http import ProtocolTestServer
from mock_connector import MockConnectorService


class RuntimeEntryTests(unittest.TestCase):
    """The runtime wrapper adds the version to health and nothing else.

    Connectors are registered records served by the application, so they are
    exercised here through the same HTTP surface a browser uses.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        os.environ["LIFE_ATLAS_DATA_DIR"] = self.temp.name
        os.environ.pop("LIFE_ATLAS_SEED_SAMPLE", None)
        os.environ["LIFE_ATLAS_VERSION"] = "0.6.0-test"
        self.addCleanup(os.environ.pop, "LIFE_ATLAS_VERSION", None)
        self.addCleanup(os.environ.pop, "LIFE_ATLAS_DATA_DIR", None)

        self.connector_server = ProtocolTestServer(MockConnectorService("healthy"))
        self.connector_server.__enter__()
        self.addCleanup(self.connector_server.__exit__, None, None, None)

        # runtime_entry patches the imported app module in place, so this test
        # points that one module at a temporary /data rather than loading a
        # second copy the wrapper would not be holding.
        import app
        self.app = app
        data = Path(self.temp.name)
        app.DATA, app.DB = data, data / "life_atlas.sqlite3"
        app.IMPORTS, app.BACKUPS, app.MEDIA = data / "imports", data / "backups", data / "media"
        app.RESTORE = None
        self.addCleanup(setattr, app, "RESTORE", None)
        app.initialise()

        import runtime_entry
        self.runtime = importlib.reload(runtime_entry)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        host, port = self.httpd.server_address[:2]
        self.base = f"http://{host}:{port}"

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def get_json(self, path):
        with urlopen(self.base + path, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path, payload=None):
        request = Request(self.base + path, method="POST",
                          data=json.dumps(payload or {}).encode("utf-8"),
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_health_reports_running_version_without_connector_dependency(self):
        self.assertEqual(self.get_json("/api/health"), {"status": "ok", "version": "0.6.0-test"})

    def test_connectors_are_listed_over_the_browser_surface(self):
        entries = self.get_json("/api/connectors")["connectors"]
        self.assertIn("reference", [entry["connector_id"] for entry in entries])
        for entry in entries:
            self.assertNotIn("auth_key", entry)

    def test_a_connector_can_be_registered_switched_on_and_checked(self):
        status, entry = self.post_json("/api/connectors", {
            "connector_id": "reference", "name": "Reference connector", "kind": "source",
            "base_url": self.connector_server.base_url, "enabled": True, "auth_key": "",
        })
        self.assertEqual(status, 201)
        self.assertTrue(entry["enabled"])

        checked = self.post_json("/api/connectors/reference/probe")[1]
        self.assertEqual(checked["state"], "available")
        self.assertIn("search", checked["capabilities"])

        switched_off = self.post_json("/api/connectors/reference/disable")[1]
        self.assertFalse(switched_off["enabled"])
        self.assertEqual(switched_off["state"], "disabled")

    def test_an_unreachable_connector_does_not_fail_the_request(self):
        self.post_json("/api/connectors", {
            "connector_id": "reference", "name": "Reference connector", "kind": "source",
            "base_url": "http://127.0.0.1:9/", "enabled": True,
        })
        status, entry = self.post_json("/api/connectors/reference/probe")
        self.assertEqual(status, 200)
        self.assertEqual(entry["state"], "unavailable")

    def test_an_unknown_connector_is_a_client_error_not_a_crash(self):
        self.assertEqual(self.post_json("/api/connectors/nope/probe")[0], 400)
        with self.assertRaises(HTTPError) as raised:
            self.get_json("/api/connectors/nope")
        self.assertEqual(raised.exception.code, 404)

    def test_the_curated_record_is_served_while_every_connector_is_off(self):
        self.assertEqual(self.get_json("/api/snapshot")["events"], [])


if __name__ == "__main__":
    unittest.main()
