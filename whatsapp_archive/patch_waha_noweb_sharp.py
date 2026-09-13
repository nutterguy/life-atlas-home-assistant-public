from __future__ import annotations

import json
import sys
from pathlib import Path


EXPECTED_VERSION = "0.35.3"


def patch(module_path: Path, package_path: Path) -> None:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    if package.get("version") != EXPECTED_VERSION:
        raise SystemExit(
            "Refusing WAHA image: unexpected nested Sharp version "
            f"{package.get('version')!r}"
        )

    source = module_path.read_text(encoding="utf-8")
    marker = 'if (!err.code.endsWith("MODULE_NOT_FOUND"))'
    if source.count(marker) != 1:
        raise SystemExit("Refusing WAHA image: expected Sharp loader marker was not found once")

    # WAHA NOWEB does not use WPPConnect's WEBJS-only image conversion paths.
    # Sharp 0.35 requires x86-64-v2 and crashes while the unused WEBJS engine is
    # imported on older Home Assistant CPUs. Keep that optional path fail-closed
    # while allowing the selected NOWEB engine to start.
    shim_path = module_path.with_name("life-atlas-noweb.cjs")
    shim_path.write_text(
        "'use strict';\n"
        "const unavailable = () => {\n"
        "  throw new Error('WPPConnect Sharp is disabled in the Life Atlas NOWEB image');\n"
        "};\n"
        "module.exports = unavailable;\n",
        encoding="utf-8",
    )
    shim_entry = "./dist/life-atlas-noweb.cjs"
    try:
        package["main"] = shim_entry
        package["exports"]["."]["require"]["default"] = shim_entry
    except (KeyError, TypeError) as exc:
        raise SystemExit("Refusing WAHA image: unexpected Sharp export map") from exc
    package_path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: patch_waha_noweb_sharp.py sharp.cjs package.json")
    patch(Path(sys.argv[1]), Path(sys.argv[2]))
