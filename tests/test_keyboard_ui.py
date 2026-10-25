import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from adaletvpn.ui import MainWindow, Gdk


class KeyboardTests(unittest.TestCase):
    def window(self, enabled=True):
        window = SimpleNamespace(pin_enter_down=False, connect_button=Mock(), connect_vpn=Mock())
        window.connect_button.get_sensitive.return_value = enabled
        window.connect_button.grab_focus.return_value = True
        return window

    def test_tab_moves_to_connect_but_shift_tab_is_unchanged(self):
        window = self.window()
        self.assertTrue(MainWindow.pin_key_pressed(window, None, Gdk.KEY_Tab, 0, 0))
        window.connect_button.grab_focus.assert_called_once()
        self.assertFalse(MainWindow.pin_key_pressed(window, None, Gdk.KEY_Tab, 0, Gdk.ModifierType.SHIFT_MASK))

    def test_enter_cannot_submit_disabled_button(self):
        window = self.window(False)
        MainWindow.pin_key_pressed(window, None, Gdk.KEY_Return, 0, 0)
        window.connect_vpn.assert_not_called()

    def test_held_enter_does_not_retry(self):
        window = self.window()
        for _ in range(3):
            MainWindow.pin_key_pressed(window, None, Gdk.KEY_Return, 0, 0)
        window.connect_vpn.assert_called_once()
        MainWindow.pin_key_released(window, None, Gdk.KEY_Return, 0, 0)
        MainWindow.pin_key_pressed(window, None, Gdk.KEY_Return, 0, 0)
        self.assertEqual(window.connect_vpn.call_count, 2)
