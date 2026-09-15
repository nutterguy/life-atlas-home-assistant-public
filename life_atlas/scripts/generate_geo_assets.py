"""Regenerate static/geo-assets.js, the bundled country flags and outlines.

Life Atlas ships as a Home Assistant add-on that must keep working with no
internet connection, so these graphics are bundled rather than fetched from a
CDN at runtime. This script is the only thing that talks to the network, and it
is run by hand when the country list changes -- never by the add-on.

Sources:
  Flags    Twemoji (https://github.com/jdecked/twemoji), CC-BY 4.0. Chosen over
           print-quality flag sets because these are drawn for small display:
           all 39 flags together are a tenth of the size and stay legible at the
           15px the interface actually renders them at.
  Outlines Natural Earth 1:50m via world-atlas, public domain. The 1:10m set is
           needlessly precise for a watermark and the 1:110m set omits small
           countries such as Andorra and Malta.

Usage: python scripts/generate_geo_assets.py
"""

from __future__ import annotations

import json
import math
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"
TARGET = ROOT / "static" / "geo-assets.js"

TWEMOJI = "https://raw.githubusercontent.com/jdecked/twemoji/main/assets/svg/{name}.svg"
WORLD_ATLAS = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json"

# Natural Earth names that differ from the country names Life Atlas stores.
DATASET_NAMES = {
    "CZ": "Czechia",
    "GB": "United Kingdom",
    "US": "United States of America",
    "VN": "Vietnam",
    "TR": "Turkey",
}

# An outline is a watermark, not a map: distant overseas territory only pushes
# the recognisable landmass into a corner. The Azores shrink mainland Portugal to
# a speck, Svalbard does the same to Norway, and Alaska to the United States.
#
# Measuring from the country's largest polygon alone is not enough either, or an
# archipelago loses most of itself: Indonesia's largest single island is nowhere
# near Sumatra. So the kept set is grown outwards instead -- start at the largest
# polygon and keep absorbing anything that lands within this margin of the region
# kept so far. Neighbouring islands chain in, genuinely remote ones never do.
CLUSTER_MARGIN_DEGREES = 3.0
# Polygons smaller than this share of the largest one are specks at card size.
MIN_AREA_SHARE = 0.004
OUTLINE_WIDTH = 100.0
SIMPLIFY_TOLERANCE = OUTLINE_WIDTH / 500.0


def country_codes() -> dict[str, str]:
    """The COUNTRY_CODES table in app.js is the single source of truth."""
    source = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"const COUNTRY_CODES=(\{.*?\});", source, re.S)
    if not match:
        raise SystemExit("app.js no longer declares COUNTRY_CODES")
    table = json.loads(match.group(1).replace("'", '"'))
    return {code: name for name, code in sorted(table.items())}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=90) as response:
        return response.read()


def flag_svg(code: str) -> str:
    name = "-".join("%x" % (0x1F1E6 + ord(ch) - 65) for ch in code)
    svg = fetch(TWEMOJI.format(name=name)).decode("utf-8")
    svg = re.sub(r"<\?xml.*?\?>", "", svg, flags=re.S)
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    svg = re.sub(r"\s*\n\s*", "", svg).strip()
    # The interface sizes the flag with CSS, so a fixed width/height would fight it.
    svg = re.sub(r'\s(?:width|height)="[^"]*"', "", svg, count=2)
    return svg


def decode_arcs(topology: dict) -> list[list[tuple[float, float]]]:
    scale = topology["transform"]["scale"]
    translate = topology["transform"]["translate"]
    decoded = []
    for arc in topology["arcs"]:
        x = y = 0
        points = []
        for dx, dy in arc:
            x += dx
            y += dy
            points.append((x * scale[0] + translate[0], y * scale[1] + translate[1]))
        decoded.append(points)
    return decoded


def ring_points(arc_indices: list[int], arcs: list[list[tuple[float, float]]]):
    points: list[tuple[float, float]] = []
    for index in arc_indices:
        arc = arcs[~index][::-1] if index < 0 else arcs[index]
        points.extend(arc[1:] if points else arc)
    return points


def polygons(geometry: dict, arcs) -> list[list[tuple[float, float]]]:
    """Outer rings only -- a hole is invisible in a flat watermark."""
    if geometry["type"] == "Polygon":
        return [ring_points(geometry["arcs"][0], arcs)]
    if geometry["type"] == "MultiPolygon":
        return [ring_points(polygon[0], arcs) for polygon in geometry["arcs"]]
    return []


def ring_area(ring) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def bounds(ring) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def centre(ring) -> tuple[float, float]:
    x0, y0, x1, y1 = bounds(ring)
    return (x0 + x1) / 2, (y0 + y1) / 2


def mainland(rings: list) -> list:
    """Grow outwards from the largest polygon; see CLUSTER_MARGIN_DEGREES."""
    largest = ring_area(rings[0])
    candidates = [ring for ring in rings[1:] if ring_area(ring) >= largest * MIN_AREA_SHARE]
    kept = [rings[0]]
    x0, y0, x1, y1 = bounds(rings[0])
    absorbing = True
    while absorbing:
        absorbing = False
        for ring in list(candidates):
            cx, cy = centre(ring)
            if (
                x0 - CLUSTER_MARGIN_DEGREES <= cx <= x1 + CLUSTER_MARGIN_DEGREES
                and y0 - CLUSTER_MARGIN_DEGREES <= cy <= y1 + CLUSTER_MARGIN_DEGREES
            ):
                candidates.remove(ring)
                kept.append(ring)
                rx0, ry0, rx1, ry1 = bounds(ring)
                x0, y0 = min(x0, rx0), min(y0, ry0)
                x1, y1 = max(x1, rx1), max(y1, ry1)
                absorbing = True
    return kept


def mercator_y(lat: float) -> float:
    lat = max(min(lat, 84.0), -84.0)
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def simplify(points, tolerance):
    """Douglas-Peucker, iterative so a long coastline cannot blow the stack."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        ax, ay = points[start]
        bx, by = points[end]
        dx, dy = bx - ax, by - ay
        span = math.hypot(dx, dy)
        worst = tolerance
        index = -1
        for i in range(start + 1, end):
            px, py = points[i]
            if span == 0:
                distance = math.hypot(px - ax, py - ay)
            else:
                distance = abs(dy * px - dx * py + bx * ay - by * ax) / span
            if distance > worst:
                worst, index = distance, i
        if index != -1:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return [point for point, keeper in zip(points, keep) if keeper]


def outline_path(geometry: dict, arcs) -> dict | None:
    rings = [ring for ring in polygons(geometry, arcs) if len(ring) >= 4]
    if not rings:
        return None
    rings.sort(key=ring_area, reverse=True)
    kept = mainland(rings)
    projected = [[(x, -mercator_y(y)) for x, y in ring] for ring in kept]
    xs = [x for ring in projected for x, _ in ring]
    ys = [y for ring in projected for _, y in ring]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    if width <= 0 or height <= 0:
        return None
    scale = OUTLINE_WIDTH / max(width, height)
    view_w = round(width * scale, 2)
    view_h = round(height * scale, 2)
    commands = []
    for ring in projected:
        placed = [((x - min(xs)) * scale, (y - min(ys)) * scale) for x, y in ring]
        placed = simplify(placed, SIMPLIFY_TOLERANCE)
        if len(placed) < 3:
            continue
        points = " ".join(
            f"{'M' if i == 0 else 'L'}{round(x, 1):g} {round(y, 1):g}"
            for i, (x, y) in enumerate(placed)
        )
        commands.append(points + "Z")
    if not commands:
        return None
    return {"box": f"0 0 {view_w:g} {view_h:g}", "d": "".join(commands)}


def main() -> None:
    codes = country_codes()
    print(f"Building assets for {len(codes)} countries")

    flags = {code: flag_svg(code) for code in codes}
    print(f"  flags   {sum(len(v) for v in flags.values()):,} bytes")

    topology = json.loads(fetch(WORLD_ATLAS))
    arcs = decode_arcs(topology)
    by_name = {
        geometry["properties"]["name"]: geometry
        for geometry in topology["objects"]["countries"]["geometries"]
    }
    outlines: dict[str, dict] = {}
    for code, name in codes.items():
        geometry = by_name.get(DATASET_NAMES.get(code, name))
        if geometry is None:
            print(f"  ! no boundary for {name} ({code}); the card keeps its pin")
            continue
        path = outline_path(geometry, arcs)
        if path:
            outlines[code] = path
    print(f"  outlines {sum(len(v['d']) for v in outlines.values()):,} bytes"
          f" for {len(outlines)} countries")

    body = [
        "// Generated by scripts/generate_geo_assets.py. Do not edit by hand.",
        "//",
        "// Bundled so that Life Atlas keeps its flags and place-card maps with no",
        "// internet connection. Flags are Twemoji (CC-BY 4.0, Twitter and",
        "// contributors); country outlines are derived from Natural Earth 1:50m",
        "// boundaries, which are public domain.",
        "//",
        "// An outline is a decorative watermark and is deliberately simplified; it",
        "// is never a statement about a border.",
        "const COUNTRY_FLAG_SVG={",
    ]
    body += [f"  {code}:{json.dumps(svg)}," for code, svg in sorted(flags.items())]
    body.append("};")
    body.append("const COUNTRY_OUTLINE={")
    body += [
        f"  {code}:{{box:{json.dumps(v['box'])},d:{json.dumps(v['d'])}}},"
        for code, v in sorted(outlines.items())
    ]
    body.append("};")
    TARGET.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
