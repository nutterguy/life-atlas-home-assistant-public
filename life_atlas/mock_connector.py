from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class MockScenario:
    name: str = "healthy"


class MockConnectorService:
    """Deterministic reference connector used to exercise the Life Atlas boundary.

    It deliberately exposes source-neutral connector operations rather than HTTP
    routes. Step 4 will freeze the HTTP/JSON mapping separately.
    """

    connector_id = "reference"

    def __init__(self, scenario: str = "healthy") -> None:
        self.scenario = scenario
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def handle(self, operation: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        request = dict(params or {})
        self.calls.append((operation, request))

        if self.scenario == "offline":
            raise ConnectionError("reference connector offline")
        if self.scenario == "timeout":
            raise TimeoutError("reference connector timeout")
        if self.scenario == "crash":
            raise RuntimeError("reference connector crashed")

        if operation == "info":
            return self._info()
        if operation == "status":
            return self._status()
        if operation == "capabilities":
            return self._capabilities()
        if operation == "search":
            return self._search(request)
        raise ValueError(f"Unsupported mock operation: {operation}")

    def _info(self) -> Mapping[str, Any]:
        if self.scenario == "malformed_info":
            return {"connector_id": self.connector_id}
        if self.scenario == "wrong_identity":
            connector_id = "someone-else"
        else:
            connector_id = self.connector_id
        protocol_version = "2.0" if self.scenario == "future_protocol" else "1.0"
        return {
            "connector_id": connector_id,
            "name": "Life Atlas Reference Connector",
            "protocol_version": protocol_version,
            "connector_version": "0.1.0",
            "upstream_name": "deterministic-fixture",
            "upstream_version": "1",
        }

    def _status(self) -> Mapping[str, Any]:
        if self.scenario == "auth_required":
            return {
                "state": "auth_required",
                "authenticated": False,
                "error": "Authentication required",
            }
        if self.scenario == "degraded":
            return {
                "state": "degraded",
                "authenticated": True,
                "last_attempted_sync": "2026-08-24T12:00:00Z",
                "last_successful_sync": "2026-08-23T12:00:00Z",
                "error": "Fixture source partially unavailable",
            }
        return {
            "state": "available",
            "authenticated": True,
            "last_attempted_sync": "2026-08-24T12:00:00Z",
            "last_successful_sync": "2026-08-24T12:00:00Z",
            "error": None,
        }

    def _capabilities(self) -> Mapping[str, Any]:
        if self.scenario == "malformed_capabilities":
            return {"capabilities": "search"}
        values = ["search", "media", "identities", "locations"]
        if self.scenario == "no_search":
            values.remove("search")
        return {"capabilities": values}

    def _search(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.scenario == "malformed_search":
            return {"items": "not-a-list", "next_cursor": None}
        if self.scenario == "repeated_cursor":
            return {"items": [], "next_cursor": "loop"}

        query = str(params.get("query", "")).lower()
        limit = int(params.get("limit", 50))
        cursor = params.get("cursor")
        try:
            offset = int(cursor) if cursor is not None else 0
        except (TypeError, ValueError):
            offset = 0

        matching = [item for item in FIXTURE_ITEMS if query in self._search_text(item)]
        page = matching[offset : offset + limit]
        next_offset = offset + len(page)
        next_cursor = str(next_offset) if next_offset < len(matching) else None
        return {"items": page, "next_cursor": next_cursor}

    @staticmethod
    def _search_text(item: Mapping[str, Any]) -> str:
        participants = " ".join(str(p.get("label", "")) for p in item.get("participants", []))
        location = item.get("location") or {}
        return " ".join(
            [
                str(item.get("title", "")),
                str(item.get("text", "")),
                participants,
                str(location.get("name", "")),
                str(item.get("item_type", "")),
            ]
        ).lower()


class MockConnectorTransport:
    """Adapter matching connectors.ConnectorTransport without fixing wire format."""

    def __init__(self, service: MockConnectorService) -> None:
        self.service = service

    def request(self, operation: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        return self.service.handle(operation, params)


FIXTURE_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "source_id": "message-001",
        "native_id": "native-message-001",
        "item_type": "message",
        "timestamp": "2025-05-10T09:15:00Z",
        "modified_at": "2025-05-10T09:15:00Z",
        "lifecycle": "created",
        "text": "Flights booked for the island trip next month.",
        "participants": [
            {"source_id": "person-alex", "kind": "contact", "label": "Alex"},
            {"source_id": "person-owner", "kind": "owner", "label": "Owner"},
        ],
        "location": None,
        "media": [],
        "content_hash": "fixture-hash-message-001",
        "metadata": {"conversation_id": "chat-travel"},
    },
    {
        "source_id": "photo-001",
        "native_id": "native-photo-001",
        "item_type": "photo",
        "timestamp": "2025-06-14T18:30:00Z",
        "modified_at": "2025-06-14T18:30:00Z",
        "lifecycle": "created",
        "title": "Harbour sunset",
        "text": None,
        "participants": [],
        "location": {"name": "Fixture Harbour", "latitude": 51.0, "longitude": 0.1},
        "media": [
            {
                "source_id": "media-photo-001",
                "mime_type": "image/jpeg",
                "filename": "harbour.jpg",
                "size_bytes": 123456,
                "metadata": {"width": 2048, "height": 1365},
            }
        ],
        "content_hash": "fixture-hash-photo-001",
        "metadata": {"album": "Fixture Holiday"},
    },
    {
        "source_id": "journal-001",
        "native_id": "native-journal-001",
        "item_type": "journal_entry",
        "timestamp": "2025-06-15T21:00:00Z",
        "modified_at": "2025-06-15T21:00:00Z",
        "lifecycle": "updated",
        "title": "A good day by the harbour",
        "text": "Met Alex for dinner after a long walk along the harbour.",
        "participants": [{"source_id": "person-alex", "kind": "contact", "label": "Alex"}],
        "location": {"name": "Fixture Harbour"},
        "media": [],
        "content_hash": "fixture-hash-journal-001",
        "metadata": {"mood": "good"},
    },
    {
        "source_id": "message-002",
        "native_id": "native-message-002",
        "item_type": "message",
        "timestamp": "2025-06-16T08:00:00Z",
        "modified_at": "2025-06-16T08:05:00Z",
        "lifecycle": "updated",
        "text": "Alex confirmed dinner moved to Saturday.",
        "participants": [{"source_id": "person-alex", "kind": "contact", "label": "Alex"}],
        "location": None,
        "media": [],
        "content_hash": "fixture-hash-message-002-v2",
        "metadata": {"conversation_id": "chat-alex"},
    },
)
