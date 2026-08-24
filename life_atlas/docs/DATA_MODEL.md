# Data model

`schema.sql` is authoritative and is shared with the Windows edition.

Current databases use SQLite `application_id` `LATL` and `user_version` 1. A legacy database with application ID/version zero is accepted only when its required Life Atlas tables and columns match; it is migrated on an isolated copy. Databases from a newer schema version are rejected rather than downgraded.

- `events` stores dated records, attendance status, confidence, importance, review state, and narrative fields.
- `people`, `places`, `trips`, and `chapters` provide alternate ways to navigate events.
- `person_aliases` stores exact alternate names for search and ingestion; `person_merge_history` audits completed merges.
- `event_people` and `event_tags` provide many-to-many relationships.
- `sources` and `evidence` retain provenance.
- `review_items` holds unresolved questions.
- `entity_links`, `media`, and `weather_cache` are optional enrichments. Photo rows may target an event, a person portrait, or an otherwise unlinked diary date. Files are stored under `/data/media` and referenced by safe paths relative to `/data`.
- `imports` records checksum-based ingestion history.

Database restore rejects failed `integrity_check` or `foreign_key_check` results, missing required tables/columns, active triggers/views/virtual tables, unsafe media paths, and missing local media files. It never accepts a WAL or SHM file as the database payload.

Dates use ISO `YYYY-MM-DD`; single-day events have matching start/end dates. Status is one of `confirmed`, `booked`, `planned`, `cancelled`, `resold`, or `uncertain`. Confidence is finite and between 0 and 1. A booking must not be promoted to confirmed attendance without evidence.
