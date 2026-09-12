import copy
import json
import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).parents[1]


class IngestCuratedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        os.environ["LIFE_ATLAS_DATA_DIR"] = str(self.data)
        os.environ.pop("LIFE_ATLAS_SEED_SAMPLE", None)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import app as app_module
        import ingest_curated
        self.app, self.ingest_curated = app_module, ingest_curated
        self._saved = {name: getattr(app_module, name) for name in ("DATA", "DB", "IMPORTS", "BACKUPS", "MEDIA", "RESTORE")}
        app_module.DATA = self.data
        app_module.DB = self.data / "life_atlas.sqlite3"
        app_module.IMPORTS = self.data / "imports"
        app_module.BACKUPS = self.data / "backups"
        app_module.MEDIA = self.data / "media"
        app_module.RESTORE = None
        app_module.initialise()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(self.app, name, value)
        self.temp.cleanup()

    def _ingest(self, payload):
        path = self.data / "curated.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return self.ingest_curated.ingest(path)

    def fixture(self):
        return {"events": [{"source": "Calendar", "source_record_id": "event-123", "title": "A day",
                "start_date": "2026-01-02", "status": "confirmed", "confidence": .9,
                "evidence": [{"source": "Calendar", "reference": "event-123", "observed_date": "2026-01-01"}]}]}

    def test_events_people_and_evidence_are_ingested(self):
        events, chapters = self._ingest({
            "people": [{"name": "Dana Scully", "aliases": ["Scully"]}],
            "chapters": [{"title": "A chapter", "start_date": "2024-01-01", "end_date": "2024-12-31"}],
            "events": [{"title": "Conference talk", "start_date": "2024-05-05", "category": "Career",
                        "status": "confirmed", "confidence": .9, "importance": "major", "people": ["Scully"],
                        "evidence": [{"source": "Gmail", "type": "booking", "reference": "msg-1", "confidence": .9}]}]})
        self.assertEqual((events, chapters), (1, 1))
        with closing(self.app.connect()) as con:
            event = con.execute("SELECT id,importance FROM events").fetchone()
            self.assertEqual(event["importance"], "major")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM people").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT evidence_type FROM evidence").fetchone()[0], "booking")

    def test_uncertain_events_enter_review_and_importance_is_inferred(self):
        self._ingest({"events": [
            {"title": "Easy run", "start_date": "2024-07-01", "category": "Exercise"},
            {"title": "Ashton parkrun", "start_date": "2024-07-08", "category": "Exercise"}]})
        with closing(self.app.connect()) as con:
            got = dict(con.execute("SELECT title,importance FROM events"))
            self.assertEqual(got, {"Easy run": "minor", "Ashton parkrun": "medium"})
            self.assertEqual(con.execute("SELECT COUNT(*) FROM review_items").fetchone()[0], 2)

    def test_invalid_status_and_alias_collision_are_refused(self):
        with self.assertRaises(ValueError):
            self._ingest({"events": [{"title": "Bad", "start_date": "2024-08-08", "status": "attended"}]})
        self._ingest({"people": [{"name": "Alex Reed", "aliases": ["Ace"]}]})
        with self.assertRaises(ValueError):
            self._ingest({"people": [{"name": "Blair Nolan", "aliases": ["Ace"]}]})

    def test_replay_and_changed_source_are_safe(self):
        payload = self.fixture()
        self.assertEqual(self._ingest(payload), (1, 0))
        self.assertEqual(self._ingest(payload), (0, 0))
        changed = copy.deepcopy(payload)
        changed["events"][0]["description"] = "New source wording"
        self.assertEqual(self._ingest(changed), (0, 0))
        with closing(self.app.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM review_items WHERE issue_type='source_record_changed'").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT observed_date FROM evidence").fetchone()[0], "2026-01-01")

    def test_reversed_date_rolls_back(self):
        payload = self.fixture()
        payload["events"][0]["end_date"] = "2025-12-31"
        with self.assertRaisesRegex(ValueError, "End date"):
            self._ingest(payload)
        with closing(self.app.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0], 0)

    def test_version_one_database_migrates_to_version_two(self):
        with closing(self.app.connect()) as con, con:
            con.execute("DROP TABLE event_source_records")
            con.execute("DROP TABLE source_records")
            con.execute("DROP TABLE ingestion_runs")
            con.execute("PRAGMA user_version=1")
        self.app.initialise()
        with closing(self.app.connect()) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"ingestion_runs", "source_records", "event_source_records"} <= tables)


if __name__ == "__main__":
    unittest.main()
