import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackupRetentionTests(unittest.TestCase):
    """/data is Supervisor-managed and finite, so neither store may grow forever."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["LIFE_ATLAS_DATA_DIR"] = self.temp.name
        os.environ.pop("LIFE_ATLAS_SEED_SAMPLE", None)
        self.app = _load("retention_app", "app.py")
        self.app.initialise()

    def tearDown(self):
        self.temp.cleanup()

    def test_taking_a_backup_prunes_older_archives(self):
        # Pre-seed past the limit so a single real backup must trigger pruning.
        for day in range(1, self.app.MAX_BACKUP_ARCHIVES + 4):
            (self.app.BACKUPS / f"life-atlas-backup-202401{day:02d}-000000.zip").write_bytes(b"x")
        seeded = len(list(self.app.BACKUPS.glob("life-atlas-backup-*.zip")))
        self.assertGreater(seeded, self.app.MAX_BACKUP_ARCHIVES)

        made = self.app.backup()
        self.assertTrue(made.exists(), "the backup just taken must survive its own pruning")
        archives = list(self.app.BACKUPS.glob("life-atlas-backup-*.zip"))
        self.assertLessEqual(len(archives), self.app.MAX_BACKUP_ARCHIVES,
                             "taking a backup should bring the store back under the cap")
        self.assertIn(made.name, [p.name for p in archives])

    def test_prune_keeps_the_newest_and_removes_the_rest(self):
        # Stamps match backup()'s own %Y%m%d-%H%M%S format, which sorts chronologically.
        names = [f"life-atlas-backup-202501{day:02d}-000000.zip" for day in range(1, 16)]
        for name in names:
            (self.app.BACKUPS / name).write_bytes(b"x")
        removed = self.app.prune_backups(keep=5)
        remaining = sorted(p.name for p in self.app.BACKUPS.glob("life-atlas-backup-*.zip"))
        self.assertEqual(len(remaining), 5)
        self.assertEqual(len(removed), 10)
        self.assertEqual(remaining, sorted(names)[-5:], "the five newest should survive")
        self.assertIn("life-atlas-backup-20250115-000000.zip", remaining)
        self.assertNotIn("life-atlas-backup-20250101-000000.zip", remaining)

    def test_pruning_leaves_unrelated_files_alone(self):
        keep = self.app.BACKUPS / "something-else.sqlite3"
        keep.write_bytes(b"x")
        for day in range(1, 13):
            (self.app.BACKUPS / f"life-atlas-backup-202502{day:02d}-000000.zip").write_bytes(b"x")
        self.app.prune_backups(keep=2)
        self.assertTrue(keep.exists(), "only Life Atlas backup archives should be pruned")


class RollbackSnapshotRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["LIFE_ATLAS_DATA_DIR"] = self.temp.name
        os.environ.pop("LIFE_ATLAS_SEED_SAMPLE", None)
        self.app = _load("rollback_retention_app", "app.py")
        self.app.initialise()
        self.manager = self.app.restore_manager()

    def tearDown(self):
        self.temp.cleanup()

    def _snapshot(self, stamp, suffix="abcdef12"):
        path = self.manager.backups / f"{stamp}-{suffix}.sqlite3"
        path.write_bytes(b"x")
        return path

    def test_older_rollback_snapshots_are_pruned(self):
        made = [self._snapshot(f"202501{day:02d}T000000Z") for day in range(1, 16)]
        newest = made[-1].stem
        removed = self.manager._prune_rollback_snapshots(keep_id=newest)
        remaining = sorted(p.stem for p in self.manager.backups.glob("*.sqlite3"))
        self.assertEqual(len(remaining), self.manager.MAX_ROLLBACK_SNAPSHOTS
                         if hasattr(self.manager, "MAX_ROLLBACK_SNAPSHOTS") else 10)
        self.assertIn(newest, remaining, "the snapshot just written must never be pruned")
        self.assertEqual(len(removed), 5)

    def test_the_snapshot_just_written_survives_even_if_it_sorts_last(self):
        for day in range(1, 16):
            self._snapshot(f"202502{day:02d}T000000Z")
        oldest = "20250201T000000Z-abcdef12"
        self.manager._prune_rollback_snapshots(keep_id=oldest)
        remaining = {p.stem for p in self.manager.backups.glob("*.sqlite3")}
        self.assertIn(oldest, remaining)

    def test_unrecognised_filenames_are_left_alone(self):
        stray = self.manager.backups / "not-a-snapshot.sqlite3"
        stray.write_bytes(b"x")
        for day in range(1, 15):
            self._snapshot(f"202503{day:02d}T000000Z")
        self.manager._prune_rollback_snapshots(keep_id="20250314T000000Z-abcdef12")
        self.assertTrue(stray.exists())


if __name__ == "__main__":
    unittest.main()
