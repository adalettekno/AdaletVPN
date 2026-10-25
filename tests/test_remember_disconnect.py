import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

from adaletvpn.core import Controller, Credentials, ConnectionError, State
from adaletvpn.ui import MainWindow


class Field:
    def __init__(self, text):
        self.text = text

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text


class RememberDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_keeps_only_opted_in_fields(self):
        for password, pin in ((False, False), (True, False), (True, True)):
            with self.subTest(password=password, pin=pin):
                client = SimpleNamespace(disconnect=AsyncMock())
                controller = Controller(None, client)
                controller.selected_id = 'card'
                w = SimpleNamespace(controller=controller, read_only=False, closing=False,
                    task=None, managed_connection=True, cancel_vault=Mock(),
                    username=Field('ab123'), password=Field('test-password'), pin=Field('123456'),
                    remember=Mock(), remember_pin=Mock(), refresh=Mock(), credentials=None)
                w.remember.get_active.return_value = password
                w.remember_pin.get_active.return_value = pin
                captured = []
                def connect(credentials, identifier, remembered):
                    captured.append((credentials, remembered))
                    async def run():
                        controller.state = State.CONNECTED
                    return run()
                w.connect_and_remember = connect
                w.start = lambda coroutine: setattr(w, 'task', asyncio.create_task(coroutine))
                MainWindow.connect_vpn(w)
                await w.task
                MainWindow.finished(w, w.task)
                MainWindow.stop_vpn(w)
                await w.task
                MainWindow.finished(w, w.task)
                self.assertEqual(controller.state, State.DISCONNECTED)
                self.assertEqual(w.password.get_text(), 'test-password' if password else '')
                self.assertEqual(w.pin.get_text(), '123456' if pin else '')
                self.assertEqual(captured[0][0].password, '')
                self.assertIsNone(controller.error)
                if captured[0][1]:
                    captured[0][1].clear()

    async def test_kept_passwords_do_not_remain_a_cleanup_error(self):
        client = SimpleNamespace(disconnect=AsyncMock())
        controller = Controller(None, client)
        controller.state = State.CONNECTED
        controller._needs_cleanup = True
        controller.error = "Bağlantı temizlenemedi. Bağlantıyı kesmeyi tekrar deneyin."
        message = Field(controller.error)
        w = SimpleNamespace(controller=controller, read_only=False, closing=False,
            task=None, managed_connection=True, cancel_vault=Mock(), message=message,
            username=Field('ab123'), password=Field('test-password'), pin=Field('123456'),
            remember=Mock(), remember_pin=Mock(), refresh=Mock(), credentials=None)
        w.remember.get_active.return_value = True
        w.remember_pin.get_active.return_value = True
        w.start = lambda coroutine: setattr(w, 'task', asyncio.create_task(coroutine))
        MainWindow.stop_vpn(w)
        await w.task
        MainWindow.finished(w, w.task)
        self.assertEqual(controller.state, State.DISCONNECTED)
        self.assertIsNone(controller.error)
        self.assertEqual(message.get_text(), '')
        self.assertEqual(w.password.get_text(), 'test-password')
        self.assertEqual(w.pin.get_text(), '123456')

    async def test_real_cleanup_failure_stays_an_error_while_passwords_remain(self):
        client = SimpleNamespace(disconnect=AsyncMock(side_effect=RuntimeError('gizli ayrıntı')))
        controller = Controller(None, client)
        controller.state = State.CONNECTED
        controller._needs_cleanup = True
        message = Field('')
        w = SimpleNamespace(controller=controller, read_only=False, closing=False,
            task=None, managed_connection=True, cancel_vault=Mock(), message=message,
            username=Field('ab123'), password=Field('test-password'), pin=Field('123456'),
            remember=Mock(), remember_pin=Mock(), refresh=Mock(), credentials=None)
        w.remember.get_active.return_value = True
        w.remember_pin.get_active.return_value = True
        w.start = lambda coroutine: setattr(w, 'task', asyncio.create_task(coroutine))
        MainWindow.stop_vpn(w)
        with self.assertRaises(ConnectionError):
            await w.task
        MainWindow.finished(w, w.task)
        self.assertEqual(controller.state, State.ERROR)
        self.assertIn('temizlenemedi', message.get_text())
        self.assertNotIn('gizli ayrıntı', message.get_text())
        self.assertEqual(w.password.get_text(), 'test-password')
        self.assertEqual(w.pin.get_text(), '123456')

    async def test_pin_warning_requires_reentry_without_clearing_password(self):
        w = SimpleNamespace(controller=SimpleNamespace(
            connect=AsyncMock(side_effect=ConnectionError('warning')),
            error_code='pin_final_warning'), pin=Field('123456'), password=Field('test-password'))
        with self.assertRaises(ConnectionError):
            await MainWindow.connect_and_remember(w, Credentials('ab123', '123456', 'test-password'), 'card', None)
        self.assertEqual(w.pin.get_text(), '')
        self.assertEqual(w.password.get_text(), 'test-password')
