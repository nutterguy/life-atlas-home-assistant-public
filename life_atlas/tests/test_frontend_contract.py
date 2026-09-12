import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        cls.html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        cls.css = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "static").glob("*.css"))
        )
        cls.photos = (ROOT / "static" / "photo-tools.js").read_text(encoding="utf-8")
        cls.dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cls.run_script = (ROOT / "run.sh").read_text(encoding="utf-8")
        cls.config = (ROOT / "config.yaml").read_text(encoding="utf-8")

    def test_home_assistant_ingress_requests_remain_relative(self):
        self.assertIn("input.startsWith('/api/')?input.slice(1):input", self.script)
        self.assertNotIn('src="/app.js', self.html)
        self.assertNotIn('href="/style.css', self.html)

    def test_first_party_assets_are_keyed_to_the_release_version(self):
        version = re.search(r'^version: "([^"]+)"$', self.config, re.MULTILINE).group(1)
        assets = re.findall(r'(?:href|src)="([^"]+\.(?:css|js)(?:\?[^"]*)?)"', self.html)
        first_party_assets = [asset for asset in assets if "://" not in asset]
        self.assertTrue(first_party_assets)
        for asset in first_party_assets:
            self.assertTrue(asset.endswith(f"?v={version}"), asset)

    def test_every_primary_view_is_registered(self):
        for view in ("home", "timeline", "diary", "years", "map", "people", "trips", "review", "stats"):
            self.assertIn(f"{view}:", self.script)

    def test_whatsapp_evidence_flow_is_explicit_and_reviewed(self):
        self.assertIn('id="whatsapp-evidence"', self.html)
        self.assertIn("function whatsappEvidence()", self.script)
        self.assertIn("/api/whatsapp/search", self.script)
        self.assertIn("/api/whatsapp/promote", self.script)
        self.assertIn("window.confirm", self.script)
        self.assertIn("at most 20 messages", self.script)

    def test_navigation_clicks_are_bound_to_buttons(self):
        self.assertIn("button[data-view]", self.script)
        self.assertIn("onclick=\"view='${id}';render()\"", self.script)

    def test_frontend_has_visible_failure_states(self):
        self.assertIn("Life Atlas could not load its data", self.script)
        self.assertIn("This view could not be displayed", self.script)
        self.assertIn("load-error", self.css)

    def test_sidebar_uses_svg_icons_and_stable_desktop_width(self):
        self.assertIn("const svgIcon", self.script)
        self.assertIn("grid-template-columns:225px", self.css)
        self.assertIn(".nav-icon svg", self.css)

    def test_grid_tracks_cannot_be_widened_by_their_content(self):
        """A bare `fr` track means minmax(auto, fr) and cannot shrink below its content.

        `.overview-grid` holds a wide year strip and a per-year bar chart. With a bare
        `1.4fr`/`1fr` the column grew to its content width, pushing a whole panel
        off-screen on Overview and bursting the layout on Stats at phone widths.
        """
        for declaration in re.findall(r"\.overview-grid\{[^}]*\}", self.css):
            columns = re.search(r"grid-template-columns:([^;}]+)", declaration)
            if not columns:
                continue
            for track in columns.group(1).split():
                if track.endswith("fr") and "minmax" not in columns.group(1):
                    self.fail(f"overview-grid track {track!r} needs minmax(0, ...): {declaration}")

    def test_wide_horizontal_strips_scroll_inside_their_panel(self):
        rule = re.search(r"\.year-bars\{([^}]*)\}", self.css)
        self.assertIsNotNone(rule, ".year-bars rule not found")
        self.assertIn("overflow-x:auto", rule.group(1).replace(" ", ""),
                      ".year-bars must scroll rather than widen its container")

    def test_year_tiles_wrap_to_fill_their_panel(self):
        """Years flow into the available width instead of one long scrolling strip."""
        rule = re.search(r"\.overview-years\{([^}]*)\}", self.css)
        self.assertIsNotNone(rule, ".overview-years rule not found")
        declaration = rule.group(1).replace(" ", "")
        self.assertIn("display:grid", declaration)
        self.assertIn("auto-fill", declaration)
        self.assertNotIn("overflow-x:auto", declaration,
                         "the year tiles should wrap, not scroll sideways")

    def test_uncertain_records_without_confidence_do_not_render_nan(self):
        """trips carry no confidence column, so the percentage must be guarded."""
        pill = re.search(r"function statusPill\(e\)\{[^\n]*?\n", self.script)
        self.assertIsNotNone(pill)
        self.assertIn("Number.isFinite(e.confidence)", pill.group(0))

    def test_counts_are_pluralised(self):
        self.assertIn("const plural=", self.script)
        self.assertIn("plural(x.event_count,'linked event')", self.script)
        self.assertNotIn("${x.event_count} linked events", self.script)

    def test_every_event_category_has_an_icon_and_colour(self):
        """Exercise is most of the archive; it must not fall back to the placeholder."""
        icons = re.search(r"const icons=\{([^}]*)\}", self.script).group(1)
        colours = re.search(r"const colors=\{([^}]*)\}", self.script).group(1)
        for category in ("Exercise", "Relationship", "Running", "Travel"):
            self.assertIn(f"{category}:", icons, f"{category} missing from icons")
            self.assertIn(f"{category}:", colours, f"{category} missing from colors")

    def test_no_function_is_defined_twice(self):
        """Declarations hoist, so an earlier duplicate is unreachable dead code.

        app.py's frontend grew by appending overrides; five functions had two or
        three definitions each and only the last ever ran. photo-tools.js wrapping
        a function by capturing it first is a different, deliberate pattern and is
        unaffected by this rule.
        """
        for path in ("app.js", "photo-tools.js", "restore-tools.js"):
            source = (ROOT / "static" / path).read_text(encoding="utf-8")
            names = re.findall(r"^(?:async )?function ([A-Za-z0-9_]+)", source, re.MULTILINE)
            duplicates = sorted({name for name in names if names.count(name) > 1})
            self.assertEqual(duplicates, [], f"{path} defines these more than once: {duplicates}")

    def test_no_user_data_is_interpolated_into_inline_handlers(self):
        """An attribute is entity-decoded before it is compiled as JavaScript.

        `onclick="f('${esc(x)}')"` therefore turns esc()'s &#39; back into a real
        quote, closing the string literal, so a category or title of
        `x'+alert(1)+'` executes on click. Data belongs in data-* attributes.
        """
        offenders = re.findall(r'on[a-z]+="[^"]*\$\{esc\([^"]*"', self.script)
        self.assertEqual(offenders, [], f"data interpolated into an inline handler: {offenders}")

    def test_interactive_data_is_carried_in_data_attributes(self):
        for attribute in ('data-action="category"', 'data-action="year"',
                          'data-action="review"', 'data-action="stat"'):
            self.assertIn(attribute, self.script)
        self.assertIn("closest('[data-action]')", self.script)

    def test_year_tiles_never_exceed_their_grid_track(self):
        """A tile wider than its track overlaps the next one and re-breaks the panel."""
        track = re.search(r"\.overview-years\{[^}]*minmax\((\d+)px", self.css)
        self.assertIsNotNone(track, "overview-years minmax floor not found")
        tile = re.search(r"\.year-tile\{([^}]*)\}", self.css)
        self.assertIsNotNone(tile, ".year-tile rule not found")
        min_width = re.search(r"min-width:(\d+)px", tile.group(1))
        if min_width:
            self.assertLessEqual(int(min_width.group(1)), int(track.group(1)),
                                 "year tile min-width exceeds the grid track floor")

    def test_filter_options_carry_explicit_values(self):
        """Without a value attribute an option's value is its collapsed text."""
        self.assertIn('<option value="${esc(x)}">${esc(x)}</option>', self.script)

    def test_stats_page_is_navigable(self):
        for contract in ("statusFilter", "categoryFilter"):
            self.assertIn(f"function {contract}(", self.script)
        self.assertIn('<button class="stat-visual"', self.script)
        self.assertIn('<button class="category-row"', self.script)
        self.assertIn('<button class="year-column"', self.script)

    def test_map_uses_a_keyless_dark_basemap(self):
        """Providers that want a key still answer 200 and draw the refusal into the tile.

        CARTO began rendering "API KEY REQUIRED" into its dark basemap and
        OpenStreetMap draws "403 Access blocked" for this client, so neither can be
        detected in code. Pin the basemap in one place, keep its attribution beside
        it, and keep the known-bad hosts out.
        """
        basemap = re.search(r"const BASEMAP=\{(.*?)\};", self.script, re.S)
        self.assertIsNotNone(basemap, "BASEMAP constant not found")
        declaration = basemap.group(1)
        self.assertIn("url:", declaration)
        self.assertIn("attribution:", declaration)
        self.assertIn("maxZoom:", declaration)
        self.assertNotIn("apikey", declaration.lower())
        for refused in ("basemaps.cartocdn.com", "tile.openstreetmap.org"):
            self.assertNotIn(refused, self.script, f"{refused} no longer serves usable tiles here")
        self.assertIn("L.tileLayer(BASEMAP.url", self.script)

    def test_places_show_their_country(self):
        self.assertIn("const COUNTRY_CODES=", self.script)
        self.assertIn("function countryFlag(", self.script)
        self.assertIn("countryFlag(p.country)", self.script)

    def test_action_rows_align_their_buttons(self):
        rules = re.findall(r"\.entity-actions\{[^}]*\}", self.css)
        self.assertEqual(len(rules), 1, f"expected one .entity-actions rule, found {rules}")
        declaration = rules[0].replace(" ", "")
        self.assertIn("align-items:center", declaration,
                      "stretch inflates a lone button to the row height")
        self.assertIn("gap:", declaration)
        self.assertNotIn("-38px", declaration, "the dead negative margin should be gone")

    def test_filter_controls_have_accessible_names(self):
        for control_id in ("search", "status", "category"):
            element = re.search(r"<(?:input|select) id=\"" + control_id + r"\"[^>]*>", self.html)
            self.assertIsNotNone(element, f"filter control {control_id} not found")
            self.assertIn("aria-label=", element.group(0),
                          f"filter control {control_id} needs an aria-label")

    def test_mobile_navigation_is_horizontally_scrollable(self):
        self.assertIn("@media(max-width:800px)", self.css)
        self.assertIn("overflow-x:auto", self.css)

    def test_timeline_layout_uses_rendered_pixel_widths(self):
        self.assertIn("function timelineMetrics", self.script)
        self.assertIn("occupiedWidth", self.script)
        self.assertIn("timelineScale=8", self.script)
        self.assertIn("setTimelineScale", self.script)

    def test_timeline_points_have_accessible_targets_and_focus_labels(self):
        self.assertIn("aria-label=", self.script)
        self.assertIn("width:24px!important", self.css)
        self.assertIn(".swim-event.point:focus-visible span", self.css)

    def test_timeline_defaults_to_highlights_and_can_restore_minor_events(self):
        self.assertIn("timelineMode='highlights'", self.script)
        self.assertIn("importance!=='minor'", self.script)
        self.assertIn("Timeline importance", self.script)
        self.assertIn(">Highlights</button>", self.script)
        self.assertIn(">Major only</button>", self.script)
        self.assertIn(">All events</button>", self.script)
        self.assertIn("minor hidden", self.script)

    def test_google_picker_tokens_remain_in_browser_memory(self):
        self.assertIn("browser's memory", self.photos)
        self.assertIn("window.isSecureContext", self.photos)
        self.assertIn("event, diary day or person", self.photos)
        self.assertIn("poll_interval", self.photos)
        self.assertNotIn("localStorage", self.photos)

    def test_google_photos_mcp_setup_uses_ingress_relative_routes(self):
        self.assertIn('id="photos"', self.html)
        self.assertIn('id="photos-dialog"', self.html)
        self.assertIn("api/google-photos-mcp/status", self.photos)
        self.assertIn("api/google-photos-mcp/auth/callback", self.photos)
        self.assertIn("new URL('api/google-photos-mcp/auth'", self.photos)
        self.assertIn("/data/google-photos-mcp/tokens.db", self.photos)

    def test_mcp_port_is_not_exposed_by_home_assistant(self):
        self.assertIn("host_network: false", self.config)
        self.assertNotIn("3000/tcp", self.config)
        self.assertIn('PORT="${GOOGLE_PHOTOS_MCP_PORT:-3000}" \\', self.run_script)
        self.assertNotIn('export PORT=', self.run_script)
        self.assertIn("WHATSAPP_API_PORT=3001", self.run_script)
        self.assertIn("LIFE_ATLAS_BACKEND_PORT=8100", self.run_script)
        self.assertIn("mcp_ingress_proxy.py", self.dockerfile)

    def test_container_includes_photo_backend_modules(self):
        self.assertIn("google_photos_picker.py", self.dockerfile)
        self.assertIn("media_store.py", self.dockerfile)
        self.assertIn("mcp_ingress_proxy.py", self.dockerfile)
        self.assertIn("/usr/local/bin/node22", self.dockerfile)
        self.assertIn("exec /usr/local/bin/node22", self.run_script)

    def test_container_copies_every_first_party_module_it_imports(self):
        """A module copied into /app must not import a sibling the image omits."""
        copied = set()
        for line in re.findall(r"^COPY (.+) \./$", self.dockerfile, re.MULTILINE):
            copied.update(part for part in line.split() if part.endswith(".py"))
        self.assertIn("app.py", copied)

        first_party = {path.name for path in ROOT.glob("*.py")}
        for name in sorted(copied):
            tree = ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported = {node.module.split(".")[0]} if node.level == 0 and node.module else set()
                else:
                    continue
                for module in imported:
                    if f"{module}.py" in first_party:
                        self.assertIn(
                            f"{module}.py", copied,
                            f"{name} imports {module}, but the Dockerfile does not copy {module}.py",
                        )

    def test_agent_api_starts_only_after_the_backend_is_initialised(self):
        self.assertIn("wait_for_backend()", self.run_script)
        self.assertIn("if ! wait_for_backend; then", self.run_script)
        agent_start = self.run_script.index("python3 /opt/life-atlas/agent_api.py")
        readiness_gate = self.run_script.index("if ! wait_for_backend; then")
        self.assertLess(readiness_gate, agent_start)

    def test_addon_options_do_not_require_supervisor_api_access(self):
        self.assertIn('/data/options.json', self.run_script)
        self.assertNotIn('bashio::config', self.run_script)

    def test_whatsapp_archive_is_ingress_relative_and_not_lan_exposed(self):
        self.assertIn('id="whatsapp"', self.html)
        self.assertIn("window.location.assign('whatsapp/')", self.script)
        self.assertNotIn("8097/tcp", self.config)
        self.assertNotIn("3000/tcp", self.config)
        self.assertIn("LIFE_ATLAS_WHATSAPP_MANAGEMENT_HOST=127.0.0.1", self.run_script)
        self.assertIn("whatsapp_archive/adapter.py", self.dockerfile)
        self.assertNotIn('hassio_api: true', self.config)

    def test_restore_database_uses_ingress_relative_chunked_workflow(self):
        restore = (ROOT / "static" / "restore-tools.js").read_text(encoding="utf-8")
        self.assertIn('id="restore"', self.html)
        self.assertIn('id="restore-dialog"', self.html)
        self.assertIn("/api/restore/sessions", restore)
        self.assertIn("X-Life-Atlas-Restore-Token", restore)
        self.assertIn("application/octet-stream", restore)
        self.assertIn(".sqlite3,.zip", restore)
        self.assertIn("package_media_files", restore)
        self.assertIn("restoreToken=session.token", restore)
        self.assertIn("token:restoreToken", restore)
        self.assertNotIn("session=await api(`/api/restore/sessions/${session.id}/chunks", restore)
        self.assertIn("2*1024*1024", restore)
        self.assertIn("Type <b>RESTORE</b>", restore)
        self.assertIn("backup: cold", self.config)
        self.assertIn("restore_service.py", self.dockerfile)

    def test_people_management_has_edit_merge_preview_and_confirmation(self):
        for contract in ("openPersonEditor", "openPersonMerge", "loadMergePreview", "confirmPersonMerge"):
            self.assertIn(contract, self.script)
        self.assertIn("/merge-preview?target_id=", self.script)
        self.assertIn("window.confirm", self.script)
        self.assertIn("Merge duplicate", self.script)


if __name__ == "__main__":
    unittest.main()
