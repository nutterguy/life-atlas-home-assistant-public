from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import agent_api
import app


class AgentAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_data, cls.original_db = app.DATA, app.DB
        cls.temp = tempfile.TemporaryDirectory()
        app.DATA = Path(cls.temp.name)
        app.DB = app.DATA / "life_atlas.sqlite3"
        app.IMPORTS = app.DATA / "imports"
        app.BACKUPS = app.DATA / "backups"
        app.MEDIA = app.DATA / "media"
        app.RESTORE = None
        os.environ["LIFE_ATLAS_AGENT_API_KEY"] = "fixture-agent-key-with-24-characters"
        os.environ["LIFE_ATLAS_SEED_SAMPLE"] = "true"
        app.initialise()
        cls.server = agent_api.ThreadingHTTPServer(("127.0.0.1", 0), agent_api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        app.DATA, app.DB = cls.original_data, cls.original_db
        app.RESTORE = None
        cls.temp.cleanup()

    def request(self, path, *, method="GET", payload=None, key=True, idempotency_key=None):
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = "Bearer fixture-agent-key-with-24-characters"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        with urlopen(Request(self.base + path, data=data, headers=headers, method=method), timeout=2) as response:
            return response.status, json.loads(response.read())

    def test_authentication_is_required(self):
        with self.assertRaises(HTTPError) as ctx:
            self.request("/v1/health", key=False)
        self.assertEqual(ctx.exception.code, 401)

    def test_health_has_version_schema_and_aggregate_counts(self):
        status, payload = self.request("/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["database"], "ok")
        self.assertGreater(payload["counts"]["events"], 0)
        self.assertIn("create_event", payload["capabilities"])

    def test_event_and_people_search_and_event_detail(self):
        _, events = self.request("/v1/events?q=Mexico&limit=5")
        self.assertEqual(events["items"][0]["title"], "Mexico holiday with Alex")
        event_id = events["items"][0]["id"]
        _, detail = self.request(f"/v1/events/{event_id}")
        self.assertEqual(detail["kind"], "event")
        _, people = self.request("/v1/people?q=Alex")
        self.assertEqual(people["items"][0]["name"], "Alex Rivera")

    def test_create_is_validated_audited_and_idempotent(self):
        payload = {"title": "Fixture milestone", "start_date": "2026-09-09",
                   "end_date": "2026-09-09", "status": "confirmed", "importance": "medium"}
        key = "fixture-request-0001"
        status, first = self.request("/v1/events", method="POST", payload=payload, idempotency_key=key)
        replay_status, replay = self.request("/v1/events", method="POST", payload=payload, idempotency_key=key)
        self.assertEqual(status, 201)
        self.assertEqual(replay_status, 200)
        self.assertEqual(first["id"], replay["id"])
        self.assertTrue(replay["replayed"])
        with closing(app.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM events WHERE title=?", (payload["title"],)).fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM agent_mutations WHERE target_id=?", (first["id"],)).fetchone()[0], 1)

    def test_date_range_selects_events_overlapping_the_window(self):
        """Riverside Festival runs 2025-06-25 to 2025-06-29 in the sample data."""
        _, inside = self.request("/v1/events?from=2025-06-26&to=2025-06-26&limit=100")
        self.assertIn("Riverside Festival", [item["title"] for item in inside["items"]])

        _, after = self.request("/v1/events?from=2025-06-30&to=2025-07-05&limit=100")
        self.assertNotIn("Riverside Festival", [item["title"] for item in after["items"]])

        _, month = self.request("/v1/events?from=2025-06-01&to=2025-06-30&limit=100")
        self.assertIn("Started at Northwind Studios", [item["title"] for item in month["items"]])

    def test_date_and_cursor_inputs_fail_closed(self):
        for path in ("/v1/events?from=09-09-2026", "/v1/events?to=not-a-date",
                     "/v1/events?from=2026-02-02&to=2026-01-01", "/v1/events?cursor=!!!not-base64!!!",
                     "/v1/events?limit=abc"):
            with self.assertRaises(HTTPError, msg=path) as ctx:
                self.request(path)
            self.assertEqual(ctx.exception.code, 400, path)

    def test_pagination_returns_every_event_exactly_once(self):
        _, unpaged = self.request("/v1/events?limit=100")
        expected = [item["id"] for item in unpaged["items"]]
        self.assertGreater(len(expected), 2, "sample data should span several pages of two")

        collected, cursor, pages = [], None, 0
        while pages < 100:
            path = "/v1/events?limit=2" + (f"&cursor={cursor}" if cursor else "")
            _, page = self.request(path)
            collected += [item["id"] for item in page["items"]]
            pages += 1
            cursor = page["next_cursor"]
            if not cursor:
                break

        self.assertEqual(collected, expected)
        self.assertEqual(len(collected), len(set(collected)))
        self.assertIsNone(cursor)

    def test_search_reports_the_full_participant_list(self):
        """Matching on one person must not hide an event's other attendees."""
        with closing(app.connect()) as con, con:
            event_id = con.execute(
                "INSERT INTO events(title,start_date,end_date) VALUES('Quiet planning session','2024-01-05','2024-01-05')"
            ).lastrowid
            for name in ("Rowan", "Sasha"):
                person_id = con.execute("INSERT INTO people(name) VALUES(?)", (name,)).lastrowid
                con.execute("INSERT INTO event_people(event_id,person_id,role) VALUES(?,?,'with')", (event_id, person_id))

        for query in ("Rowan", "Sasha", "planning"):
            _, found = self.request(f"/v1/events?q={query}&limit=10")
            match = [item for item in found["items"] if item["id"] == event_id]
            self.assertTrue(match, query)
            self.assertEqual(sorted(match[0]["people"].split(",")), ["Rowan", "Sasha"], query)

    def test_create_accepts_evidence_without_duplicating_it_on_replay(self):
        payload = {
            "title": "Evidence fixture", "start_date": "2026-03-01", "status": "uncertain",
            "evidence": [
                {"source": "ChatGPT conversation", "type": "note", "reference": "thread-1",
                 "excerpt": "Discussed the trip", "confidence": 0.6, "observed_date": "2026-03-02"},
                {"source": "Gmail", "type": "booking", "reference": "msg-1", "confidence": 0.9},
            ],
        }
        key = "fixture-evidence-0001"
        status, created = self.request("/v1/events", method="POST", payload=payload, idempotency_key=key)
        self.assertEqual(status, 201)
        replay_status, replayed = self.request("/v1/events", method="POST", payload=payload, idempotency_key=key)
        self.assertEqual(replay_status, 200)
        self.assertEqual(created["id"], replayed["id"])

        with closing(app.connect()) as con:
            rows = con.execute("""SELECT s.name,ev.evidence_type,ev.source_ref,ev.observed_date,ev.confidence
                                  FROM evidence ev JOIN sources s ON s.id=ev.source_id
                                  WHERE ev.event_id=? ORDER BY ev.id""", (created["id"],)).fetchall()
            self.assertEqual(len(rows), 2, "replay must not append a second copy of the evidence")
            self.assertEqual(rows[0]["name"], "ChatGPT conversation")
            self.assertEqual(rows[0]["observed_date"], "2026-03-02")
            self.assertEqual(rows[1]["confidence"], 0.9)
            # The seeded Gmail source is reused rather than duplicated.
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sources WHERE name='Gmail'").fetchone()[0], 1)

        _, detail = self.request(f"/v1/events/{created['id']}")
        self.assertEqual(len(detail["evidence"]), 2)

    def test_evidence_validation_fails_closed(self):
        cases = {
            "empty-source": [{"source": "", "confidence": 0.5}],
            "high-confidence": [{"source": "X", "confidence": 5}],
            "bad-observed-date": [{"source": "X", "observed_date": "09/09/2026"}],
            "not-a-list": "definitely not a list",
            "too-many": [{"source": "X"}] * (agent_api.MAX_EVIDENCE_RECORDS + 1),
        }
        for label, evidence in cases.items():
            body = {"title": f"Rejected {label}", "start_date": "2026-04-01", "evidence": evidence}
            with self.assertRaises(HTTPError, msg=label) as ctx:
                self.request("/v1/events", method="POST", payload=body,
                             idempotency_key=f"reject-{label}-000000")
            self.assertEqual(ctx.exception.code, 400, label)

        with closing(app.connect()) as con:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM events WHERE title LIKE 'Rejected %'").fetchone()[0], 0,
                "a rejected evidence payload must not leave an event behind",
            )

    def test_health_advertises_the_new_capabilities(self):
        _, payload = self.request("/v1/health")
        for capability in ("filter_events_by_date", "paginate_events", "create_event_evidence"):
            self.assertIn(capability, payload["capabilities"])

    def test_oversized_identifiers_are_rejected_not_crashed(self):
        """SQLite cannot bind a bignum; that must surface as 400, not a dropped connection."""
        huge = "9" * 25
        cursor = base64.urlsafe_b64encode(f"2025-01-01|{huge}".encode()).decode().rstrip("=")
        for path in (f"/v1/events?cursor={cursor}", f"/v1/events/{huge}"):
            with self.assertRaises(HTTPError, msg=path) as ctx:
                self.request(path)
            self.assertEqual(ctx.exception.code, 400, path)

        for bad in ("MHww", base64.urlsafe_b64encode(b"2025-01-01|-4").decode().rstrip("=")):
            with self.assertRaises(HTTPError, msg=bad) as ctx:
                self.request(f"/v1/events?cursor={bad}")
            self.assertEqual(ctx.exception.code, 400, bad)

    def test_pagination_survives_a_non_iso_start_date(self):
        """start_date is only TEXT, so the decoder must accept whatever the encoder emits."""
        with closing(app.connect()) as con, con:
            odd_id = con.execute(
                "INSERT INTO events(title,start_date,end_date) VALUES('Undated note','March 2025','March 2025')"
            ).lastrowid
        try:
            _, unpaged = self.request("/v1/events?limit=100")
            expected = [item["id"] for item in unpaged["items"]]
            self.assertIn(odd_id, expected)

            collected, cursor, pages = [], None, 0
            while pages < 100:
                path = "/v1/events?limit=1" + (f"&cursor={cursor}" if cursor else "")
                _, page = self.request(path)
                collected += [item["id"] for item in page["items"]]
                pages += 1
                cursor = page["next_cursor"]
                if not cursor:
                    break
            self.assertEqual(collected, expected, "paging must not strand rows after a non-ISO date")
        finally:
            with closing(app.connect()) as con, con:
                con.execute("DELETE FROM events WHERE id=?", (odd_id,))

    def test_a_person_in_two_roles_is_listed_once(self):
        with closing(app.connect()) as con, con:
            event_id = con.execute(
                "INSERT INTO events(title,start_date,end_date) VALUES('Committee meeting','2024-02-02','2024-02-02')"
            ).lastrowid
            person_id = con.execute("INSERT INTO people(name) VALUES('Devon')").lastrowid
            for role in ("with", "organiser"):
                con.execute("INSERT INTO event_people(event_id,person_id,role) VALUES(?,?,?)",
                            (event_id, person_id, role))

        _, found = self.request("/v1/events?q=Committee&limit=5")
        match = [item for item in found["items"] if item["id"] == event_id][0]
        self.assertEqual(match["people"], "Devon")

    def test_documentation_lists_every_advertised_capability(self):
        """The doc tells clients to trust `capabilities`, so it must not drift from the code."""
        documentation = (Path(__file__).parents[1] / "docs" / "AGENT_API.md").read_text(encoding="utf-8")
        _, payload = self.request("/v1/health")
        for capability in payload["capabilities"]:
            self.assertIn(capability, documentation,
                          f"docs/AGENT_API.md does not mention the advertised capability {capability!r}")

    def test_reversed_dates_and_key_reuse_fail_closed(self):
        bad = {"title": "Bad", "start_date": "2026-09-10", "end_date": "2026-09-09"}
        with self.assertRaises(HTTPError) as ctx:
            self.request("/v1/events", method="POST", payload=bad, idempotency_key="fixture-request-0002")
        self.assertEqual(ctx.exception.code, 400)
        good = {"title": "First", "start_date": "2026-09-09"}
        self.request("/v1/events", method="POST", payload=good, idempotency_key="fixture-request-0003")
        with self.assertRaises(HTTPError) as ctx:
            self.request("/v1/events", method="POST", payload=dict(good, title="Changed"), idempotency_key="fixture-request-0003")
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
