import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from adaletvpn.core import Controller, ConnectionError, State
from adaletvpn.ui import GLib, MainWindow, certificate_labels
from test_networkmanager import certificate
import test_cleanup_ui


class UIImprovementTests(unittest.IsolatedAsyncioTestCase):
    def test_unique_names_stay_short_and_duplicate_names_are_distinct(self):
        first = replace(certificate(), subject='CN=Örnek Kullanıcı')
        second = replace(first, identifier='other', not_after=first.not_after.replace(year=2040))
        self.assertEqual(certificate_labels([first]), ['Örnek Kullanıcı'])
        self.assertEqual(len(set(certificate_labels([first, second]))), 2)
        self.assertEqual(len(set(certificate_labels([first, replace(first, identifier='other')]))), 2)

    def test_unsuitable_certificate_is_red_and_explains_disabled_connect(self):
        controller = Controller(None, SimpleNamespace(disconnect=AsyncMock()))
        window = test_cleanup_ui.CleanupUITests().window(controller)
        controller.certificates = (replace(certificate(), client_auth=False),)
        window.vault_task = None
        MainWindow.refresh(window)
        window.details.set_from_icon_name.assert_called_with('window-close-symbolic')
        window.connect_button.set_sensitive.assert_called_with(False)
        reason = controller.certificates[0].connection_problem()
        window.details.set_tooltip_text.assert_called_with(reason)
        window.connect_button.set_tooltip_text.assert_called_with(reason)

    def scan_window(self, error):
        async def scan():
            raise error
        window = SimpleNamespace(
            controller=SimpleNamespace(selected_id="kept", scan=scan),
            updating_list=False,
            certificates=Mock(),
            details=Mock(),
            pin=Mock(),
            password=Mock(),
            username=Mock(),
            vault_selected="kept",
        )
        return window

    async def test_cancelled_scan_keeps_the_list_and_typed_fields(self):
        window = self.scan_window(asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await MainWindow.scan(window)
        window.certificates.set_model.assert_not_called()
        window.details.set_visible.assert_not_called()
        window.pin.set_text.assert_not_called()
        window.password.set_text.assert_not_called()
        window.username.set_text.assert_not_called()
        self.assertEqual(window.vault_selected, "kept")

    def pump_window(self, loop):
        controller = SimpleNamespace(
            state=State.DISCONNECTED, can_start=True, error=None, scan_detail="",
            selected_id=None, certificates=(),
        )
        username, pin, password = Mock(), Mock(), Mock()
        remember, remember_pin = Mock(), Mock()
        for field, value in ((username, "ab1"), (pin, ""), (password, "")):
            field.get_text.return_value = value
        remember.get_active.return_value = False
        remember_pin.get_active.return_value = False
        window = SimpleNamespace(
            loop=loop, timer=1, _ui_snapshot=None, closing=False, cancelling=False,
            task=None, controller=controller, dark_theme=False,
            username=username, pin=pin, password=password,
            remember=remember, remember_pin=remember_pin, refresh=Mock(),
        )
        window.ui_snapshot = MainWindow.ui_snapshot.__get__(window)
        return window

    def test_idle_tick_stops_without_refreshing(self):
        loop = asyncio.new_event_loop()
        try:
            window = self.pump_window(loop)
            window._ui_snapshot = MainWindow.ui_snapshot(window)
            self.assertEqual(MainWindow.tick(window), GLib.SOURCE_REMOVE)
            self.assertIsNone(window.timer)
            window.refresh.assert_not_called()
        finally:
            loop.close()

    def test_pump_refreshes_on_state_change_and_skips_unchanged_ticks(self):
        loop = asyncio.new_event_loop()
        try:
            window = self.pump_window(loop)
            window._ui_snapshot = MainWindow.ui_snapshot(window)

            async def change():
                window.controller.state = State.SCANNING
                await asyncio.sleep(60)

            task = loop.create_task(change())
            self.assertEqual(MainWindow.tick(window), GLib.SOURCE_CONTINUE)
            window.refresh.assert_called_once()
            self.assertEqual(window.timer, 1)
            window.refresh.reset_mock()
            self.assertEqual(MainWindow.tick(window), GLib.SOURCE_CONTINUE)
            window.refresh.assert_not_called()
            task.cancel()
            self.assertEqual(MainWindow.tick(window), GLib.SOURCE_REMOVE)
            self.assertIsNone(window.timer)
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def test_ensure_pump_schedules_one_timer(self):
        window = SimpleNamespace(timer=None, tick=Mock())
        with patch("adaletvpn.ui.GLib.timeout_add", return_value=7) as add:
            MainWindow.ensure_pump(window)
            MainWindow.ensure_pump(window)
        add.assert_called_once_with(20, window.tick)
        self.assertEqual(window.timer, 7)

    def test_tick_after_cancelled_scan_refreshes_without_clearing_fields(self):
        loop = asyncio.new_event_loop()
        try:
            release = loop.create_future()

            async def block():
                await release

            controller = Controller(SimpleNamespace(list_certificates=block), None)
            window = test_cleanup_ui.CleanupUITests().window(controller)
            item = controller.certificates[0]
            window.loop = loop
            window.timer = None
            window._ui_snapshot = None
            window.ui_snapshot = MainWindow.ui_snapshot.__get__(window)
            window.finished = MainWindow.finished.__get__(window)
            window.tick = MainWindow.tick.__get__(window)
            window.ensure_pump = MainWindow.ensure_pump.__get__(window)
            with patch("adaletvpn.ui.GLib.timeout_add", return_value=5):
                MainWindow.start(window, MainWindow.scan(window))
            self.assertEqual(MainWindow.tick(window), GLib.SOURCE_CONTINUE)
            window.pin.set_text.reset_mock()
            window.password.set_text.reset_mock()
            window.username.set_text.reset_mock()
            window.certificates.set_model.reset_mock()
            window.task.cancel()
            self.assertEqual(MainWindow.tick(window), GLib.SOURCE_REMOVE)
            window.pin.set_text.assert_not_called()
            window.password.set_text.assert_not_called()
            window.username.set_text.assert_not_called()
            window.certificates.set_model.assert_not_called()
            self.assertEqual(controller.certificates, (item,))
            self.assertEqual(controller.selected_id, item.identifier)
            self.assertEqual(controller.state, State.DISCONNECTED)
            self.assertIsNone(window.timer)
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def test_failed_scan_keeps_typed_fields(self):
        window = self.scan_window(ConnectionError("Sertifika bulunamadı!"))
        with self.assertRaises(ConnectionError):
            await MainWindow.scan(window)
        window.certificates.set_model.assert_called_once()
        window.certificates.add_css_class.assert_called_with("certificate-missing")
        window.details.set_visible.assert_called_with(False)
        window.pin.set_text.assert_not_called()
        window.password.set_text.assert_not_called()
        window.username.set_text.assert_not_called()
        self.assertEqual(window.vault_selected, "kept")
        self.assertEqual(window.certificates.set_model.call_args.args[0].get_string(0), "Sertifika bulunamadı!")

    async def test_scan_reason_is_preserved_but_unexpected_error_is_not_exposed(self):
        for error, expected in ((ConnectionError('p11tool başlatılamadı.'), 'p11tool başlatılamadı.'),
                                (RuntimeError('private detail'), 'Sertifika listesi okunamadı.')):
            controller = Controller(SimpleNamespace(list_certificates=AsyncMock(side_effect=error)), None)
            with self.assertRaises(ConnectionError):
                await controller.scan()
            self.assertEqual(controller.error, 'Sertifika bulunamadı!')
            self.assertIn(expected, controller.scan_detail)
            self.assertNotIn('private detail', controller.scan_detail)

    async def test_successful_rescan_clears_previous_warning(self):
        provider = SimpleNamespace(list_certificates=AsyncMock(return_value=[]))
        controller = Controller(provider, None)
        await controller.scan()
        self.assertTrue(controller.scan_detail)
        provider.list_certificates.return_value = [certificate()]
        await controller.scan()
        self.assertEqual(controller.scan_detail, '')

    def test_dark_status_has_readable_foreground(self):
        controller = Controller(None, SimpleNamespace(disconnect=AsyncMock()))
        window = test_cleanup_ui.CleanupUITests().window(controller)
        window.dark_theme = True
        window.vault_task = None
        MainWindow.refresh(window)
        self.assertIn('#c0c0c0', window.status.set_markup.call_args.args[0])
