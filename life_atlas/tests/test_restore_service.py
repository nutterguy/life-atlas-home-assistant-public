import importlib.util
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
import zipfile
import hashlib
from pathlib import Path


ROOT = Path(__file__).parents[1]


class RestoreServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["LIFE_ATLAS_DATA_DIR"] = self.temp.name
        os.environ.pop("LIFE_ATLAS_SEED_SAMPLE", None)
        spec = importlib.util.spec_from_file_location("restore_test_app", ROOT / "app.py")
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.app.initialise()
        self.manager = self.app.restore_manager()

    def tearDown(self):
        self.temp.cleanup()

    def candidate(self, title="Replacement"):
        path = Path(self.temp.name) / f"candidate-{title}.sqlite3"
        self.app.prepare_database(path)
        con = sqlite3.connect(path)
        try:
            with con:
                con.execute(
                    "INSERT INTO events(title,start_date,end_date) VALUES(?,?,?)",
                    (title, "2026-08-24", "2026-08-24"),
                )
        finally:
            con.close()
        return path

    def upload(self, source, chunk_size=64 * 1024):
        raw = source.read_bytes()
        session = self.manager.create_session(len(raw))
        for offset in range(0, len(raw), chunk_size):
            self.manager.append_chunk(session["id"], session["token"], offset, raw[offset:offset + chunk_size])
        return session

    def package(self, title="Packaged replacement"):
        source = self.candidate(title)
        raw = b"verified packaged image"
        digest = hashlib.sha256(raw).hexdigest()
        relative = f"media/{digest[:2]}/{digest}.webp"
        con = sqlite3.connect(source)
        try:
            with con:
                event_id = con.execute("SELECT id FROM events").fetchone()[0]
                con.execute(
                    "INSERT INTO media(event_id,local_path,sha256) VALUES(?,?,?)",
                    (event_id, relative, digest),
                )
        finally:
            con.close()
        package = Path(self.temp.name) / "restore.zip"
        with zipfile.ZipFile(package, "w", zipfile.ZIP_STORED) as archive:
            archive.write(source, "data/life_atlas.sqlite3")
            archive.writestr(f"data/{relative}", raw)
        return package, relative, raw

    def test_valid_restore_creates_rollback_and_replaces_database(self):
        self.app.save_event({"title": "Original", "start_date": "2026-01-01"})
        session = self.upload(self.candidate())
        validated = self.manager.validate_session(session["id"], session["token"])
        self.assertEqual(validated["report"]["counts"]["events"], 1)
        result = self.manager.commit_session(session["id"], session["token"], "RESTORE")
        self.assertTrue((self.manager.backups / f"{result['backup_id']}.sqlite3").is_file())
        self.assertEqual(self.app.snapshot()["events"][0]["title"], "Replacement")

    def test_rollback_restores_previous_database(self):
        self.app.save_event({"title": "Original", "start_date": "2026-01-01"})
        session = self.upload(self.candidate())
        self.manager.validate_session(session["id"], session["token"])
        restored = self.manager.commit_session(session["id"], session["token"], "RESTORE")
        self.manager.rollback(restored["backup_id"], "ROLLBACK")
        self.assertEqual(self.app.snapshot()["events"][0]["title"], "Original")

    def test_zip_restore_installs_verified_media_before_database_switch(self):
        package, relative, raw = self.package()
        session = self.upload(package)
        validated = self.manager.validate_session(session["id"], session["token"])
        self.assertEqual(validated["report"]["package_kind"], "zip")
        self.assertEqual(validated["report"]["package_media_files"], 1)
        result = self.manager.commit_session(session["id"], session["token"], "RESTORE")
        self.assertEqual(result["media"], {"copied": 1, "existing": 0})
        self.assertEqual((Path(self.temp.name) / relative).read_bytes(), raw)
        self.assertEqual(self.app.snapshot()["events"][0]["title"], "Packaged replacement")

    def test_zip_restore_rejects_traversal_and_unmatched_media(self):
        unsafe = Path(self.temp.name) / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.write(self.candidate("Unsafe"), "data/life_atlas.sqlite3")
            archive.writestr("data/media/../../escape.webp", b"bad")
        session = self.upload(unsafe)
        with self.assertRaisesRegex(ValueError, "unsupported path"):
            self.manager.validate_session(session["id"], session["token"])

        unmatched = Path(self.temp.name) / "unmatched.zip"
        extra = b"extra"
        extra_hash = hashlib.sha256(extra).hexdigest()
        with zipfile.ZipFile(unmatched, "w") as archive:
            archive.write(self.candidate("Unmatched"), "data/life_atlas.sqlite3")
            archive.writestr(f"data/media/{extra_hash[:2]}/{extra_hash}.webp", extra)
        session = self.upload(unmatched)
        with self.assertRaisesRegex(ValueError, "do not exactly match"):
            self.manager.validate_session(session["id"], session["token"])

    def test_zip_restore_rejects_symlinks_and_content_address_mismatch(self):
        symlink = Path(self.temp.name) / "symlink.zip"
        with zipfile.ZipFile(symlink, "w") as archive:
            archive.write(self.candidate("Symlink"), "data/life_atlas.sqlite3")
            item = zipfile.ZipInfo("data/media/aa/" + "a" * 64 + ".webp")
            item.create_system = 3
            item.external_attr = 0o120777 << 16
            archive.writestr(item, b"target")
        session = self.upload(symlink)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.manager.validate_session(session["id"], session["token"])

        package, _, _ = self.package("Wrong content address")
        broken = Path(self.temp.name) / "wrong-address.zip"
        with zipfile.ZipFile(package) as source, zipfile.ZipFile(broken, "w") as target:
            for item in source.infolist():
                data = source.read(item)
                if item.filename.endswith(".webp"):
                    data += b"tampered"
                target.writestr(item.filename, data)
        session = self.upload(broken)
        with self.assertRaisesRegex(ValueError, "content-addressed name"):
            self.manager.validate_session(session["id"], session["token"])

    def test_zip_restore_refuses_conflicting_existing_media(self):
        self.app.save_event({"title": "Still live", "start_date": "2026-01-01"})
        package, relative, _ = self.package("Blocked replacement")
        target = Path(self.temp.name) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"conflicting content")
        session = self.upload(package)
        self.manager.validate_session(session["id"], session["token"])
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.manager.commit_session(session["id"], session["token"], "RESTORE")
        self.assertEqual(self.app.snapshot()["events"][0]["title"], "Still live")

    def test_rejects_non_sqlite_and_wrong_upload_offset(self):
        invalid = b"not a sqlite database"
        session = self.manager.create_session(len(invalid))
        with self.assertRaisesRegex(ValueError, "Expected upload offset"):
            self.manager.append_chunk(session["id"], session["token"], 1, b"bad")
        self.manager.append_chunk(session["id"], session["token"], 0, invalid)
        with self.assertRaisesRegex(ValueError, "SQLite"):
            self.manager.validate_session(session["id"], session["token"])

    def test_rejects_foreign_key_violation(self):
        source = self.candidate("Broken")
        con = sqlite3.connect(source)
        try:
            with con:
                con.execute("PRAGMA foreign_keys=OFF")
                con.execute("INSERT INTO event_people(event_id,person_id,role) VALUES(999,999,'with')")
        finally:
            con.close()
        session = self.upload(source)
        with self.assertRaisesRegex(ValueError, "Foreign-key"):
            self.manager.validate_session(session["id"], session["token"])

    def test_rejects_missing_media(self):
        source = self.candidate("Missing media")
        con = sqlite3.connect(source)
        try:
            with con:
                event_id = con.execute("SELECT id FROM events").fetchone()[0]
                con.execute("INSERT INTO media(event_id,local_path) VALUES(?,?)", (event_id, "media/ff/missing.webp"))
        finally:
            con.close()
        session = self.upload(source)
        with self.assertRaisesRegex(ValueError, "missing media"):
            self.manager.validate_session(session["id"], session["token"])

    def test_audit_log_contains_checksums_but_not_session_token(self):
        session = self.upload(self.candidate("Audit"))
        self.manager.validate_session(session["id"], session["token"])
        audit = self.manager.audit.read_text(encoding="utf-8")
        self.assertIn('"action": "validated"', audit)
        self.assertNotIn(session["token"], audit)
        for line in audit.splitlines():
            json.loads(line)

    def test_http_restore_round_trip(self):
        source = self.candidate("HTTP replacement")
        raw = source.read_bytes()
        server = self.app.ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def request(path, body, headers=None):
            req = urllib.request.Request(base + path, data=body, method="POST", headers=headers or {})
            with urllib.request.urlopen(req) as response:
                return json.load(response)

        try:
            session = request(
                "/api/restore/sessions",
                json.dumps({"total_size": len(raw)}).encode(),
                {"Content-Type": "application/json"},
            )
            token_header = {"Content-Type": "application/octet-stream", "X-Life-Atlas-Restore-Token": session["token"]}
            request(f"/api/restore/sessions/{session['id']}/chunks?offset=0", raw, token_header)
            json_header = {"Content-Type": "application/json", "X-Life-Atlas-Restore-Token": session["token"]}
            request(f"/api/restore/sessions/{session['id']}/validate", b"{}", json_header)
            request(
                f"/api/restore/sessions/{session['id']}/commit",
                b'{"confirmation":"RESTORE"}',
                json_header,
            )
            self.assertEqual(self.app.snapshot()["events"][0]["title"], "HTTP replacement")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
