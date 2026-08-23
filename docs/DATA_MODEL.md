# Data model

`schema.sql` is authoritative and is shared with the Windows edition.

- `events` stores dated records, attendance status, confidence, importance, review state, and narrative fields.
- `people`, `places`, `trips`, and `chapters` provide alternate ways to navigate events.
- `event_people` and `event_tags` provide many-to-many relationships.
- `sources` and `evidence` retain provenance.
- `review_items` holds unresolved questions.
- `entity_links`, `media`, and `weather_cache` are optional enrichments. Photo rows may target an event, a person portrait, or an otherwise unlinked diary date. Files are stored under `/data/media` and referenced by safe paths relative to `/data`.
- `imports` records checksum-based ingestion history.

Dates use ISO `YYYY-MM-DD`; single-day events have matching start/end dates. Status is one of `confirmed`, `booked`, `planned`, `cancelled`, `resold`, or `uncertain`. Confidence is finite and between 0 and 1. A booking must not be promoted to confirmed attendance without evidence.
