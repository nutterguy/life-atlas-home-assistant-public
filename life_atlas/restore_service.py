from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


SQLITE_HEADER = b"SQLite format 3\x00"
APPLICATION_ID = 0x4C41544C  # "LATL"
SCHEMA_VERSION = 1
MAX_CHUNK_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_UPLOAD_BYTES = 512 * 1024 * 1024
SESSION_TTL_SECONDS = 24 * 60 * 60
SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
BACKUP_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")

REQUIRED_COLUMNS = {
    "events": {"id", "title", "start_date", "end_date", "description", "category", "status", "confidence", "importance", "place_id", "trip_id", "review_state"},
    "sources": {"id", "name", "source_type"},
    "evidence": {"id", "event_id", "source_id", "evidence_type"},
    "people": {"id", "name"},
    "event_people": {"event_id", "person_id", "role"},
    "places": {"id", "name"},
    "trips": {"id", "title"},
    "tags": {"id", "name"},
    "event_tags": {"event_id", "tag_id"},
    "review_items": {"id", "event_id", "status"},
    "imports": {"id", "filename", "checksum"},
    "entity_links": {"id", "entity_type", "entity_id", "url"},
    "chapters": {"id", "title", "start_date"},
    "media": {"id", "event_id", "local_path", "external_url"},
    "weather_cache": {"event_id", "weather_json"},
}


class RestoreError(ValueError):
    pass


class MaintenanceBusy(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_connection(path: Path, *, writable: bool = False) -> sqlite3.Connection:
    if writable:
        con = sqlite3.connect(path)
    else:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.execute("PRAGMA trusted_schema=OFF")
    con.execute("PRAGMA cell_size_check=ON")
    con.execute("PRAGMA mmap_size=0")
    con.execute("PRAGMA foreign_keys=ON")
    return con


class RequestGate:
    """Stops new API work and drains in-flight requests during a database switch."""

    def __init__(self):
        self._condition = threading.Condition()
        self._active = 0
        self._maintenance = False

    @contextmanager
    def request(self):
        with self._condition:
            if self._maintenance:
                raise MaintenanceBusy("Life Atlas is briefly offline while its database is restored")
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    @contextmanager
    def maintenance(self, timeout: float = 30.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            if self._maintenance:
                raise MaintenanceBusy("A database restore is already running")
            self._maintenance = True
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._maintenance = False
                    self._condition.notify_all()
                    raise MaintenanceBusy("Timed out waiting for active requests to finish")
                self._condition.wait(remaining)
        try:
            yield
        finally:
            with self._condition:
                self._maintenance = False
                self._condition.notify_all()


class RestoreManager:
    def __init__(self, data_dir: Path, database: Path, gate: RequestGate, prepare_database):
        self.data_dir = Path(data_dir).resolve()
        self.database = Path(database).resolve()
        self.gate = gate
        self.prepare_database = prepare_database
        self.root = self.data_dir / "restore-staging"
        self.backups = self.data_dir / "restore-backups"
        self.audit = self.data_dir / "restore-audit.jsonl"
        self.max_upload_bytes = int(os.environ.get("LIFE_ATLAS_MAX_RESTORE_BYTES", DEFAULT_MAX_UPLOAD_BYTES))
        self._lock = threading.RLock()

    def initialise(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        os.chmod(self.backups, 0o700)
        self._recover_interrupted_switch()
        self._expire_sessions()

    def capabilities(self) -> dict:
        return {
            "max_upload_bytes": self.max_upload_bytes,
            "max_chunk_bytes": MAX_CHUNK_BYTES,
            "confirmation": "RESTORE",
            "schema_version": SCHEMA_VERSION,
            "backups": self.list_backups(),
        }

    def create_session(self, total_size: int) -> dict:
        self._expire_sessions()
        if total_size <= 0 or total_size > self.max_upload_bytes:
            raise RestoreError(f"Database must be between 1 byte and {self.max_upload_bytes} bytes")
        free = shutil.disk_usage(self.data_dir).free
        current_size = self.database.stat().st_size if self.database.exists() else 0
        required = total_size * 3 + current_size + 16 * 1024 * 1024
        if free < required:
            raise RestoreError("There is not enough free space to stage, back up and verify this database")
        session_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        path = self.root / session_id
        path.mkdir(mode=0o700)
        state = {
            "id": session_id,
            "token": token,
            "created_at": _utc_now(),
            "created_epoch": time.time(),
            "total_size": total_size,
            "received": 0,
            "status": "uploading",
        }
        _atomic_json(path / "session.json", state)
        (path / "upload.sqlite3.part").touch(mode=0o600)
        self._audit("session_created", session_id=session_id, size=total_size)
        return self._public_state(state, include_token=True)

    def append_chunk(self, session_id: str, token: str, offset: int, chunk: bytes) -> dict:
        if not chunk or len(chunk) > MAX_CHUNK_BYTES:
            raise RestoreError(f"Each upload chunk must be between 1 byte and {MAX_CHUNK_BYTES} bytes")
        with self._lock:
            state, path = self._load_session(session_id, token)
            if state["status"] != "uploading":
                raise RestoreError("This upload is no longer accepting chunks")
            if offset != state["received"]:
                raise RestoreError(f"Expected upload offset {state['received']}")
            if offset + len(chunk) > state["total_size"]:
                raise RestoreError("Chunk exceeds the declared database size")
            with (path / "upload.sqlite3.part").open("ab") as stream:
                stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            state["received"] += len(chunk)
            if state["received"] == state["total_size"]:
                state["status"] = "uploaded"
            _atomic_json(path / "session.json", state)
            return self._public_state(state)

    def validate_session(self, session_id: str, token: str) -> dict:
        with self._lock:
            state, path = self._load_session(session_id, token)
            if state["status"] not in {"uploaded", "validated"}:
                raise RestoreError("Finish uploading the complete database before validation")
            upload = path / "upload.sqlite3.part"
            if upload.stat().st_size != state["total_size"]:
                raise RestoreError("Uploaded size does not match the declared database size")
            candidate = path / "candidate.sqlite3"
            candidate.unlink(missing_ok=True)
            initial = self._inspect_database(upload, check_media=False)
            if initial["user_version"] > SCHEMA_VERSION:
                raise RestoreError("This database was created by a newer Life Atlas schema")
            source = _safe_connection(upload)
            target = sqlite3.connect(candidate)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            self.prepare_database(candidate)
            report = self._inspect_database(candidate, check_media=True)
            state.update({
                "status": "validated",
                "sha256": _sha256(upload),
                "report": report,
                "validated_at": _utc_now(),
            })
            _atomic_json(path / "session.json", state)
            self._audit("validated", session_id=session_id, checksum=state["sha256"], counts=report["counts"])
            return self._public_state(state)

    def commit_session(self, session_id: str, token: str, confirmation: str) -> dict:
        if confirmation != "RESTORE":
            raise RestoreError("Type RESTORE to confirm database replacement")
        with self._lock:
            state, path = self._load_session(session_id, token)
            if state["status"] != "validated":
                raise RestoreError("Validate this database before restoring it")
            if _sha256(path / "upload.sqlite3.part") != state.get("sha256"):
                raise RestoreError("The uploaded database changed after validation")
            result = self._switch_database(path / "candidate.sqlite3", source="upload", source_checksum=state["sha256"])
            state.update({"status": "committed", "committed_at": _utc_now(), "backup_id": result["backup_id"]})
            _atomic_json(path / "session.json", state)
            return {**self._public_state(state), **result}

    def rollback(self, backup_id: str, confirmation: str) -> dict:
        if confirmation != "ROLLBACK":
            raise RestoreError("Type ROLLBACK to confirm database rollback")
        backup = self._backup_path(backup_id)
        if not backup.exists():
            raise RestoreError("Restore backup not found")
        self._inspect_database(backup, check_media=True)
        return self._switch_database(backup, source="rollback", source_checksum=_sha256(backup))

    def status(self, session_id: str, token: str) -> dict:
        state, _ = self._load_session(session_id, token)
        return self._public_state(state)

    def list_backups(self) -> list[dict]:
        result = []
        if not self.backups.exists():
            return result
        for path in sorted(self.backups.glob("*.sqlite3"), reverse=True)[:10]:
            backup_id = path.stem
            if BACKUP_ID.fullmatch(backup_id):
                result.append({"id": backup_id, "size": path.stat().st_size})
        return result

    def _switch_database(self, candidate: Path, *, source: str, source_checksum: str) -> dict:
        self._inspect_database(candidate, check_media=True)
        current_size = self.database.stat().st_size if self.database.exists() else 0
        if shutil.disk_usage(self.data_dir).free < candidate.stat().st_size + current_size + 16 * 1024 * 1024:
            raise RestoreError("There is not enough free space to create the rollback and incoming databases")
        backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
        backup_path = self._backup_path(backup_id)
        incoming = self.database.with_suffix(".sqlite3.incoming")
        recovery = self.database.with_suffix(".sqlite3.recovery")
        journal = self.data_dir / "restore-switch.json"
        with self.gate.maintenance():
            self._sqlite_snapshot(self.database, backup_path)
            self._sqlite_snapshot(candidate, incoming)
            self._inspect_database(incoming, check_media=True)
            _atomic_json(journal, {"state": "prepared", "backup_id": backup_id, "started_at": _utc_now()})
            self._checkpoint_live_database()
            os.replace(incoming, self.database)
            _atomic_json(journal, {"state": "switched", "backup_id": backup_id, "started_at": _utc_now()})
            try:
                self._inspect_database(self.database, check_media=True)
            except Exception:
                self._sqlite_snapshot(backup_path, recovery)
                os.replace(recovery, self.database)
                self._inspect_database(self.database, check_media=True)
                _atomic_json(journal, {"state": "rolled_back", "backup_id": backup_id, "finished_at": _utc_now()})
                self._audit("automatic_rollback", backup_id=backup_id, source=source)
                raise RestoreError("The restored database failed verification; the previous database was put back")
            _atomic_json(journal, {"state": "verified", "backup_id": backup_id, "finished_at": _utc_now()})
        report = self._inspect_database(self.database, check_media=True)
        self._audit("committed", backup_id=backup_id, source=source, checksum=source_checksum, counts=report["counts"])
        return {"ok": True, "backup_id": backup_id, "report": report}

    def _inspect_database(self, path: Path, *, check_media: bool) -> dict:
        if not path.is_file() or path.stat().st_size < len(SQLITE_HEADER):
            raise RestoreError("The upload is not a complete SQLite database")
        with path.open("rb") as stream:
            if stream.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                raise RestoreError("The upload does not have a valid SQLite header")
        con = _safe_connection(path)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RestoreError(f"SQLite integrity check failed: {integrity}")
            foreign_keys = [tuple(row) for row in con.execute("PRAGMA foreign_key_check").fetchall()]
            if foreign_keys:
                raise RestoreError(f"Foreign-key check found {len(foreign_keys)} violation(s)")
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            unsafe_objects = [row[0] for row in con.execute(
                "SELECT name FROM sqlite_schema WHERE type IN ('trigger','view') OR upper(COALESCE(sql,'')) LIKE 'CREATE VIRTUAL TABLE%'"
            )]
            if unsafe_objects:
                raise RestoreError("Database contains unsupported active schema objects: " + ", ".join(unsafe_objects[:10]))
            missing_tables = sorted(set(REQUIRED_COLUMNS) - tables)
            if missing_tables:
                raise RestoreError("Missing Life Atlas tables: " + ", ".join(missing_tables))
            for table, required in REQUIRED_COLUMNS.items():
                columns = {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}
                missing = sorted(required - columns)
                if missing:
                    raise RestoreError(f"Table {table} is missing columns: {', '.join(missing)}")
            application_id = int(con.execute("PRAGMA application_id").fetchone()[0])
            if application_id not in {0, APPLICATION_ID}:
                raise RestoreError("SQLite application ID does not identify a Life Atlas database")
            user_version = int(con.execute("PRAGMA user_version").fetchone()[0])
            if user_version > SCHEMA_VERSION:
                raise RestoreError("This database uses a newer Life Atlas schema")
            counts = {table: int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in ("events", "people", "places", "trips", "media")}
            if check_media:
                self._check_media(con)
            return {
                "integrity": "ok",
                "foreign_keys": "ok",
                "application_id": application_id,
                "user_version": user_version,
                "counts": counts,
                "media": "matched" if check_media else "not_checked",
            }
        except sqlite3.Error as exc:
            raise RestoreError(f"SQLite validation failed: {exc}") from exc
        finally:
            con.close()

    def _check_media(self, con: sqlite3.Connection) -> None:
        missing = []
        for media_id, local_path in con.execute("SELECT id, local_path FROM media WHERE local_path IS NOT NULL AND local_path != ''"):
            relative = Path(local_path)
            target = (self.data_dir / relative).resolve()
            try:
                target.relative_to(self.data_dir)
            except ValueError:
                raise RestoreError(f"Media row {media_id} points outside the Life Atlas data directory") from None
            if relative.is_absolute() or not target.is_file():
                missing.append(str(media_id))
        if missing:
            shown = ", ".join(missing[:10])
            suffix = "…" if len(missing) > 10 else ""
            raise RestoreError(f"Database references {len(missing)} missing media file(s): {shown}{suffix}")

    def _sqlite_snapshot(self, source: Path, destination: Path) -> None:
        destination.unlink(missing_ok=True)
        src = _safe_connection(source)
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.commit()
        finally:
            dst.close()
            src.close()
        os.chmod(destination, 0o600)

    def _checkpoint_live_database(self) -> None:
        con = sqlite3.connect(self.database)
        try:
            checkpoint = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and checkpoint[0]:
                raise MaintenanceBusy("The live SQLite WAL is busy; no database files were replaced")
        finally:
            con.close()
        Path(str(self.database) + "-wal").unlink(missing_ok=True)
        Path(str(self.database) + "-shm").unlink(missing_ok=True)

    def _load_session(self, session_id: str, token: str) -> tuple[dict, Path]:
        if not SESSION_ID.fullmatch(session_id):
            raise RestoreError("Restore session not found")
        path = self.root / session_id
        try:
            state = json.loads((path / "session.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise RestoreError("Restore session not found") from None
        if not secrets.compare_digest(str(state.get("token", "")), str(token or "")):
            raise RestoreError("Restore session token is invalid")
        if time.time() - float(state.get("created_epoch", 0)) > SESSION_TTL_SECONDS:
            raise RestoreError("Restore session has expired")
        return state, path

    @staticmethod
    def _public_state(state: dict, *, include_token: bool = False) -> dict:
        allowed = {"id", "created_at", "total_size", "received", "status", "sha256", "report", "validated_at", "committed_at", "backup_id"}
        result = {key: value for key, value in state.items() if key in allowed}
        if include_token:
            result["token"] = state["token"]
        return result

    def _backup_path(self, backup_id: str) -> Path:
        if not BACKUP_ID.fullmatch(backup_id):
            raise RestoreError("Restore backup not found")
        return self.backups / f"{backup_id}.sqlite3"

    def _expire_sessions(self) -> None:
        for path in self.root.iterdir():
            if not path.is_dir() or not SESSION_ID.fullmatch(path.name):
                continue
            try:
                state = json.loads((path / "session.json").read_text(encoding="utf-8"))
                expired = time.time() - float(state.get("created_epoch", 0)) > SESSION_TTL_SECONDS
            except (OSError, ValueError):
                expired = True
            if expired:
                shutil.rmtree(path, ignore_errors=True)

    def _recover_interrupted_switch(self) -> None:
        journal = self.data_dir / "restore-switch.json"
        if not journal.exists():
            return
        try:
            state = json.loads(journal.read_text(encoding="utf-8"))
            backup = self._backup_path(str(state.get("backup_id", "")))
            if state.get("state") == "switched" and backup.exists():
                try:
                    self._inspect_database(self.database, check_media=True)
                except Exception:
                    recovery = self.database.with_suffix(".sqlite3.recovery")
                    self._sqlite_snapshot(backup, recovery)
                    os.replace(recovery, self.database)
                    self._audit("startup_rollback", backup_id=backup.stem)
        except Exception as exc:
            self._audit("startup_recovery_failed", error=type(exc).__name__)

    def _audit(self, action: str, **details) -> None:
        record = {"at": _utc_now(), "action": action, **details}
        with self._lock:
            with self.audit.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
            os.chmod(self.audit, 0o600)
