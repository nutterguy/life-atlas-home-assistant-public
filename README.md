# Life Atlas Home Assistant repository

This is the sanitised distribution repository for the private Life Atlas Home
Assistant application. It contains no database, media, backups, credentials, or
Home Assistant connection details.

Add this repository in **Settings → Apps → App store → Repositories**:

`https://github.com/nutterguy/life-atlas-home-assistant-public`

Life Atlas is built as a versioned container image. Home Assistant updates the
application image while preserving its Supervisor-managed `/data` volume.

The contents of `life_atlas/` are generated from an explicit allowlist in the
private canonical repository. Changes should not be made directly here.
