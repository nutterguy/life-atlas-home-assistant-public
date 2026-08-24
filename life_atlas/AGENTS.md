# Life Atlas Home Assistant agent guide

This private repository is the canonical source for the Home Assistant edition. Personal data is not committed here; it lives in the add-on's persistent `/data` directory. The private Windows repository `nutterguy/life-atlas-windows` holds the canonical portable database snapshot.

## Start here

1. Read `README.md`, `docs/ARCHITECTURE.md`, `docs/DESIGN.md`, and `docs/DATA_MODEL.md`.
2. Read `docs/CHATGPT_INGESTION.md` before importing or replacing data.
3. Read `docs/DEPLOYMENT.md` before changing a Home Assistant installation.
4. Run `python scripts/validate_repository.py` before and after changes.

## Invariants

- Preserve Home Assistant Ingress: browser API and asset requests must remain relative, never rooted at `/api` or `/static`.
- Preserve Home Assistant authentication; do not add a second login or request Home Assistant API permissions without an explicit requirement.
- Durable state belongs only under `/data`.
- Never commit personal databases, backups, imports, credentials, hostnames, IP addresses, SSH keys, Ingress tokens, or machine-specific paths.
- Back up the add-on before database replacement or schema migration.
- Transfer only a consistent SQLite snapshot; never copy live `-wal` or `-shm` files.
- Keep `schema.sql` compatible with `nutterguy/life-atlas-windows`.
- Run integrity, foreign-key, backend, frontend-contract, and live health checks after deployment.

## Normal workflow

Inspect status, make a feature branch, implement a focused change, run validation, review the diff for secrets and absolute paths, commit, publish, deploy with the documented procedure, and verify the live version and aggregate counts. Do not print personal event content to logs or public issues.

These instructions are tool-neutral and sufficient for ChatGPT, Codex, another agent, or a human working from a clean clone.
