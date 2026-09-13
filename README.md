# Life Atlas Home Assistant repository

This is the sanitised distribution repository for the private Life Atlas Home
Assistant application. It contains no database, media, backups, credentials, or
Home Assistant connection details.

Add this repository in **Settings → Apps → App store → Repositories**:

`https://github.com/nutterguy/life-atlas-home-assistant-public`

The repository contains two independently installed apps:

- **Life Atlas**, the curated personal timeline.
- **Life Atlas WhatsApp Archive**, the read-only WhatsApp source archive.

Both are built as versioned container images. Home Assistant updates each app
independently while preserving its own Supervisor-managed `/data` volume.

The app folders are generated from private canonical repositories and contain no
personal messages, databases, credentials, backups, or pairing state.
