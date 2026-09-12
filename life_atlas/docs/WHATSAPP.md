# WhatsApp archive

Life Atlas includes a read-only WhatsApp archive powered by the pinned WAHA NOWEB bridge. It is part of the Life Atlas Home Assistant app; there is no separate connector repository to install.

## Link and choose chats

1. Update and start Life Atlas, then open it through Home Assistant.
2. Select **WhatsApp archive** in the Life Atlas sidebar.
3. Select **Create / start** and scan the QR code from WhatsApp under **Linked devices**.
4. Wait for the session status to become `WORKING` and refresh the chat inventory.
5. Review the chat list. Automatic policy includes active chats and excludes archived chats. **Always include** and **Always exclude** override that default.
6. Review the proposed totals and select **Confirm selection & archive**. No message content is written to the durable archive before this confirmation.
7. Wait for the full reconciliation and confirm the durable counts and coverage dates shown in the interface.

WAHA must temporarily receive the account-wide linked-device history that WhatsApp supplies. Its staging database is excluded from Home Assistant backups. Only effectively included chats are copied to the durable archive.

## Storage and backup

- `/data/whatsapp/archive/whatsapp-archive.sqlite3` is the durable selected-message archive.
- `/data/whatsapp/waha` contains linked-device state and WAHA staging.
- `/data/whatsapp/secrets` contains generated internal credentials.
- Home Assistant cold backups retain the durable archive, pairing state, and internal credentials.
- `whatsapp/waha/noweb/*/store.sqlite3*` is excluded because it is rebuildable staging and can be large.

The **Back up** download inside Life Atlas remains the portable canonical timeline package; use a Home Assistant backup to protect the WhatsApp archive and pairing state.

## Read-only boundary

WAHA, the archive adapter, and the connector protocol all bind to loopback inside the Life Atlas container. Home Assistant publishes none of their ports. The steady-state WAHA key explicitly denies send, reaction, typing, presence, mark-read, edit, forward, and delete-message capabilities. Only the authenticated Home Assistant Ingress management interface is exposed.

## Historical coverage

Historical coverage means the history WhatsApp supplies to a newly linked device. WAHA documents NOWEB full synchronization as approximately one year and roughly 100,000 messages per chat. It is not guaranteed to be a lifetime export. Once selected content reaches the durable archive, reconciliations preserve it and retain immutable edited/revoked revisions.

## Relationship to Life Atlas events

Messages are source evidence, not timeline events. **WhatsApp evidence** in the Life Atlas sidebar provides the reviewed promotion flow:

1. search the selected durable archive;
2. choose up to 20 messages that support one event;
3. draft the canonical event separately from the raw conversation;
4. review its dates, attendance state, confidence, importance and people;
5. explicitly create the event and immutable evidence excerpts.

Search results remain connector-owned until the final confirmation. Promotion re-fetches every selected message through the private connector boundary, stores stable source IDs and content hashes, and copies bounded text excerpts into canonical evidence. An unavailable or previously promoted message fails closed. Events default to `uncertain` and enter the Detective queue; message timestamps are evidence dates and are not assumed to be event dates.
