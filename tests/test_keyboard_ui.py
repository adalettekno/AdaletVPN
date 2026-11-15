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

    def test_username_tab_moves_directly_to_password(self):
        window = SimpleNamespace(password=Mock())
        window.password.grab_focus.return_value = True
        self.assertTrue(MainWindow.username_key_pressed(window, None, Gdk.KEY_Tab, 0, 0))
        window.password.grab_focus.assert_called_once()
        self.assertFalse(MainWindow.username_key_pressed(
            window, None, Gdk.KEY_Tab, 0, Gdk.ModifierType.SHIFT_MASK))

    def test_initial_focus_only_for_empty_secrets_and_only_once(self):
        for password, pin in (('', ''), ('saved-password', ''), ('', 'saved-pin')):
            with self.subTest(password=bool(password), pin=bool(pin)):
                window = SimpleNamespace(
                    _initial_focus_pending=True, _initial_focus_waiting=False,
                    closing=False, read_only=False, task=None,
                    vault_actions=SimpleNamespace(task=None),
                    get_mapped=Mock(return_value=True), get_focus=Mock(return_value=None),
                    username=Mock(), password=Mock(), pin=Mock())
                window.username.get_text.return_value = 'ab'
                window.username.get_sensitive.return_value = True
                window.password.get_text.return_value = password
                window.pin.get_text.return_value = pin
                MainWindow.focus_initial_username(window)
                if not password and not pin:
                    window.username.grab_focus.assert_called_once()
                    window.username.select_region.assert_called_once_with(-1, -1)
                    window.username.set_position.assert_called_once_with(-1)
                else:
                    window.username.grab_focus.assert_not_called()
                window.username.grab_focus.reset_mock()
                MainWindow.focus_initial_username(window)
                window.username.grab_focus.assert_not_called()
