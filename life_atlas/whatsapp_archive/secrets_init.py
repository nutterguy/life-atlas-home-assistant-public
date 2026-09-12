from __future__ import annotations

import json
import hashlib
import os
import secrets
from pathlib import Path


DATA = Path(os.environ.get("LIFE_ATLAS_WHATSAPP_DATA", "/data"))
OPTIONS_FILE = Path(os.environ.get("LIFE_ATLAS_OPTIONS_FILE", str(DATA / "options.json")))
SECRETS = DATA / "secrets"


def ensure_secret(path: Path) -> None:
    if path.exists():
        os.chmod(path, 0o600)
        return
    value = secrets.token_urlsafe(48)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(value, encoding="ascii")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def ensure_waha_admin_credentials() -> None:
    plaintext = SECRETS / "waha-api-key"
    digest_file = SECRETS / "waha-api-key-hash"
    if digest_file.exists():
        os.chmod(digest_file, 0o600)
        return
    ensure_secret(plaintext)
    value = plaintext.read_text(encoding="ascii").strip()
    digest = "sha512:" + hashlib.sha512(value.encode("ascii")).hexdigest()
    temporary = digest_file.with_suffix(".tmp")
    temporary.write_text(digest, encoding="ascii")
    os.chmod(temporary, 0o600)
    os.replace(temporary, digest_file)


def options() -> dict[str, object]:
    try:
        value = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def integer(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def main() -> None:
    for path in (DATA, SECRETS, DATA / "waha", DATA / "archive"):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    ensure_waha_admin_credentials()
    ensure_secret(SECRETS / "webhook-hmac-key")
    ensure_secret(SECRETS / "connector-api-key")

    raw = options()
    session_name = str(raw.get("whatsapp_session_name", "life-atlas"))
    if not session_name or len(session_name) > 64 or not all(
        char.isalnum() or char in "_-" for char in session_name
    ):
        session_name = "life-atlas"
    config = {
        "session_name": session_name,
        "reconcile_seconds": integer(raw.get("whatsapp_reconcile_seconds"), 300, 30, 3600),
        "full_rescan_hours": integer(raw.get("whatsapp_full_rescan_hours"), 24, 1, 168),
        "history_page_size": integer(raw.get("whatsapp_history_page_size"), 500, 50, 1000),
    }
    target = DATA / "runtime-config.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, separators=(",", ":")), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)


if __name__ == "__main__":
    main()
