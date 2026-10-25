import unittest
from pathlib import Path

from PIL import Image

from adaletvpn.core import State
from adaletvpn.tray import icon_frames, render_flight_icon, tray_minimize, tray_view, write_connected_icon


class TrayViewTests(unittest.TestCase):
    def test_connected_session_appears_once(self):
        self.assertEqual(tray_view(False, State.CONNECTED, False, False), (True, "appear"))
        self.assertEqual(tray_view(True, State.CONNECTED, False, False), (True, None))

    def test_busy_connection_waits(self):
        self.assertEqual(tray_view(False, State.CONNECTED, True, False), (False, None))

    def test_unverified_session_keeps_the_icon(self):
        self.assertEqual(tray_view(True, State.UNKNOWN, False, False), (True, None))

    def test_dropped_connection_returns_the_window(self):
        for state in (State.ERROR, State.DISCONNECTED, State.DISCONNECTING):
            self.assertEqual(tray_view(True, state, False, False), (False, "retire"))

    def test_idle_window_stays_open(self):
        self.assertEqual(tray_view(False, State.DISCONNECTED, False, False), (False, None))

    def test_minimize_returns_to_the_tray_only_while_the_session_holds(self):
        self.assertTrue(tray_minimize(True, True, State.CONNECTED, False))
        self.assertTrue(tray_minimize(True, True, State.UNKNOWN, False))
        self.assertFalse(tray_minimize(True, True, State.DISCONNECTED, False))
        self.assertFalse(tray_minimize(True, True, State.ERROR, False))
        self.assertFalse(tray_minimize(True, False, State.CONNECTED, False))
        self.assertFalse(tray_minimize(False, True, State.CONNECTED, False))
        self.assertFalse(tray_minimize(True, True, State.CONNECTED, True))

    def test_closing_removes_the_icon_without_showing_the_window(self):
        self.assertEqual(tray_view(True, State.CONNECTED, False, True), (False, "drop"))
        self.assertEqual(tray_view(False, State.CONNECTED, False, True), (False, None))


class ConnectedIconTests(unittest.TestCase):
    def test_ring_sits_outside_the_scales_and_scales_to_tray_sizes(self):
        root = Path(__file__).resolve().parents[1]
        source = root / "data/app-icons/hicolor/512x512/apps/com.adalettekno.AdaletVPN.png"
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "hicolor/512x512/apps/com.adalettekno.AdaletVPN-connected.png"
            write_connected_icon(source, destination)
            written = destination.stat().st_mtime_ns
            write_connected_icon(source, destination)
            self.assertEqual(destination.stat().st_mtime_ns, written)
            image = Image.open(destination).convert("RGBA")
            field = image.getpixel((400, 256))
            ring = image.getpixel((500, 256))
            star = image.getpixel((256, 90))
            self.assertGreater(field[2], field[0] + 40)
            self.assertGreater(field[2], field[1])
            self.assertGreater(ring[1], ring[0] + 40)
            self.assertGreater(ring[1], ring[2])
            self.assertGreater(star[0], 180)
            self.assertGreater(star[1], 180)
            self.assertGreater(star[2], 180)
            frames = icon_frames(source)
            self.assertEqual([frame[0] for frame in frames], [22, 24, 32, 48, 64])
            for width, height, pixels in frames:
                self.assertEqual(len(pixels), width * height * 4)
                self.assertEqual(width, height)
            width, _height, pixels = frames[0]
            offset = (width + width // 2) * 4
            edge = pixels[offset:offset + 4]
            self.assertGreater(edge[2], edge[1] + 20)
            self.assertGreater(edge[2], edge[3])

    def test_flight_ring_is_a_thin_line_outside_the_emblem(self):
        root = Path(__file__).resolve().parents[1]
        source = root / "data/app-icons/hicolor/512x512/apps/com.adalettekno.AdaletVPN.png"
        image = render_flight_icon(source, 96)
        field = image.getpixel((48, 22))
        self.assertGreater(field[2], field[0] + 40)
        self.assertGreater(field[2], field[1])
        column = [image.getpixel((48, y)) for y in range(96)]

        def green(pixel):
            red, green_value, blue, alpha = pixel
            return alpha > 180 and green_value > red + 40 and green_value > blue

        run = 0
        longest = 0
        for pixel in column:
            if green(pixel):
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        self.assertLessEqual(longest, 4)
        self.assertGreaterEqual(longest, 1)
        self.assertLess(column[3][3], 40)
