# Home Assistant deployment and recovery

## Install from a clean clone

1. Clone this private repository on any trusted computer.
2. Run `python scripts/validate_repository.py`.
3. Transfer the repository contents to `/addons/life_atlas` on Home Assistant OS using Terminal & SSH or Samba.
4. In Home Assistant, open Settings > Apps > App store and check for updates.
5. Install or update the local Life Atlas app, start it, and enable its sidebar entry.

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

Bump `config.yaml`, update documentation and tests, validate locally, commit, publish, refresh the local app store, update/rebuild, and verify the version actually running. Keep runtime data outside Git and the image.
