from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from archive import Archive, finite_timestamp, message_chat_id


class WahaError(RuntimeError):
    pass


class WahaUnavailable(WahaError):
    pass


class WahaClient:
    """Narrow WAHA client. Deliberately contains no message mutation methods."""

    def __init__(
        self,
        api_key_file: Path,
        webhook_key_file: Path,
        scoped_api_key_file: Path | None = None,
        *,
        base_url: str = "http://127.0.0.1:3001",
        webhook_url: str = "http://127.0.0.1:8110/internal/waha-events",
        timeout: float = 20,
    ) -> None:
        self.api_key_file = api_key_file
        self.webhook_key_file = webhook_key_file
        self.scoped_api_key_file = scoped_api_key_file or api_key_file.with_name(
            "waha-scoped-api-key"
        )
        self.base_url = base_url.rstrip("/")
        self.webhook_url = webhook_url
        self.timeout = timeout

    def _api_key(self, *, admin: bool = False) -> str:
        path = self.api_key_file
        if not admin and self.scoped_api_key_file.exists():
            path = self.scoped_api_key_file
        try:
            value = path.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise WahaError("WAHA internal API key is unavailable") from exc
        if len(value) < 32:
            raise WahaError("WAHA internal API key is unavailable")
        return value

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        accept: str = "application/json",
        admin: bool = False,
    ) -> tuple[bytes, str]:
        data = json.dumps(dict(body), separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {"Accept": accept, "X-Api-Key": self._api_key(admin=admin)}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read(), response.headers.get_content_type()
        except HTTPError as exc:
            raise WahaError(f"WAHA {method} {path.split('?')[0]} returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise WahaUnavailable("WAHA is not ready") from exc

    def _json(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        admin: bool = False,
    ) -> Any:
        raw, content_type = self._request(method, path, body, admin=admin)
        if content_type != "application/json":
            raise WahaError("WAHA returned a non-JSON response")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WahaError("WAHA returned malformed JSON") from exc

    def session(self, name: str) -> dict[str, Any] | None:
        try:
            value = self._json("GET", f"/api/sessions/{quote(name, safe='')}")
        except WahaError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        if not isinstance(value, dict):
            raise WahaError("WAHA session response is not an object")
        return value

    def ensure_session(self, name: str) -> dict[str, Any]:
        existing = self.session(name)
        if existing is not None:
            self._ensure_scoped_key(name)
            if existing.get("status") == "STOPPED":
                value = self._json("POST", f"/api/sessions/{quote(name, safe='')}/start", {})
                if isinstance(value, dict):
                    return value
            return existing
        webhook_key = self.webhook_key_file.read_text(encoding="ascii").strip()
        request = {
            "name": name,
            "start": True,
            "config": {
                "debug": False,
                "noweb": {"store": {"enabled": True, "fullSync": True}, "markOnline": False},
                "client": {"deviceName": "Life Atlas Archive", "browserName": "Chrome"},
                "webhooks": [
                    {
                        "url": self.webhook_url,
                        "events": [
                            "session.status",
                            "message.any",
                            "message.edited",
                            "message.revoked",
                            "chat.archive",
                        ],
                        "hmac": {"key": webhook_key},
                        "retries": {"policy": "exponential", "delaySeconds": 2, "attempts": 15},
                    }
                ],
            },
        }
        value = self._json("POST", "/api/sessions", request, admin=True)
        if not isinstance(value, dict):
            raise WahaError("WAHA create-session response is not an object")
        self._ensure_scoped_key(name)
        return value

    def _ensure_scoped_key(self, name: str) -> None:
        """Mint the only steady-state key with WAHA send permission disabled."""
        if self.scoped_api_key_file.exists():
            return
        request = {
            "isAdmin": False,
            "isActive": True,
            "session": name,
            "actions": {
                "read": True,
                "send": False,
                "control": True,
                "setting": False,
                "app": False,
                "delete": False,
            },
        }
        value = self._json("POST", "/api/keys/", request, admin=True)
        key = value.get("key") if isinstance(value, Mapping) else None
        actions = value.get("actions") if isinstance(value, Mapping) else None
        if (
            not isinstance(key, str)
            or len(key) < 32
            or not isinstance(actions, Mapping)
            or actions.get("read") is not True
            or actions.get("send") is not False
            or actions.get("control") is not True
        ):
            raise WahaError("WAHA did not return the required read-only scoped key")
        temporary = self.scoped_api_key_file.with_suffix(".tmp")
        temporary.write_text(key, encoding="ascii")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.scoped_api_key_file)
        # WAHA retained only the SHA-512 hash. Removing the bootstrap plaintext
        # leaves no usable administrator credential in the steady-state add-on.
        try:
            self.api_key_file.unlink()
        except FileNotFoundError:
            pass

    def restart_session(self, name: str) -> dict[str, Any]:
        value = self._json("POST", f"/api/sessions/{quote(name, safe='')}/restart", {})
        if not isinstance(value, dict):
            raise WahaError("WAHA restart response is not an object")
        return value

    def qr(self, name: str) -> bytes:
        raw, content_type = self._request(
            "GET", f"/api/{quote(name, safe='')}/auth/qr", accept="image/png"
        )
        if content_type != "image/png" or len(raw) > 2 * 1024 * 1024:
            raise WahaError("WAHA returned an invalid QR image")
        return raw

    def _paged_get(self, path: str, params: Mapping[str, Any]) -> Any:
        return self._json("GET", path + "?" + urlencode(params))

    def list_chats(self, name: str, *, limit: int, offset: int) -> list[dict[str, Any]]:
        value = self._paged_get(
            f"/api/{quote(name, safe='')}/chats",
            {"limit": limit, "offset": offset, "sortBy": "id", "sortOrder": "asc"},
        )
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise WahaError("WAHA chats response is not a list of objects")
        return value

    def list_messages(
        self,
        name: str,
        *,
        limit: int,
        offset: int,
        timestamp_gte: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "downloadMedia": "false",
            "sortBy": "timestamp",
            "sortOrder": "asc",
        }
        if timestamp_gte is not None:
            params["filter.timestamp.gte"] = timestamp_gte
        value = self._paged_get(
            f"/api/{quote(name, safe='')}/chats/all/messages", params
        )
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise WahaError("WAHA messages response is not a list of objects")
        return value


class HistorySynchronizer:
    def __init__(
        self,
        client: WahaClient,
        archive: Archive,
        session_name: str,
        *,
        page_size: int = 500,
    ) -> None:
        self.client = client
        self.archive = archive
        self.session_name = session_name
        self.page_size = min(max(page_size, 50), 1000)
        self.lock = threading.Lock()

    def run(self, *, full: bool) -> dict[str, Any]:
        if not self.lock.acquire(blocking=False):
            return {"status": "already_running"}
        sync_type = "full" if full else "incremental"
        sync_id = self.archive.start_sync(sync_type)
        pages = seen = changed = 0
        metrics: dict[str, list[int | None]] = {}
        upstream_latest: int | None = None
        try:
            session = self.client.session(self.session_name)
            if not session or session.get("status") != "WORKING":
                raise WahaError("WhatsApp session is not working")
            # NOWEB does not currently promise chat.archive events. Refreshing
            # chat state on every reconciliation makes archive/unarchive policy
            # changes converge without relying on that optional webhook.
            selection_expanded = self._sync_chats()
            self.archive.purge_excluded_chats()
            gte = (
                None
                if full or selection_expanded
                else max((self.archive.upstream_latest_timestamp() or 0) - 300, 0)
            )
            offset = 0
            prior_fingerprint: tuple[str | None, str | None, int] | None = None
            while pages < 20_000:
                batch = self.client.list_messages(
                    self.session_name,
                    limit=self.page_size,
                    offset=offset,
                    timestamp_gte=gte,
                )
                pages += 1
                if not batch:
                    break
                fingerprint = (
                    str(batch[0].get("id")) if batch else None,
                    str(batch[-1].get("id")) if batch else None,
                    len(batch),
                )
                if fingerprint == prior_fingerprint:
                    raise WahaError("WAHA repeated a history page without making progress")
                prior_fingerprint = fingerprint
                for message in batch:
                    seen += 1
                    chat_id = message_chat_id(message)
                    timestamp = finite_timestamp(message.get("timestamp"))
                    if timestamp is not None:
                        upstream_latest = max(upstream_latest or timestamp, timestamp)
                    if full and chat_id:
                        values = metrics.setdefault(chat_id, [0, None, None])
                        values[0] = int(values[0] or 0) + 1
                        if timestamp is not None:
                            values[1] = (
                                timestamp
                                if values[1] is None
                                else min(int(values[1]), timestamp)
                            )
                            values[2] = (
                                timestamp
                                if values[2] is None
                                else max(int(values[2]), timestamp)
                            )
                    if self.archive.ingest_message(message):
                        changed += 1
                offset += self.page_size
            else:
                raise WahaError("WAHA history exceeded the bounded page limit")
            if full:
                inventory = {
                    chat_id: (int(values[0] or 0), values[1], values[2])
                    for chat_id, values in metrics.items()
                }
                self.archive.replace_inventory_metrics(inventory)
            else:
                self.archive.record_upstream_latest_timestamp(upstream_latest)
            self.archive.finish_sync(
                sync_id, status="completed", pages=pages, seen=seen, changed=changed
            )
            return {
                "status": "completed",
                "sync_type": sync_type,
                "pages": pages,
                "messages_seen": seen,
                "messages_changed": changed,
                "selection_confirmed": self.archive.selection_confirmed(),
                "backfill_triggered": bool(selection_expanded and not full),
            }
        except Exception as exc:
            message = str(exc)[:500]
            self.archive.finish_sync(
                sync_id,
                status="failed",
                pages=pages,
                seen=seen,
                changed=changed,
                error=message,
            )
            raise
        finally:
            self.lock.release()

    def _sync_chats(self) -> bool:
        confirmed = self.archive.selection_confirmed()
        before = self.archive.effective_chat_selections() if confirmed else {}
        offset = 0
        prior_fingerprint: tuple[str | None, str | None, int] | None = None
        for _ in range(10_000):
            batch = self.client.list_chats(
                self.session_name, limit=self.page_size, offset=offset
            )
            if not batch:
                if not confirmed:
                    return False
                after = self.archive.effective_chat_selections()
                return any(
                    selected and not before.get(chat_id, False)
                    for chat_id, selected in after.items()
                )
            fingerprint = (
                str(batch[0].get("id")) if batch else None,
                str(batch[-1].get("id")) if batch else None,
                len(batch),
            )
            if fingerprint == prior_fingerprint:
                raise WahaError("WAHA repeated a chat page without making progress")
            prior_fingerprint = fingerprint
            for chat in batch:
                self.archive.upsert_chat(chat)
            offset += self.page_size
        raise WahaError("WAHA chat list exceeded the bounded page limit")
