import importlib.util
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).parents[1]


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _png_bytes(size=(40, 30), colour=(200, 30, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


class MediaStoreTests(unittest.TestCase):
    """media_store holds the path guards for serving and deleting local files."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        os.environ["LIFE_ATLAS_DATA_DIR"] = str(self.data)
        os.environ.pop("LIFE_ATLAS_SEED_SAMPLE", None)
        self.app = _load("media_store_app", "app.py")
        self.app.initialise()
        self.media_store = _load("media_store_module", "media_store.py")
        self.connect = self.app.connect
        with closing(self.connect()) as con, con:
            self.event_id = con.execute(
                "INSERT INTO events(title,start_date,end_date) VALUES('Media fixture','2025-01-01','2025-01-01')"
            ).lastrowid
            self.person_id = con.execute("INSERT INTO people(name) VALUES('Fixture Person')").lastrowid

    def tearDown(self):
        self.temp.cleanup()

    def _store(self, **payload):
        raw = payload.pop("raw", None) or _png_bytes()
        return self.media_store.store_image(self.connect, self.data, raw, payload)

    def test_stored_image_is_content_addressed_webp(self):
        stored = self._store(event_id=self.event_id)
        with closing(self.connect()) as con:
            row = con.execute("SELECT local_path,mime_type,sha256 FROM media WHERE id=?", (stored["id"],)).fetchone()
        self.assertEqual(row["mime_type"], "image/webp")
        self.assertEqual(row["local_path"], f"media/{row['sha256'][:2]}/{row['sha256']}.webp")
        self.assertTrue((self.data / row["local_path"]).is_file())

    def test_identical_images_share_one_file_on_disk(self):
        raw = _png_bytes()
        first = self._store(event_id=self.event_id, raw=raw)
        second = self._store(person_id=self.person_id, raw=raw)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["sha256"], second["sha256"])
        stored = list((self.data / "media").rglob("*.webp"))
        self.assertEqual(len(stored), 1, "content-addressed storage should not duplicate the file")

    def test_media_file_refuses_paths_outside_the_data_directory(self):
        outside = self.data.parent / "escaped.webp"
        outside.write_bytes(b"not really an image")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with closing(self.connect()) as con, con:
            hostile = con.execute(
                "INSERT INTO media(event_id,local_path,mime_type) VALUES(?,?,?)",
                (self.event_id, "../escaped.webp", "image/webp"),
            ).lastrowid
        with self.assertRaises(ValueError):
            self.media_store.media_file(self.connect, self.data, hostile)

    def test_media_file_refuses_an_absolute_path(self):
        # The file must exist, otherwise the "missing file" branch would reject it
        # and the test would pass without ever exercising the containment guard.
        outside = self.data.parent / "escaped-absolute.webp"
        outside.write_bytes(_png_bytes())
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        self.assertTrue(outside.is_file())
        with closing(self.connect()) as con, con:
            hostile = con.execute(
                "INSERT INTO media(event_id,local_path,mime_type) VALUES(?,?,?)",
                (self.event_id, str(outside), "image/webp"),
            ).lastrowid
        with self.assertRaises(ValueError):
            self.media_store.media_file(self.connect, self.data, hostile)

    def test_deleting_one_reference_keeps_a_file_another_row_still_uses(self):
        raw = _png_bytes()
        first = self._store(event_id=self.event_id, raw=raw)
        second = self._store(person_id=self.person_id, raw=raw)
        path = self.data / f"media/{first['sha256'][:2]}/{first['sha256']}.webp"

        result = self.media_store.delete_media(self.connect, self.data, first["id"])
        self.assertFalse(result["removed_file"], "the file is still referenced by the other row")
        self.assertTrue(path.is_file())

        result = self.media_store.delete_media(self.connect, self.data, second["id"])
        self.assertTrue(result["removed_file"], "the last reference should remove the file")
        self.assertFalse(path.exists())

    def test_a_photo_needs_exactly_one_target(self):
        with self.assertRaises(ValueError):
            self._store()
        with self.assertRaises(ValueError):
            self._store(event_id=self.event_id, person_id=self.person_id)

    def test_unreadable_and_oversized_uploads_are_rejected(self):
        with self.assertRaises(ValueError):
            self.media_store.store_image(self.connect, self.data, b"definitely not an image",
                                         {"event_id": self.event_id})
        with self.assertRaises(ValueError):
            self.media_store.decode_data_url("https://example.invalid/photo.png")
        with self.assertRaises(ValueError):
            self.media_store.decode_data_url("data:image/png;base64," + "A" * 80_000_000)

    def test_large_images_are_downscaled(self):
        stored = self._store(event_id=self.event_id, raw=_png_bytes(size=(3000, 2000)))
        self.assertLessEqual(max(stored["width"], stored["height"]), 1600)


if __name__ == "__main__":
    unittest.main()
