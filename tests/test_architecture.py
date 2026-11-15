import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from adaletvpn.async_bridge import create_loop, has_pending_tasks, pump_once
from adaletvpn.connection_view import ConnectionView
from adaletvpn.core import Certificate, CertificateValidity, Controller, State
from adaletvpn.diagnostics import Stage, report_failure
from adaletvpn.vault_coordinator import VaultCoordinator, VaultOperation


class ViewTests(unittest.TestCase):
    def test_cleanup_and_closing_never_enable_reconnection(self):
        controller = Controller(None, None)
        cert = Certificate(
            "card",
            "device",
            "name",
            "issuer",
            not_before=datetime(2020, 1, 1, tzinfo=timezone.utc),
            not_after=datetime(2040, 1, 1, tzinfo=timezone.utc),
        )
        controller.certificates = (cert,)
        controller.selected_id = cert.identifier
        self.assertTrue(
            ConnectionView.from_controller(controller, managed=True, credentials_valid=True).can_connect
        )
        for state in State:
            controller.state = state
            controller._needs_cleanup = True
            view = ConnectionView.from_controller(controller, managed=True, credentials_valid=True)
            self.assertFalse(view.can_connect, state)
            self.assertFalse(view.editable, state)
            self.assertTrue(view.show_stop, state)
            closing = ConnectionView.from_controller(controller, closing=True, credentials_valid=True)
            self.assertFalse(closing.can_stop, state)
        controller._needs_cleanup = False
        controller.state = State.DISCONNECTED
        controller.certificates = (replace(cert, client_auth=False),)
        self.assertFalse(
            ConnectionView.from_controller(controller, managed=True, credentials_valid=True).can_connect
        )

    def test_certificate_validity_uses_codes(self):
        item = Certificate("card", "", "", "")
        self.assertIs(item.validity_code(), CertificateValidity.UNKNOWN)
        self.assertIsNotNone(item.problem_code())


class BridgeTests(unittest.TestCase):
    def test_completion_callbacks_and_cancellation_are_drained(self):
        loop = create_loop()
        self.addCleanup(loop.close)
        finished = []

        async def wait():
            await asyncio.Future()

        task = loop.create_task(wait())
        task.add_done_callback(lambda task: finished.append(task.cancelled()))
        pump_once(loop)
        self.assertTrue(has_pending_tasks(loop))
        task.cancel()
        pump_once(loop)
        self.assertTrue(has_pending_tasks(loop))
        pump_once(loop)
        self.assertFalse(has_pending_tasks(loop))
        self.assertEqual(finished, [True])

    def test_completed_task_waits_for_its_cleanup_callback(self):
        loop = create_loop()
        self.addCleanup(loop.close)
        finished = []

        async def complete():
            await asyncio.sleep(0)
            return "ok"

        task = loop.create_task(complete())
        task.add_done_callback(lambda done: finished.append(done.result()))
        pump_once(loop)
        self.assertTrue(task.done())
        self.assertEqual(finished, [])
        self.assertTrue(has_pending_tasks(loop))
        pump_once(loop)
        self.assertEqual(finished, ["ok"])
        self.assertFalse(has_pending_tasks(loop))

    def test_unhandled_errors_do_not_log_secrets(self):
        loop = create_loop()
        self.addCleanup(loop.close)
        with self.assertLogs("adaletvpn", level="ERROR") as logs:
            loop.call_exception_handler(
                {"message": "private-cookie", "exception": RuntimeError("private-pin")}
            )
        output = "\n".join(logs.output)
        self.assertIn("event_loop", output)
        self.assertNotIn("private", output)

    def test_exception_diagnostics_omit_message_and_source_text(self):
        try:
            raise RuntimeError("private-pin private-password private-cookie")
        except RuntimeError as error:
            with self.assertLogs("adaletvpn", level="ERROR") as logs:
                report_failure(Stage.UI_REFRESH, error)
        output = "\n".join(logs.output)
        self.assertIn("ui_refresh", output)
        self.assertIn("test_architecture.py:", output)
        self.assertNotIn("private", output)


class VaultSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_operation_target_does_not_depend_on_function_name(self):
        view = SimpleNamespace(loop=asyncio.get_running_loop(), closing=False, vault_message=Mock(), timer=1)
        coordinator = VaultCoordinator(view)
        called = []

        async def unrelated_name():
            called.append(True)

        coordinator.start_vault(VaultOperation.DELETE, unrelated_name())
        await coordinator.task
        view.vault_message.set_target.assert_called_once_with("forget")
        self.assertEqual(called, [True])

    async def test_cancelled_queued_save_clears_copy_without_running(self):
        view = SimpleNamespace(loop=asyncio.get_running_loop(), closing=False, vault_message=Mock(), timer=1)
        coordinator = VaultCoordinator(view)
        operation = AsyncMock()
        cleanup = Mock()
        coordinator.start_vault(VaultOperation.SAVE, operation(), cleanup=cleanup)
        coordinator.cancel_vault()
        with self.assertRaises(asyncio.CancelledError):
            await coordinator.task
        await asyncio.sleep(0)
        operation.assert_not_awaited()
        cleanup.assert_called_once()
