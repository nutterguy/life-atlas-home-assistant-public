# Architecture

Life Atlas for Home Assistant packages an ordinary Python/SQLite web application as a local Home Assistant app.

- `app.py`: local HTTP API, validation, backups, export, and SQLite access.
- `restore_service.py`: bounded upload sessions, defensive SQLite validation, maintenance-mode switching, rollback, and restore audit records.
- `agent_api.py`: the bearer-key HTTP/JSON interface for machine clients on port 8096, documented in `docs/AGENT_API.md`. It runs as its own process and does not pass through the Ingress proxy.
- `importance.py`: shared rules for inferring event importance, used by curated ingestion and the race-promotion script.
- `connectors.py`, `connector_http.py`, `connector_runtime.py`, `mock_connector.py`: the source-neutral connector boundary and its HTTP transport, described in `docs/CONNECTORS.md`. The bundled WhatsApp archive is the first live connector.
- `whatsapp_archive/`: the read-only WAHA adapter, selective durable archive, management interface, and Connector Protocol v1 service described in `docs/WHATSAPP.md`.
- `mcp_ingress_proxy.py`: the Home Assistant Ingress-facing proxy on port 8099. It forwards normal Life Atlas traffic to the internal Python backend and exposes only the bounded Google Photos MCP and WhatsApp management routes needed by the UI.
- `static/`: responsive browser interface with Ingress-relative requests.
- `schema.sql`: schema shared with the Windows edition.
- `Dockerfile`, `config.yaml`, `run.sh`: Home Assistant packaging.
- `/data`: Supervisor-managed persistent database, imports, local media, backups, Google Photos MCP tokens, and WhatsApp archive/session state; never part of the image.
- `tests/`: backend and frontend/Ingress regression contracts.

Home Assistant provides authentication and navigation through Ingress. The app deliberately requests no Home Assistant API, host network, device, or smart-home entity access.

The add-on image contains the upstream `savethepolarbears/google-photos-mcp` service and pinned WAHA NOWEB. WAHA, its archive adapter, Google Photos MCP, and the Life Atlas backend all run inside the same Home Assistant app container. Google Photos MCP listens on `127.0.0.1:3000`, WAHA only on `127.0.0.1:3001`, the WhatsApp Connector Protocol only on `127.0.0.1:8097`, its management service only on `127.0.0.1:8110`, and the ordinary Life Atlas Python server only on `127.0.0.1:8100`; `mcp_ingress_proxy.py` owns the Ingress port 8099. `host_network` remains disabled and none of the connector ports is published. Home Assistant publishes only the Ingress port and port 8096 for the bearer-key agent API. The proxy exposes bounded setup routes, not either connector's raw upstream API. `run.sh` supervises all required processes and stops the app if one exits unexpectedly. It waits for the backend to bind before starting the agent API so those processes do not race to initialise the canonical database.

The WhatsApp durable archive is `/data/whatsapp/archive/whatsapp-archive.sqlite3`. WAHA's selected-message archive and Life Atlas communicate only through Connector Protocol v1; neither opens the other's SQLite database. The large rebuildable WAHA message store is excluded from cold backups, while pairing state, generated internal credentials, and the durable selected-message archive are retained.

Google Photos MCP OAuth refresh credentials are stored in `/data/google-photos-mcp/tokens.db`. The upstream MCP requires its token path to remain inside its project directory, so `run.sh` provides a project-local `runtime-data` symlink to the persistent `/data/google-photos-mcp` directory. The directory is owner-only and the MCP process starts under `umask 077`. OAuth client configuration is supplied through Home Assistant app options and is never built into the image or committed to Git.

The private Windows repository is the portable data source of truth. Cross-edition transfer normally uses the authenticated Ingress restore screen with either a standalone consistent SQLite snapshot or a Life Atlas ZIP containing `data/life_atlas.sqlite3` and the exact matching content-addressed `data/media` files. Standalone-database validation blocks missing live media. Package validation additionally rejects unexpected or duplicate paths, symlinks, encryption, excessive entry counts, expanded sizes or compression ratios, unmatched files, content-address hash failures, and live-media conflicts. Uploads are sent in bounded chunks through the Ingress proxy. During the final switch, ordinary API work is drained, verified missing media is installed atomically, the live WAL is checkpointed, and an SQLite-native rollback snapshot is retained before an atomic same-directory database replacement. The drain covers the Ingress-facing backend only: the agent API is a separate process holding its own SQLite connection, so it is not gated. Do not drive the agent API while a restore is committing. Application code must not depend on a particular Home Assistant host, local IP, SSH key, Codex directory, or Ingress token.

Home Assistant backups are declared `cold`, so Supervisor stops the app while copying `/data`. Restore staging files, rollback copies, the switch journal, and the checksum/count audit log all remain under `/data` and are included in that lifecycle.

Google Photos Picker remains a separate browser-session workflow. Its non-secret Web client ID is stored under `/data`; the Picker access token remains only in browser memory and is sent to the add-on for one short picker session. The add-on stores only the selected compressed image and its source identifier. The bundled Google Photos MCP does not replace that Picker flow; broader MCP-backed ingestion will be introduced separately and must preserve provenance and review-before-confirmation rules.
