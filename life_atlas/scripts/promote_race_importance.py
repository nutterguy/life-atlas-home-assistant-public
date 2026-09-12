import argparse
import sqlite3
from contextlib import closing
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from importance import is_race_event  # noqa: E402


def race_candidates(connection):
    rows = connection.execute(
        """SELECT id,title,start_date,category FROM events
           WHERE importance='minor' AND lower(category) IN ('exercise','running')
           ORDER BY start_date,id"""
    ).fetchall()
    return [row for row in rows if is_race_event(dict(row), allow_title=True)]


def promote_races(database, *, apply=False, event_ids=(), backup=None):
    database = Path(database)
    if not database.is_file():
        raise ValueError(f"Database does not exist: {database}")

    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        candidates = race_candidates(connection)
        candidate_ids = {row["id"] for row in candidates}
        selected_ids = set(event_ids)

        if not apply:
            return candidates
        if not selected_ids:
            raise ValueError("Apply requires at least one reviewed --event-id")
        unknown = selected_ids - candidate_ids
        if unknown:
            raise ValueError(f"Event IDs are not current race candidates: {sorted(unknown)}")
        if backup is None:
            raise ValueError("Apply requires a backup path")

        backup = Path(backup)
        if backup.exists():
            raise ValueError(f"Backup path already exists: {backup}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(backup)) as backup_connection:
            connection.backup(backup_connection)

        connection.executemany(
            "UPDATE events SET importance='medium' WHERE id=? AND importance='minor'",
            [(event_id,) for event_id in sorted(selected_ids)],
        )
        connection.commit()
        return [row for row in candidates if row["id"] in selected_ids]


def main():
    parser = argparse.ArgumentParser(
        description="Preview minor exercise events that look like races, or promote reviewed IDs on a source snapshot."
    )
    parser.add_argument("database", type=Path, help="Path to a stopped or consistent Life Atlas SQLite snapshot")
    parser.add_argument("--show-details", action="store_true", help="Show private candidate dates and titles in this terminal")
    parser.add_argument("--apply", action="store_true", help="Promote only the reviewed --event-id values to medium")
    parser.add_argument("--event-id", action="append", type=int, default=[], help="Reviewed candidate ID to promote; repeat as needed")
    parser.add_argument("--backup", type=Path, help="Required new SQLite backup path when applying")
    args = parser.parse_args()

    try:
        matches = promote_races(
            args.database,
            apply=args.apply,
            event_ids=args.event_id,
            backup=args.backup,
        )
    except ValueError as error:
        parser.error(str(error))

    action = "Promoted" if args.apply else "Found"
    print(f"{action} {len(matches)} race candidate(s).")
    if args.show_details:
        for row in matches:
            print(f"{row['id']}\t{row['start_date']}\t{row['title']}")
    elif matches and not args.apply:
        print("Re-run with --show-details in a private terminal to review candidate IDs.")


if __name__ == "__main__":
    main()
