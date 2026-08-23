from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import sqlite3
import sys
import threading
import time
import webbrowser
import zipfile
from contextlib import closing
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from media_store import decode_data_url, media_file, store_image

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", APP_DIR))
ROOT = APP_DIR
DATA = Path(os.environ.get("LIFE_ATLAS_DATA_DIR", str(APP_DIR / "data"))).resolve()
DB = DATA / "life_atlas.sqlite3"
STATIC = RESOURCE_ROOT / "static"
IMPORTS = DATA / "imports"
BACKUPS = DATA / "backups"
MEDIA = DATA / "media"


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def initialise():
    DATA.mkdir(exist_ok=True)
    IMPORTS.mkdir(exist_ok=True)
    BACKUPS.mkdir(exist_ok=True)
    MEDIA.mkdir(exist_ok=True)
    with closing(connect()) as con, con:
        con.executescript((RESOURCE_ROOT / "schema.sql").read_text(encoding="utf-8"))
        migrate(con)


def migrate(con):
    columns = {r[1] for r in con.execute("PRAGMA table_info(events)")}
    additions = {
        "notable_score": "REAL NOT NULL DEFAULT 0.5",
        "date_precision": "TEXT NOT NULL DEFAULT 'day'",
        "memory": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            con.execute(f"ALTER TABLE events ADD COLUMN {name} {definition}")
    con.execute("UPDATE events SET end_date=start_date WHERE end_date IS NULL OR end_date=''")
    media_columns = {r[1] for r in con.execute("PRAGMA table_info(media)")}
    media_additions = {
        "person_id": "INTEGER REFERENCES people(id) ON DELETE CASCADE",
        "source_ref": "TEXT DEFAULT ''",
        "mime_type": "TEXT NOT NULL DEFAULT 'image/webp'",
        "sha256": "TEXT DEFAULT ''",
        "width": "INTEGER",
        "height": "INTEGER",
        "created_at": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in media_additions.items():
        if name not in media_columns:
            con.execute(f"ALTER TABLE media ADD COLUMN {name} {definition}")
    con.execute("CREATE INDEX IF NOT EXISTS idx_media_person ON media(person_id,is_featured)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_media_date ON media(captured_date,is_featured)")


def rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def snapshot():
    with closing(connect()) as con, con:
        events = rows(con, """SELECT e.*, p.name place_name, p.city, p.country, p.latitude, p.longitude,
          t.title trip_title,
          GROUP_CONCAT(DISTINCT pe.name) people,
          (SELECT COUNT(*) FROM evidence ev WHERE ev.event_id=e.id) evidence_count
          FROM events e LEFT JOIN places p ON p.id=e.place_id LEFT JOIN trips t ON t.id=e.trip_id
          LEFT JOIN event_people ep ON ep.event_id=e.id LEFT JOIN people pe ON pe.id=ep.person_id
          GROUP BY e.id ORDER BY e.start_date DESC""")
        return {
            "events": events,
            "places": rows(con, """SELECT p.*, COUNT(e.id) event_count FROM places p LEFT JOIN events e ON e.place_id=p.id GROUP BY p.id ORDER BY event_count DESC, p.name"""),
            "people": rows(con, """SELECT p.*, COUNT(ep.event_id) event_count, MIN(e.start_date) first_event, MAX(e.start_date) latest_event,
              (SELECT m.id FROM media m WHERE m.person_id=p.id ORDER BY m.is_featured DESC,m.id DESC LIMIT 1) profile_media_id
              FROM people p LEFT JOIN event_people ep ON ep.person_id=p.id LEFT JOIN events e ON e.id=ep.event_id GROUP BY p.id ORDER BY event_count DESC, p.name"""),
            "trips": rows(con, """SELECT t.*, COUNT(DISTINCT e.id) event_count,
              GROUP_CONCAT(DISTINCT COALESCE(pl.name, pl.country)) places,
              GROUP_CONCAT(DISTINCT pe.name) people
              FROM trips t LEFT JOIN events e ON e.trip_id=t.id
              LEFT JOIN places pl ON pl.id=e.place_id
              LEFT JOIN event_people ep ON ep.event_id=e.id LEFT JOIN people pe ON pe.id=ep.person_id
              GROUP BY t.id ORDER BY COALESCE(t.start_date,'') DESC"""),
            "review": rows(con, """SELECT r.*, e.title event_title, e.start_date, e.confidence FROM review_items r LEFT JOIN events e ON e.id=r.event_id WHERE r.status='open' ORDER BY r.created_at DESC"""),
            "sources": rows(con, "SELECT * FROM sources ORDER BY name"),
            "chapters": rows(con, "SELECT * FROM chapters ORDER BY start_date"),
            "years": rows(con, """SELECT substr(start_date,1,4) year, COUNT(*) event_count,
              SUM(CASE WHEN importance='major' THEN 1 ELSE 0 END) major_count,
              COUNT(DISTINCT place_id) place_count,
              COUNT(DISTINCT trip_id) trip_count
              FROM events GROUP BY substr(start_date,1,4) ORDER BY year DESC"""),
            "daily_media": rows(con, """SELECT * FROM media WHERE event_id IS NULL AND person_id IS NULL
              AND captured_date IS NOT NULL AND captured_date<>'' ORDER BY captured_date DESC,is_featured DESC,id DESC"""),
            "features": {"photo_upload": True, "takeout_import": False, "google_photos_picker": False},
        }


def entity_detail(kind, entity_id):
    table = {"event": "events", "trip": "trips", "place": "places", "person": "people"}.get(kind)
    if not table:
        raise ValueError("Unknown detail type")
    with closing(connect()) as con, con:
        item = con.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
        if not item:
            raise ValueError("Record not found")
        result = {"kind": kind, "item": dict(item), "links": rows(con, "SELECT * FROM entity_links WHERE entity_type=? AND entity_id=? ORDER BY label", (kind, entity_id))}
        base = """SELECT e.*, p.name place_name, p.city, p.country, t.title trip_title,
          GROUP_CONCAT(DISTINCT pe.name) people,
          (SELECT COUNT(*) FROM evidence ev WHERE ev.event_id=e.id) evidence_count
          FROM events e LEFT JOIN places p ON p.id=e.place_id LEFT JOIN trips t ON t.id=e.trip_id
          LEFT JOIN event_people ep ON ep.event_id=e.id LEFT JOIN people pe ON pe.id=ep.person_id"""
        if kind == "event":
            result["events"] = rows(con, base + " WHERE e.id=? GROUP BY e.id", (entity_id,))
            result["evidence"] = rows(con, """SELECT ev.*, s.name source_name, s.source_type FROM evidence ev LEFT JOIN sources s ON s.id=ev.source_id WHERE ev.event_id=? ORDER BY ev.imported_at DESC""", (entity_id,))
            result["people"] = rows(con, """SELECT p.*, ep.role FROM people p JOIN event_people ep ON ep.person_id=p.id WHERE ep.event_id=? ORDER BY p.name""", (entity_id,))
            result["tags"] = rows(con, """SELECT t.* FROM tags t JOIN event_tags et ON et.tag_id=t.id WHERE et.event_id=? ORDER BY t.name""", (entity_id,))
            result["media"] = rows(con, "SELECT * FROM media WHERE event_id=? ORDER BY is_featured DESC, captured_date", (entity_id,))
        elif kind == "trip":
            result["events"] = rows(con, base + " WHERE e.trip_id=? GROUP BY e.id ORDER BY e.start_date", (entity_id,))
        elif kind == "place":
            result["events"] = rows(con, base + " WHERE e.place_id=? GROUP BY e.id ORDER BY e.start_date DESC", (entity_id,))
        else:
            result["events"] = rows(con, base + " JOIN event_people selected ON selected.event_id=e.id WHERE selected.person_id=? GROUP BY e.id ORDER BY e.start_date DESC", (entity_id,))
            result["media"] = rows(con, "SELECT * FROM media WHERE person_id=? ORDER BY is_featured DESC,id DESC", (entity_id,))
        return result


def add_media(payload):
    raw = decode_data_url(payload.get("data_url", ""))
    return store_image(connect, DATA, raw, payload)


def event_weather(event_id):
    with closing(connect()) as con, con:
        cached = con.execute("SELECT weather_json FROM weather_cache WHERE event_id=?", (event_id,)).fetchone()
        if cached:
            return json.loads(cached[0])
        event = con.execute("""SELECT e.start_date,e.end_date,e.title,p.name place_name,p.latitude,p.longitude
          FROM events e LEFT JOIN places p ON p.id=e.place_id WHERE e.id=?""", (event_id,)).fetchone()
        if not event or event["latitude"] is None:
            return {"available": False, "reason": "No geocoded place is linked to this event."}
        if event["start_date"] > datetime.now().strftime("%Y-%m-%d"):
            return {"available": False, "reason": "Historical weather is available after the event has happened."}
        end_date = min(event["end_date"] or event["start_date"], datetime.now().strftime("%Y-%m-%d"))
        params = urlencode({
            "latitude": event["latitude"], "longitude": event["longitude"],
            "start_date": event["start_date"], "end_date": end_date,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": "auto",
        })
        weather_url = f"https://archive-api.open-meteo.com/v1/archive?{params}"
        try:
            request = Request(weather_url, headers={"User-Agent": "LifeAtlas/1.0"})
            with urlopen(request, timeout=15) as response:
                raw = json.loads(response.read().decode("utf-8"))
            daily = raw.get("daily", {})
            result = {"available": True, "provider": "Open-Meteo", "place": event["place_name"], "days": [
                {"date": date, "code": daily.get("weather_code", [None])[i],
                 "max": daily.get("temperature_2m_max", [None])[i], "min": daily.get("temperature_2m_min", [None])[i],
                 "rain": daily.get("precipitation_sum", [None])[i]}
                for i, date in enumerate(daily.get("time", []))
            ]}
            con.execute("INSERT OR REPLACE INTO weather_cache(event_id,weather_json,fetched_at) VALUES(?,?,CURRENT_TIMESTAMP)", (event_id, json.dumps(result)))
            return result
        except Exception as exc:
            return {"available": False,
                    "reason": "The local weather connection did not respond. Life Atlas can try through the app window instead.",
                    "direct_url": weather_url, "diagnostic": type(exc).__name__}


def add_link(payload):
    if payload.get("entity_type") not in {"event", "trip", "place", "person"}:
        raise ValueError("Invalid link target")
    url = payload.get("url", "").strip()
    if not url.startswith(("https://", "http://")):
        raise ValueError("Link must begin with https:// or http://")
    with closing(connect()) as con, con:
        cur = con.execute("INSERT INTO entity_links(entity_type,entity_id,label,url,link_type,notes) VALUES(?,?,?,?,?,?)",
          (payload["entity_type"], int(payload["entity_id"]), payload.get("label", "Link").strip(), url, payload.get("link_type", "website"), payload.get("notes", "")))
        return cur.lastrowid


def validate_event_values(payload):
    allowed_status = {"confirmed", "booked", "planned", "cancelled", "resold", "uncertain"}
    allowed_importance = {"major", "medium", "minor"}
    title = payload.get("title", "").strip()
    start_date = payload.get("start_date", "").strip()
    end_date = (payload.get("end_date") or "").strip() or start_date
    if not title:
        raise ValueError("Title is required")
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        raise ValueError("Dates must use YYYY-MM-DD") from None
    if end < start:
        raise ValueError("End date cannot be before start date")
    status = payload.get("status", "confirmed")
    importance = payload.get("importance", "medium")
    if status not in allowed_status or importance not in allowed_importance:
        raise ValueError("Invalid status or importance")
    confidence = float(payload.get("confidence", 1))
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("Confidence must be between 0 and 1")
    return title, start_date, end_date, status, importance, confidence


def save_event(payload):
    title, start_date, end_date, status, importance, confidence = validate_event_values(payload)
    person_ids = {int(value) for value in payload.get("person_ids", [])}
    with closing(connect()) as con, con:
        cur = con.execute("""INSERT INTO events(title,start_date,end_date,description,category,status,confidence,importance,place_id,trip_id,review_state)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (title, start_date, end_date,
          payload.get("description", ""), payload.get("category", "Life"), status, confidence, importance,
          int(payload["place_id"]) if payload.get("place_id") else None,
          int(payload["trip_id"]) if payload.get("trip_id") else None,
          "needs_review" if status == "uncertain" else "clear"))
        con.executemany("INSERT INTO event_people(event_id,person_id,role) VALUES(?,?,'with')",
                       [(cur.lastrowid, person_id) for person_id in person_ids])
        if status == "uncertain":
            con.execute("INSERT INTO review_items(event_id,issue_type,summary,details) VALUES(?,?,?,?)", (cur.lastrowid, "uncertain_event", f"Review: {title}", "New uncertain event requires corroboration."))
        return cur.lastrowid


def update_event(event_id, payload):
    title, start_date, end_date, status, importance, confidence = validate_event_values(payload)
    person_ids = {int(value) for value in payload.get("person_ids", [])}
    with closing(connect()) as con, con:
        if not con.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
            raise ValueError("Event not found")
        con.execute("""UPDATE events SET title=?,start_date=?,end_date=?,description=?,category=?,status=?,
          confidence=?,importance=?,place_id=?,trip_id=?,review_state=? WHERE id=?""",
          (title, start_date, end_date, payload.get("description", ""),
           payload.get("category", "Life"), status, confidence, importance,
           int(payload["place_id"]) if payload.get("place_id") else None,
           int(payload["trip_id"]) if payload.get("trip_id") else None,
           "needs_review" if status == "uncertain" else "clear", event_id))
        con.execute("DELETE FROM event_people WHERE event_id=?", (event_id,))
        con.executemany("INSERT INTO event_people(event_id,person_id,role) VALUES(?,?,'with')",
                       [(event_id, person_id) for person_id in person_ids])
        con.execute("DELETE FROM weather_cache WHERE event_id=?", (event_id,))
        if status == "uncertain":
            if not con.execute("SELECT 1 FROM review_items WHERE event_id=? AND status='open'", (event_id,)).fetchone():
                con.execute("INSERT INTO review_items(event_id,issue_type,summary,details) VALUES(?,?,?,?)",
                            (event_id, "uncertain_event", f"Review: {title}", "This event needs attendance or provenance review."))
        else:
            con.execute("UPDATE review_items SET status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE event_id=? AND status='open'", (event_id,))
    return event_id


def generic_csv_import(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with closing(connect()) as con, con:
        if con.execute("SELECT 1 FROM imports WHERE checksum=?", (digest,)).fetchone():
            return {"message": "Already imported", "count": 0}
        count = 0
        with path.open(encoding="utf-8-sig", newline="") as f:
            for record in csv.DictReader(f):
                if not record.get("title") or not record.get("start_date"):
                    continue
                status = record.get("status", "uncertain")
                if status not in {"confirmed", "booked", "planned", "cancelled", "resold", "uncertain"}:
                    status = "uncertain"
                cur = con.execute("""INSERT INTO events(title,start_date,end_date,description,category,status,confidence,importance,review_state)
                  VALUES(?,?,?,?,?,?,?,?,?)""", (record["title"].strip(), record["start_date"], record.get("end_date") or record["start_date"],
                  record.get("description", ""), record.get("category", "Life"), status, float(record.get("confidence") or .5),
                  record.get("importance", "medium"), "needs_review" if status == "uncertain" else "clear"))
                if status == "uncertain":
                    con.execute("INSERT INTO review_items(event_id,issue_type,summary,details) VALUES(?,?,?,?)", (cur.lastrowid, "uncertain_event", f"Review: {record['title']}", "Imported uncertain event requires corroboration."))
                count += 1
        con.execute("INSERT INTO imports(filename,checksum,row_count) VALUES(?,?,?)", (path.name, digest, count))
    return {"message": "Import complete", "count": count}


def export_csv():
    out = DATA / "life_atlas_export.csv"
    with closing(connect()) as con, out.open("w", encoding="utf-8-sig", newline="") as f:
        records = rows(con, "SELECT title,start_date,end_date,description,category,status,confidence,importance FROM events ORDER BY start_date")
        writer = csv.DictWriter(f, fieldnames=records[0].keys() if records else ["title", "start_date"])
        writer.writeheader(); writer.writerows(records)
    return out


def backup():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUPS / f"life-atlas-backup-{stamp}.zip"
    temp_db = BACKUPS / f"snapshot-{stamp}.sqlite3"
    src = connect()
    dst = sqlite3.connect(temp_db)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(temp_db, "data/life_atlas.sqlite3")
        if IMPORTS.exists():
            for item in IMPORTS.rglob("*"):
                if item.is_file(): z.write(item, Path("data") / item.relative_to(DATA))
        if MEDIA.exists():
            for item in MEDIA.rglob("*"):
                if item.is_file(): z.write(item, Path("data") / item.relative_to(DATA))
    temp_db.unlink()
    return target


def resolve_review(item_id, outcome):
    status = {"attended": "confirmed", "did_not_attend": "cancelled", "resold": "resold", "still_uncertain": "uncertain"}.get(outcome)
    if not status:
        raise ValueError("Choose an attendance outcome")
    with closing(connect()) as con, con:
        item = con.execute("SELECT event_id FROM review_items WHERE id=? AND status='open'", (item_id,)).fetchone()
        if not item:
            raise ValueError("This review item is no longer open")
        if outcome == "still_uncertain":
            con.execute("UPDATE events SET review_state='needs_review',status='uncertain',confidence=.5 WHERE id=?", (item["event_id"],))
        else:
            con.execute("UPDATE review_items SET status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE id=?", (item_id,))
            con.execute("UPDATE events SET review_state='resolved',status=?,confidence=1 WHERE id=?", (status, item["event_id"]))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, format, *args):
        pass

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def send_file(self, path, content_type, download_name):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_media(self, path, content_type):
        body = path.read_bytes()
        self.send_response(200); self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "private, max-age=86400")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/snapshot": return self.send_json(snapshot())
        if route.startswith("/api/detail/"):
            parts = route.strip("/").split("/")
            return self.send_json(entity_detail(parts[2], int(parts[3])))
        if route.startswith("/api/weather/"):
            return self.send_json(event_weather(int(route.rsplit("/", 1)[1])))
        if route.startswith("/api/media/"):
            path, content_type = media_file(connect, DATA, int(route.rsplit("/", 1)[1]))
            return self.send_media(path, content_type)
        if route == "/api/health": return self.send_json({"status": "ok"})
        if route == "/api/export":
            target = export_csv()
            return self.send_file(target, "text/csv; charset=utf-8", target.name)
        if route == "/api/backup":
            target = backup()
            return self.send_file(target, "application/zip", target.name)
        return super().do_GET()

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            size = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(size) or b"{}")
            if route == "/api/events":
                return self.send_json({"id": save_event(payload)}, 201)
            if route.startswith("/api/events/"):
                return self.send_json({"id": update_event(int(route.rsplit("/", 1)[1]), payload)})
            if route == "/api/links":
                return self.send_json({"id": add_link(payload)}, 201)
            if route == "/api/media":
                return self.send_json(add_media(payload), 201)
            if route.startswith("/api/review/"):
                item_id = int(route.rsplit("/", 1)[1])
                resolve_review(item_id, payload.get("outcome"))
                return self.send_json({"ok": True})
            return self.send_json({"error": "Not found"}, 404)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, 400)


def run():
    initialise()
    host = os.environ.get("LIFE_ATLAS_HOST", "127.0.0.1")
    port = int(os.environ.get("LIFE_ATLAS_PORT", "0"))
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    if os.environ.get("LIFE_ATLAS_SERVER_ONLY", "false").lower() == "true":
        print(f"Life Atlas listening on {host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        import webview
        webview.create_window("Life Atlas", url, width=1320, height=850, min_size=(900, 650))
        webview.start(private_mode=True)
    except ImportError:
        webbrowser.open(url)
        print(f"Life Atlas is open at {url}. Close this window to stop it.")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--import":
        initialise(); print(json.dumps(generic_csv_import(Path(sys.argv[2])), indent=2))
    else:
        run()
