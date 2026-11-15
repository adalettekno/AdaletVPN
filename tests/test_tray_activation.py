import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adaletvpn.ui import MainWindow


class TrayActivationTests(unittest.TestCase):
    def window(self):
        window = Mock(closing=False)
        window.show_from_tray.side_effect = lambda timestamp=None: MainWindow.show_from_tray(window, timestamp)
        return window

    def test_x11_click_uses_fresh_server_time(self):
        display = object()
        backend = SimpleNamespace(X11Display=object, x11_get_server_time=Mock(side_effect=[123, 456]))
        window = self.window()
        window.get_display.return_value = display
        with patch('adaletvpn.ui.GdkX11', backend):
            MainWindow.reveal_from_tray(window)
            MainWindow.reveal_from_tray(window)
        self.assertEqual([call.args for call in window.present_with_time.call_args_list], [(123,), (456,)])
        window.present.assert_not_called()
        window.set_visible.assert_called_with(True)
        window.unminimize.assert_called()
        self.assertFalse(window._tray_requested)

    def test_other_backend_and_missing_x11_keep_normal_presentation(self):
        backend = SimpleNamespace(X11Display=str, x11_get_server_time=Mock())
        for optional_backend in (backend, None):
            window = self.window()
            with patch('adaletvpn.ui.GdkX11', optional_backend):
                MainWindow.reveal_from_tray(window)
            window.present.assert_called_once()
            window.present_with_time.assert_not_called()
        backend.x11_get_server_time.assert_not_called()

    def test_automatic_reveal_does_not_claim_a_new_user_action(self):
        window = self.window()
        MainWindow.show_from_tray(window)
        window.present.assert_called_once()
        window.present_with_time.assert_not_called()

    def test_closing_ignores_tray_click(self):
        window = self.window()
        window.closing = True
        MainWindow.reveal_from_tray(window)
        window.show_from_tray.assert_not_called()



class ManualTrayTests(unittest.TestCase):
    def test_normal_minimize_never_hides_to_tray(self):
        window = Mock(closing=False)
        MainWindow.title_minimize(window)
        window.minimize.assert_called_once()
        window.set_visible.assert_not_called()

    def test_connected_refresh_does_not_create_tray_or_hide_window(self):
        from adaletvpn.core import State
        window = Mock(closing=False, _tray=None)
        window.controller.state = State.CONNECTED
        MainWindow.sync_tray(window)
        window.open_tray.assert_not_called()
        window.set_visible.assert_not_called()

    def test_only_explicit_request_hides_after_tray_is_ready(self):
        window = Mock(closing=False, _tray_requested=False)
        window._tray.ready = True
        MainWindow.tray_ready(window)
        window.set_visible.assert_not_called()
        window._tray_requested = True
        MainWindow.tray_ready(window)
        window.set_visible.assert_called_once_with(False)
        self.assertFalse(window._tray_requested)

    def test_unavailable_tray_keeps_window_visible(self):
        window = Mock(closing=False, _tray_requested=True, _tray=None)
        MainWindow.tray_failed(window)
        window.show_from_tray.assert_called_once()
        window.set_visible.assert_not_called()
