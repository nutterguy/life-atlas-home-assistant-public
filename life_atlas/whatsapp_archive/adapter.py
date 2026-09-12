from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from archive import Archive
from waha_client import HistorySynchronizer, WahaClient, WahaError, WahaUnavailable


DATA = Path(os.environ.get("LIFE_ATLAS_WHATSAPP_DATA", "/data"))
CONFIG_FILE = DATA / "runtime-config.json"
CONNECTOR_HOST = os.environ.get("LIFE_ATLAS_WHATSAPP_CONNECTOR_HOST", "127.0.0.1")
CONNECTOR_PORT = int(os.environ.get("LIFE_ATLAS_WHATSAPP_CONNECTOR_PORT", "8097"))
MANAGEMENT_HOST = os.environ.get("LIFE_ATLAS_WHATSAPP_MANAGEMENT_HOST", "127.0.0.1")
MANAGEMENT_PORT = int(os.environ.get("LIFE_ATLAS_WHATSAPP_MANAGEMENT_PORT", "8110"))
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
CONNECTOR_VERSION = "0.2.0"
WAHA_VERSION = "2026.8.1"


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def read_secret(path: Path) -> str:
    value = path.read_text(encoding="ascii").strip()
    if len(value) < 32:
        raise RuntimeError(f"{path.name} is unavailable")
    return value


class Service:
    def __init__(self) -> None:
        config = read_json(CONFIG_FILE)
        self.session_name = str(config["session_name"])
        self.reconcile_seconds = int(config["reconcile_seconds"])
        self.full_rescan_seconds = int(config["full_rescan_hours"]) * 3600
        page_size = int(config["history_page_size"])
        self.archive = Archive(DATA / "archive" / "whatsapp-archive.sqlite3")
        self.webhook_key_file = DATA / "secrets" / "webhook-hmac-key"
        self.connector_key_file = DATA / "secrets" / "connector-api-key"
        self.waha = WahaClient(
            DATA / "secrets" / "waha-api-key",
            self.webhook_key_file,
            DATA / "secrets" / "waha-scoped-api-key",
            webhook_url=f"http://127.0.0.1:{MANAGEMENT_PORT}/internal/waha-events",
        )
        self.synchronizer = HistorySynchronizer(
            self.waha, self.archive, self.session_name, page_size=page_size
        )
        self._stop = threading.Event()
        self._sync_thread: threading.Thread | None = None
        self._sync_state_lock = threading.Lock()
        self._sync_state: dict[str, Any] = {"status": "idle"}
        self._last_full = self.archive.latest_successful_sync_timestamp("full") or 0.0
        self._background = threading.Thread(
            target=self._background_loop, name="whatsapp-reconcile", daemon=True
        )

    def start(self) -> None:
        self._background.start()

    def stop(self) -> None:
        self._stop.set()
        self._background.join(timeout=3)

    def connector_key_matches(self, supplied: str | None) -> bool:
        if not supplied:
            return False
        expected = read_secret(self.connector_key_file)
        return hmac.compare_digest(supplied, expected)

    def session(self) -> dict[str, Any] | None:
        return self.waha.session(self.session_name)

    def safe_session(self) -> dict[str, Any]:
        try:
            value = self.session()
        except WahaUnavailable:
            return {"status": "WAHA_STARTING"}
        except WahaError as exc:
            return {"status": "FAILED", "error": str(exc)}
        if value is None:
            return {"status": "NOT_CREATED"}
        return {
            "name": value.get("name"),
            "status": value.get("status"),
            "engine": nested_value(value, "engine", "engine"),
            "paired": bool(value.get("me")),
        }

    def management_status(self) -> dict[str, Any]:
        with self._sync_state_lock:
            sync_state = dict(self._sync_state)
        return {
            "session": self.safe_session(),
            "archive": self.archive.stats(),
            "sync": sync_state,
            "read_only_boundary": True,
            "waha_send_permission": False,
            "waha_version": WAHA_VERSION,
            "connector_version": CONNECTOR_VERSION,
        }

    def chat_inventory(self, *, limit: int, offset: int) -> dict[str, Any]:
        chats, total = self.archive.list_chat_policies(limit=limit, offset=offset)
        next_offset = offset + len(chats)
        return {
            "chats": chats,
            "total": total,
            "next_offset": next_offset if next_offset < total else None,
            "selection": self.archive.selection_summary(),
        }

    def set_chat_policy(self, chat_id: str, mode: str) -> dict[str, Any]:
        result = self.archive.set_chat_policy(chat_id, mode)
        if self.archive.selection_confirmed() and result["effective_selected"]:
            result["sync"] = self.trigger_sync(full=True)
        return result

    def confirm_chat_selection(self) -> dict[str, Any]:
        self.archive.confirm_selection()
        return {
            "selection": self.archive.selection_summary(),
            "sync": self.trigger_sync(full=True),
        }

    def start_session(self) -> dict[str, Any]:
        value = self.waha.ensure_session(self.session_name)
        return {"status": value.get("status"), "name": value.get("name")}

    def restart_session(self) -> dict[str, Any]:
        value = self.waha.restart_session(self.session_name)
        return {"status": value.get("status"), "name": value.get("name")}

    def trigger_sync(self, *, full: bool) -> dict[str, Any]:
        with self._sync_state_lock:
            if self._sync_thread and self._sync_thread.is_alive():
                return {"status": "already_running"}
            self._sync_state = {
                "status": "running",
                "sync_type": "full" if full else "incremental",
                "started_at": iso_now(),
            }
            self._sync_thread = threading.Thread(
                target=self._run_sync, args=(full,), name="whatsapp-sync", daemon=True
            )
            self._sync_thread.start()
            return dict(self._sync_state)

    def _run_sync(self, full: bool) -> None:
        try:
            result = self.synchronizer.run(full=full)
            if full and result.get("status") == "completed":
                self._last_full = time.time()
            state = {**result, "finished_at": iso_now()}
        except Exception as exc:
            state = {"status": "failed", "error": str(exc)[:500], "finished_at": iso_now()}
        with self._sync_state_lock:
            self._sync_state = state

    def _background_loop(self) -> None:
        self._stop.wait(10)
        while not self._stop.is_set():
            session = self.safe_session()
            if session.get("status") == "WORKING":
                if not self.archive.selection_confirmed():
                    if not self.archive.inventory_available():
                        self.trigger_sync(full=True)
                else:
                    full = self._last_full == 0 or (
                        time.time() - self._last_full >= self.full_rescan_seconds
                    )
                    self.trigger_sync(full=full)
            self._stop.wait(self.reconcile_seconds)


def nested_value(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


SERVICE: Service


class HandlerBase(BaseHTTPRequestHandler):
    server_version = "LifeAtlasWhatsAppArchive/0.2.0"

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def send_bytes(self, status: int, raw: bytes, content_type: str) -> None:
        if len(raw) > MAX_RESPONSE_BYTES:
            self.send_error_json(500, "response_too_large", "Response exceeds connector limit")
            return
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self._security_headers()
        self.send_header("X-Life-Atlas-Connector-Protocol", "1")
        self.end_headers()
        self.wfile.write(raw)

    def send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        self.send_bytes(
            status,
            json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def send_error_json(self, status: int, code: str, message: str) -> None:
        self.send_json(status, {"error": {"code": code, "message": message}})

    def read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise OverflowError("Request exceeds connector limit")
        return self.rfile.read(length)

    def read_json(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise TypeError("Content-Type must be application/json")
        raw = self.read_body()
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def dispatch_json(self, action) -> None:
        try:
            payload = self.read_json()
            result = action(payload)
            self.send_json(200, result)
        except TypeError as exc:
            self.send_error_json(415, "unsupported_media_type", str(exc))
        except OverflowError as exc:
            self.send_error_json(413, "request_too_large", str(exc))
        except ValueError as exc:
            self.send_error_json(400, "bad_request", str(exc))
        except WahaUnavailable:
            self.send_error_json(503, "unavailable", "WAHA is not ready")
        except WahaError as exc:
            self.send_error_json(502, "upstream_error", str(exc))
        except Exception:
            self.send_error_json(500, "internal_error", "Internal connector error")

    def log_message(self, format: str, *args: Any) -> None:
        return


class ConnectorHandler(HandlerBase):
    def _authorised(self) -> bool:
        supplied = self.headers.get("X-Life-Atlas-Connector-Key")
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            supplied = authorization[7:]
        if SERVICE.connector_key_matches(supplied):
            return True
        self.send_error_json(401, "unauthorized", "Connector key is required")
        return False

    def do_GET(self) -> None:
        if not self._authorised():
            return
        route = urlparse(self.path).path
        if route == "/v1/info":
            self.send_json(
                200,
                {
                    "connector_id": "whatsapp",
                    "name": "Life Atlas WhatsApp Archive",
                    "protocol_version": "1.0",
                    "connector_version": CONNECTOR_VERSION,
                    "upstream_name": "WAHA NOWEB",
                    "upstream_version": WAHA_VERSION,
                },
            )
        elif route == "/v1/status":
            session = SERVICE.safe_session()
            working = session.get("status") == "WORKING"
            confirmed = SERVICE.archive.selection_confirmed()
            state = (
                "available"
                if working and confirmed
                else ("degraded" if working else "auth_required")
            )
            stats = SERVICE.archive.stats()
            sync = stats.get("last_sync") or {}
            self.send_json(
                200,
                {
                    "state": state,
                    "authenticated": working,
                    "last_attempted_sync": sync.get("started_at"),
                    "last_successful_sync": sync.get("finished_at")
                    if sync.get("status") == "completed"
                    else None,
                    "error": sync.get("error") if sync.get("status") == "failed" else None,
                    "metadata": {"selection": SERVICE.archive.selection_summary()},
                },
            )
        elif route == "/v1/capabilities":
            self.send_json(
                200,
                {
                    "capabilities": [
                        "search",
                        "incremental_changes",
                        "item_retrieval",
                        "identities",
                        "history_sync",
                        "read_only",
                    ]
                },
            )
        else:
            self.send_error_json(404, "not_found", "Unknown Connector Protocol endpoint")

    def do_POST(self) -> None:
        if not self._authorised():
            return
        route = urlparse(self.path).path
        if route == "/v1/search":
            self.dispatch_json(self._search)
        elif route == "/v1/changes":
            self.dispatch_json(self._changes)
        elif route == "/v1/item":
            self.dispatch_json(self._item)
        else:
            self.send_error_json(404, "not_found", "Unknown Connector Protocol endpoint")

    @staticmethod
    def _limit(payload: Mapping[str, Any], default: int = 50) -> int:
        value = payload.get("limit", default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 500:
            raise ValueError("limit must be an integer between 1 and 500")
        return value

    def _search(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        limit = self._limit(payload)
        cursor = payload.get("cursor")
        try:
            offset = int(cursor) if cursor is not None else 0
        except (TypeError, ValueError) as exc:
            raise ValueError("cursor is invalid") from exc
        if offset < 0:
            raise ValueError("cursor is invalid")
        items = SERVICE.archive.search(query, limit=limit, offset=offset)
        next_cursor = str(offset + len(items)) if len(items) == limit else None
        return {"items": items, "next_cursor": next_cursor}

    def _changes(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        limit = self._limit(payload, 100)
        try:
            cursor = int(payload.get("cursor") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("cursor is invalid") from exc
        if cursor < 0:
            raise ValueError("cursor is invalid")
        items, next_cursor, has_more = SERVICE.archive.changes(cursor, limit=limit)
        return {"items": items, "next_cursor": str(next_cursor), "has_more": has_more}

    def _item(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        source_id = payload.get("source_id")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise ValueError("source_id is invalid")
        item = SERVICE.archive.get_item(source_id)
        if item is None:
            raise ValueError("source item was not found")
        return {"item": item}


class ManagementHandler(HandlerBase):
    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route in {"", "/"}:
            raw = MANAGEMENT_HTML.encode("utf-8")
            self.send_bytes(200, raw, "text/html; charset=utf-8")
        elif route == "/manage/status":
            self.send_json(200, SERVICE.management_status())
        elif route == "/manage/qr":
            try:
                self.send_bytes(200, SERVICE.waha.qr(SERVICE.session_name), "image/png")
            except WahaUnavailable:
                self.send_error_json(503, "unavailable", "WAHA is not ready")
            except WahaError as exc:
                self.send_error_json(409, "qr_unavailable", str(exc))
        elif route == "/manage/connector-key":
            self.send_json(200, {"connector_api_key": read_secret(SERVICE.connector_key_file)})
        elif route == "/manage/chats":
            query = parse_qs(urlparse(self.path).query)
            try:
                limit = int(query.get("limit", ["200"])[0])
                offset = int(query.get("offset", ["0"])[0])
                if limit < 1 or limit > 500 or offset < 0:
                    raise ValueError
            except (TypeError, ValueError):
                self.send_error_json(400, "bad_request", "Invalid inventory pagination")
                return
            self.send_json(200, SERVICE.chat_inventory(limit=limit, offset=offset))
        else:
            self.send_error_json(404, "not_found", "Unknown management endpoint")

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route == "/internal/waha-events":
            self._webhook()
            return
        if self.headers.get("X-Life-Atlas-Action") != "1":
            self.send_error_json(403, "forbidden", "Management action header is required")
            return
        actions = {
            "/manage/session/start": lambda _: SERVICE.start_session(),
            "/manage/session/restart": lambda _: SERVICE.restart_session(),
            "/manage/sync/full": lambda _: SERVICE.trigger_sync(full=True),
            "/manage/sync/incremental": lambda _: SERVICE.trigger_sync(full=False),
            "/manage/chats/policy": self._chat_policy,
            "/manage/chats/confirm": lambda _: SERVICE.confirm_chat_selection(),
        }
        action = actions.get(route)
        if action is None:
            self.send_error_json(404, "not_found", "Unknown management endpoint")
            return
        self.dispatch_json(action)

    @staticmethod
    def _chat_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
        chat_id = payload.get("chat_id")
        mode = payload.get("mode")
        if not isinstance(chat_id, str) or not isinstance(mode, str):
            raise ValueError("chat_id and mode are required")
        return SERVICE.set_chat_policy(chat_id, mode)

    def _webhook(self) -> None:
        try:
            raw = self.read_body()
        except OverflowError as exc:
            self.send_error_json(413, "request_too_large", str(exc))
            return
        except ValueError as exc:
            self.send_error_json(400, "bad_request", str(exc))
            return
        request_id = self.headers.get("X-Webhook-Request-Id")
        supplied = self.headers.get("X-Webhook-Hmac", "")
        algorithm = self.headers.get("X-Webhook-Hmac-Algorithm", "")
        if supplied.startswith("sha512="):
            supplied = supplied[7:]
        expected = hmac.new(
            read_secret(SERVICE.webhook_key_file).encode("ascii"), raw, hashlib.sha512
        ).hexdigest()
        if (
            not request_id
            or len(request_id) > 256
            or algorithm.lower() != "sha512"
            or not hmac.compare_digest(supplied, expected)
        ):
            self.send_error_json(401, "unauthorized", "Invalid WAHA webhook signature")
            return
        timestamp = self.headers.get("X-Webhook-Timestamp")
        if timestamp:
            try:
                age = abs(time.time() - int(timestamp) / 1000)
            except ValueError:
                age = 10_000
            if age > 600:
                self.send_error_json(401, "stale_webhook", "WAHA webhook timestamp is stale")
                return
        try:
            event = json.loads(raw.decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self.send_error_json(400, "invalid_json", "Webhook must be a JSON object")
            return
        try:
            accepted = SERVICE.archive.record_webhook(
                request_id, hashlib.sha256(raw).hexdigest(), event
            )
        except ValueError as exc:
            self.send_error_json(400, "bad_event", str(exc))
            return
        if accepted and event.get("event") == "chat.archive":
            payload = event.get("payload")
            chat_id = (
                str(payload.get("id") or "") if isinstance(payload, Mapping) else ""
            )
            if SERVICE.archive.should_archive_chat(chat_id):
                SERVICE.trigger_sync(full=True)
        self.send_json(200, {"accepted": accepted})


MANAGEMENT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Life Atlas WhatsApp Archive</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}main{max-width:1100px;margin:auto}
.card{background:#172033;border:1px solid #334155;border-radius:14px;padding:18px;margin:14px 0}button,select{border:0;border-radius:9px;padding:9px 12px;font-weight:700}button{background:#25d366;color:#062b18;margin:4px}button.secondary,select{background:#334155;color:#e2e8f0}button:disabled{opacity:.45}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.muted{color:#94a3b8}.warning{background:#422006;border:1px solid #a16207;padding:12px;border-radius:9px}.ok{background:#052e25;border:1px solid #047857;padding:12px;border-radius:9px}code{word-break:break-all}img{max-width:300px;background:white;padding:10px;border-radius:12px}pre{white-space:pre-wrap;background:#0f172a;padding:12px;border-radius:9px;overflow:auto}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:850px}th,td{text-align:left;border-bottom:1px solid #334155;padding:9px 7px}th{color:#94a3b8;font-size:.85rem}td small{color:#94a3b8}.pill{display:inline-block;border:1px solid #475569;border-radius:99px;padding:2px 7px;font-size:.8rem}.excluded{color:#fca5a5}.included{color:#86efac}
</style></head><body><main>
<p><a href="../" style="color:#86efac">← Back to Life Atlas</a></p><h1>Life Atlas WhatsApp Archive</h1><p class="muted">Read-only local history capture. WAHA is loopback-only and has no send permission.</p>
<section class="card"><h2>1. WhatsApp session</h2><div id="session">Loading…</div><div class="row"><button onclick="action('manage/session/start')">Create / start</button><button class="secondary" onclick="action('manage/session/restart')">Restart pairing</button></div><p>When the status is <code>SCAN_QR_CODE</code>, scan this code in WhatsApp → Linked devices.</p><img id="qr" alt="Pairing QR code" hidden></section>
<section class="card"><h2>2. Choose durable chats</h2><div id="selection" class="warning">Waiting for inventory…</div><p class="muted">WhatsApp supplies linked-device history account-wide. WAHA temporarily stages it, but that staging database is excluded from Home Assistant backups. Only chats selected below enter the durable, backed-up Life Atlas archive. Archived chats default to excluded.</p><div class="row"><button onclick="action('manage/sync/full',true)">Refresh chat inventory</button><button id="confirm" onclick="confirmSelection()" disabled>Confirm selection &amp; archive</button></div><div class="table"><table><thead><tr><th>Chat</th><th>State</th><th>Messages seen</th><th>Available range</th><th>Policy</th><th>Durable / backed up</th></tr></thead><tbody id="chats"><tr><td colspan="6">Run an inventory scan after pairing.</td></tr></tbody></table></div></section>
<section class="card"><h2>3. Durable archive</h2><div id="archive">Loading…</div><div class="row"><button onclick="action('manage/sync/full',true)">Full reconciliation</button><button class="secondary" onclick="action('manage/sync/incremental')">Reconcile recent messages</button></div></section>
<section class="card"><h2>Diagnostics</h2><pre id="diagnostics">Loading…</pre></section>
</main><script>
const headers={'Content-Type':'application/json','X-Life-Atlas-Action':'1'};
async function jsonFetch(path,options={}){const r=await fetch(path,{cache:'no-store',...options});const v=await r.json();if(!r.ok)throw new Error(v.error?.message||`HTTP ${r.status}`);return v}
async function refresh(){try{const s=await jsonFetch('manage/status');document.getElementById('session').textContent=`${s.session.status} · ${s.session.engine||'NOWEB'} · paired: ${s.session.paired?'yes':'no'}`;const c=s.archive.counts,cv=s.archive.coverage,p=s.archive.selection;document.getElementById('archive').textContent=`${c.messages} selected messages · ${c.message_versions} revisions · coverage ${cv.earliest||'unknown'} to ${cv.latest||'unknown'}`;const sel=document.getElementById('selection');sel.className=p.confirmed?'ok':'warning';sel.textContent=p.confirmed?`Confirmed: ${p.included} chats included (${p.included_messages} observed messages); ${p.excluded} excluded.`:`Not confirmed: nothing is copied to the durable archive. Proposed: ${p.included} chats included (${p.included_messages} messages); ${p.excluded} excluded (${p.excluded_messages} messages).`;document.getElementById('confirm').disabled=!p.inventory_updated_at||p.confirmed;document.getElementById('diagnostics').textContent=JSON.stringify({sync:s.sync,last_sync:s.archive.last_sync,selection:p,staging_backed_up:false,read_only_boundary:s.read_only_boundary,waha_send_permission:s.waha_send_permission,waha_version:s.waha_version},null,2);const qr=document.getElementById('qr');if(s.session.status==='SCAN_QR_CODE'){qr.src='manage/qr?t='+Date.now();qr.hidden=false}else{qr.hidden=true}await loadChats()}catch(e){document.getElementById('diagnostics').textContent=e.message}}
async function loadChats(){let all=[],offset=0,selection=null;for(let page=0;page<20;page++){const v=await jsonFetch(`manage/chats?limit=500&offset=${offset}`);all=all.concat(v.chats);selection=v.selection;if(v.next_offset===null)break;offset=v.next_offset}const body=document.getElementById('chats');body.replaceChildren();if(!all.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.textContent='No chats discovered yet.';tr.append(td);body.append(tr);return}for(const chat of all){const tr=document.createElement('tr');const title=document.createElement('td');const strong=document.createElement('strong');strong.textContent=chat.name||chat.chat_id;const small=document.createElement('small');small.textContent=chat.name?chat.chat_id:chat.chat_type;title.append(strong,document.createElement('br'),small);const state=document.createElement('td');state.textContent=chat.archived?'Archived':chat.chat_type;const count=document.createElement('td');count.textContent=String(chat.observed_message_count);const range=document.createElement('td');range.textContent=`${chat.observed_earliest||'—'} → ${chat.observed_latest||'—'}`;const policy=document.createElement('td');const select=document.createElement('select');for(const [value,label] of [['auto','Automatic'],['include','Always include'],['exclude','Always exclude']]){const option=document.createElement('option');option.value=value;option.textContent=label;option.selected=chat.mode===value;select.append(option)}select.onchange=()=>setPolicy(chat.chat_id,select.value);policy.append(select);const durable=document.createElement('td');durable.className=chat.effective_selected?'included':'excluded';durable.textContent=chat.effective_selected?`Include · ${chat.durable_message_count} stored`:'Exclude';tr.append(title,state,count,range,policy,durable);body.append(tr)}}
async function action(path,reload=false){try{await jsonFetch(path,{method:'POST',headers,body:'{}'});setTimeout(refresh,reload?1000:700)}catch(e){alert(e.message)}}
async function setPolicy(chat_id,mode){try{await jsonFetch('manage/chats/policy',{method:'POST',headers,body:JSON.stringify({chat_id,mode})});await refresh()}catch(e){alert(e.message);await refresh()}}
async function confirmSelection(){if(!confirm('Only chats shown as Include will be copied into the durable archive and Home Assistant backups. Continue?'))return;await action('manage/chats/confirm',true)}
refresh();setInterval(refresh,10000);
</script></body></html>"""


def main() -> None:
    global SERVICE
    SERVICE = Service()
    SERVICE.start()
    connector = ThreadingHTTPServer((CONNECTOR_HOST, CONNECTOR_PORT), ConnectorHandler)
    management = ThreadingHTTPServer((MANAGEMENT_HOST, MANAGEMENT_PORT), ManagementHandler)
    connector_thread = threading.Thread(
        target=connector.serve_forever, name="connector-protocol", daemon=True
    )
    connector_thread.start()
    try:
        management.serve_forever()
    finally:
        management.shutdown()
        connector.shutdown()
        connector.server_close()
        management.server_close()
        SERVICE.stop()
        connector_thread.join(timeout=3)


if __name__ == "__main__":
    main()
