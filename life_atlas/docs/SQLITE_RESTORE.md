# Importing or restoring a Life Atlas SQLite database

Use this process when a Life Atlas database has been edited or rebuilt on another computer and should replace the database used by the Home Assistant edition. The supported path is the authenticated **Restore database** screen inside Life Atlas; SSH is a recovery fallback, not the normal import method.

Restoring replaces the complete Life Atlas database. It is not a row-by-row merge.

## Before you start

Prepare one of these artifacts:

- **Standalone SQLite snapshot** — use this when the candidate database refers only to media that is already present in Home Assistant.
- **Life Atlas restore ZIP** — use this when the database and its matching local media must move together.

The SQLite file must be a consistent snapshot created with SQLite's backup API or `VACUUM INTO`. Do not copy a database file while its source application is writing to it, and never include or upload `life_atlas.sqlite3-wal` or `life_atlas.sqlite3-shm`.

Before leaving the source computer, verify:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

`integrity_check` must return `ok`; `foreign_key_check` must return no rows. Record the artifact's SHA-256 checksum and the expected aggregate counts for events, people, places, trips, and media. Keep the artifact private: it contains personal data.

## Restore ZIP layout

A restore ZIP has this exact shape:

```text
data/
  life_atlas.sqlite3
  media/
    ab/
      abcdef...64-lowercase-hex-characters.webp
```

Include exactly the media files referenced by the database—no more and no fewer. Media paths are relative to `data/`, and each packaged media filename is its lowercase SHA-256 content hash plus `.webp`, under the matching two-character prefix directory.

Do not include imports, exports, logs, restore staging, restore backups, source archives, credentials, WAL/SHM files, or unrelated media. A ZIP produced by Life Atlas's **Back up** button may contain additional backup material and is not automatically the same thing as this deliberately narrow restore package.

## Human-readable restore procedure

1. In Home Assistant, confirm Life Atlas is on the intended current version.
2. Create a Home Assistant backup that includes Life Atlas. Keep it until the restored data has been checked.
3. Open Life Atlas through its normal Home Assistant sidebar/Ingress page.
4. Select **Restore database**.
5. Choose the standalone `.sqlite`/`.sqlite3` file or the restore `.zip` package.
6. Select **Upload and validate**. Keep the page open until the upload and validation finish.
7. If validation fails, stop. The live database has not been replaced. Correct the source artifact and start a new upload session.
8. Compare the displayed event, person, place, trip, and media counts with the source. Confirm that integrity, foreign keys, and media all show as passed/matched.
9. Only when the preview is correct, type `RESTORE` and select **Replace database**.
10. Allow the page to reload. Check the Life Atlas version, aggregate counts, several representative records, search, and representative photos.
11. Retain both the Home Assistant backup and the automatic Life Atlas rollback copy until the result has been used successfully for a reasonable period.

Uploading and validation are non-destructive. The live database changes only after the explicit `RESTORE` confirmation. During the final switch, Life Atlas briefly enters maintenance mode, snapshots the current database, checkpoints its WAL, installs verified missing package media, atomically switches the database, and verifies the installed result. If verification fails, it restores the previous database automatically.

## Rollback

Open **Restore database**, find the appropriate automatic rollback copy, select **Roll back**, and type `ROLLBACK`. Life Atlas first makes a safety copy of the current database, so the rollback itself can be reversed.

If the app UI is unavailable, restore the Home Assistant backup. The stopped-app SSH procedure in `docs/DEPLOYMENT.md` is the last-resort recovery path.

## Common failures

- **Restore session token is invalid** — update Life Atlas to version 0.8.2 or newer, reopen the app, choose the file again, and start a fresh upload. Sessions cannot be resumed across a page reload, app restart, or expired session.
- **Session expired** — sessions last 24 hours. Start a new upload.
- **Missing media** — a standalone database refers to files not already in `/data/media`, or the ZIP does not contain the exact referenced media set. Build a matching restore ZIP.
- **Media hash/conflict error** — a media file does not match its content-addressed name, or Home Assistant already has different content at that path. Do not overwrite it manually; resolve the source package.
- **Newer schema** — update the Home Assistant edition before importing. Life Atlas refuses to downgrade a newer database.
- **Not enough space** — free Home Assistant storage. Staging needs room for the upload, extraction, prepared database, media, current-database backup, and verification copies.
- **Upload interrupted** — leave the live data alone and start a new upload. Do not attempt to assemble partial chunks manually.

The default upload limit is 512 MiB. ZIPs are limited to 5,000 entries, 1 GiB expanded size, and a 100:1 compression ratio. Browser uploads use 2 MiB chunks; the server accepts at most 4 MiB per chunk.

## Maintainer and agent contract

Agents working on imports or restores must first read `AGENTS.md`, this document, `docs/DATA_MODEL.md`, and `docs/DEPLOYMENT.md`.

Preserve these invariants:

- The feature remains behind Home Assistant Ingress authentication and uses only relative browser routes.
- The session token is returned only when the session is created, remains in browser memory, is sent in `X-Life-Atlas-Restore-Token` on every chunk/validate/commit request, and is never replaced by token-free chunk progress responses or written to logs.
- Validation happens on an isolated SQLite backup copy. The candidate must pass header, application/schema version, required-table/column, integrity, foreign-key, unsupported-schema-object, media-path, media-presence, and media-hash checks.
- ZIP handling remains fail-closed for traversal, absolute paths, duplicate names, symlinks, encryption, unexpected entries, zip bombs, oversized content, missing/extra media, hash mismatches, and conflicts with existing media.
- The commit path creates a rollback snapshot before replacement, blocks ordinary requests during the switch, checkpoints the live WAL, uses same-filesystem atomic replacement, verifies after installation, and automatically recovers on failure or an interrupted switch.
- Restore audit records contain timestamps, checksums, aggregate counts, and outcomes—not session tokens, source filenames, personal rows, or media contents.
- Personal databases, packages, backups, paths, addresses, and credentials are never committed or printed in public CI/issues.

When changing the workflow, test at least: a real multi-chunk upload, standalone SQLite success, packaged-media success, invalid token, wrong offset, interrupted upload, integrity/FK failure, schema incompatibility, each ZIP safety class, missing/hash-conflicting media, commit rollback, manual rollback, and startup recovery. Run `python scripts/validate_repository.py`, review the diff for private artifacts, and verify the published Home Assistant image before using it with live data.
