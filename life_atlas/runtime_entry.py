from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import app as life_atlas

def _declared_version() -> str:
    """The add-on version, read from config.yaml when run.sh has not exported it.

    A hard-coded fallback silently goes stale: this read "0.6.1" while the
    add-on shipped 0.18.x, so anything started outside run.sh misreported which
    build it was, and that version is what /api/health is trusted to prove
    after a deployment.
    """
    declared = os.environ.get("LIFE_ATLAS_VERSION")
    if declared:
        return declared
    config = Path(__file__).resolve().parent / "config.yaml"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "unknown"


APP_VERSION = _declared_version()
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
