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

    def test_every_primary_view_is_registered(self):
        for view in ("home", "timeline", "diary", "years", "map", "people", "trips", "review", "stats"):
            self.assertIn(f"{view}:", self.script)

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
        self.assertIn("LIFE_ATLAS_BACKEND_PORT=8100", self.run_script)
        self.assertIn("mcp_ingress_proxy.py", self.dockerfile)

    def test_container_includes_photo_backend_modules(self):
        self.assertIn("google_photos_picker.py", self.dockerfile)
        self.assertIn("media_store.py", self.dockerfile)
        self.assertIn("mcp_ingress_proxy.py", self.dockerfile)

    def test_restore_database_uses_ingress_relative_chunked_workflow(self):
        restore = (ROOT / "static" / "restore-tools.js").read_text(encoding="utf-8")
        self.assertIn('id="restore"', self.html)
        self.assertIn('id="restore-dialog"', self.html)
        self.assertIn("/api/restore/sessions", restore)
        self.assertIn("X-Life-Atlas-Restore-Token", restore)
        self.assertIn("application/octet-stream", restore)
        self.assertIn(".sqlite3,.zip", restore)
        self.assertIn("package_media_files", restore)
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
