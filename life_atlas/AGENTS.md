# Life Atlas Home Assistant agent guide

This private repository is the canonical source for the Home Assistant edition. Personal data is not committed here; it lives in the add-on's persistent `/data` directory. The private Windows repository `nutterguy/life-atlas-windows` holds the canonical portable database snapshot.

## Start here

1. Read `README.md` and only the documentation relevant to the requested change. Do not reread the whole documentation set on every task.
2. Read `docs/SQLITE_RESTORE.md` before importing or replacing a database. Read `docs/CHATGPT_INGESTION.md` as well when curating or synchronizing records.
3. Read `docs/DEPLOYMENT.md` before changing a Home Assistant installation.
4. Run `python scripts/validate_repository.py` after code changes, or `python scripts/deploy.py` when preparing a release.

## Invariants

- Preserve Home Assistant Ingress: browser API and asset requests must remain relative, never rooted at `/api` or `/static`.
- Preserve Home Assistant authentication; do not add a second login or request Home Assistant API permissions without an explicit requirement.
- Durable state belongs only under `/data`.
- Never commit personal databases, backups, imports, credentials, hostnames, IP addresses, SSH keys, Ingress tokens, or machine-specific paths.
- Back up the add-on before database replacement or schema migration.
- Transfer only a consistent SQLite snapshot; never copy live `-wal` or `-shm` files.
- Preserve the restore session token across every upload chunk, validation, and commit request; never log or persist it.
- Keep `schema.sql` compatible with `nutterguy/life-atlas-windows`.
- Run integrity, foreign-key, backend, frontend-contract, and live health checks after deployment.

## Low-token working rules

Use the smallest inspection that can answer the question.

1. Start with `git status --short` and `git diff --name-only`. Do not scan repository history unless the task explicitly depends on history.
2. Read only files named by the diff, direct dependencies of those files, and the relevant section of a runbook. Do not recursively reread the repository for routine changes.
3. Use targeted tests for the touched subsystem while iterating. Run `scripts/validate_repository.py` once before release.
4. Inspect CI job status first. Fetch full logs only for failed jobs, and only the failed job where possible.
5. Prefer compact, machine-readable status from `scripts/deploy.py` and CI summaries over narrating raw command output.
6. Once an implementation plan is agreed, execute it without re-planning every step unless validation exposes a new constraint.
7. Never inspect or print personal database contents merely to verify an application deployment. Verify health, version, integrity and aggregate counts instead.

## Fast deployment path

For a normal application release, use this sequence and do not rediscover it from scratch:

1. Inspect `git status --short` and `git diff --name-only`.
2. If releasing a new version, run `python scripts/deploy.py --set-version <version>`. Otherwise run `python scripts/deploy.py`.
3. Review only the changed-file diff and the single `LIFE_ATLAS_DEPLOY={...}` result. Use `--verbose` only after failure.
4. Commit and merge/push the focused change to private `main`. The private publish workflow validates and copies the allowlisted snapshot to the public repository.
5. Check the private publish job and public image-build job statuses. Do not fetch successful logs.
6. When Home Assistant offers the new Life Atlas version, create a backup and install the Life Atlas add-on update through Supervisor/Home Assistant tooling.
7. Verify the installed add-on is started, then request `/api/health` through Ingress and confirm `status: ok` and the expected version. Inspect recent add-on logs only if verification fails.

The private push, public snapshot and image build are automated. The Home Assistant update remains an explicit final deployment action so a repository push cannot unexpectedly replace the running installation.

## Normal development workflow

Inspect status, make a focused change, run targeted tests, run final validation, review the changed-file diff for secrets and absolute paths, commit, publish, deploy with the fast path above, and verify the live version and aggregate counts. Do not print personal event content to logs or public issues.

These instructions are tool-neutral and sufficient for ChatGPT, Codex, another agent, or a human working from a clean clone.
