from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import app as life_atlas
from connector_runtime import reference_connector_diagnostic

APP_VERSION = os.environ.get("LIFE_ATLAS_VERSION", "0.6.1")
_ORIGINAL_DO_GET = life_atlas.Handler.do_GET


def runtime_do_get(handler) -> None:
    parsed = urlparse(handler.path)
    route = parsed.path
    if route == "/api/health":
        return handler.send_json({"status": "ok", "version": APP_VERSION})
    if route == "/api/connectors/reference":
        query = parse_qs(parsed.query).get("q", [None])[0]
        return handler.send_json(reference_connector_diagnostic(query=query))
    return _ORIGINAL_DO_GET(handler)


life_atlas.Handler.do_GET = runtime_do_get


if __name__ == "__main__":
    life_atlas.run()
