# Home Assistant deployment and recovery

## Install from the Home Assistant app repository

1. In Home Assistant, open Settings > Apps > App store > Repositories.
2. Add `https://github.com/nutterguy/life-atlas-home-assistant-public`.
3. Install Life Atlas, start it, and enable its sidebar entry.

The app image contains application code only. Home Assistant mounts the existing
Supervisor-managed `/data` directory into every new image, so an ordinary app
update preserves the database, media, imports, and backups.

No fixed host address, local username, SSH key, Ingress token, or workstation path is part of the repository.

## Deploy a database snapshot and media

1. Validate the source snapshot with `PRAGMA integrity_check` and `PRAGMA foreign_key_check`.
2. Create a partial Home Assistant backup containing `local_life_atlas`.
3. Stop Life Atlas.
4. Copy the existing `/data/life_atlas.sqlite3` and `/data/media` to `/data/backups`.
5. Replace the database with the consistent source snapshot and restore its matching `media` directory. Do not copy WAL/SHM files.
6. Start Life Atlas.
7. Verify `/api/health`, the live app version, logs, and aggregate entity counts.
8. Remove temporary transfer files and any temporary image-layer database copy.

## Recovery

Restore the partial Home Assistant backup, or stop the app and restore the retained SQLite backup. Start it and repeat the health and count checks.

## Release discipline

1. Make and validate changes only in the private canonical repository.
2. Bump `config.yaml` and the `LIFE_ATLAS_VERSION` value in `run.sh` together.
3. Merge to `main`. The publish workflow validates the private tree, copies only
   files in `.public-files`, validates that clean snapshot, and pushes it to the
   public distribution repository.
4. The public workflow validates the snapshot and publishes the versioned
   `ghcr.io/nutterguy/life-atlas-home-assistant` image.
5. In Home Assistant, check for app updates, create a partial backup, and apply the update.
6. Open the app and verify `/api/health` reports `status: ok` and the expected
   version. Check logs and aggregate counts without logging personal event content.

If validation or image building fails, no installable version is published. Keep
runtime data outside Git and the image.
