import unittest
from pathlib import Path

from PIL import Image

from adaletvpn.tray import icon_frames, write_connected_icon


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
