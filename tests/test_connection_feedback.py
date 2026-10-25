import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from adaletvpn.ui import MainWindow, show_message
from adaletvpn.errors import MESSAGES


class ConnectionFeedbackTests(unittest.TestCase):
    def test_same_error_opens_once_and_remains_reopenable(self):
        window = SimpleNamespace(status_display=Mock(), connection_error_panel=Mock(),
                                 last_connection_message="")
        label = Mock()
        label.get_text.return_value = "Bağlantı temizlenemedi."
        MainWindow.connection_message_changed(window, label)
        MainWindow.connection_message_changed(window, label)
        window.connection_error_panel.popup.assert_called_once()
        window.status_display.set_visible_child_name.assert_called_with("error")
        label.get_text.return_value = ""
        MainWindow.connection_message_changed(window, label)
        window.connection_error_panel.popdown.assert_called_once()
        window.status_display.set_visible_child_name.assert_called_with("normal")
        label.get_text.return_value = "Bağlantı temizlenemedi."
        MainWindow.connection_message_changed(window, label)
        self.assertEqual(window.connection_error_panel.popup.call_count, 2)

    def test_pin_attempt_warning_is_red_and_bold(self):
        label = Mock()
        show_message(label, MESSAGES["pin_last_left"])
        markup = label.set_markup.call_args.args[0]
        self.assertIn('foreground="#c01c28" weight="bold"', markup)
        self.assertIn(MESSAGES["pin_last_left"], markup)
