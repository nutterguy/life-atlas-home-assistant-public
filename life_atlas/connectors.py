from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence


SUPPORTED_PROTOCOL_MAJOR = 1


class ConnectorError(RuntimeError):
    """Base class for connector failures that should not take Life Atlas down."""


class ConnectorUnavailable(ConnectorError):
    pass


class ConnectorTimeout(ConnectorError):
    pass


class ConnectorProtocolError(ConnectorError):
    pass


class UnsupportedCapability(ConnectorError):
    pass


class ConnectorState(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    AUTH_REQUIRED = "auth_required"
    INCOMPATIBLE = "incompatible"


class SourceLifecycle(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ConnectorCapabilities:
    values: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_value(cls, value: Any) -> "ConnectorCapabilities":
        if value is None:
            return cls()
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ConnectorProtocolError("Connector capabilities must be a list of strings")
        result: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ConnectorProtocolError("Connector capability names must be non-empty strings")
            result.add(item.strip())
        return cls(frozenset(result))

    def supports(self, capability: str) -> bool:
        return capability in self.values

    def require(self, capability: str) -> None:
        if not self.supports(capability):
            raise UnsupportedCapability(f"Connector does not support capability: {capability}")


@dataclass(frozen=True)
class ConnectorInfo:
    connector_id: str
    name: str
    protocol_version: str
    connector_version: str
    upstream_name: str | None = None
    upstream_version: str | None = None

    @property
    def protocol_major(self) -> int:
        return _protocol_major(self.protocol_version)


@dataclass(frozen=True)
class ConnectorStatus:
    connector_id: str
    state: ConnectorState
    authenticated: bool | None = None
    last_attempted_sync: str | None = None
    last_successful_sync: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class SourceIdentity:
    source_id: str
    kind: str | None = None
    label: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceMedia:
    source_id: str
    mime_type: str | None = None
    filename: str | None = None
    size_bytes: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceItem:
    source_id: str
    item_type: str
    timestamp: str | None = None
    modified_at: str | None = None
    lifecycle: SourceLifecycle = SourceLifecycle.CREATED
    title: str | None = None
    text: str | None = None
    participants: tuple[SourceIdentity, ...] = ()
    location: Mapping[str, Any] | None = None
    media: tuple[SourceMedia, ...] = ()
    native_id: str | None = None
    content_hash: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchPage:
    items: tuple[SourceItem, ...]
    next_cursor: str | None = None


class ConnectorTransport(Protocol):
    """Transport boundary used by Life Atlas core.

    Step 2 deliberately does not define HTTP paths or MCP tools. A later protocol
    step will adapt HTTP/JSON onto this small operation interface.
    """

    def request(self, operation: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        ...


class ConnectorClient:
    def __init__(
        self,
        connector_id: str,
        transport: ConnectorTransport,
        *,
        supported_protocol_major: int = SUPPORTED_PROTOCOL_MAJOR,
    ) -> None:
        if not connector_id.strip():
            raise ValueError("connector_id must not be empty")
        self.connector_id = connector_id
        self.transport = transport
        self.supported_protocol_major = supported_protocol_major
        self._info: ConnectorInfo | None = None
        self._capabilities: ConnectorCapabilities | None = None

    def info(self, *, refresh: bool = False) -> ConnectorInfo:
        if self._info is not None and not refresh:
            return self._info
        payload = self._request("info")
        info = _parse_info(payload)
        if info.connector_id != self.connector_id:
            raise ConnectorProtocolError(
                f"Connector identity mismatch: expected {self.connector_id}, got {info.connector_id}"
            )
        if info.protocol_major != self.supported_protocol_major:
            raise ConnectorProtocolError(
                f"Unsupported connector protocol {info.protocol_version}; "
                f"Life Atlas supports major version {self.supported_protocol_major}"
            )
        self._info = info
        return info

    def status(self) -> ConnectorStatus:
        self.info()
        return _parse_status(self.connector_id, self._request("status"))

    def capabilities(self, *, refresh: bool = False) -> ConnectorCapabilities:
        self.info()
        if self._capabilities is not None and not refresh:
            return self._capabilities
        payload = self._request("capabilities")
        capabilities = ConnectorCapabilities.from_value(payload.get("capabilities"))
        self._capabilities = capabilities
        return capabilities

    def search(self, query: str, *, cursor: str | None = None, limit: int = 50) -> SearchPage:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be empty")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        self.capabilities().require("search")
        params: dict[str, Any] = {"query": query, "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        payload = self._request("search", params)
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ConnectorProtocolError("Search response must contain an items list")
        items = tuple(_parse_source_item(item) for item in raw_items)
        next_cursor = payload.get("next_cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ConnectorProtocolError("next_cursor must be a string or null")
        return SearchPage(items=items, next_cursor=next_cursor)

    def iter_search(self, query: str, *, limit: int = 50, max_pages: int | None = None):
        cursor: str | None = None
        pages = 0
        seen_cursors: set[str] = set()
        while True:
            page = self.search(query, cursor=cursor, limit=limit)
            yield from page.items
            pages += 1
            if page.next_cursor is None:
                return
            if page.next_cursor in seen_cursors:
                raise ConnectorProtocolError("Connector returned a repeated pagination cursor")
            seen_cursors.add(page.next_cursor)
            if max_pages is not None and pages >= max_pages:
                return
            cursor = page.next_cursor

    def invalidate_cache(self) -> None:
        self._info = None
        self._capabilities = None

    def _request(self, operation: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        try:
            payload = self.transport.request(operation, params)
        except ConnectorError:
            raise
        except TimeoutError as exc:
            raise ConnectorTimeout(f"Connector timed out during {operation}") from exc
        except (ConnectionError, OSError) as exc:
            raise ConnectorUnavailable(f"Connector unavailable during {operation}") from exc
        except Exception as exc:
            raise ConnectorError(f"Connector failed during {operation}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ConnectorProtocolError(f"Connector {operation} response must be an object")
        return payload


class ConnectorRegistry:
    def __init__(self) -> None:
        self._clients: dict[str, ConnectorClient] = {}

    def register(self, client: ConnectorClient) -> None:
        if client.connector_id in self._clients:
            raise ValueError(f"Connector already registered: {client.connector_id}")
        self._clients[client.connector_id] = client

    def unregister(self, connector_id: str) -> ConnectorClient | None:
        return self._clients.pop(connector_id, None)

    def get(self, connector_id: str) -> ConnectorClient:
        try:
            return self._clients[connector_id]
        except KeyError as exc:
            raise KeyError(f"Unknown connector: {connector_id}") from exc

    def all(self) -> tuple[ConnectorClient, ...]:
        return tuple(self._clients[key] for key in sorted(self._clients))

    def statuses(self) -> dict[str, ConnectorStatus]:
        result: dict[str, ConnectorStatus] = {}
        for client in self.all():
            try:
                result[client.connector_id] = client.status()
            except ConnectorProtocolError as exc:
                result[client.connector_id] = ConnectorStatus(
                    connector_id=client.connector_id,
                    state=ConnectorState.INCOMPATIBLE,
                    error=str(exc),
                )
            except ConnectorUnavailable as exc:
                result[client.connector_id] = ConnectorStatus(
                    connector_id=client.connector_id,
                    state=ConnectorState.UNAVAILABLE,
                    error=str(exc),
                )
            except ConnectorError as exc:
                result[client.connector_id] = ConnectorStatus(
                    connector_id=client.connector_id,
                    state=ConnectorState.DEGRADED,
                    error=str(exc),
                )
        return result


def _protocol_major(version: str) -> int:
    if not isinstance(version, str) or not version.strip():
        raise ConnectorProtocolError("protocol_version must be a non-empty string")
    first = version.split(".", 1)[0]
    if not first.isdigit():
        raise ConnectorProtocolError(f"Invalid protocol_version: {version}")
    return int(first)


def _require_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConnectorProtocolError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConnectorProtocolError(f"{key} must be a string or null")
    return value


def _parse_info(payload: Mapping[str, Any]) -> ConnectorInfo:
    return ConnectorInfo(
        connector_id=_require_string(payload, "connector_id"),
        name=_require_string(payload, "name"),
        protocol_version=_require_string(payload, "protocol_version"),
        connector_version=_require_string(payload, "connector_version"),
        upstream_name=_optional_string(payload, "upstream_name"),
        upstream_version=_optional_string(payload, "upstream_version"),
    )


def _parse_status(connector_id: str, payload: Mapping[str, Any]) -> ConnectorStatus:
    raw_state = _require_string(payload, "state")
    try:
        state = ConnectorState(raw_state)
    except ValueError as exc:
        raise ConnectorProtocolError(f"Unknown connector state: {raw_state}") from exc
    authenticated = payload.get("authenticated")
    if authenticated is not None and not isinstance(authenticated, bool):
        raise ConnectorProtocolError("authenticated must be boolean or null")
    return ConnectorStatus(
        connector_id=connector_id,
        state=state,
        authenticated=authenticated,
        last_attempted_sync=_optional_string(payload, "last_attempted_sync"),
        last_successful_sync=_optional_string(payload, "last_successful_sync"),
        error=_optional_string(payload, "error"),
    )


def _parse_identity(value: Any) -> SourceIdentity:
    if not isinstance(value, Mapping):
        raise ConnectorProtocolError("Participant must be an object")
    metadata = value.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ConnectorProtocolError("Participant metadata must be an object")
    return SourceIdentity(
        source_id=_require_string(value, "source_id"),
        kind=_optional_string(value, "kind"),
        label=_optional_string(value, "label"),
        metadata=dict(metadata),
    )


def _parse_media(value: Any) -> SourceMedia:
    if not isinstance(value, Mapping):
        raise ConnectorProtocolError("Media item must be an object")
    metadata = value.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ConnectorProtocolError("Media metadata must be an object")
    size_bytes = value.get("size_bytes")
    if size_bytes is not None and (not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0):
        raise ConnectorProtocolError("Media size_bytes must be a non-negative integer or null")
    return SourceMedia(
        source_id=_require_string(value, "source_id"),
        mime_type=_optional_string(value, "mime_type"),
        filename=_optional_string(value, "filename"),
        size_bytes=size_bytes,
        metadata=dict(metadata),
    )


def _parse_source_item(value: Any) -> SourceItem:
    if not isinstance(value, Mapping):
        raise ConnectorProtocolError("Source item must be an object")
    lifecycle_value = value.get("lifecycle", SourceLifecycle.CREATED.value)
    try:
        lifecycle = SourceLifecycle(lifecycle_value)
    except (TypeError, ValueError) as exc:
        raise ConnectorProtocolError(f"Unknown source lifecycle: {lifecycle_value}") from exc
    participants = value.get("participants", [])
    media = value.get("media", [])
    metadata = value.get("metadata", {})
    location = value.get("location")
    if not isinstance(participants, list):
        raise ConnectorProtocolError("participants must be a list")
    if not isinstance(media, list):
        raise ConnectorProtocolError("media must be a list")
    if not isinstance(metadata, Mapping):
        raise ConnectorProtocolError("metadata must be an object")
    if location is not None and not isinstance(location, Mapping):
        raise ConnectorProtocolError("location must be an object or null")
    return SourceItem(
        source_id=_require_string(value, "source_id"),
        item_type=_require_string(value, "item_type"),
        timestamp=_optional_string(value, "timestamp"),
        modified_at=_optional_string(value, "modified_at"),
        lifecycle=lifecycle,
        title=_optional_string(value, "title"),
        text=_optional_string(value, "text"),
        participants=tuple(_parse_identity(item) for item in participants),
        location=dict(location) if location is not None else None,
        media=tuple(_parse_media(item) for item in media),
        native_id=_optional_string(value, "native_id"),
        content_hash=_optional_string(value, "content_hash"),
        metadata=dict(metadata),
    )
