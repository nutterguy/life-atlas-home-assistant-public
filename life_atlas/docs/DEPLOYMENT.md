# Home Assistant deployment and recovery

## Fast release path

Routine releases should use the compact path below. The goal is to avoid re-reading full repository history, successful CI logs, or Home Assistant logs when the deployment is healthy.

1. Inspect only `git status --short` and `git diff --name-only`, then review the focused changed-file diff.
2. Run `python scripts/deploy.py`. To bump the release version at the same time, run `python scripts/deploy.py --set-version <version>`.
3. A healthy preflight prints one line beginning `LIFE_ATLAS_DEPLOY=` with `version_sync:true`, `validation:"pass"`, and `release_ready:true`. Use `--verbose` only if that compact result reports a failure.
4. Merge/push the focused change to private `main`. The publish workflow emits `LIFE_ATLAS_PUBLISH={...}` and synchronizes the allowlisted application snapshot to the public distribution repository.
5. The public image workflow emits `LIFE_ATLAS_IMAGE={...}`. Check job status or that compact summary first; fetch verbose logs only for a failed job.
6. In Home Assistant, create a backup and install the offered Life Atlas add-on update.
7. Confirm the add-on is started and request `/api/health` through Home Assistant Ingress. The deployment is complete when health is `ok` and the reported version matches the release. Check recent add-on logs only if this verification fails.

The repository and image publication steps are automated. Updating the running Home Assistant installation intentionally remains an explicit final action.

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

Follow `docs/SQLITE_RESTORE.md`. It is the canonical operator runbook and maintainer contract for preparing a consistent snapshot or matching-media ZIP, backing up, uploading, validating, confirming, verifying, troubleshooting, and rolling back.

In summary: create a Home Assistant backup, upload through the authenticated **Restore database** screen, review all validation results and aggregate counts, and type `RESTORE` only when the preview matches the source. Never use a copied live main file, `-wal`, or `-shm` file.

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
2. Bump `config.yaml` and the `LIFE_ATLAS_VERSION` value in `run.sh` together. `scripts/deploy.py --set-version <version>` performs that edit and validates the result.
3. Merge to `main`. The publish workflow validates the private tree, copies only files in `.public-files`, validates that clean snapshot, and pushes it to the public distribution repository.
4. The public workflow validates the snapshot and publishes the versioned `ghcr.io/nutterguy/life-atlas-home-assistant` image.
5. In Home Assistant, check for app updates, create a partial backup, and apply the update.
6. Open the app and verify `/api/health` reports `status: ok` and the expected version. Check logs and aggregate counts without logging personal event content.

If validation or image building fails, no installable version is published. Keep runtime data outside Git and the image.
