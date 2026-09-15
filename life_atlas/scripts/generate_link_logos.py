"""Regenerate static/link-logos.js, the brand marks for external event links.

An event page shows a chip for every external link it carries, so that a Strava
activity or a photo album is obvious from the top of the page rather than only
from the list at the bottom. The marks are bundled, not fetched, because the
add-on has to keep working with no internet connection.

Source: Simple Icons (https://simpleicons.org), CC0 1.0. Each icon is a single
24x24 path plus the brand's own colour, which is what makes a chip readable at a
glance. Brand marks remain the trademarks of their respective owners and are
used here only to point at that company's own page.

Usage: python scripts/generate_link_logos.py
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "static" / "link-logos.js"
ICON = "https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/{slug}.svg"
COLOURS = "https://cdn.jsdelivr.net/npm/simple-icons@latest/_data/simple-icons.json"

# Matched against "host/path" of the link, lowercased and without any "www.",
# first match wins. Order matters: the Google properties share a domain, so the
# specific subdomains have to be tested before anything shorter.
PATTERNS: list[tuple[str, str]] = [
    ("photos.google.com", "googlephotos"),
    ("photos.app.goo.gl", "googlephotos"),
    ("drive.google.com", "googledrive"),
    ("docs.google.com", "googledocs"),
    ("calendar.google.com", "googlecalendar"),
    ("maps.google.", "googlemaps"),
    ("maps.app.goo.gl", "googlemaps"),
    ("google.com/maps", "googlemaps"),
    ("goo.gl/maps", "googlemaps"),
    ("strava.com", "strava"),
    ("connect.garmin.com", "garmin"),
    ("garmin.com", "garmin"),
    ("komoot.", "komoot"),
    ("runkeeper.com", "runkeeper"),
    ("justgiving.com", "justgiving"),
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("vimeo.com", "vimeo"),
    ("flickr.com", "flickr"),
    ("icloud.com", "icloud"),
    ("dropbox.com", "dropbox"),
    ("open.spotify.com", "spotify"),
    ("spotify.com", "spotify"),
    ("music.apple.com", "applemusic"),
    ("soundcloud.com", "soundcloud"),
    ("bandcamp.com", "bandcamp"),
    ("last.fm", "lastdotfm"),
    ("songkick.com", "songkick"),
    ("ticketmaster.", "ticketmaster"),
    ("eventbrite.", "eventbrite"),
    ("meetup.com", "meetup"),
    ("airbnb.", "airbnb"),
    ("tripadvisor.", "tripadvisor"),
    ("expedia.", "expedia"),
    ("ryanair.com", "ryanair"),
    ("easyjet.com", "easyjet"),
    ("britishairways.com", "britishairways"),
    ("instagram.com", "instagram"),
    ("facebook.com", "facebook"),
    ("fb.com", "facebook"),
    ("twitter.com", "x"),
    ("x.com", "x"),
    ("bsky.app", "bluesky"),
    ("threads.net", "threads"),
    ("reddit.com", "reddit"),
    ("whatsapp.com", "whatsapp"),
    ("wa.me", "whatsapp"),
    ("t.me", "telegram"),
    ("telegram.", "telegram"),
    ("twitch.tv", "twitch"),
    ("pinterest.", "pinterest"),
    ("notion.so", "notion"),
    ("notion.site", "notion"),
    ("medium.com", "medium"),
    ("substack.com", "substack"),
    ("wikipedia.org", "wikipedia"),
    ("goodreads.com", "goodreads"),
    ("imdb.com", "imdb"),
    ("github.com", "github"),
]

# Simple Icons has removed the Booking.com, LinkedIn and OneDrive marks, so links
# to those sites fall back to the generic chip rather than ship a look-alike.

# A dark brand mark disappears against the interface, which is dark too. Lift any
# colour below this relative luminance until it reads, keeping its hue.
MIN_LUMINANCE = 0.34


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=40) as response:
        return response.read().decode("utf-8")


def title_to_slug(title: str) -> str:
    """Simple Icons' own rule, so a title can be matched back to a slug."""
    lowered = (
        unicodedata.normalize("NFD", title.lower())
        .replace("+", "plus")
        .replace(".", "dot")
        .replace("&", "and")
        .replace("đ", "d")
        .replace("ħ", "h")
        .replace("ı", "i")
        .replace("ĸ", "k")
        .replace("ŀ", "l")
        .replace("ł", "l")
        .replace("ß", "ss")
        .replace("ŧ", "t")
    )
    return re.sub(r"[^a-z0-9]", "", lowered)


def brand_colours() -> dict[str, str]:
    """slug -> brand hex. Only a handful of icons still ship as JS modules, but
    every one of them ships as an SVG with no colour in it, so the colour has to
    come from the package's data file instead."""
    colours = {}
    for entry in json.loads(fetch(COLOURS)):
        slug = entry.get("slug") or title_to_slug(entry["title"])
        colours[slug] = entry["hex"].upper()
    return colours


def icon_path(slug: str) -> str:
    svg = fetch(ICON.format(slug=slug))
    path = re.search(r'\sd="([^"]+)"', svg)
    if not path:
        raise SystemExit(f"simple-icons no longer describes {slug} as expected")
    return path.group(1)


def readable(hexcode: str) -> str:
    r, g, b = (int(hexcode[i:i + 2], 16) / 255 for i in (0, 2, 4))
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if luminance >= MIN_LUMINANCE:
        return f"#{hexcode}"
    if max(r, g, b) == 0:
        return "#C9D5F2"
    lift = MIN_LUMINANCE / max(luminance, 0.02)
    r, g, b = (min(1.0, channel * lift) for channel in (r, g, b))
    return "#%02X%02X%02X" % tuple(round(channel * 255) for channel in (r, g, b))


def main() -> None:
    slugs = sorted({slug for _, slug in PATTERNS})
    colours = brand_colours()
    missing = [slug for slug in slugs if slug not in colours]
    if missing:
        raise SystemExit(f"simple-icons has no brand colour for: {missing}")
    marks = {
        slug: {"d": icon_path(slug), "c": readable(colours[slug])} for slug in slugs
    }
    print(f"Fetched {len(marks)} brand marks")

    body = [
        "// Generated by scripts/generate_link_logos.py. Do not edit by hand.",
        "//",
        "// Brand marks for the external links on an event page, bundled so the",
        "// add-on keeps them with no internet connection. Source: Simple Icons",
        "// (https://simpleicons.org), CC0 1.0. Each mark remains the trademark of",
        "// its owner and is shown only as a pointer to that owner's own page.",
        "//",
        "// Colours are the brands' own, lifted where a dark mark would otherwise",
        "// vanish against the interface.",
        "const LINK_LOGO={",
    ]
    body += [
        f"  {slug}:{{d:{json.dumps(mark['d'])},c:{json.dumps(mark['c'])}}},"
        for slug, mark in sorted(marks.items())
    ]
    body.append("};")
    body.append("// [match, mark] against \"host/path\"; first match wins, so the")
    body.append("// specific Google subdomains have to come before anything shorter.")
    body.append("const LINK_LOGO_PATTERNS=[")
    body += [f"  [{json.dumps(pattern)},{json.dumps(slug)}]," for pattern, slug in PATTERNS]
    body.append("];")
    TARGET.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
