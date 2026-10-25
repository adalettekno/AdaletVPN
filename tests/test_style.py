import re
import unittest
from pathlib import Path

from adaletvpn.style import load_css, _CSS


class StyleTests(unittest.TestCase):
    def test_font_rule_stays_inside_the_window_and_focus_ring_remains(self):
        css = Path(_CSS).read_text()
        self.assertIn(".adaletvpn *", css)
        self.assertIsNone(re.search(r"(?m)^\s*\*\s*\{", css))
        self.assertIn("button.connection-action:focus-visible", css)
        for token in ("@error_color", "@success_color", "@theme_selected_bg_color"):
            self.assertIn(token, css)
        self.assertTrue(load_css())
