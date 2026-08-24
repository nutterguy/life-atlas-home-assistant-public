# Life Atlas for Home Assistant

Life Atlas is a private, single-user life timeline packaged as a Home Assistant app. Its source is maintained privately and released through a sanitised public distribution repository. It runs entirely on your Home Assistant OS machine, uses Home Assistant Ingress for access, and does not integrate with smart-home entities or require a separate login.

## Documentation and handoff

- `AGENTS.md`: instructions for ChatGPT, Codex, other agents, and maintainers
- `docs/ARCHITECTURE.md`: components and edition boundaries
- `docs/DESIGN.md`: product and interface principles
- `docs/DATA_MODEL.md`: shared schema concepts and invariants
- `docs/CHATGPT_INGESTION.md`: safe data synchronization workflow
- `docs/DEPLOYMENT.md`: clean installation, updates, verification, and recovery

Run `python scripts/validate_repository.py` after cloning and before deployment. The repository intentionally contains no computer-specific address, credential, or personal database.
The same validation runs automatically on GitHub for pushes and pull requests.

## Current features

- Timeline, search, category and attendance filters
- Overview, chapters, notable events, yearbooks and calendar views
- People, places, trips and relationship timelines
- Evidence, sources, external links and media metadata
- Local photo uploads for events, diary days, and people portraits
- Statistics and a review queue for uncertain events
- Event creation
- Downloadable SQLite backup archive and CSV export
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
```

The SQLite schema is intentionally kept compatible with the Windows edition. The **Back up** button downloads a ZIP containing the database, imports, and local media; **Export CSV** downloads the event timeline as CSV. Google Takeout and Google Photos Picker setup live in the Windows edition, while imported photos remain visible and can be supplemented with local uploads here.

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
- The optional Places map loads Leaflet and OpenStreetMap resources over the internet.
- SQLite data is not encrypted by the app; rely on secured Home Assistant access and encrypted backups where appropriate.
