import importlib.util
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
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
