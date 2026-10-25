import unittest

from adaletvpn.flight import (
    ICON_LEAD,
    flight_progress,
    icon_corner,
    icon_motion,
    icon_opacity,
    icon_start,
    mix_rect,
    tray_slot,
    window_fade,
)


class FlightMathTests(unittest.TestCase):
    def test_progress_stays_inside_the_duration(self):
        self.assertEqual(flight_progress(0), 0)
        self.assertEqual(flight_progress(480), 1)
        self.assertEqual(flight_progress(1000), 1)
        self.assertGreater(flight_progress(200), 0)
        self.assertLess(flight_progress(200), 1)

    def test_window_fade_starts_immediately_and_ends_clear(self):
        self.assertEqual(window_fade(0), 1)
        self.assertEqual(window_fade(0.25), 0.75)
        self.assertEqual(window_fade(1), 0)

    def test_icon_stays_on_the_window_during_the_lead(self):
        self.assertEqual(icon_motion(0), 0)
        self.assertEqual(icon_motion(ICON_LEAD), 0)
        start = icon_start((100, 80, 528, 349), 64)
        corner = icon_corner((0, 0, 1920, 1040), 64, 1)
        self.assertEqual(mix_rect(start, corner, icon_motion(ICON_LEAD / 2)), start)

    def test_icon_leaves_the_center_and_ends_in_the_corner(self):
        start = icon_start((100, 80, 528, 349), 64)
        corner = icon_corner((0, 0, 1920, 1040), 64, 1)
        self.assertEqual(start[2:], (64, 64))
        self.assertEqual(start[0], 100 + 528 // 2 - 32)
        self.assertEqual(start[1], 80 + 349 // 2 - 32)
        self.assertEqual(mix_rect(start, corner, 1), corner)
        self.assertEqual(corner[0] + corner[2], 1920 - 16)
        self.assertEqual(corner[1] + corner[3], 1040 - 12)
        mid = mix_rect(start, corner, icon_motion(0.5))
        self.assertGreater(mid[0], start[0])
        self.assertGreater(mid[1], start[1])
        self.assertLess(mid[0], corner[0])

    def test_icon_stays_visible_until_it_reaches_the_corner(self):
        self.assertEqual(icon_opacity(0), 1)
        self.assertEqual(icon_opacity(0.5), 1)
        self.assertGreater(icon_opacity(0.9), 0)
        self.assertLess(icon_opacity(0.9), 1)
        self.assertEqual(icon_opacity(1), 0)

    def test_high_scale_keeps_the_corner_inset(self):
        corner = icon_corner((0, 0, 3840, 2080), 128, 2)
        self.assertEqual(corner[0] + corner[2], 3840 - 32)
        self.assertEqual(corner[1] + corner[3], 2080 - 24)

    def test_tray_slot_is_the_leading_icon(self):
        self.assertEqual(tray_slot((1411, 1042, 386, 30)), (1417, 1046, 22, 22))
        self.assertEqual(tray_slot((40, 100, 30, 200)), (44, 106, 22, 22))
