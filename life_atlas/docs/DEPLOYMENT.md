# Home Assistant deployment and recovery

## Install from the Home Assistant app repository

1. In Home Assistant, open Settings > Apps > App store > Repositories.
2. Add `https://github.com/nutterguy/life-atlas-home-assistant-public`.
3. Install Life Atlas, start it, and enable its sidebar entry.

The app image contains application code only. Home Assistant mounts the existing
Supervisor-managed `/data` directory into every new image, so an ordinary app
update preserves the database, media, imports, and backups.

No fixed host address, local username, SSH key, Ingress token, or workstation path is part of the repository.

## Google Photos MCP dependency updates

The bundled Google Photos MCP is tracked in `dependencies/google-photos-mcp.json` and must be pinned to a full upstream commit SHA. The Docker build reads that file directly, so there is only one version pin to maintain.

To check out the latest upstream `main` commit into the dependency file:

```bash
python scripts/update_google_photos_mcp.py
```

To pin a specific upstream branch, tag, or commit instead:

```bash
python scripts/update_google_photos_mcp.py <ref>
```

After any dependency bump, review the upstream diff, run `python scripts/validate_repository.py`, and allow the GitHub validation workflow to complete. CI builds the complete Home Assistant add-on image, including the pinned MCP, so dependency or native-module build failures are caught before merge. Do not point the Docker build directly at a moving branch such as `main`.

## Restore a database through Home Assistant Ingress

1. In the source edition, finish local processing and create either one standalone SQLite snapshot with the SQLite backup API or `VACUUM INTO`, or a Life Atlas ZIP containing `data/life_atlas.sqlite3` plus its exact `data/media` tree. Do not use a copied live main file, `-wal`, or `-shm` file.
2. Make a Home Assistant backup containing Life Atlas.
3. Open Life Atlas through Home Assistant, choose **Restore database**, and select the snapshot.
4. Wait for integrity, foreign-key, schema, version, and matching-media validation. Compare the displayed entity counts with the source.
5. Type `RESTORE` and confirm. Normal API requests briefly receive a maintenance response while the app drains work, checkpoints the live WAL, retains a rollback snapshot, atomically replaces the database, and verifies the installed copy.
6. Confirm the expected version and aggregate counts. Restore audit records contain timestamps, checksums, counts, and outcomes—not personal rows or the local source filename.

A standalone-database upload deliberately preserves `/data/media` and blocks replacement if the candidate refers to missing or unsafe local media paths. A Life Atlas ZIP may introduce matching media without SSH: the app applies ZIP safety limits, verifies the exact media set and hashes, refuses conflicting existing paths, and atomically adds only missing content-addressed files before switching databases. A failed database switch can therefore leave only harmless unreferenced new media; it never removes or overwrites an existing media file.

Upload sessions expire after 24 hours. The default upload limit is 512 MiB and can be changed with `LIFE_ATLAS_MAX_RESTORE_BYTES`; ZIP expansion is capped at 1 GiB and a 100:1 compression ratio. The browser uses 2 MiB chunks and the server accepts no chunk over 4 MiB. Staging requires enough free space for the upload, extraction, prepared copy, media installation, current database backup, and verification.

## Stopped-app SSH fallback

Use this only when Ingress is unavailable or matching media must also move:

1. Create a Home Assistant backup containing Life Atlas, then stop the app.
2. Retain a consistent copy of `/data/life_atlas.sqlite3` and the matching `/data/media` tree.
3. Copy the standalone source snapshot into the same filesystem as the destination, validate it there, and rename it into place. Never transfer source WAL/SHM files.
4. If media changes, transfer the matching media tree as one reviewed set while the app remains stopped.
5. Start Life Atlas and verify health, version, integrity, foreign keys, media availability, and aggregate counts.

## Recovery

Open **Restore database** and select an automatic rollback copy, or restore the Home Assistant backup. The rollback action first snapshots the current database, so reversing a rollback remains possible. If the UI is unavailable, stop the app and restore a retained SQLite backup, then repeat the health, integrity and count checks.

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
