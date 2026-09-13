from __future__ import annotations

import os
from urllib.parse import urlparse

import app as life_atlas

APP_VERSION = os.environ.get("LIFE_ATLAS_VERSION", "0.6.1")
# Reloading this module must not wrap an already-wrapped handler: that chains
# runtime_do_get to itself and every non-health GET recurses until it dies.
_ORIGINAL_DO_GET = getattr(life_atlas.Handler.do_GET, "life_atlas_original", life_atlas.Handler.do_GET)


def runtime_do_get(handler) -> None:
    """Only health is answered here.

    Connectors used to be a single hard-coded diagnostic route at this layer.
    They are now registered records served by the application itself, so that a
    connector can be added, addressed, switched off and inspected without a code
    change. Nothing connector-specific belongs in the runtime wrapper.
    """
    if urlparse(handler.path).path == "/api/health":
        return handler.send_json({"status": "ok", "version": APP_VERSION})
    return _ORIGINAL_DO_GET(handler)


runtime_do_get.life_atlas_original = _ORIGINAL_DO_GET
life_atlas.Handler.do_GET = runtime_do_get


if __name__ == "__main__":
    life_atlas.run()
