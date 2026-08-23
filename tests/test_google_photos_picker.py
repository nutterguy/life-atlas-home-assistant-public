import base64
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import google_photos_picker as picker


class GooglePhotosPickerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.db = self.data / "test.sqlite3"
        schema = (Path(__file__).parents[1] / "schema.sql").read_text(encoding="utf-8")
        with self.connect() as con:
            con.executescript(schema)
        picker.FLOWS.clear()
        picker.SESSIONS.clear()
        output = io.BytesIO()
        Image.new("RGB", (32, 24), "purple").save(output, "JPEG")
        self.photo = output.getvalue()

    def tearDown(self):
        picker.FLOWS.clear()
        picker.SESSIONS.clear()
        self.temp.cleanup()

    def connect(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def google_response(self, url, **kwargs):
        if url.endswith("/sessions") and kwargs.get("method") == "POST":
            return {"id": "google-session", "pickerUri": "https://photos.google.com/picker",
                    "pollingConfig": {"pollInterval": "2s", "timeoutIn": "120s"}}
        if "/sessions/google-session" in url and kwargs.get("method", "GET") == "GET":
            return {"mediaItemsSet": True, "pollingConfig": {"pollInterval": "2s", "timeoutIn": "90s"}}
        if "/mediaItems?" in url:
            return {"mediaItems": [{"id": "photo-id", "type": "PHOTO", "createTime": "2026-08-21T14:00:00Z",
                                     "mediaFile": {"baseUrl": "https://lh3.googleusercontent.com/photo",
                                                   "filename": "portrait.jpg"}}]}
        if kwargs.get("method") == "DELETE":
            return {}
        raise AssertionError(url)

    def test_web_client_configuration_stores_no_token(self):
        result = picker.configure_web(self.data, "123-example.apps.googleusercontent.com")
        self.assertTrue(result["configured"])
        self.assertEqual(result["storage"], "browser memory only")
        self.assertFalse((self.data / "google_photos_token.bin").exists())

    def test_picker_uses_opaque_local_session_and_deduplicates_target(self):
        with patch.object(picker, "_request_json", side_effect=self.google_response), \
             patch.object(picker, "_download_photo", return_value=self.photo):
            session = picker.create_session(self.data, {"captured_date": "2026-08-21"}, access_token="x" * 30)
            self.assertNotEqual(session["session_id"], "google-session")
            self.assertNotIn("x" * 30, json.dumps(session))
            self.assertEqual(session["poll_interval"], 2)
            first = picker.poll_session(self.data, self.connect, session["session_id"])
            self.assertEqual(first["count"], 1)

            again = picker.create_session(self.data, {"captured_date": "2026-08-21"}, access_token="x" * 30)
            second = picker.poll_session(self.data, self.connect, again["session_id"])
            self.assertTrue(second["media"][0]["duplicate"])
        with self.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM media").fetchone()[0], 1)

    def test_desktop_token_is_protected_and_plaintext_legacy_file_is_removed(self):
        client = self.data / "client.json"
        client.write_text(json.dumps({"installed": {"client_id": "123-example.apps.googleusercontent.com",
                                                     "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                                                     "token_uri": "https://oauth2.googleapis.com/token"}}), encoding="utf-8")
        reversible = lambda raw, protect: base64.b64encode(raw) if protect else base64.b64decode(raw)
        with patch.object(picker, "_dpapi", side_effect=reversible):
            picker.configure(self.data, str(client))
            picker.FLOWS["state"] = {"verifier": "verifier", "redirect_uri": "http://127.0.0.1/callback",
                                     "created": time.time()}
            with patch.object(picker, "_request_json", return_value={"access_token": "token", "refresh_token": "refresh",
                                                                      "scope": picker.SCOPE, "expires_in": 3600}):
                picker.complete_authorisation(self.data, {"state": ["state"], "code": ["code"]})
            self.assertTrue((self.data / "google_photos_token.bin").is_file())
            self.assertFalse((self.data / "google_photos_token.json").exists())
            self.assertEqual(picker._load_token(self.data)["refresh_token"], "refresh")

    def test_invalid_target_and_media_host_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Choose one"):
            picker.create_session(self.data, {"event_id": 1, "person_id": 2}, access_token="x" * 30)
        with self.assertRaisesRegex(ValueError, "invalid media address"):
            picker._download_photo("https://example.com/photo", "x" * 30)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is only available on Windows")
    def test_windows_dpapi_round_trip(self):
        secret = b"Life Atlas Google Photos token test"
        self.assertEqual(picker._dpapi(picker._dpapi(secret, protect=True), protect=False), secret)


if __name__ == "__main__":
    unittest.main()
