"""Life Atlas connector registry.

Every connector is a separate, independently deployed service. Life Atlas holds
only its registration: identity, direction, address, its own credential to that
service, an on/off switch, and the last observed status. Nothing here reaches
into a connector's own database and nothing here belongs to a connector.

The registry lives in its own SQLite file rather than `life_atlas.sqlite3` for
two reasons. `schema.sql` stays byte-compatible with the Windows edition, and
machine-specific addresses and connector keys stay out of the canonical
database, its backups, and its CSV export.

Direction is explicit. A connector may feed Life Atlas (`source`), read from it
(`consumer`), or both (`bidirectional`). Inbound traffic speaks Connector
Protocol v1; outbound traffic authenticates to the Life Atlas agent API. A
connector that does neither cannot be registered.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from connector_http import HTTPConnectorTransport
from connectors import (ConnectorAuthRequired, ConnectorClient, ConnectorError,
                        ConnectorUnavailable, SourceItem)

REGISTRY_FILENAME = "connectors.sqlite3"
DEFAULT_TIMEOUT_SECONDS = 2.0
MAX_SEARCH_PREVIEW = 10

KINDS = ("source", "consumer", "bidirectional")
INBOUND_KINDS = ("source", "bidirectional")
OUTBOUND_KINDS = ("consumer", "bidirectional")

SCHEMA = """
CREATE TABLE IF NOT EXISTS connectors (
  connector_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('source','consumer','bidirectional')),
  base_url TEXT NOT NULL DEFAULT '',
  auth_key TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
  notes TEXT NOT NULL DEFAULT '',
  cursor TEXT NOT NULL DEFAULT '',
  last_state TEXT NOT NULL DEFAULT 'unknown',
  last_error TEXT NOT NULL DEFAULT '',
  last_checked_at TEXT,
  last_attempted_sync TEXT,
  last_successful_sync TEXT,
  last_info_json TEXT NOT NULL DEFAULT '{}',
  last_capabilities_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Seeded once, on an empty registry, so the Sources view opens with the known
# connectors listed and switched off rather than empty. A deleted row stays
# deleted: seeding never runs again once the table holds anything.
BUILT_IN = (
    {
        "connector_id": "reference",
        "name": "Reference connector",
        "kind": "source",
        "base_url": "http://local-life-atlas-reference-connector:8098",
        "notes": "Deterministic connector used to prove packaging, networking and protocol compatibility.",
    },
    {
        "connector_id": "whatsapp_archive",
        "name": "WhatsApp archive",
        "kind": "source",
        "base_url": "http://local-life-atlas-whatsapp-archive:8097",
        "notes": "Read-only WhatsApp ingress. Paste its connector key from the add-on's own page.",
    },
    {
        "connector_id": "chatgpt_bridge",
        "name": "ChatGPT bridge",
        "kind": "consumer",
        "base_url": "",
        "notes": "Desktop MCP bridge. Reads the curated record through the agent API on port 8096.",
    },
)


class RegistryError(ValueError):
    """A registration was rejected. Connector *failures* are status, not errors."""


def registry_path(data_dir: Path) -> Path:
    return Path(data_dir) / REGISTRY_FILENAME


def connect(data_dir: Path) -> sqlite3.Connection:
    con = sqlite3.connect(registry_path(data_dir))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def initialise(data_dir: Path) -> None:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    with closing(connect(data_dir)) as con, con:
        con.executescript(SCHEMA)
        if con.execute("SELECT COUNT(*) FROM connectors").fetchone()[0]:
            return
        for entry in BUILT_IN:
            base_url = entry["base_url"]
            if entry["connector_id"] == "reference":
                base_url = os.environ.get("LIFE_ATLAS_REFERENCE_CONNECTOR_URL", base_url)
            con.execute(
                "INSERT INTO connectors(connector_id,name,kind,base_url,notes,enabled) VALUES(?,?,?,?,?,0)",
                (entry["connector_id"], entry["name"], entry["kind"], base_url.rstrip("/"), entry["notes"]),
            )


def list_connectors(data_dir: Path) -> list[dict[str, Any]]:
    with closing(connect(data_dir)) as con:
        rows = con.execute("SELECT * FROM connectors ORDER BY name COLLATE NOCASE").fetchall()
    return [_public(row) for row in rows]


def get_connector(data_dir: Path, connector_id: str) -> dict[str, Any]:
    return _public(_row(data_dir, connector_id))


def save_connector(data_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Register a new connector or update an existing one.

    An omitted key leaves the stored key untouched, so the UI can save a row it
    only ever received redacted. An empty string clears it deliberately.
    """
    connector_id = _identifier(payload.get("connector_id"))
    kind = str(payload.get("kind") or "source").strip()
    if kind not in KINDS:
        raise RegistryError(f"Connector kind must be one of: {', '.join(KINDS)}")
    name = str(payload.get("name") or connector_id).strip() or connector_id
    base_url = str(payload.get("base_url") or "").strip().rstrip("/")
    notes = str(payload.get("notes") or "").strip()
    enabled = 1 if _flag(payload.get("enabled"), default=False) else 0

    if kind in INBOUND_KINDS and not base_url:
        raise RegistryError("A connector that feeds Life Atlas needs a Connector Protocol address")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise RegistryError("Connector address must be an http:// or https:// URL")

    with closing(connect(data_dir)) as con, con:
        existing = con.execute(
            "SELECT auth_key FROM connectors WHERE connector_id=?", (connector_id,)
        ).fetchone()
        if "auth_key" in payload:
            auth_key = str(payload.get("auth_key") or "")
        else:
            auth_key = existing["auth_key"] if existing else ""
        con.execute(
            """
            INSERT INTO connectors(connector_id,name,kind,base_url,auth_key,enabled,notes)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(connector_id) DO UPDATE SET
              name=excluded.name, kind=excluded.kind, base_url=excluded.base_url,
              auth_key=excluded.auth_key, enabled=excluded.enabled, notes=excluded.notes,
              updated_at=CURRENT_TIMESTAMP
            """,
            (connector_id, name, kind, base_url, auth_key, enabled, notes),
        )
        # Address, credential or kind may all have changed; the cached status
        # describes the old configuration and must not be presented as current.
        con.execute(
            "UPDATE connectors SET last_state='unknown', last_error='', last_checked_at=NULL,"
            " last_info_json='{}', last_capabilities_json='[]' WHERE connector_id=?",
            (connector_id,),
        )
    return get_connector(data_dir, connector_id)


def set_enabled(data_dir: Path, connector_id: str, enabled: bool) -> dict[str, Any]:
    _row(data_dir, connector_id)
    with closing(connect(data_dir)) as con, con:
        con.execute(
            "UPDATE connectors SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE connector_id=?",
            (1 if enabled else 0, connector_id),
        )
        if not enabled:
            con.execute(
                "UPDATE connectors SET last_state='disabled', last_error='', last_checked_at=NULL"
                " WHERE connector_id=?",
                (connector_id,),
            )
    return get_connector(data_dir, connector_id)


def delete_connector(data_dir: Path, connector_id: str) -> dict[str, Any]:
    _row(data_dir, connector_id)
    with closing(connect(data_dir)) as con, con:
        con.execute("DELETE FROM connectors WHERE connector_id=?", (connector_id,))
    return {"connector_id": connector_id, "deleted": True}


def probe(data_dir: Path, connector_id: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Ask one connector how it is, and cache the answer.

    A connector that is missing, unauthenticated, incompatible or simply broken
    resolves to a state on its own row. It never raises into the request that
    asked, because no connector may take Life Atlas down with it.
    """
    row = _row(data_dir, connector_id)
    observed = _observe(row, timeout=timeout)
    _record(data_dir, connector_id, observed)
    return get_connector(data_dir, connector_id)


def probe_all(data_dir: Path, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    for entry in list_connectors(data_dir):
        probe(data_dir, entry["connector_id"], timeout=timeout)
    return list_connectors(data_dir)


def search(data_dir: Path, connector_id: str, query: str, *, limit: int = MAX_SEARCH_PREVIEW,
           timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Look into one connector's archive without promoting anything.

    This is inspection only. Nothing returned here is written to the canonical
    database; promotion stays an explicit, separate act.
    """
    row = _row(data_dir, connector_id)
    if not row["enabled"]:
        raise RegistryError("This connector is switched off")
    if row["kind"] not in INBOUND_KINDS:
        raise RegistryError("This connector does not feed Life Atlas, so it cannot be searched")
    if not isinstance(query, str) or not query.strip():
        raise RegistryError("Search needs a query")
    try:
        page = _client(row, timeout=timeout).search(query.strip(), limit=max(1, min(int(limit), MAX_SEARCH_PREVIEW)))
    except ConnectorError as exc:
        return {"connector_id": connector_id, "error": str(exc), "error_type": type(exc).__name__, "items": []}
    return {
        "connector_id": connector_id,
        "query": query.strip(),
        "items": [_item_json(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


def client_for(data_dir: Path, connector_id: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> ConnectorClient:
    """Return a live client for a registered, switched-on inbound connector.

    A connector's address and key belong to its registration, not to code. This
    is the only way core features should reach one, so that switching a
    connector off in the Sources view genuinely stops Life Atlas calling it.
    """
    row = _row(data_dir, connector_id)
    if not row["enabled"]:
        raise ConnectorUnavailable(f"The {row['name']} connector is switched off")
    if row["kind"] not in INBOUND_KINDS:
        raise ConnectorUnavailable(f"The {row['name']} connector does not feed Life Atlas")
    if not row["base_url"]:
        raise ConnectorUnavailable(f"The {row['name']} connector has no address")
    return _client(row, timeout=timeout)


def _client(row: sqlite3.Row, *, timeout: float) -> ConnectorClient:
    # Life Atlas's credential *to* the connector, not a provider token. A
    # connector that publishes no port on the app network may still require it.
    key = str(row["auth_key"] or "").strip()
    return ConnectorClient(
        row["connector_id"],
        HTTPConnectorTransport(
            row["base_url"],
            timeout=timeout,
            headers={"X-Life-Atlas-Connector-Key": key} if key else None,
        ),
    )


def _observe(row: sqlite3.Row, *, timeout: float) -> dict[str, Any]:
    if not row["enabled"]:
        return {"state": "disabled", "error": ""}
    if row["kind"] not in INBOUND_KINDS:
        return _observe_outbound()

    started = perf_counter()
    try:
        client = _client(row, timeout=timeout)
        info = client.info()
        status = client.status()
        capabilities = client.capabilities()
    except ConnectorAuthRequired as exc:
        return {"state": "auth_required", "error": str(exc), "latency_ms": _ms(started)}
    except ConnectorError as exc:
        state = "incompatible" if type(exc).__name__ == "ConnectorProtocolError" else "unavailable"
        return {"state": state, "error": str(exc), "error_type": type(exc).__name__, "latency_ms": _ms(started)}
    return {
        "state": status.state.value,
        "error": status.error or "",
        "authenticated": status.authenticated,
        "last_attempted_sync": status.last_attempted_sync,
        "last_successful_sync": status.last_successful_sync,
        "latency_ms": _ms(started),
        "info": {
            "name": info.name,
            "protocol_version": info.protocol_version,
            "connector_version": info.connector_version,
            "upstream_name": info.upstream_name,
            "upstream_version": info.upstream_version,
        },
        "capabilities": sorted(capabilities.values),
    }


def _observe_outbound() -> dict[str, Any]:
    """A consumer is not probed; it calls in. Report whether it could.

    Life Atlas has no address for an outbound client and must not acquire one.
    The honest status is whether the credential it authenticates with exists.
    """
    if not os.environ.get("LIFE_ATLAS_AGENT_API_KEY", "").strip():
        return {
            "state": "auth_required",
            "error": "No agent API key is set. Add one in the add-on configuration before a client can connect.",
            "authenticated": False,
            "capabilities": [],
        }
    return {"state": "available", "error": "", "authenticated": True, "capabilities": ["agent_api"]}


def _record(data_dir: Path, connector_id: str, observed: dict[str, Any]) -> None:
    with closing(connect(data_dir)) as con, con:
        con.execute(
            """
            UPDATE connectors SET
              last_state=?, last_error=?, last_checked_at=?,
              last_attempted_sync=COALESCE(?, last_attempted_sync),
              last_successful_sync=COALESCE(?, last_successful_sync),
              last_info_json=?, last_capabilities_json=?
            WHERE connector_id=?
            """,
            (
                observed.get("state", "unknown"),
                observed.get("error") or "",
                _now(),
                observed.get("last_attempted_sync"),
                observed.get("last_successful_sync"),
                json.dumps(observed.get("info") or {}, separators=(",", ":")),
                json.dumps(observed.get("capabilities") or [], separators=(",", ":")),
                connector_id,
            ),
        )


def _public(row: sqlite3.Row) -> dict[str, Any]:
    """The wire shape. The connector key is never part of it."""
    kind = row["kind"]
    return {
        "connector_id": row["connector_id"],
        "name": row["name"],
        "kind": kind,
        "reads_into_life_atlas": kind in INBOUND_KINDS,
        "reads_from_life_atlas": kind in OUTBOUND_KINDS,
        "base_url": row["base_url"],
        "has_key": bool(row["auth_key"]),
        "enabled": bool(row["enabled"]),
        "notes": row["notes"],
        "state": row["last_state"],
        "error": row["last_error"] or None,
        "last_checked_at": row["last_checked_at"],
        "last_attempted_sync": row["last_attempted_sync"],
        "last_successful_sync": row["last_successful_sync"],
        "info": _json_or(row["last_info_json"], {}),
        "capabilities": _json_or(row["last_capabilities_json"], []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row(data_dir: Path, connector_id: str) -> sqlite3.Row:
    with closing(connect(data_dir)) as con:
        row = con.execute(
            "SELECT * FROM connectors WHERE connector_id=?", (_identifier(connector_id),)
        ).fetchone()
    if row is None:
        raise RegistryError(f"Unknown connector: {connector_id}")
    return row


def _identifier(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise RegistryError("connector_id must not be empty")
    if len(text) > 64 or not all(character.isalnum() or character in "-_" for character in text):
        raise RegistryError("connector_id may use only letters, digits, hyphen and underscore")
    return text


def _flag(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _json_or(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _item_json(item: SourceItem) -> dict[str, Any]:
    return {
        "source_id": item.source_id,
        "item_type": item.item_type,
        "timestamp": item.timestamp,
        "lifecycle": item.lifecycle.value,
        "title": item.title,
        "text": item.text,
        "participants": [
            {"source_id": identity.source_id, "kind": identity.kind, "label": identity.label}
            for identity in item.participants
        ],
        "media_count": len(item.media),
    }


def _ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
