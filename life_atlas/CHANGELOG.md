# Changelog

Notable user-visible changes to Life Atlas are recorded here. Home Assistant
displays this file when an app update is available.

## 0.14.4

- Prevented demonstration chapters from being inserted into existing timelines that have no chapters.
- Corrected the documented agent API key variable and removed a stale build-file reference.

## 0.14.3

- Replaced the first-run sample timeline with fully synthetic records. The sample data now lives in `sample-seed.json` rather than being embedded in the application, so it is easy to inspect and replace.
- Sample data is still only written to a database that has no events of its own; existing timelines are untouched by this update.

## 0.14.2

- Fixed WAHA startup on older Home Assistant x86-64 hosts that do not provide the CPU features required by an unused WPPConnect image-processing dependency.
- Kept the incompatible image-processing path disabled and fail-closed; Life Atlas continues to use only WAHA NOWEB with message sending denied.

## 0.14.1

- Fixed Life Atlas startup by isolating the Google Photos MCP port setting so it no longer overrides WAHA's private loopback port.
- Added a container startup smoke test to catch bundled-service failures before release.

## 0.14.0

- Added an integrated, read-only WhatsApp archive powered by pinned WAHA NOWEB.
- Added QR linking, chat inventory, automatic archived-chat exclusion, per-chat include/exclude controls, and explicit confirmation before durable message storage.
- Added historical reconciliation, ongoing message capture, immutable message revisions, source-neutral search/change access, and a durable SQLite archive under `/data/whatsapp`.
- Kept WAHA and connector services loopback-only, denied message sending, and excluded only rebuildable WAHA message staging from Home Assistant backups.

## 0.13.2

- Added this Home Assistant-visible changelog.
- Made repository validation reject a release when its version has no changelog entry.
- Added changelog maintenance to the documented release workflow.

## 0.13.1

- Fixed Google Photos MCP readiness detection before the first Google sign-in.
- The Photos screen now recognises the running MCP service while it is waiting for OAuth, allowing **Connect MCP** to start the authorization flow.

## 0.13.0

- Added the write-capable, bearer-key Life Atlas agent API for safe event creation, evidence attachment, search, and detail retrieval.
- Upgraded the database to schema version 2 with durable ingestion provenance, idempotency, audit history, and richer evidence metadata.
- Hardened multi-source ingestion against replay duplicates, invalid date ranges, malformed pagination, connector outages, and protocol mismatches.
- Added the persistent Google Photos MCP service alongside the existing Google Photos Picker workflow.
- Preserved Google Photos OAuth state under the Home Assistant app data directory and kept the service private behind Ingress.
- Improved database restore safety, backup retention, media validation, and startup ordering.
- Fixed responsive layouts, confidence rendering, map tiles, timeline filtering, and several frontend accessibility and security issues.

