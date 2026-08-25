import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from importance import infer_importance, is_race_event
from scripts.promote_race_importance import promote_races


class ImportanceTests(unittest.TestCase):
    def test_imported_exercise_defaults_to_minor(self):
        self.assertEqual(infer_importance({"title": "Morning Run", "category": "Exercise"}), "minor")

    def test_race_metadata_and_titles_default_to_medium(self):
        self.assertEqual(
            infer_importance({"title": "Saturday effort", "category": "Running", "source_metadata": {"workout_type": "race"}}),
            "medium",
        )
        self.assertTrue(is_race_event({"title": "Love Trails 27k race!", "category": "Exercise"}))
        self.assertEqual(infer_importance({"title": "City Marathon", "category": "Exercise"}), "medium")

    def test_explicit_importance_always_wins(self):
        self.assertEqual(
            infer_importance({"title": "Parkrun", "category": "Running", "importance": "minor"}),
            "minor",
        )
        with self.assertRaises(ValueError):
            infer_importance({"title": "Run", "category": "Exercise", "importance": "important"})

    def test_historical_promotion_is_previewed_backed_up_and_id_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "life_atlas.sqlite3"
            backup = Path(temp) / "before-race-promotion.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE events(id INTEGER PRIMARY KEY,title TEXT,start_date TEXT,category TEXT,importance TEXT)"
                )
                connection.executemany(
                    "INSERT INTO events(title,start_date,category,importance) VALUES(?,?,?,?)",
                    [
                        ("Morning Run", "2026-01-01", "Exercise", "minor"),
                        ("Trail race", "2026-02-01", "Exercise", "minor"),
                        ("City Marathon", "2026-03-01", "Running", "minor"),
                    ],
                )
                connection.commit()

            candidates = promote_races(database)
            self.assertEqual([row["id"] for row in candidates], [2, 3])
            promoted = promote_races(database, apply=True, event_ids=[2], backup=backup)
            self.assertEqual([row["id"] for row in promoted], [2])

            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT importance FROM events WHERE id=2").fetchone()[0], "medium")
                self.assertEqual(connection.execute("SELECT importance FROM events WHERE id=3").fetchone()[0], "minor")
            with closing(sqlite3.connect(backup)) as connection:
                self.assertEqual(connection.execute("SELECT importance FROM events WHERE id=2").fetchone()[0], "minor")


if __name__ == "__main__":
    unittest.main()
