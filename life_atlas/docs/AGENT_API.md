# Life Atlas agent API

The agent API is a small, read-mostly HTTP/JSON interface intended for a machine client such as ChatGPT, an MCP bridge, or a script. It exists so an agent can ask questions about the timeline and add a single event at a time without driving the browser interface.

It is deliberately separate from the Ingress interface. Home Assistant Ingress authenticates a signed-in browser session; a remote machine client has no such session, so the agent API uses its own bearer key on its own port instead.

## Status and scope

The agent API is a work in progress. It currently supports four operations: search and filter events, read one event in full, search people, and create one event with its evidence. It cannot edit or delete records, manage media, or run a restore. Anything not listed under **Endpoints** is not available.

`GET /v1/health` returns a `capabilities` list. Read it rather than assuming what this build supports.

## Enabling it

1. Generate a random key of at least 24 characters. Anything shorter is rejected and the API will refuse every request.
2. Open **Settings > Apps > Life Atlas > Configuration**.
3. Put the key in the `agent_api_key` option and save.
4. Restart Life Atlas.

The option is declared as `password?` so Home Assistant masks it in the interface. `run.sh` exports it to the agent process as `LIFE_ATLAS_AGENT_API_KEY`; it is never written to `/data` and never returned by any endpoint.

If `agent_api_key` is left empty the API stays running but refuses everything with `401`. This is the default and is the intended state when no agent is configured.

**A key shorter than 24 characters fails the same way as no key at all**, and the failure is silent — the agent process does not log rejected requests. If every call returns `401` with a key you believe is correct, check its length first.

## Reaching it

The API listens on port `8096` on the Home Assistant host, and `config.yaml` publishes that port so clients on your own network can reach it directly:

```bash
curl -H "Authorization: Bearer $LIFE_ATLAS_AGENT_API_KEY" \
  http://homeassistant.local:8096/v1/health
```

This is the intended deployment: a client on the same network — such as the ChatGPT desktop application connecting to a local endpoint — talks straight to the add-on. No tunnel, reverse proxy, or public hostname is involved, and the traffic never leaves the network.

The bearer key crosses the LAN in clear text, since the API does not terminate TLS itself. On a trusted home network that is a deliberate trade for simplicity.

Two constraints apply if you ever want to reach the API from outside the network:

- **Do not port-forward 8096 directly.** The key would then cross the internet in clear text on every request.
- **Home Assistant Cloud (Nabu Casa) will not carry it.** Remote UI proxies Home Assistant itself, including Ingress add-ons; it does not expose arbitrary add-on ports. A tunnel that terminates TLS — a Cloudflare Tunnel, or an existing reverse proxy with a real certificate — is the route to use.

## Authentication

Every request must carry:

```text
Authorization: Bearer <agent_api_key>
```

The comparison is constant-time. A missing, malformed, or incorrect key returns:

```json
{"error": {"code": "unauthorized", "message": "Valid bearer key required"}}
```

## Endpoints

Responses from the endpoints below are `application/json` with `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`. Only `GET` and `POST` are implemented; any other method returns the Python standard library's default `501` error page as HTML, without those headers. Do not treat their presence as a way to detect a valid response.

### `GET /v1/health`

Liveness plus a cheap integrity check. Safe to poll.

```json
{
  "status": "ok",
  "version": "0.14.4",
  "schema_version": 2,
  "database": "ok",
  "counts": {"events": 6, "people": 1, "places": 5, "trips": 1},
  "capabilities": ["search_events", "filter_events_by_date", "paginate_events", "get_event",
                   "search_people", "create_event", "create_event_evidence"]
}
```

`status` is `degraded` when SQLite `quick_check` does not return `ok`. `capabilities` is the authoritative list of what this build supports; a client should read it rather than assume.

### `GET /v1/events?q=<text>&from=<date>&to=<date>&limit=<n>&cursor=<cursor>`

Searches and filters events. Every parameter is optional and they combine with AND.

| Parameter | Meaning |
|---|---|
| `q` | Substring match against event title, description, linked person names, and place name |
| `from` | Include events that finish on or after this date (`YYYY-MM-DD`) |
| `to` | Include events that start on or before this date (`YYYY-MM-DD`) |
| `limit` | Page size, default 20, clamped to 1–100 |
| `cursor` | Opaque `next_cursor` from a previous response |

`q` is matched with SQL `LIKE`, so `%` and `_` behave as wildcards, and an empty `q` matches everything. Results are ordered by `start_date` descending, then `id` descending.

**Date filtering uses overlap, not containment.** An event is included when it overlaps the window at any point, so a trip running 25–29 June is returned by `from=2025-06-26&to=2025-06-26`. Asking for a single day therefore returns everything that was happening on that day, which is usually what a question about a date means. `from` must not be after `to`, and a malformed date is rejected with `400`.

Each item carries the event's core fields plus `place_name`, `trip_title`, a comma-joined `people`, and `evidence_count`. The `people` list is always the event's complete participant list, even when the match was on one person's name.

Responses are paginated:

```json
{"items": [ ... ], "next_cursor": "MjAyNS0wNi0yNXw1"}
```

`next_cursor` is `null` on the last page. Pass it back as `cursor` to fetch the next one. Paging is keyset-based rather than offset-based, so walking the pages returns every event exactly once even if records are added while you page. A cursor that cannot be decoded is rejected with `400`.

```bash
# Everything that happened in March 2025, oldest page first
curl -H "Authorization: Bearer $LIFE_ATLAS_AGENT_API_KEY" \
  "http://localhost:8096/v1/events?from=2025-03-01&to=2025-03-31&limit=50"
```

### `GET /v1/events/<id>`

The full detail record for one event: the event itself, its evidence with source names, linked people, tags, media rows, and external links. This is the same structure the browser interface uses.

### `GET /v1/people?q=<text>&limit=<n>`

Searches person names and aliases. Returns each person with their aliases, event count, and first and latest event dates. Ordered by event count descending.

This endpoint takes `limit` but not `cursor`. The people table is small enough that a single page is sufficient; only events are paginated.

### `POST /v1/events`

Creates one event. Requires an `Idempotency-Key` header of 16 to 128 characters.

```bash
curl -X POST http://localhost:8096/v1/events \
  -H "Authorization: Bearer $LIFE_ATLAS_AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: chatgpt-2026-09-09-0001" \
  -d '{"title":"Dinner with Alex","start_date":"2026-09-09","status":"uncertain","confidence":0.6}'
```

Accepted fields are `title` and `start_date` (both required), `end_date`, `description`, `category`, `status`, `confidence`, `importance`, `place_id`, `trip_id`, `person_ids`, and `evidence`. Validation is shared with the browser interface: dates must be `YYYY-MM-DD`, `end_date` must not precede `start_date`, `status` must be one of `confirmed`, `booked`, `planned`, `cancelled`, `resold`, or `uncertain`, `importance` must be `major`, `medium`, or `minor`, and `confidence` must be finite and between 0 and 1. Unknown `person_ids` are rejected.

#### Attaching evidence

`evidence` is an optional list of up to 20 records recording where the claim came from. Life Atlas treats provenance as part of the record rather than an afterthought, so an agent that infers an event should say what it inferred it from.

```json
"evidence": [
  {
    "source": "ChatGPT conversation",
    "type": "note",
    "reference": "thread-42",
    "excerpt": "Mentioned booking a table for the 1st",
    "confidence": 0.6,
    "observed_date": "2026-03-02"
  }
]
```

Only `source` is required. `type` defaults to `record` and `confidence` to `0.5`; `reference`, `excerpt`, and `observed_date` are optional. Sources are matched by name and created on demand with source type `agent`, so naming an existing source such as `Gmail` reuses that row rather than duplicating it.

The whole request is validated before anything is written, so a malformed evidence record leaves no partial event behind. Evidence is written inside the same transaction as the event and is covered by the idempotency key — replaying a request returns the original event without appending a second copy of its evidence.

An event created with `status: "uncertain"` is automatically given `review_state: "needs_review"` and raises a review item, so agent-sourced records surface in the detective queue rather than entering the timeline as settled fact. **This is the intended way for an agent to add anything it inferred rather than read directly from a source.**

Responses:

| Case | Status | Body |
|---|---|---|
| Created | `201` | `{"id": 7, "replayed": false}` |
| Same key, same body | `200` | `{"id": 7, "replayed": true}` |
| Same key, different body | `400` | `Idempotency key was already used for a different request` |
| Validation failure | `400` | `{"error": {"code": "bad_request", "message": "..."}}` |

Replays are served from the `agent_mutations` table, which records the idempotency key, the operation, the target row, and a SHA-256 of the canonical request. A retry after a network failure is therefore safe and cannot create a duplicate event.

## Current limitations

These are known and deliberate, but a client should not be surprised by them.

- **No TLS.** Terminate it in front of the add-on.
- **No rate limiting.** A client in a retry loop can generate unbounded load.
- **No CORS headers.** The API cannot be called from a web page's JavaScript; it is for server-side and command-line clients.
- **`Content-Type` is not enforced on `POST`.** The body is parsed as JSON regardless of the declared type.
- **Request bodies are capped at 256 KiB.**
- **Search is substring matching, not ranked relevance.** There is no full-text index.
- **Event search does not match person aliases.** `/v1/people` does; `/v1/events?q=` matches canonical person names only.
- **Writes are limited to creating events and their evidence.** There is no update, delete, or media path.
- **There are no `/v1/places` or `/v1/trips` endpoints.** Place and trip names come back on event records, and `/v1/events/<id>` carries the full detail.

## Relationship to the rest of Life Atlas

The agent API reads and writes the same `/data/life_atlas.sqlite3` as the browser interface, in a separate process. It is not routed through `mcp_ingress_proxy.py` and does not participate in the restore maintenance gate described in `docs/SQLITE_RESTORE.md`. Do not drive the agent API while a database restore is in progress.

The bundled Google Photos MCP is a different thing entirely and is documented in `docs/GOOGLE_PHOTOS.md`. It is an outbound integration for photo workflows; the agent API is an inbound interface for querying the timeline.
