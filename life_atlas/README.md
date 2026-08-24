# Life Atlas for Home Assistant

Life Atlas is a private, single-user life timeline packaged as a Home Assistant app. Its source is maintained privately and released through a sanitised public distribution repository. It runs entirely on your Home Assistant OS machine, uses Home Assistant Ingress for access, and does not integrate with smart-home entities or require a separate login.

## Documentation and handoff

- `AGENTS.md`: instructions for ChatGPT, Codex, other agents, and maintainers
- `docs/ARCHITECTURE.md`: components and edition boundaries
- `docs/DESIGN.md`: product and interface principles
- `docs/DATA_MODEL.md`: shared schema concepts and invariants
- `docs/SQLITE_RESTORE.md`: human restore runbook, package format, troubleshooting, and agent safety contract
- `docs/CHATGPT_INGESTION.md`: safe data synchronization workflow
- `docs/DEPLOYMENT.md`: clean installation, updates, verification, and recovery
- `docs/GOOGLE_PHOTOS.md`: Google Photos Picker setup, privacy, and removal

Run `python scripts/validate_repository.py` after cloning and before deployment. The repository intentionally contains no computer-specific address, credential, or personal database.
The same validation runs automatically on GitHub for pushes and pull requests.

## Current features

- Timeline, search, category and attendance filters
- Overview, chapters, notable events, yearbooks and calendar views
- People, places, trips and relationship timelines
- Evidence, sources, external links and media metadata
- Local photo uploads for events, diary days, and people portraits
- Google Photos Picker for explicitly selecting an event, diary day, or person photo
- Statistics and a review queue for uncertain events
- Event creation
- Downloadable backup archive, guarded SQLite restore, and CSV export
- Responsive layouts for desktop, tablet and phone
- Database compatibility with the portable Windows Life Atlas application

## Install

Add `https://github.com/nutterguy/life-atlas-home-assistant-public` under
**Settings → Apps → App store → Repositories**, then install Life Atlas. Updates
arrive through the standard Apps update mechanism as prebuilt images.

## Data

The application stores all durable state in `/data`, the persistent data directory managed for the app by Home Assistant:

```text
/data/life_atlas.sqlite3
/data/imports/
/data/backups/
/data/media/
/data/restore-backups/
/data/restore-staging/
```

The SQLite schema is intentionally kept compatible with the Windows edition. **Restore database** accepts either one standalone SQLite snapshot or a guarded Life Atlas ZIP containing `data/life_atlas.sqlite3` and its exact `data/media` tree. A standalone database must match media already under `/data/media`; a package installs only verified content-addressed media files and refuses conflicts. Both paths create an automatic rollback database before replacing records. Follow `docs/SQLITE_RESTORE.md` for the complete preparation, validation, restore, verification, troubleshooting, and rollback procedure. The **Back up** button downloads a ZIP containing the database, imports, and local media; **Export CSV** downloads the event timeline as CSV. Google Photos Picker is available in both editions. Home Assistant keeps its short-lived Google access token only in browser memory and immediately saves the selected photo locally.

Do not commit personal databases, backups or imports to Git.

## Development

Run the web service outside Home Assistant with Python 3.11 or newer:

```powershell
$env:LIFE_ATLAS_DATA_DIR = "$PWD/data"
$env:LIFE_ATLAS_HOST = "127.0.0.1"
$env:LIFE_ATLAS_PORT = "8099"
$env:LIFE_ATLAS_SERVER_ONLY = "true"
python app.py
```

Then open `http://127.0.0.1:8099`.

Home Assistant-specific behaviour is limited to `config.yaml`, `build.yaml`, `Dockerfile`, and `run.sh`; the application itself remains an ordinary Python/SQLite web service.

## Privacy

- Access through Home Assistant is protected by Home Assistant Ingress.
- The app requests no Home Assistant API access and no host networking.
- No telemetry or cloud service is used.
- Google Photos is contacted only when you explicitly connect or choose a photo. The access token is not written to the add-on data directory.
- The optional Places map loads Leaflet and OpenStreetMap resources over the internet.
- SQLite data is not encrypted by the app; rely on secured Home Assistant access and encrypted backups where appropriate.
