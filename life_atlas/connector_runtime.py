from __future__ import annotations

import os
from dataclasses import asdict
from time import perf_counter
from typing import Any

from connector_http import HTTPConnectorTransport
from connectors import ConnectorClient, ConnectorError, SourceItem

REFERENCE_CONNECTOR_ID = "reference"
DEFAULT_REFERENCE_CONNECTOR_URL = "http://local-life-atlas-reference-connector:8098"
DEFAULT_CONNECTOR_TIMEOUT_SECONDS = 2.0


def reference_connector_url() -> str:
    return os.environ.get("LIFE_ATLAS_REFERENCE_CONNECTOR_URL", DEFAULT_REFERENCE_CONNECTOR_URL).rstrip("/")


def make_reference_client(*, base_url: str | None = None, timeout: float = DEFAULT_CONNECTOR_TIMEOUT_SECONDS) -> ConnectorClient:
    return ConnectorClient(
        REFERENCE_CONNECTOR_ID,
        HTTPConnectorTransport(base_url or reference_connector_url(), timeout=timeout),
    )


def reference_connector_diagnostic(
    *,
    query: str | None = None,
    base_url: str | None = None,
    timeout: float = DEFAULT_CONNECTOR_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return a read-only, source-neutral diagnostic for the reference connector.

    Connector failures are deliberately contained and returned as connector state;
    they must never make the Life Atlas application unavailable.
    """
    started = perf_counter()
    client = make_reference_client(base_url=base_url, timeout=timeout)
    try:
        info = client.info()
        status = client.status()
        capabilities = client.capabilities()
        result: dict[str, Any] = {
            "connector_id": info.connector_id,
            "name": info.name,
            "available": status.state.value in {"available", "degraded"},
            "state": status.state.value,
            "authenticated": status.authenticated,
            "protocol_version": info.protocol_version,
            "connector_version": info.connector_version,
            "upstream_name": info.upstream_name,
            "upstream_version": info.upstream_version,
            "capabilities": sorted(capabilities.values),
            "last_attempted_sync": status.last_attempted_sync,
            "last_successful_sync": status.last_successful_sync,
            "error": status.error,
        }
        if query and query.strip():
            page = client.search(query.strip(), limit=10)
            result["search"] = {
                "query": query.strip(),
                "items": [_source_item_json(item) for item in page.items],
                "next_cursor": page.next_cursor,
            }
        result["latency_ms"] = round((perf_counter() - started) * 1000, 1)
        return result
    except ConnectorError as exc:
        return {
            "connector_id": REFERENCE_CONNECTOR_ID,
            "available": False,
            "state": "unavailable",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "latency_ms": round((perf_counter() - started) * 1000, 1),
        }


def _source_item_json(item: SourceItem) -> dict[str, Any]:
    payload = asdict(item)
    payload["lifecycle"] = item.lifecycle.value
    return payload
