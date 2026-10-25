import unittest

from adaletvpn.fonts import load_fonts


class FontTests(unittest.TestCase):
    def test_bundled_fonts_cover_turkish_and_digits(self):
        load_fonts()
        from gi.repository import Pango, PangoCairo
        context = PangoCairo.FontMap.get_default().create_context()
        for family in ("AdaletVPN Text",):
            with self.subTest(family=family):
                layout = Pango.Layout.new(context)
                layout.set_font_description(Pango.FontDescription.from_string(
                    f"{family} 12"))
                text = "AdaletVPN ab123456 ÇığÖşÜ 17.03.2027"
                layout.set_text(text, -1)
                self.assertEqual(layout.get_unknown_glyphs_count(), 0)
                iterator = layout.get_iter()
                checked = ""
                while True:
                    run = iterator.get_run_readonly()
                    if run:
                        item = run.item
                        segment = text.encode()[item.offset:item.offset + item.length].decode()
                        actual = item.analysis.font.describe().get_family()
                        for char in segment:
                            if not char.isspace():
                                self.assertEqual(actual, family)
                        checked += segment
                    if not iterator.next_run():
                        break
                self.assertEqual(checked, text)
