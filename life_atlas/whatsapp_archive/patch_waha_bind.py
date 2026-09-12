from __future__ import annotations

import re
import sys
from pathlib import Path


def patch(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    pattern = re.compile(r"await\s+app\.listen\(config\.port\)\s*;")
    updated, count = pattern.subn(
        "await app.listen(config.port, '127.0.0.1');", source
    )
    if count != 1:
        raise SystemExit(
            f"Refusing WAHA image: expected one unbound app.listen call, found {count}"
        )
    if "app.listen(config.port, '127.0.0.1')" not in updated:
        raise SystemExit("WAHA loopback bind verification failed")
    path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch_waha_bind.py /app/dist/main.js")
    patch(Path(sys.argv[1]))


