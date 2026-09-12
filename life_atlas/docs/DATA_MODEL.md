# Data model

`schema.sql` is authoritative and is shared with the Windows edition.

Current databases use SQLite `application_id` `LATL` and `user_version` 2. A legacy database with application ID/version zero is accepted only when its required Life Atlas tables and columns match; it is migrated on an isolated copy. Databases from a newer schema version are rejected rather than downgraded.

- `events` stores dated records, attendance status, confidence, importance, review state, and narrative fields.
- `people`, `places`, `trips`, and `chapters` provide alternate ways to navigate events.
- `person_aliases` stores exact alternate names for search and ingestion; `person_merge_history` audits completed merges.
- `event_people` and `event_tags` provide many-to-many relationships.
- `sources` and `evidence` retain provenance.
- `review_items` holds unresolved questions.
- `entity_links`, `media`, and `weather_cache` are optional enrichments. Photo rows may target an event, a person portrait, or an otherwise unlinked diary date. Files are stored under `/data/media` and referenced by safe paths relative to `/data`.
- `imports` records checksum-based ingestion history.
- `agent_mutations` records one row per write accepted through the agent API: its idempotency key, the operation, the row it created, and a SHA-256 of the canonical request. It makes a retried request safe to replay and is the audit trail for anything an automated client added. It is not personal content and may be empty.
- `ingestion_runs` records each curated connector package and its inserted, skipped, and review counts.
- `source_records` gives an upstream object a stable identity using `(source, object type, external ID)`.
- `event_source_records` links one accepted event to one or more upstream records without conflating provenance with the event itself.

Schema version 2 introduces these ingestion identity tables. Re-seeing the same source record never creates a second event. If its content changes, Life Atlas updates the source-record fingerprint and opens a review item; it does not silently overwrite the accepted event.

Database restore rejects failed `integrity_check` or `foreign_key_check` results, missing required tables/columns, active triggers/views/virtual tables, unsafe media paths, missing local media files, media hash mismatches, and conflicting content-addressed files. A restore ZIP must contain exactly one database plus the media paths referenced by that database. It never accepts a WAL or SHM file as the database payload.

Dates use ISO `YYYY-MM-DD`; single-day events have matching start/end dates. Status is one of `confirmed`, `booked`, `planned`, `cancelled`, `resold`, or `uncertain`. Confidence is finite and between 0 and 1. A booking must not be promoted to confirmed attendance without evidence.
