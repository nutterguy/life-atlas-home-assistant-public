# Architecture

Life Atlas for Home Assistant packages an ordinary Python/SQLite web application as a local Home Assistant app.

- `app.py`: local HTTP API, validation, backups, export, and SQLite access.
- `mcp_ingress_proxy.py`: the Home Assistant Ingress-facing proxy on port 8099. It forwards normal Life Atlas traffic to the internal Python backend and exposes only the small Google Photos MCP status/OAuth bridge needed by the UI.
- `static/`: responsive browser interface with Ingress-relative requests.
- `schema.sql`: schema shared with the Windows edition.
- `Dockerfile`, `config.yaml`, `build.yaml`, `run.sh`: Home Assistant packaging.
- `/data`: Supervisor-managed persistent database, imports, local media, backups, and Google Photos MCP token state; never part of the image.
- `tests/`: backend and frontend/Ingress regression contracts.

Home Assistant provides authentication and navigation through Ingress. The app deliberately requests no Home Assistant API, host network, device, or smart-home entity access.

The add-on image also contains the upstream `savethepolarbears/google-photos-mcp` service. It is built in a separate Node.js stage and runs inside the same Home Assistant add-on container on port 3000. The ordinary Life Atlas Python server listens only on `127.0.0.1:8100`; `mcp_ingress_proxy.py` owns the Ingress port 8099. Home Assistant therefore still publishes only port 8099, `host_network` remains disabled, and port 3000 is never declared as an add-on port. The proxy allows only MCP health/status and OAuth start/callback routes needed by Life Atlas, rather than exposing the complete MCP HTTP service. `run.sh` supervises all three processes and stops the add-on if any required process exits unexpectedly.

Google Photos MCP OAuth refresh credentials are stored in `/data/google-photos-mcp/tokens.db`. The upstream MCP requires its token path to remain inside its project directory, so `run.sh` provides a project-local `runtime-data` symlink to the persistent `/data/google-photos-mcp` directory. The directory is owner-only and the MCP process starts under `umask 077`. OAuth client configuration is supplied through Home Assistant app options and is never built into the image or committed to Git.

The private Windows repository is the portable data source of truth. Cross-edition transfer uses a consistent database snapshot plus the matching `media` directory, normally carried together in the in-app backup ZIP. Application code must not depend on a particular Home Assistant host, local IP, SSH key, Codex directory, or Ingress token.

Google Photos Picker remains a separate browser-session workflow. Its non-secret Web client ID is stored under `/data`; the Picker access token remains only in browser memory and is sent to the add-on for one short picker session. The add-on stores only the selected compressed image and its source identifier. The bundled Google Photos MCP does not replace that Picker flow; broader MCP-backed ingestion will be introduced separately and must preserve provenance and review-before-confirmation rules.
