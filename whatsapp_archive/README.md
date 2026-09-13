# Life Atlas WhatsApp Archive

Version 0.5.6 improves direct-chat labels in the selection inventory by using
the latest archived incoming sender label when WAHA only supplies a phone
number, and by preferring human contact fields over numeric contact aliases.
The underlying WhatsApp identifier remains visible beneath the label.

This Home Assistant app is the read-only WhatsApp ingress for Life Atlas. It runs WAHA NOWEB and a small Life Atlas adapter in one container, uses WAHA's message store as disposable staging, and independently archives only approved chats and revisions in SQLite.

## What version 0.5 does

Version 0.7 adds a visible runtime version and a read-only, paginated message preview for included chats on the
archive management page. Excluded chats cannot be previewed and no preview action
copies messages into Life Atlas.

- creates one WAHA NOWEB session with its store and `fullSync` enabled
- asks WhatsApp for the historical messages that Linked Devices makes available
- captures new, edited, and revoked messages through authenticated webhooks
- reconciles recent messages every five minutes by default
- performs a full reconciliation every 24 hours by default
- inventories chats before copying message content into the durable archive
- excludes archived chats by default and supports per-chat automatic/include/exclude policy
- shows chat name, type, archived state, upstream message count, available date range, effective selection, and durable count
- keeps selected current messages, immutable revisions, raw WAHA JSON, webhook audits, sync audits, minimal chat inventory, identities, and a monotonic change feed in `/data/archive/whatsapp-archive.sqlite3`
- exposes Life Atlas Connector Protocol v1 on internal port `8097`
- provides an administrator-only Home Assistant Ingress screen for pairing, status, sync, and connector-key retrieval

### Added in 0.5

- Resolves an unnamed direct chat from the display name on its incoming messages, including already archived messages at query time, and exposes a bounded read-only conversation-context operation for Life Atlas.
- Pairs itself with Life Atlas. `POST /v1/pair` hands the connector key to the first caller and to nobody afterwards, so nothing has to be copied by hand. Both apps are installed by the same person on the same machine, and making them introduce themselves through a copied secret was ceremony rather than security. The claim and the peer that made it are recorded and shown on this page, so a claim you did not expect is visible rather than silent, and **Forget pairing** hands the key over again deliberately.
- Names chats that WAHA no longer lists. WhatsApp has migrated direct chats to `@lid` and `@s.whatsapp.net`, so older `@c.us` rows never pass through a sync again; they are now labelled from the phone number already in the identifier.

### Fixed in 0.4

- Direct chats show who they are with. `/chats` returns a null name for most direct chats, and WhatsApp now addresses a direct chat by an opaque LID rather than a phone number, so the identifier was not readable either. The inventory now resolves a name from the chat overview, then from the LID-to-phone-number mapping combined with the address book, and falls back to the phone number itself. All three lookups are optional: a build or engine that lacks one costs a nicer label, never a sync.

### Fixed in 0.3

- runs on older x86-64 Home Assistant hosts. An unused WPPConnect image-processing dependency required CPU features those hosts do not provide; it is now patched out, version-pinned, and fails closed.
- a reconciliation replay that carries no lifecycle no longer resurrects a message WhatsApp has revoked.
- reports `degraded` rather than the non-protocol `selection_required` when a session is paired but no chats have been confirmed, so Life Atlas can read the status.

It does **not** download media binaries in this first version. Media flags and metadata present in WAHA's message payload are retained. Reactions are retained when they occur inside a message payload, but they are not yet modelled as separate Life Atlas events.

## Chat selection and backups

WhatsApp Linked Devices and WAHA `fullSync` do not offer selective upstream history delivery. WAHA must temporarily receive the account-wide history that WhatsApp supplies. The app therefore uses two distinct storage tiers:

1. `/data/waha/noweb/<session>/store.sqlite3*` is disposable WAHA staging. Home Assistant backups explicitly exclude these files.
2. `/data/archive/whatsapp-archive.sqlite3` is the durable, backed-up Life Atlas archive. Message content enters it only after the inventory is reviewed and confirmed, and only for effectively included chats.

Before confirmation, live message webhook bodies are not retained in the durable database; only a redacted delivery audit is recorded. A reconciliation after confirmation obtains included messages from WAHA staging.

The automatic policy includes active chats and excludes archived chats. **Always include** and **Always exclude** override that default. Excluding a previously included chat purges its messages, revisions, full-text rows, content-bearing webhook events, and orphaned identities from the local durable archive. Minimal inventory metadata—chat ID, display name, archived state, counts, dates, and policy—remains so the selection can be inspected and changed.

If an automatic-policy chat is later archived in WhatsApp, its durable local messages are purged at the next periodic reconciliation (or sooner if WAHA supplies an archive event). If it is unarchived, the next reconciliation automatically performs an all-history backfill for the newly eligible chat. An explicit **Always include** override prevents the purge. Chat inventory is persisted through an explicit metadata allowlist, so an upstream last-message preview cannot cross this boundary.

## No-send boundary

The add-on deliberately has no message, reaction, typing, presence, mark-read, edit, forward, or delete-message method.

WAHA itself listens only on `127.0.0.1:3000`; that port is not published by Home Assistant. The Docker build patches WAHA's compiled listener to loopback and fails if the expected upstream code has changed. The external Life Atlas adapter exposes only its bounded Connector Protocol endpoints—there is no WAHA proxy.

At first pairing, a random administrator credential creates the session and a session-scoped API key with these WAHA permissions:

```json
{
  "read": true,
  "send": false,
  "control": true,
  "setting": false,
  "app": false,
  "delete": false
}
```

The administrator plaintext is then deleted. WAHA retains only its SHA-512 verifier, and the adapter uses only the scoped key thereafter. `control` is required to start or restart the linked-device session; it does not grant message sending.

## Install and pair

1. Add this private GitHub repository to the Home Assistant app store and install **Life Atlas WhatsApp Archive**.
2. Start the app and open its Web UI.
3. Select **Create / start**. When the status is `SCAN_QR_CODE`, scan the QR code in WhatsApp under **Linked devices**.
4. Wait for `WORKING`. The first inventory scan begins automatically; **Refresh chat inventory** can also trigger it manually.
5. Review every chat. Archived chats are excluded automatically. Use **Always include** or **Always exclude** for exceptions.
6. Check the proposed included/excluded chat and message totals, then select **Confirm selection & archive**. Until this confirmation, no message content is copied into the durable Life Atlas archive.
7. Wait for the post-confirmation full reconciliation and verify the per-chat durable counts, overall coverage dates, and sync result.
8. Reveal the Life Atlas connector key only when configuring Life Atlas. The Connector Protocol address is the app's internal Home Assistant hostname on port `8097`; it is intentionally not a host/LAN port.

For repository-installed apps, Home Assistant derives the internal hostname from the repository identifier and slug. Confirm the actual hostname shown by Supervisor rather than assuming the `local-...` development name.

## Historical-data limitation

"Full history" means all history WhatsApp supplies to this newly linked device, not necessarily every message ever held on the phone. WAHA documents NOWEB `fullSync=true` as approximately one year, capped at roughly 100,000 messages per chat. That upstream ceiling cannot be removed by this add-on.

Once a message reaches the local archive it is retained across subsequent WAHA reconciliation, including old revisions. An official chat export can later be added as a one-time backfill path for older gaps, without replacing this always-on connector.

## Connector Protocol

Every request to port `8097` requires either:

```text
X-Life-Atlas-Connector-Key: <key>
```

or a bearer token containing the same key. The adapter implements:

- `GET /v1/info`
- `GET /v1/status`
- `GET /v1/capabilities`
- `POST /v1/search`
- `POST /v1/changes`
- `POST /v1/item`
- `POST /v1/context`

Life Atlas should persist the opaque cursor returned by `/v1/changes`. Replaying the same cursor is safe; every message revision has a stable source ID, version, lifecycle, and content hash.

## Operational notes

- App backups are configured as `cold`; the durable archive and pairing/session state are backed up, while WAHA's disposable `store.sqlite3`, WAL, and SHM files are excluded.
- Never edit WAHA's own SQLite files while the app is running. The separate archive database is the supported Life Atlas boundary.
- If WhatsApp unlinks the device, reopen the Web UI and restart pairing. Existing archived messages remain local.
- A full sync is idempotent; unchanged messages do not create new revisions or change-feed entries.
- Version 0.2 archives selected data but does not automatically create Life Atlas events. Event extraction is the downstream, explicit processing stage.

## Validate

```sh
python3 -m py_compile whatsapp_archive/archive.py whatsapp_archive/adapter.py whatsapp_archive/patch_waha_bind.py whatsapp_archive/secrets_init.py whatsapp_archive/waha_client.py
python3 -m unittest discover -s whatsapp_archive/tests -v
```
