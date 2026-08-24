import json
import sys
from pathlib import Path

import app


def ingest(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    app.initialise()
    with app.connect() as con:
        for person in payload.get("people", []):
            record = {"name": person} if isinstance(person, str) else person
            person_id = app.resolve_person(con, record["name"])
            for alias in record.get("aliases", []):
                normalized = app.normalize_person_name(alias)
                owner = app._person_name_owner(con, normalized, person_id)
                if owner:
                    raise ValueError(f"Alias already belongs to another person: {alias}")
                if normalized != app.normalize_person_name(record["name"]):
                    con.execute("""INSERT INTO person_aliases(person_id,alias,normalized_alias,source)
                      VALUES(?,?,?,'ingestion') ON CONFLICT(normalized_alias) DO NOTHING""",
                      (person_id, " ".join(alias.strip().split()), normalized))
        for chapter in payload.get("chapters", []):
            con.execute("INSERT INTO chapters(title,start_date,end_date,summary,color,confidence) VALUES(?,?,?,?,?,?)", (
                chapter["title"], chapter["start_date"], chapter.get("end_date"), chapter.get("summary", ""),
                chapter.get("color", "#8ba3ff"), float(chapter.get("confidence", .7))))
        for event in payload.get("events", []):
            status = event.get("status", "uncertain")
            if status not in {"confirmed", "booked", "planned", "cancelled", "resold", "uncertain"}:
                raise ValueError(f"Invalid attendance state for {event.get('title')}")
            cur = con.execute("""INSERT INTO events(title,start_date,end_date,description,category,status,confidence,importance,
              review_state,notable_score,date_precision,memory) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                event["title"], event["start_date"], event.get("end_date"), event.get("description", ""),
                event.get("category", "Life"), status, float(event.get("confidence", .5)), event.get("importance", "medium"),
                "needs_review" if status == "uncertain" else "clear", float(event.get("notable_score", .5)),
                event.get("date_precision", "day"), event.get("memory", "")))
            event_id = cur.lastrowid
            for person_name in event.get("people", []):
                person_id = app.resolve_person(con, person_name)
                con.execute("INSERT OR IGNORE INTO event_people(event_id,person_id,role) VALUES(?,?,'with')", (event_id, person_id))
            for evidence in event.get("evidence", []):
                source = con.execute("SELECT id FROM sources WHERE name=?", (evidence.get("source"),)).fetchone()
                if not source:
                    con.execute("INSERT INTO sources(name,source_type) VALUES(?,?)", (evidence.get("source", "Curated source"), "curated"))
                    source = con.execute("SELECT id FROM sources WHERE name=?", (evidence.get("source", "Curated source"),)).fetchone()
                con.execute("""INSERT INTO evidence(event_id,source_id,evidence_type,source_ref,excerpt,confidence)
                  VALUES(?,?,?,?,?,?)""", (event_id, source[0], evidence.get("type", "record"), evidence.get("reference", ""),
                  evidence.get("excerpt", ""), float(evidence.get("confidence", .5))))
            if status == "uncertain":
                con.execute("INSERT INTO review_items(event_id,issue_type,summary,details) VALUES(?,?,?,?)", (
                    event_id, "curated_uncertainty", f"Review: {event['title']}", "ChatGPT retained this as uncertain after source reconciliation."))
    return len(payload.get("events", [])), len(payload.get("chapters", []))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: py ingest_curated.py curated-data.json")
    events, chapters = ingest(sys.argv[1])
    print(f"Imported {events} events and {chapters} chapters.")
