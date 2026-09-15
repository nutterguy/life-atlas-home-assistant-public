# Product and interface design

Life Atlas is a private, evidence-aware personal timeline presented inside Home Assistant without becoming a home-automation integration.

Principles: timeline first; provenance and confidence remain visible; uncertain records enter review; navigation is predictable; the same information works on desktop, tablet, and phone; Home Assistant owns authentication; and application data stays portable.

The year timeline opens in **Highlights** mode, showing major and medium events while keeping routine minor activity available through **All events**. **Major only** remains available for the most selective view. Month and diary views stay complete so hiding minor events never makes them undiscoverable.

Primary views are overview, timeline, diary/calendar, years, map/places, people, trips, detective review, and statistics. Entity details connect events with evidence, sources, people, places, trips, media metadata, and external links.

Home Assistant-specific concerns stop at packaging, persistence, and Ingress. The data model and core workflows remain compatible with the Windows edition.

## Bundled graphics

Three sets of graphics ship inside the app rather than loading from a CDN, so the
interface looks the same offline as online and no third party learns which page
is open. Each set is generated, never hand-edited, and each generator is the only
part of the project that talks to the network.

| File | Contents | Generator | Source and licence |
| --- | --- | --- | --- |
| `static/geo-assets.js` | Country flags and country outlines | `scripts/generate_geo_assets.py` | Flags from [Twemoji](https://github.com/jdecked/twemoji) (CC-BY 4.0); outlines derived from Natural Earth 1:50m boundaries via world-atlas (public domain) |
| `static/link-logos.js` | Brand marks for external links | `scripts/generate_link_logos.py` | [Simple Icons](https://simpleicons.org) (CC0 1.0) |

`COUNTRY_CODES` in `static/app.js` is the single source of truth for which
countries have graphics. A country missing from that table simply gets no flag
and keeps the plain pin on its card; to add one, put it in the table and rerun
`scripts/generate_geo_assets.py`.

A country outline is a watermark, deliberately simplified, and small or distant
overseas territory is dropped so the recognisable landmass fills the card. It is
decoration, never a statement about a border. Brand marks remain the trademarks
of their owners and appear only as pointers to those owners' own pages; where
Simple Icons has removed a mark, the link falls back to a plain chain-link chip.
