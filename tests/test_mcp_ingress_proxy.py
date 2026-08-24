import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mcp_ingress_proxy as proxy


class GooglePhotosMcpProxyTests(unittest.TestCase):
    def test_status_reports_configuration_health_and_persistent_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_db = Path(tmp) / "tokens.db"
            con = sqlite3.connect(token_db)
            try:
                con.execute("CREATE TABLE keyv (key TEXT PRIMARY KEY, value TEXT)")
                con.execute("INSERT INTO keyv(key,value) VALUES(?,?)", ("tokens:user", "{}"))
                con.commit()
            finally:
                con.close()

            with mock.patch.object(proxy, "TOKEN_DB", token_db), \
                 mock.patch.object(proxy, "request_local", return_value=(200, "OK", [], b'{"status":"healthy"}')), \
                 mock.patch.dict(os.environ, {
                     "GOOGLE_CLIENT_ID": "client-id",
                     "GOOGLE_CLIENT_SECRET": "client-secret",
                     "GOOGLE_REDIRECT_URI": "https://example.test/api/google-photos-mcp/auth/callback",
                 }, clear=False):
                status = proxy.mcp_status()

            self.assertTrue(status["available"])
            self.assertTrue(status["configured"])
            self.assertTrue(status["authenticated"])
            self.assertEqual(status["token_storage"], "/data/google-photos-mcp/tokens.db")

    def test_status_does_not_expose_oauth_secret(self):
        with mock.patch.object(proxy, "request_local", side_effect=OSError("offline")), \
             mock.patch.object(proxy, "TOKEN_DB", Path("/definitely/not/present/tokens.db")), \
             mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "super-secret"}, clear=False):
            status = proxy.mcp_status()
        self.assertNotIn("client_secret", status)
        self.assertNotIn("super-secret", str(status))
        self.assertFalse(status["available"])


if __name__ == "__main__":
    unittest.main()
