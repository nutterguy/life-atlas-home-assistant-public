import base64
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image


class LifeAtlasTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["LIFE_ATLAS_DATA_DIR"] = self.temp.name
        spec = importlib.util.spec_from_file_location("life_atlas_app", Path(__file__).parents[1] / "app.py")
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.app.initialise()

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_install_is_empty(self):
        snapshot = self.app.snapshot()
        self.assertEqual(snapshot["events"], [])
        self.assertEqual(snapshot["people"], [])
        self.assertEqual(snapshot["chapters"], [])

    def test_event_round_trip(self):
        event_id = self.app.save_event({
            "title": "A remembered day",
            "start_date": "2026-08-21",
            "category": "Life",
            "status": "confirmed",
            "confidence": "0.9",
            "description": "Created by the API-compatible backend",
        })
        detail = self.app.entity_detail("event", event_id)
        self.assertEqual(detail["item"]["title"], "A remembered day")
        self.assertEqual(detail["item"]["end_date"], "2026-08-21")

    def test_event_edit_updates_core_fields(self):
        event_id = self.app.save_event({
            "title": "Original",
            "start_date": "2026-08-21",
            "category": "Life",
            "status": "confirmed",
            "confidence": "0.8",
        })
        self.app.update_event(event_id, {
            "title": "Updated",
            "start_date": "2026-08-22",
            "end_date": "2026-08-24",
            "category": "Projects",
            "status": "planned",
            "confidence": "0.7",
            "importance": "major",
            "person_ids": [],
        })
        event = self.app.entity_detail("event", event_id)["item"]
        self.assertEqual((event["title"], event["start_date"], event["end_date"]),
                         ("Updated", "2026-08-22", "2026-08-24"))
        self.assertEqual(event["importance"], "major")

    def test_event_validation_rejects_invalid_values(self):
        invalid = {
            "title": "Invalid",
            "start_date": "2026-08-22",
            "end_date": "2026-08-21",
            "confidence": "1.2",
        }
        with self.assertRaisesRegex(ValueError, "End date"):
            self.app.save_event(invalid)

    def test_uncertain_review_can_remain_open_then_resolve(self):
        event_id = self.app.save_event({
            "title": "Uncertain memory",
            "start_date": "2026-08-21",
            "status": "uncertain",
            "confidence": "0.4",
        })
        connection = self.app.connect()
        try:
            review_id = connection.execute(
                "SELECT id FROM review_items WHERE event_id=?", (event_id,)
            ).fetchone()["id"]
        finally:
            connection.close()
        self.app.resolve_review(review_id, "still_uncertain")
        self.assertEqual(self.app.entity_detail("event", event_id)["item"]["review_state"], "needs_review")
        self.app.resolve_review(review_id, "attended")
        event = self.app.entity_detail("event", event_id)["item"]
        self.assertEqual((event["status"], event["review_state"]), ("confirmed", "resolved"))

    def test_backup_is_portable_from_external_data_directory(self):
        backup = self.app.backup()
        with zipfile.ZipFile(backup) as archive:
            self.assertIn("data/life_atlas.sqlite3", archive.namelist())

    def test_photo_upload_person_day_and_backup(self):
        event_id = self.app.save_event({"title": "Photo day", "start_date": "2026-08-21"})
        connection = self.app.connect()
        try:
            with connection:
                connection.execute("INSERT INTO people(name) VALUES('Test person')")
                person_id = connection.execute("SELECT id FROM people WHERE name='Test person'").fetchone()[0]
        finally:
            connection.close()
        output = io.BytesIO(); Image.new("RGB", (32, 24), "blue").save(output, "PNG")
        data_url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
        event_photo = self.app.add_media({"event_id": event_id, "data_url": data_url})
        self.app.add_media({"person_id": person_id, "data_url": data_url})
        self.app.add_media({"captured_date": "2026-08-21", "data_url": data_url})
        self.assertEqual(len(self.app.entity_detail("event", event_id)["media"]), 1)
        self.assertEqual(len(self.app.entity_detail("person", person_id)["media"]), 1)
        self.assertEqual(self.app.snapshot()["daily_media"][0]["captured_date"], "2026-08-21")
        path, mime = self.app.media_file(self.app.connect, self.app.DATA, event_photo["id"])
        self.assertTrue(path.is_file()); self.assertEqual(mime, "image/webp")
        with zipfile.ZipFile(self.app.backup()) as archive:
            self.assertTrue(any(name.startswith("data/media/") for name in archive.namelist()))

    def test_photo_deletion_keeps_shared_file_until_last_reference(self):
        event_id = self.app.save_event({"title": "Shared photo", "start_date": "2026-08-21"})
        output = io.BytesIO(); Image.new("RGB", (32, 24), "green").save(output, "PNG")
        data_url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
        first = self.app.add_media({"event_id": event_id, "data_url": data_url})
        second = self.app.add_media({"captured_date": "2026-08-21", "data_url": data_url})
        path, _ = self.app.media_file(self.app.connect, self.app.DATA, first["id"])
        self.assertFalse(self.app.delete_media(self.app.connect, self.app.DATA, first["id"])["removed_file"])
        self.assertTrue(path.exists())
        self.assertTrue(self.app.delete_media(self.app.connect, self.app.DATA, second["id"])["removed_file"])
        self.assertFalse(path.exists())

    def test_http_health_and_ingress_safe_relative_assets(self):
        server = self.app.ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(base + "/api/health") as response:
                self.assertEqual(json.load(response), {"status": "ok"})
            with urllib.request.urlopen(base + "/") as response:
                html = response.read().decode()
            self.assertIn('src="app.js?', html)
            self.assertNotIn('src="/app.js"', html)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
