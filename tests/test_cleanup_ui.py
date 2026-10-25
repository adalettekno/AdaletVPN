import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from adaletvpn.core import Controller, State
from adaletvpn.ui import MainWindow
from test_networkmanager import certificate


class CleanupUITests(unittest.IsolatedAsyncioTestCase):
    def window(self, controller):
        window = SimpleNamespace(controller=controller, task=None, closing=False,
                                 cancelling=False, read_only=False, managed_connection=True,
                                 credentials=None, start=Mock(), vault_task=None)
        for name in ("status", "status_indicator", "status_spinner", "connection_actions", "details", "username", "pin", "password", "certificates",
                     "scan_button", "connect_button", "stop_button", "remember", "remember_pin", "certificate_info", "forget_button", "message"):
            setattr(window, name, Mock())
        controller.certificates = (certificate(),)
        controller.selected_id = controller.certificates[0].identifier
        window.username.get_text.return_value = "ab123"
        window.pin.get_text.return_value = "fake-pin"
        window.password.get_text.return_value = "fake-password"
        window.refresh = lambda: MainWindow.refresh(window)
        return window

    async def test_cancelled_disconnect_can_be_retried_and_releases_controls_only_after_cleanup(self):
        entered = asyncio.Event()
        async def stall():
            entered.set()
            await asyncio.Future()
        client = SimpleNamespace(disconnect=AsyncMock(side_effect=stall))
        controller = Controller(None, client)
        controller.state = State.CONNECTED
        controller._needs_cleanup = True
        window = self.window(controller)
        window.task = asyncio.create_task(controller.disconnect())
        await entered.wait()
        window.cancelling = True
        window.task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await window.task
        MainWindow.finished(window, window.task)
        self.assertFalse(window.cancelling)
        self.assertEqual(controller.state, State.ERROR)
        self.assertTrue(controller._needs_cleanup)
        self.assertFalse(controller._busy)
        self.assertIn("iptal", controller.error)
        window.vault_task = None
        MainWindow.refresh(window)
        window.stop_button.set_sensitive.assert_called_with(True)
        window.connect_button.set_sensitive.assert_called_with(False)
        window.connection_actions.set_visible_child_name.assert_called_with("stop")
        client.disconnect.side_effect = None
        await controller.disconnect()
        self.assertEqual(client.disconnect.await_count, 2)
        self.assertEqual(controller.state, State.DISCONNECTED)
        self.assertTrue(controller.can_start)
        window.vault_task = None
        MainWindow.refresh(window)
        window.connect_button.set_sensitive.assert_called_with(True)
        window.scan_button.set_sensitive.assert_called_with(True)
        window.connection_actions.set_visible_child_name.assert_called_with("connect")

    async def test_pending_cleanup_disables_inputs_and_does_not_consume_entered_secrets(self):
        controller = Controller(None, SimpleNamespace(disconnect=AsyncMock()))
        controller.state = State.ERROR
        controller._needs_cleanup = True
        window = self.window(controller)
        window.vault_task = None
        MainWindow.refresh(window)
        for name in ("connect_button", "scan_button", "certificates", "username", "pin", "password"):
            getattr(window, name).set_sensitive.assert_called_with(False)
        window.stop_button.set_sensitive.assert_called_with(True)
        window.connection_actions.set_visible_child_name.assert_called_with("stop")

        MainWindow.connect_vpn(window)
        window.pin.set_text.assert_not_called()
        window.password.set_text.assert_not_called()
        window.start.assert_not_called()
        self.assertIsNone(window.credentials)

    async def test_ordinary_error_without_pending_cleanup_allows_retry(self):
        controller = Controller(None, SimpleNamespace(disconnect=AsyncMock()))
        controller.state = State.ERROR
        window = self.window(controller)
        window.vault_task = None
        MainWindow.refresh(window)
        window.connect_button.set_sensitive.assert_called_with(True)
        window.scan_button.set_sensitive.assert_called_with(True)
        window.connection_actions.set_visible_child_name.assert_called_with("connect")

    async def test_background_cleanup_blocks_inputs_even_without_foreground_task(self):
        controller = Controller(None, SimpleNamespace(disconnect=AsyncMock()))
        controller.state = State.DISCONNECTING
        controller._busy = True
        controller._needs_cleanup = True
        window = self.window(controller)
        window.vault_task = None
        MainWindow.refresh(window)
        window.connect_button.set_sensitive.assert_called_with(False)
        window.scan_button.set_sensitive.assert_called_with(False)
