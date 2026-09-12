import hashlib, json, sys
from contextlib import closing
from datetime import date
from pathlib import Path
import app

try:
    from importance import infer_importance
except ImportError:
    infer_importance = lambda event: event.get("importance", "medium")


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def identity(event):
    primary = (event.get("evidence") or [{}])[0]
    source = str(event.get("source") or primary.get("source") or "Curated import").strip()
    external_id = str(event.get("source_record_id") or primary.get("reference") or f"sha256:{digest(event)}").strip()
    if not source or not external_id:
        raise ValueError("Each event needs a non-empty source and source_record_id")
    if len(source) > 200 or len(external_id) > 1000:
        raise ValueError("Event source identity is too long")
    return source, external_id, str(primary.get("reference") or ""), primary.get("observed_date")


def iso_date(value, label):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValueError(f"{label} must use YYYY-MM-DD") from None


def ingest(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    package_hash = digest(payload)
    app.initialise()
    with closing(app.connect()) as con, con:
        if con.execute("SELECT 1 FROM ingestion_runs WHERE connector='curated-json' AND package_checksum=? AND status='completed'", (package_hash,)).fetchone():
            return 0, 0
        run_id = con.execute("INSERT INTO ingestion_runs(connector,package_checksum,discovered_count) VALUES('curated-json',?,?)",
                             (package_hash, len(payload.get("events", [])))).lastrowid
        for person in payload.get("people", []):
            record = {"name": person} if isinstance(person, str) else person
            person_id = app.resolve_person(con, record["name"])
            for alias in record.get("aliases", []):
                normalized = app.normalize_person_name(alias)
                if app._person_name_owner(con, normalized, person_id):
                    raise ValueError(f"Alias already belongs to another person: {alias}")
                if normalized != app.normalize_person_name(record["name"]):
                    con.execute("INSERT INTO person_aliases(person_id,alias,normalized_alias,source) VALUES(?,?,?,'ingestion') ON CONFLICT(normalized_alias) DO NOTHING",
                                (person_id, " ".join(alias.strip().split()), normalized))
        chapters = 0
        for chapter in payload.get("chapters", []):
            start, end = chapter["start_date"], chapter.get("end_date") or chapter["start_date"]
            app.validate_event_values({"title": chapter["title"], "start_date": start, "end_date": end})
            cur = con.execute("""INSERT INTO chapters(title,start_date,end_date,summary,color,confidence)
              SELECT ?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM chapters WHERE title=? AND start_date=? AND COALESCE(end_date,start_date)=?)""",
              (chapter["title"], start, end, chapter.get("summary", ""), chapter.get("color", "#8ba3ff"), float(chapter.get("confidence", .7)),
               chapter["title"], start, end))
            chapters += cur.rowcount
        inserted = skipped = reviews = 0
        for event in payload.get("events", []):
            values = dict(event, status=event.get("status", "uncertain"), importance=infer_importance(event))
            title, start, end, status, importance, confidence = app.validate_event_values(values)
            source_name, external_id, source_ref, observed_date = identity(event)
            observed_date = iso_date(observed_date, "Source observed_date")
            con.execute("INSERT OR IGNORE INTO sources(name,source_type) VALUES(?,?)", (source_name, "curated"))
            source_id = con.execute("SELECT id FROM sources WHERE name=?", (source_name,)).fetchone()[0]
            content_hash = digest(event)
            con.execute("""INSERT OR IGNORE INTO source_records(source_id,object_type,external_id,content_sha256,source_ref,observed_date,first_seen_run_id,last_seen_run_id)
              VALUES(?,'event',?,?,?,?,?,?)""", (source_id, external_id, content_hash, source_ref, observed_date, run_id, run_id))
            source_record = con.execute("SELECT id,content_sha256 FROM source_records WHERE source_id=? AND object_type='event' AND external_id=?",
                                        (source_id, external_id)).fetchone()
            existing = con.execute("SELECT event_id FROM event_source_records WHERE source_record_id=?", (source_record["id"],)).fetchone()
            if existing:
                con.execute("UPDATE source_records SET content_sha256=?,source_ref=?,observed_date=?,last_seen_run_id=?,last_seen_at=CURRENT_TIMESTAMP WHERE id=?",
                            (content_hash, source_ref, observed_date, run_id, source_record["id"]))
                if source_record["content_sha256"] != content_hash and not con.execute(
                    "SELECT 1 FROM review_items WHERE event_id=? AND issue_type='source_record_changed' AND status='open'", (existing["event_id"],)).fetchone():
                    con.execute("INSERT INTO review_items(event_id,issue_type,summary,details) VALUES(?,?,?,?)",
                                (existing["event_id"], "source_record_changed", f"Review updated source: {title}",
                                 "A previously ingested source record changed; the accepted event was not overwritten."))
                    reviews += 1
                skipped += 1
                continue
            cur = con.execute("""INSERT INTO events(title,start_date,end_date,description,category,status,confidence,importance,
              review_state,notable_score,date_precision,memory) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (title, start, end, event.get("description", ""), event.get("category", "Life"), status, confidence, importance,
               "needs_review" if status == "uncertain" else "clear", float(event.get("notable_score", .5)), event.get("date_precision", "day"), event.get("memory", "")))
            event_id = cur.lastrowid
            con.execute("INSERT INTO event_source_records(event_id,source_record_id) VALUES(?,?)", (event_id, source_record["id"]))
            for name in event.get("people", []):
                con.execute("INSERT OR IGNORE INTO event_people(event_id,person_id,role) VALUES(?,?,'with')", (event_id, app.resolve_person(con, name)))
            for evidence in event.get("evidence", []):
                evidence_source = str(evidence.get("source") or source_name)
                con.execute("INSERT OR IGNORE INTO sources(name,source_type) VALUES(?,?)", (evidence_source, "curated"))
                evidence_source_id = con.execute("SELECT id FROM sources WHERE name=?", (evidence_source,)).fetchone()[0]
                con.execute("""INSERT INTO evidence(event_id,source_id,evidence_type,source_ref,observed_date,excerpt,confidence)
                  VALUES(?,?,?,?,?,?,?)""", (event_id, evidence_source_id, evidence.get("type", "record"), evidence.get("reference", ""),
                  iso_date(evidence.get("observed_date"), "Evidence observed_date"), evidence.get("excerpt", ""), float(evidence.get("confidence", .5))))
            if status == "uncertain":
                con.execute("INSERT INTO review_items(event_id,issue_type,summary,details) VALUES(?,?,?,?)",
                            (event_id, "curated_uncertainty", f"Review: {title}", "ChatGPT retained this as uncertain after source reconciliation."))
                reviews += 1
            inserted += 1
        con.execute("UPDATE ingestion_runs SET status='completed',inserted_count=?,skipped_count=?,review_count=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (inserted, skipped, reviews, run_id))
        con.execute("INSERT OR IGNORE INTO imports(filename,checksum,row_count,status) VALUES(?,?,?,'completed')", (Path(path).name, package_hash, inserted))
    return inserted, chapters


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: py ingest_curated.py curated-data.json")
    events, chapters = ingest(sys.argv[1])
    print(f"Imported {events} events and {chapters} chapters.")
