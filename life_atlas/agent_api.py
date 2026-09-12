from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
from contextlib import closing
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import app

HOST = "0.0.0.0"
PORT = int(os.environ.get("LIFE_ATLAS_AGENT_PORT", "8096"))
MAX_REQUEST_BYTES = 256 * 1024
MAX_LIMIT = 100
MAX_EVIDENCE_RECORDS = 20
MAX_CURSOR_CHARS = 256
SQLITE_MAX_INTEGER = 2**63 - 1

_EVENT_COLUMNS = """SELECT e.id,e.title,e.start_date,e.end_date,e.description,e.category,
  e.status,e.confidence,e.importance,e.review_state,p.name place_name,t.title trip_title,
  -- DISTINCT matters: event_people is keyed on (event_id, person_id, role), so one
  -- person attached in two roles would otherwise be listed twice.
  (SELECT GROUP_CONCAT(DISTINCT xp.name) FROM event_people xep JOIN people xp ON xp.id=xep.person_id
   WHERE xep.event_id=e.id) people,
  (SELECT COUNT(*) FROM evidence ev WHERE ev.event_id=e.id) evidence_count
  FROM events e LEFT JOIN places p ON p.id=e.place_id LEFT JOIN trips t ON t.id=e.trip_id"""


def _iso_date(value: object, field: str) -> str | None:
    """Return a validated YYYY-MM-DD string, or None when the value is absent."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{field} must use YYYY-MM-DD") from None


def _encode_cursor(row: dict) -> str:
    raw = f"{row['start_date']}|{row['id']}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[str, int]:
    """Decode a cursor previously produced by _encode_cursor.

    The date half is deliberately not format-checked. `events.start_date` is only
    TEXT, and the CSV and curated-ingest paths do not validate it, so demanding an
    ISO date here would make the server reject its own cursors and strand every
    page beyond a non-ISO row. The value is bound as a query parameter, so any
    string is safe; the identifier is bounded because SQLite cannot bind a bignum.
    """
    if len(value) > MAX_CURSOR_CHARS:
        raise ValueError("Invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        text = base64.urlsafe_b64decode(padded.encode()).decode("utf-8")
        start_date, separator, event_id = text.rpartition("|")
        if not separator:
            raise ValueError
        cursor_id = int(event_id)
    except (ValueError, TypeError, UnicodeDecodeError):
        # binascii.Error subclasses ValueError, so malformed base64 lands here too.
        raise ValueError("Invalid cursor") from None
    if not 0 < cursor_id <= SQLITE_MAX_INTEGER:
        raise ValueError("Invalid cursor")
    return start_date, cursor_id


def _event_rows(query: str, limit: int, *, date_from: str | None = None,
                date_to: str | None = None, cursor: str | None = None) -> tuple[list[dict], str | None]:
    """Return one page of events, newest first, plus a cursor for the next page.

    Person and place matching uses EXISTS rather than a filtered join, so the
    participant list reported for a match is the event's real one.
    """
    clauses: list[str] = []
    params: list[object] = []

    text = str(query or "").strip()
    if text:
        needle = f"%{text}%"
        clauses.append("""(e.title LIKE ? OR e.description LIKE ?
          OR EXISTS (SELECT 1 FROM event_people fep JOIN people fp ON fp.id=fep.person_id
                     WHERE fep.event_id=e.id AND fp.name LIKE ?)
          OR EXISTS (SELECT 1 FROM places fpl WHERE fpl.id=e.place_id AND fpl.name LIKE ?))""")
        params += [needle, needle, needle, needle]

    # An event overlaps the window when it starts on or before the end of the
    # window and finishes on or after the start of it.
    if date_from:
        clauses.append("COALESCE(NULLIF(e.end_date,''),e.start_date) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("e.start_date <= ?")
        params.append(date_to)

    if cursor:
        cursor_date, cursor_id = _decode_cursor(cursor)
        clauses.append("(e.start_date < ? OR (e.start_date = ? AND e.id < ?))")
        params += [cursor_date, cursor_date, cursor_id]

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    # Fetch one extra row to discover whether a further page exists.
    params.append(limit + 1)
    with closing(app.connect()) as con:
        rows = app.rows(con, f"{_EVENT_COLUMNS} {where} ORDER BY e.start_date DESC,e.id DESC LIMIT ?", params)

    next_cursor = _encode_cursor(rows[limit - 1]) if len(rows) > limit else None
    return rows[:limit], next_cursor


def _people_rows(query: str, limit: int) -> list[dict]:
    needle = f"%{query.strip()}%"
    with closing(app.connect()) as con:
        return app.rows(con, """SELECT p.id,p.name,p.relationship,p.notes,
          GROUP_CONCAT(DISTINCT pa.alias) aliases,COUNT(DISTINCT ep.event_id) event_count,
          MIN(e.start_date) first_event,MAX(e.start_date) latest_event
          FROM people p LEFT JOIN person_aliases pa ON pa.person_id=p.id
          LEFT JOIN event_people ep ON ep.person_id=p.id LEFT JOIN events e ON e.id=ep.event_id
          WHERE p.name LIKE ? OR pa.alias LIKE ? GROUP BY p.id ORDER BY event_count DESC,p.name LIMIT ?""",
          (needle, needle, limit))


def _validate_evidence(payload: dict) -> list[dict]:
    """Normalise the optional evidence records attached to a created event."""
    raw = payload.get("evidence") or []
    if not isinstance(raw, list):
        raise ValueError("evidence must be a list")
    if len(raw) > MAX_EVIDENCE_RECORDS:
        raise ValueError(f"An event accepts at most {MAX_EVIDENCE_RECORDS} evidence records")
    records = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each evidence record must be an object")
        source = " ".join(str(item.get("source") or "").split())
        if not source:
            raise ValueError("Each evidence record needs a source")
        if len(source) > 200:
            raise ValueError("Evidence source name is too long")
        confidence = float(item.get("confidence", 0.5))
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Evidence confidence must be between 0 and 1")
        records.append({
            "source": source,
            "evidence_type": str(item.get("type") or "record").strip()[:50] or "record",
            "source_ref": str(item.get("reference") or "")[:1000],
            "excerpt": str(item.get("excerpt") or "")[:2000],
            "observed_date": _iso_date(item.get("observed_date"), "Evidence observed_date"),
            "confidence": confidence,
        })
    return records


def create_event(payload: dict, idempotency_key: str) -> tuple[int, bool]:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    request_hash = hashlib.sha256(canonical).hexdigest()
    title, start_date, end_date, status, importance, confidence = app.validate_event_values(payload)
    evidence = _validate_evidence(payload)
    person_ids = {int(value) for value in payload.get("person_ids", [])}
    with closing(app.connect()) as con, con:
        prior = con.execute("SELECT target_id,request_sha256 FROM agent_mutations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if prior:
            if prior["request_sha256"] != request_hash:
                raise ValueError("Idempotency key was already used for a different request")
            return int(prior["target_id"]), True
        missing = [person_id for person_id in person_ids if not con.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone()]
        if missing:
            raise ValueError("Unknown person id")
        cur = con.execute("""INSERT INTO events(title,start_date,end_date,description,category,status,confidence,
          importance,place_id,trip_id,review_state) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
          title, start_date, end_date, payload.get("description", ""), payload.get("category", "Life"),
          status, confidence, importance, int(payload["place_id"]) if payload.get("place_id") else None,
          int(payload["trip_id"]) if payload.get("trip_id") else None,
          "needs_review" if status == "uncertain" else "clear"))
        event_id = int(cur.lastrowid)
        con.executemany("INSERT INTO event_people(event_id,person_id,role) VALUES(?,?,'with')",
                        [(event_id, person_id) for person_id in person_ids])
        for record in evidence:
            con.execute("INSERT OR IGNORE INTO sources(name,source_type,notes) VALUES(?,?,?)",
                        (record["source"], "agent", "Supplied through the Life Atlas agent API"))
            source_id = con.execute("SELECT id FROM sources WHERE name=?", (record["source"],)).fetchone()[0]
            con.execute("""INSERT INTO evidence(event_id,source_id,evidence_type,source_ref,observed_date,
              excerpt,confidence) VALUES(?,?,?,?,?,?,?)""",
              (event_id, source_id, record["evidence_type"], record["source_ref"],
               record["observed_date"], record["excerpt"], record["confidence"]))
        if status == "uncertain":
            con.execute("INSERT INTO review_items(event_id,issue_type,summary,details) VALUES(?,?,?,?)",
                        (event_id, "uncertain_event", f"Review: {title}", "Agent-created uncertain event requires review."))
        con.execute("""INSERT INTO agent_mutations(idempotency_key,operation,target_type,target_id,request_sha256)
          VALUES(?,?,?,?,?)""", (idempotency_key, "create", "event", event_id, request_hash))
        return event_id, False


class Handler(BaseHTTPRequestHandler):
    server_version = "LifeAtlasAgent/1.0"

    def _authorized(self) -> bool:
        expected = os.environ.get("LIFE_ATLAS_AGENT_API_KEY", "")
        supplied = self.headers.get("Authorization", "")
        return len(expected) >= 24 and hmac.compare_digest(supplied, f"Bearer {expected}")

    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send(401, {"error": {"code": "unauthorized", "message": "Valid bearer key required"}})
        return False

    def do_GET(self) -> None:
        if not self._require_auth():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/v1/health":
                with closing(app.connect()) as con:
                    integrity = con.execute("PRAGMA quick_check").fetchone()[0]
                    counts = {name: con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                              for name in ("events", "people", "places", "trips")}
                    schema_version = con.execute("PRAGMA user_version").fetchone()[0]
                return self._send(200, {"status": "ok" if integrity == "ok" else "degraded",
                    "version": os.environ.get("LIFE_ATLAS_VERSION", "development"),
                    "schema_version": schema_version, "database": integrity, "counts": counts,
                    "capabilities": ["search_events", "filter_events_by_date", "paginate_events",
                                     "get_event", "search_people", "create_event", "create_event_evidence"]})
            params = parse_qs(parsed.query)
            if parsed.path == "/v1/people":
                query = params.get("q", [""])[0]
                limit = min(MAX_LIMIT, max(1, int(params.get("limit", ["20"])[0])))
                return self._send(200, {"items": _people_rows(query, limit)})
            if parsed.path == "/v1/events":
                query = params.get("q", [""])[0]
                limit = min(MAX_LIMIT, max(1, int(params.get("limit", ["20"])[0])))
                date_from = _iso_date(params.get("from", [""])[0], "from")
                date_to = _iso_date(params.get("to", [""])[0], "to")
                if date_from and date_to and date_from > date_to:
                    raise ValueError("from must not be after to")
                items, next_cursor = _event_rows(query, limit, date_from=date_from, date_to=date_to,
                                                 cursor=params.get("cursor", [""])[0] or None)
                return self._send(200, {"items": items, "next_cursor": next_cursor})
            if parsed.path.startswith("/v1/events/"):
                return self._send(200, app.entity_detail("event", int(parsed.path.rsplit("/", 1)[1])))
            return self._send(404, {"error": {"code": "not_found", "message": "Unknown endpoint"}})
        except (ValueError, TypeError, OverflowError) as exc:
            return self._send(400, {"error": {"code": "bad_request", "message": str(exc)}})

    def do_POST(self) -> None:
        if not self._require_auth():
            return
        if self.path != "/v1/events":
            return self._send(404, {"error": {"code": "not_found", "message": "Unknown endpoint"}})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > MAX_REQUEST_BYTES:
                raise ValueError("Request size is invalid")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object")
            key = self.headers.get("Idempotency-Key", "").strip()
            if len(key) < 16 or len(key) > 128:
                raise ValueError("Idempotency-Key must contain 16 to 128 characters")
            event_id, replayed = create_event(payload, key)
            return self._send(200 if replayed else 201, {"id": event_id, "replayed": replayed})
        except (ValueError, TypeError, OverflowError, json.JSONDecodeError) as exc:
            return self._send(400, {"error": {"code": "bad_request", "message": str(exc)}})

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    app.initialise()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
