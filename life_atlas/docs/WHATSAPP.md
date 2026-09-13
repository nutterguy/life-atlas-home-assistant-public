# WhatsApp archive

The WhatsApp archive is a **separate Home Assistant app**, not part of Life Atlas. Life Atlas holds a registration for it and talks to it only over Life Atlas Connector Protocol v1. Install it, switch it on, and Life Atlas can read it; remove it and Life Atlas carries on with every other source untouched.

This separation is deliberate. Life Atlas does not ship a WhatsApp bridge, does not hold pairing state, and cannot be taken offline by one.

## Install the archive app

`nutterguy/life-atlas-connectors` is private, so Home Assistant cannot add it as an app repository — Supervisor has no credentials for it. Connectors are installed as **local apps** instead, the same way the reference connector was: copy the app's folder into Home Assistant's `/addons` share and Supervisor builds it on the box.

Install it as a local app, not through a repository URL. A repository-installed app is addressed by a repository hash prefix, while a local app is addressed as `local-` plus its slug — and `local-life-atlas-whatsapp-archive` is the address Life Atlas seeds and the reference connector already uses.

1. Copy the `whatsapp_archive` folder from the connectors repository into `/addons/life_atlas_whatsapp_archive` on the Home Assistant host. Use the Samba share (`\\homeassistant\addons`), the Terminal & SSH app, or `scp`.
2. Make sure `run.sh` keeps Unix (LF) line endings. A copy made on Windows can convert them to CRLF, and the container then fails to start with a bad-interpreter error.
3. In Home Assistant, open **Settings → Apps**, select the three-dot menu and **Check for updates**, then look under **Local apps**.
4. Install **Life Atlas WhatsApp Archive** and start it. The first build pulls the pinned WAHA image, so it takes several minutes and needs disk space.
5. Open its own Ingress page and follow the pairing steps below.

## Link and choose chats

1. Select **Create / start** and scan the QR code from WhatsApp under **Linked devices**.
2. Wait for the session status to become `WORKING` and refresh the chat inventory.
3. Review the chat list. Automatic policy includes active chats and excludes archived chats. **Always include** and **Always exclude** override that default.
4. Review the proposed totals and select **Confirm selection & archive**. No message content is written to the durable archive before this confirmation.
5. Wait for the full reconciliation and confirm the durable counts and coverage dates shown in the interface.

WAHA must temporarily receive the account-wide linked-device history that WhatsApp supplies. Its staging database is excluded from Home Assistant backups. Only effectively included chats are copied to the durable archive.

## Connect it to Life Atlas

1. On the archive app's page, select **Reveal key** and copy its connector key.
2. In Life Atlas, open **Sources**, find **WhatsApp archive**, and select **Configure**.
3. Paste the key. The address is the app's internal name on the Home Assistant app network — for a locally installed app, `http://local-life-atlas-whatsapp-archive:8097`. A repository-installed app uses the repository hash prefix instead, so take the name Supervisor actually shows.
4. Switch the connector on and select **Check now**. It should read *Available* with its capabilities listed.

Life Atlas stores that address and key in `/data/connectors.sqlite3`, never in the life record. The key is write-only: it is never shown again, and never returned by the API.

## Storage and backup

Everything WhatsApp lives in the archive app's own `/data`:

- `/data/archive/whatsapp-archive.sqlite3` is the durable selected-message archive.
- `/data/waha` contains linked-device state and WAHA staging.
- `/data/secrets` contains generated internal credentials.
- `waha/noweb/*/store.sqlite3*` is excluded from backups because it is rebuildable staging and can be large.

A Life Atlas backup does **not** contain the WhatsApp archive or its pairing state; a Home Assistant backup of the archive app does. The **Back up** download inside Life Atlas remains the portable canonical timeline package.

## Read-only boundary

WAHA and the archive adapter bind to loopback inside the archive app's own container, and it publishes no host port — only Connector Protocol v1 on the internal app network, and an administrator-only Ingress page for pairing and diagnostics. The steady-state WAHA key explicitly denies send, reaction, typing, presence, mark-read, edit, forward, and delete-message capabilities.

## Historical coverage

Historical coverage means the history WhatsApp supplies to a newly linked device. WAHA documents NOWEB full synchronization as approximately one year and roughly 100,000 messages per chat. It is not guaranteed to be a lifetime export. Once selected content reaches the durable archive, reconciliations preserve it and retain immutable edited/revoked revisions.

## Relationship to Life Atlas events

Messages are source evidence, not timeline events. **WhatsApp evidence** in the Life Atlas sidebar provides the reviewed promotion flow, and it stays in Life Atlas because it writes to the life record:

1. search the selected durable archive;
2. choose up to 20 messages that support one event;
3. draft the canonical event separately from the raw conversation;
4. review its dates, attendance state, confidence, importance and people;
5. explicitly create the event and immutable evidence excerpts.

Search results remain connector-owned until the final confirmation. Promotion re-fetches every selected message through the connector boundary, stores stable source IDs and content hashes, and copies bounded text excerpts into canonical evidence. An unavailable or previously promoted message fails closed. Events default to `uncertain` and enter the Detective queue; message timestamps are evidence dates and are not assumed to be event dates.

If the connector is switched off or the archive app is stopped, evidence search and promotion report that plainly and nothing else in Life Atlas is affected.
