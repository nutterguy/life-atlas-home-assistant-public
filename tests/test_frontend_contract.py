import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        cls.html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "static" / "v8.css").read_text(encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()

