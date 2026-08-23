# Architecture

Life Atlas for Home Assistant packages an ordinary Python/SQLite web application as a local Home Assistant app.

- `app.py`: local HTTP API, validation, backups, export, and SQLite access.
- `static/`: responsive browser interface with Ingress-relative requests.
- `schema.sql`: schema shared with the Windows edition.
- `Dockerfile`, `config.yaml`, `build.yaml`, `run.sh`: Home Assistant packaging.
- `/data`: Supervisor-managed persistent database, imports, local media, and backups; never part of the image.
- `tests/`: backend and frontend/Ingress regression contracts.

Home Assistant provides authentication and navigation through Ingress. The app deliberately requests no Home Assistant API, host network, device, or smart-home entity access.

The add-on image also contains the upstream `savethepolarbears/google-photos-mcp` service. It is built in a separate Node.js stage and runs as a second process inside the same Home Assistant add-on container, using port 3000 internally. Home Assistant Ingress continues to expose only the Life Atlas web service on port 8099; `host_network` remains disabled and the MCP port is not declared as an add-on port. `run.sh` supervises both processes so the add-on stops if either required service exits unexpectedly. OAuth configuration and durable MCP token storage are separate concerns and must use `/data` when enabled.

The private Windows repository is the portable data source of truth. Cross-edition transfer uses a consistent database snapshot plus the matching `media` directory, normally carried together in the in-app backup ZIP. Application code must not depend on a particular Home Assistant host, local IP, SSH key, Codex directory, or Ingress token.

Google Photos Picker uses a Web OAuth client in Home Assistant. The non-secret client ID is stored under `/data`; the access token remains only in browser memory and is sent to the add-on for the lifetime of one short picker session. The add-on stores only the selected compressed image and its source identifier. The bundled Google Photos MCP does not change this existing Picker workflow; broader MCP-backed ingestion will be introduced separately and must preserve provenance and review-before-confirmation rules.
