from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import secrets
import threading
import time
from contextlib import closing
from ctypes import wintypes
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from media_store import store_image


SCOPE = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
PICKER_API = "https://photospicker.googleapis.com/v1"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024
SESSION_FALLBACK_SECONDS = 15 * 60
MAX_PICKER_ITEMS = 50
CLIENT_ID = re.compile(r"^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$")
FLOWS: dict[str, dict] = {}
SESSIONS: dict[str, dict] = {}
STATE_LOCK = threading.Lock()


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _private_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def _dpapi(raw: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise ValueError("Persistent Google authorisation is available only in the Windows edition")
    source_buffer = ctypes.create_string_buffer(raw)
    source = _DataBlob(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(source), "Life Atlas Google Photos", None, None, None, 1,
                                      ctypes.byref(output))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1,
                                        ctypes.byref(output))
    if not ok:
        raise ValueError("Windows could not unlock the Google Photos authorisation")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, wintypes.HLOCAL))


def _token_path(data_dir: Path) -> Path:
    return data_dir / "google_photos_token.bin"


def _save_token(data_dir: Path, token: dict) -> None:
    raw = json.dumps(token, separators=(",", ":")).encode("utf-8")
    _private_write(_token_path(data_dir), _dpapi(raw, protect=True))


def _load_token(data_dir: Path) -> dict:
    path = _token_path(data_dir)
    if not path.is_file():
        raise ValueError("Authorise Google Photos first")
    try:
        return json.loads(_dpapi(path.read_bytes(), protect=False))
    except (ValueError, json.JSONDecodeError):
        raise ValueError("Google Photos authorisation could not be unlocked. Authorise it again.") from None


def configure(data_dir: Path, source_path: str) -> dict:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError("Google OAuth client JSON file not found")
    value = _read_json(source)
    client = value.get("installed")
    if not client or not client.get("client_id") or not client.get("auth_uri") or not client.get("token_uri"):
        raise ValueError("Choose OAuth credentials for a Desktop app")
    if not CLIENT_ID.fullmatch(str(client["client_id"])):
        raise ValueError("The OAuth client ID is not valid")
    target = data_dir / "google_photos_client.json"
    _private_write(target, json.dumps({"installed": client}, indent=2).encode("utf-8"))
    disconnect(data_dir, remove_client=False, revoke=False)
    return status(data_dir)


def configure_web(data_dir: Path, client_id: str) -> dict:
    client_id = str(client_id or "").strip()
    if not CLIENT_ID.fullmatch(client_id):
        raise ValueError("Enter a valid Google OAuth Web client ID")
    _private_write(data_dir / "google_photos_web_client.json",
                   json.dumps({"client_id": client_id}, indent=2).encode("utf-8"))
    return web_status(data_dir)


def status(data_dir: Path) -> dict:
    (data_dir / "google_photos_token.json").unlink(missing_ok=True)
    client = data_dir / "google_photos_client.json"
    token = _token_path(data_dir)
    return {"configured": client.is_file(), "authorised": token.is_file(), "scope": SCOPE,
            "storage": "Windows Data Protection API"}


def web_status(data_dir: Path) -> dict:
    path = data_dir / "google_photos_web_client.json"
    value = _read_json(path) if path.is_file() else {}
    return {"configured": bool(value.get("client_id")), "client_id": value.get("client_id", ""),
            "authorised": False, "scope": SCOPE, "storage": "browser memory only"}


def disconnect(data_dir: Path, *, remove_client: bool = False, revoke: bool = True) -> dict:
    token = None
    if _token_path(data_dir).is_file():
        try:
            current = _load_token(data_dir)
            token = current.get("refresh_token") or current.get("access_token")
        except ValueError:
            pass
    if revoke and token:
        try:
            _request_json(REVOKE_ENDPOINT, method="POST", form={"token": token})
        except ValueError:
            pass
    _token_path(data_dir).unlink(missing_ok=True)
    (data_dir / "google_photos_token.json").unlink(missing_ok=True)
    if remove_client:
        (data_dir / "google_photos_client.json").unlink(missing_ok=True)
    with STATE_LOCK:
        FLOWS.clear()
        SESSIONS.clear()
    return status(data_dir)


def disconnect_web(data_dir: Path) -> dict:
    (data_dir / "google_photos_web_client.json").unlink(missing_ok=True)
    with STATE_LOCK:
        SESSIONS.clear()
    return web_status(data_dir)


def authorisation_url(data_dir: Path, base_url: str) -> str:
    client = _read_json(data_dir / "google_photos_client.json").get("installed", {})
    if not client:
        raise ValueError("Set up Google Photos first")
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Desktop Google authorisation must use the local Life Atlas address")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    redirect_uri = f"{base_url}/api/google-photos/callback"
    with STATE_LOCK:
        _prune_locked()
        FLOWS[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "created": time.time()}
    params = {
        "client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return f"{client['auth_uri']}?{urlencode(params)}"


def complete_authorisation(data_dir: Path, query: dict) -> None:
    if query.get("error"):
        raise ValueError("Google Photos authorisation was cancelled")
    state = (query.get("state") or [""])[0]
    code = (query.get("code") or [""])[0]
    with STATE_LOCK:
        flow = FLOWS.pop(state, None)
    if not flow or time.time() - flow["created"] > 600 or not code:
        raise ValueError("Google authorisation expired. Start it again from Life Atlas.")
    client = _read_json(data_dir / "google_photos_client.json")["installed"]
    fields = {"code": code, "client_id": client["client_id"], "redirect_uri": flow["redirect_uri"],
              "grant_type": "authorization_code", "code_verifier": flow["verifier"]}
    if client.get("client_secret"):
        fields["client_secret"] = client["client_secret"]
    token = _request_json(client.get("token_uri") or TOKEN_ENDPOINT, method="POST", form=fields)
    granted = set(str(token.get("scope") or SCOPE).split())
    if SCOPE not in granted or not token.get("access_token"):
        raise ValueError("Google did not grant the Photos Picker permission")
    token["expires_at"] = time.time() + max(60, int(token.get("expires_in", 3600))) - 60
    _save_token(data_dir, token)


def _access_token(data_dir: Path) -> str:
    token = _load_token(data_dir)
    if token.get("access_token") and float(token.get("expires_at", 0)) > time.time():
        return str(token["access_token"])
    if not token.get("refresh_token"):
        raise ValueError("Google Photos authorisation expired. Authorise it again.")
    client = _read_json(data_dir / "google_photos_client.json")["installed"]
    fields = {"refresh_token": token["refresh_token"], "client_id": client["client_id"],
              "grant_type": "refresh_token"}
    if client.get("client_secret"):
        fields["client_secret"] = client["client_secret"]
    refreshed = _request_json(client.get("token_uri") or TOKEN_ENDPOINT, method="POST", form=fields)
    if not refreshed.get("access_token"):
        raise ValueError("Google Photos authorisation expired. Authorise it again.")
    token.update(refreshed)
    token["expires_at"] = time.time() + max(60, int(token.get("expires_in", 3600))) - 60
    _save_token(data_dir, token)
    return str(token["access_token"])


def _request_json(url: str, *, method: str = "GET", token: str = "", body: dict | None = None,
                  form: dict | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "LifeAtlas/1.0"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=30) as response:
            raw = response.read(MAX_DOWNLOAD_BYTES + 1)
        if len(raw) > MAX_DOWNLOAD_BYTES:
            raise ValueError("Google Photos returned an unexpectedly large response")
        return json.loads(raw or b"{}")
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read(MAX_DOWNLOAD_BYTES)).get("error", {}).get("message")
        except Exception:
            detail = None
        if exc.code in {401, 403}:
            raise ValueError("Google Photos authorisation expired or was revoked") from None
        raise ValueError(detail or f"Google Photos returned HTTP {exc.code}") from None
    except (URLError, TimeoutError):
        raise ValueError("Google Photos could not be reached. Check the connection and try again.") from None


def _duration_seconds(value: object, default: float) -> float:
    text = str(value or "")
    try:
        seconds = float(text[:-1]) if text.endswith("s") else float(text)
        return min(max(seconds, 1), 3600)
    except ValueError:
        return default


def _target(value: dict) -> dict:
    allowed = {key: value.get(key) for key in ("event_id", "person_id", "captured_date") if value.get(key)}
    if len(allowed) != 1:
        raise ValueError("Choose one event, person or day")
    if "event_id" in allowed:
        allowed["event_id"] = int(allowed["event_id"])
        if allowed["event_id"] <= 0:
            raise ValueError("Event identifier is not valid")
    if "person_id" in allowed:
        allowed["person_id"] = int(allowed["person_id"])
        if allowed["person_id"] <= 0:
            raise ValueError("Person identifier is not valid")
    if "captured_date" in allowed:
        try:
            allowed["captured_date"] = time.strftime("%Y-%m-%d", time.strptime(str(allowed["captured_date"]), "%Y-%m-%d"))
        except ValueError:
            raise ValueError("Photo date must use YYYY-MM-DD") from None
    return allowed


def _prune_locked() -> None:
    now = time.time()
    for key in [key for key, value in FLOWS.items() if now - value["created"] > 600]:
        FLOWS.pop(key, None)
    for key in [key for key, value in SESSIONS.items() if value["expires_at"] <= now]:
        SESSIONS.pop(key, None)


def create_session(data_dir: Path, target: dict, *, access_token: str = "") -> dict:
    selected_target = _target(target)
    token = str(access_token or "").strip() or _access_token(data_dir)
    if len(token) < 20 or len(token) > 4096:
        raise ValueError("Google Photos authorisation is not valid")
    google_session = _request_json(f"{PICKER_API}/sessions", method="POST", token=token,
                                   body={"pickingConfig": {"maxItemCount": str(MAX_PICKER_ITEMS)}})
    google_id = str(google_session.get("id") or "")
    picker_uri = str(google_session.get("pickerUri") or "")
    if not google_id or not picker_uri.startswith("https://"):
        raise ValueError("Google Photos did not create a picker session")
    polling = google_session.get("pollingConfig", {})
    interval = _duration_seconds(polling.get("pollInterval"), 3)
    timeout = _duration_seconds(polling.get("timeoutIn"), SESSION_FALLBACK_SECONDS)
    local_id = secrets.token_urlsafe(32)
    with STATE_LOCK:
        _prune_locked()
        SESSIONS[local_id] = {"google_id": google_id, "target": selected_target, "token": token,
                              "expires_at": time.time() + timeout}
    return {"session_id": local_id, "picker_uri": picker_uri.rstrip("/") + "/autoclose",
            "poll_interval": interval, "timeout_in": timeout}


def _download_photo(base_url: str, token: str) -> bytes:
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (hostname == "googleusercontent.com" or hostname.endswith(".googleusercontent.com")):
        raise ValueError("Google Photos returned an invalid media address")
    request = Request(base_url + "=w1600-h1600", headers={"Authorization": f"Bearer {token}",
                                                            "User-Agent": "LifeAtlas/1.0"})
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read(MAX_DOWNLOAD_BYTES + 1)
        if not raw or len(raw) > MAX_DOWNLOAD_BYTES:
            raise ValueError("The selected photo is too large")
        return raw
    except HTTPError as exc:
        raise ValueError(f"Google Photos could not download the selected photo (HTTP {exc.code})") from None
    except (URLError, TimeoutError):
        raise ValueError("The selected Google photo could not be downloaded") from None


def _existing_media(connect, source_id: int, source_ref: str, target: dict) -> dict | None:
    clauses = ["source_id=?", "source_ref=?"]
    params: list[object] = [source_id, source_ref]
    for key in ("event_id", "person_id"):
        if target.get(key):
            clauses.append(f"{key}=?")
            params.append(target[key])
        else:
            clauses.append(f"{key} IS NULL")
    if not target.get("event_id") and not target.get("person_id"):
        clauses.append("captured_date=?")
        params.append(target["captured_date"])
    with closing(connect()) as con, con:
        row = con.execute(f"SELECT id,width,height,sha256 FROM media WHERE {' AND '.join(clauses)} ORDER BY id LIMIT 1",
                          params).fetchone()
    return dict(row) if row else None


def poll_session(data_dir: Path, connect, session_id: str) -> dict:
    with STATE_LOCK:
        _prune_locked()
        session = SESSIONS.get(session_id)
    if not session:
        raise ValueError("This picker session has expired. Choose the photo again.")
    token = session["token"]
    google_id = session["google_id"]
    google_session = _request_json(f"{PICKER_API}/sessions/{google_id}", token=token)
    polling = google_session.get("pollingConfig", {})
    interval = _duration_seconds(polling.get("pollInterval"), 3)
    timeout = _duration_seconds(polling.get("timeoutIn"), max(1, session["expires_at"] - time.time()))
    with STATE_LOCK:
        if session_id in SESSIONS:
            SESSIONS[session_id]["expires_at"] = min(SESSIONS[session_id]["expires_at"], time.time() + timeout)
    if not google_session.get("mediaItemsSet"):
        return {"state": "waiting", "poll_interval": interval, "timeout_in": timeout}
    selected = []
    page_token = ""
    while len(selected) < MAX_PICKER_ITEMS:
        query = {"sessionId": google_id, "pageSize": min(100, MAX_PICKER_ITEMS - len(selected))}
        if page_token:
            query["pageToken"] = page_token
        page = _request_json(f"{PICKER_API}/mediaItems?{urlencode(query)}", token=token)
        selected.extend(page.get("mediaItems", [])[: MAX_PICKER_ITEMS - len(selected)])
        page_token = str(page.get("nextPageToken") or "")
        if not page_token:
            break
    imported = []
    for item in selected:
        media = item.get("mediaFile", {})
        if item.get("type") != "PHOTO" or not media.get("baseUrl"):
            continue
        source_ref = str(item.get("id") or "")
        with closing(connect()) as con, con:
            con.execute("INSERT OR IGNORE INTO sources(name,source_type,notes) VALUES('Google Photos Picker','photo_picker','Explicitly selected with Google Photos Picker API')")
            source_id = con.execute("SELECT id FROM sources WHERE name='Google Photos Picker'").fetchone()[0]
        existing = _existing_media(connect, source_id, source_ref, session["target"])
        if existing:
            imported.append({**existing, "url": f"api/media/{existing['id']}", "duplicate": True})
            continue
        raw = _download_photo(str(media["baseUrl"]), token)
        captured = str(item.get("createTime") or "")[:10]
        payload = {**session["target"], "caption": str(media.get("filename") or "Google Photos")[:500],
                   "source_ref": source_ref, "captured_date": session["target"].get("captured_date") or captured,
                   "is_featured": True, "source_id": source_id}
        imported.append(store_image(connect, data_dir, raw, payload))
    try:
        _request_json(f"{PICKER_API}/sessions/{google_id}", method="DELETE", token=token)
    finally:
        with STATE_LOCK:
            SESSIONS.pop(session_id, None)
    return {"state": "complete", "count": len(imported), "media": imported}


def cancel_session(session_id: str) -> None:
    with STATE_LOCK:
        session = SESSIONS.pop(session_id, None)
    if session:
        try:
            _request_json(f"{PICKER_API}/sessions/{session['google_id']}", method="DELETE", token=session["token"])
        except ValueError:
            pass
