import asyncio
import ctypes as C
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from gi.repository import GLib

from adaletvpn.auth import FormAnswers
from adaletvpn.auth_worker import Form, Option, answer_native_form
from adaletvpn.core import Controller, Credentials, State, ConnectionError
from adaletvpn.errors import CodedError
from adaletvpn.networkmanager import GioTransport, NetworkManagerVPN, SupervisedTransport, connection_settings
from adaletvpn.nm_worker import serve
from adaletvpn.ui import MainWindow
from test_networkmanager import certificate
from test_session_and_ipc import IPCProcess


class PinWarningTests(unittest.TestCase):
    def test_native_warning_stops_before_writing_any_secret(self):
        for banner in (b"This is the final try before locking!", b"Only a few tries left before locking!",
                       "Son deneme".encode(), b"unknown warning"):
            policy = FormAnswers(Credentials("ab123", "secret-pin", "secret-password"))
            option = Option(type=2, name=b"pkcs11_pin")
            form = Form(auth_id=b"pkcs11_pin", banner=banner, opts=C.pointer(option))
            setter = Mock(return_value=0)
            with self.assertRaises(CodedError) as caught:
                answer_native_form(form, policy, setter)
            expected = {b"This is the final try before locking!": "pin_final_warning",
                        b"Only a few tries left before locking!": "pin_low_warning"}.get(banner, "pin_attempts_low")
            self.assertEqual(caught.exception.code, expected)
            setter.assert_not_called()
            self.assertFalse(policy.pin_used)
            self.assertFalse(policy.login_used)

    def test_normal_native_pin_is_supplied_once(self):
        policy = FormAnswers(Credentials("ab123", "secret-pin", "secret-password"))
        option = Option(type=2, name=b"pkcs11_pin")
        form = Form(auth_id=b"pkcs11_pin", opts=C.pointer(option))
        setter = Mock(return_value=0)
        answer_native_form(form, policy, setter)
        self.assertEqual(setter.call_args.args[1], b"secret-pin")
        with self.assertRaises(CodedError):
            answer_native_form(form, policy, setter)
        self.assertEqual(setter.call_count, 1)

    def test_login_banner_is_not_a_card_warning(self):
        policy = FormAnswers(Credentials("ab123", "pin", "password"))
        result = policy.answer("login", [("username", 1), ("password", 2)], warning=True)
        self.assertEqual(result["username"], "ab123")


class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_status_disables_new_connection_but_allows_stop(self):
        window = self.window()
        window.controller.state = State.UNKNOWN
        window.task = None
        window.closing = window.cancelling = window.read_only = False
        window.managed_connection = True
        for name in ("status", "status_indicator", "status_spinner", "connection_actions", "details", "username", "pin", "password", "certificates",
                     "scan_button", "connect_button", "stop_button", "remember", "remember_pin", "certificate_info", "forget_button"):
            setattr(window, name, Mock())
        window.vault_task = None
        MainWindow.refresh(window)
        window.status.set_markup.assert_called_once_with('<span foreground="#1c71d8" weight="bold">Doğrulanamıyor</span>')
        window.connect_button.set_sensitive.assert_called_once_with(False)
        window.scan_button.set_sensitive.assert_called_once_with(False)
        window.username.set_sensitive.assert_called_once_with(False)
        window.stop_button.set_sensitive.assert_called_once_with(True)

    def window(self):
        client = SimpleNamespace(is_connected=AsyncMock(), disconnect=AsyncMock())
        controller = Controller(None, client)
        controller.state = State.CONNECTED
        controller._needs_cleanup = True
        return SimpleNamespace(controller=controller, message=Mock())

    async def test_query_error_leaves_unknown_not_connected(self):
        window = self.window()
        window.controller.client.is_connected.side_effect = CodedError("worker_unavailable")
        await MainWindow.check_connection(window)
        self.assertEqual(window.controller.state, State.UNKNOWN)
        self.assertIn("doğrulanamadı", window.controller.error)
        with self.assertRaises(ConnectionError):
            await window.controller.scan()
        await window.controller.disconnect()
        self.assertEqual(window.controller.state, State.DISCONNECTED)

    async def test_status_can_recover_from_unknown(self):
        window = self.window()
        window.controller.state = State.UNKNOWN
        window.controller.error = "previous error"
        window.controller.client.is_connected.return_value = True
        await MainWindow.check_connection(window)
        self.assertEqual(window.controller.state, State.CONNECTED)
        self.assertIsNone(window.controller.error)

    async def test_disconnected_after_unknown_is_cleaned(self):
        window = self.window()
        window.controller.state = State.UNKNOWN
        window.controller.client.is_connected.return_value = False
        await MainWindow.check_connection(window)
        window.controller.client.disconnect.assert_awaited_once()
        self.assertEqual(window.controller.state, State.ERROR)

    async def test_late_status_failure_does_not_override_disconnect(self):
        window = self.window()
        async def query():
            window.controller.state = State.DISCONNECTED
            raise CodedError("worker_unavailable")
        window.controller.client.is_connected.side_effect = query
        await MainWindow.check_connection(window)
        self.assertEqual(window.controller.state, State.DISCONNECTED)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def broken_client(self, returncode=1):
        process = IPCProcess()
        process.returncode = returncode
        process.wait.return_value = 1
        transport = SupervisedTransport(process)
        with self.assertRaises(CodedError):
            await transport.close()
        client = NetworkManagerVPN(transport=transport)
        client.active = "/org/freedesktop/NetworkManager/ActiveConnection/123"
        client.owned_uuid = connection_settings(certificate())["connection"]["uuid"]
        return client

    async def test_first_disconnect_accepts_a_worker_that_already_exited(self):
        process = IPCProcess()
        process.returncode = 0
        transport = SupervisedTransport(process)
        client = NetworkManagerVPN(transport=transport)
        client.active = "/org/freedesktop/NetworkManager/ActiveConnection/7"
        client.owned_uuid = connection_settings(certificate())["connection"]["uuid"]

        async def deactivate(active):
            transport.broken = True
            transport.process = None

            async def retired():
                return None

            transport.retirement = asyncio.create_task(retired())
            raise CodedError("network_operation")

        transport.deactivate = deactivate
        await client.disconnect()
        self.assertIsNone(client.active)
        self.assertIsNone(client.owned_uuid)
        self.assertIsNone(client.transport)

    async def test_first_disconnect_recovers_without_asking_again(self):
        process = IPCProcess()
        process.returncode = 1
        transport = SupervisedTransport(process)
        client = NetworkManagerVPN(transport=transport)
        client.active = "/org/freedesktop/NetworkManager/ActiveConnection/7"
        owned_uuid = connection_settings(certificate())["connection"]["uuid"]
        client.owned_uuid = owned_uuid

        async def deactivate(active):
            transport.broken = True

            async def failed():
                raise CodedError("cleanup_unconfirmed")

            transport.retirement = asyncio.create_task(failed())
            raise CodedError("network_operation")

        transport.deactivate = deactivate
        with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch.object(
                SupervisedTransport, "request", new_callable=AsyncMock) as request:
            await client.disconnect()
        request.assert_awaited_once_with("recover", owned_uuid)
        self.assertIsNone(client.active)
        self.assertIsNone(client.owned_uuid)

    async def test_first_disconnect_keeps_a_running_worker_for_retry(self):
        process = IPCProcess()
        process.returncode = None
        transport = SupervisedTransport(process)
        client = NetworkManagerVPN(transport=transport)
        client.active = "/org/freedesktop/NetworkManager/ActiveConnection/7"
        owned_uuid = connection_settings(certificate())["connection"]["uuid"]
        client.owned_uuid = owned_uuid

        async def deactivate(active):
            transport.broken = True

            async def failed():
                raise CodedError("cleanup_unconfirmed")

            transport.retirement = asyncio.create_task(failed())
            raise CodedError("network_operation")

        transport.deactivate = deactivate
        with patch.object(SupervisedTransport, "request", new_callable=AsyncMock) as request:
            with self.assertRaises(CodedError):
                await client.disconnect()
        request.assert_not_called()
        self.assertEqual(client.active, "/org/freedesktop/NetworkManager/ActiveConnection/7")
        self.assertEqual(client.owned_uuid, owned_uuid)

    async def test_retry_uses_fresh_worker_and_remembered_uuid(self):
        client = await self.broken_client()
        owned_uuid = client.owned_uuid
        old = client.transport
        with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch.object(
                SupervisedTransport, "request", new_callable=AsyncMock) as request:
            await client.disconnect()
        request.assert_awaited_once_with("recover", owned_uuid)
        self.assertIsNone(client.transport)
        self.assertIsNone(client.active)
        self.assertIsNone(client.owned_uuid)
        self.assertTrue(old.broken)

    async def test_failed_recovery_keeps_identity_for_another_retry(self):
        client = await self.broken_client()
        owned_uuid = client.owned_uuid
        with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch.object(
                SupervisedTransport, "request", new_callable=AsyncMock) as request:
            request.side_effect = [CodedError("network_operation"), None]
            with self.assertRaises(CodedError):
                await client.disconnect()
            self.assertEqual(client.owned_uuid, owned_uuid)
            self.assertIsNotNone(client.active)
            await client.disconnect()
            self.assertEqual(request.await_count, 2)
        self.assertIsNone(client.active)

    async def test_running_old_worker_prevents_recovery(self):
        client = await self.broken_client(returncode=None)
        with patch.object(SupervisedTransport, "request", new_callable=AsyncMock) as request:
            with self.assertRaises(CodedError):
                await client.disconnect()
        request.assert_not_called()

    @patch("adaletvpn.networkmanager.LIVE_ENABLED", False)
    async def test_live_lock_also_prevents_recovery(self):
        client = await self.broken_client()
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            with self.assertRaises(ConnectionError):
                await client.disconnect()
        spawn.assert_not_called()

    def gio(self, settings, active_uuid=None, unsaved=True):
        transport = object.__new__(GioTransport)
        transport.GLib = GLib
        transport.profile_path = None
        owned_uuid = settings["connection"]["uuid"]
        transport.call = AsyncMock(side_effect=[
            (["/profile/owned"],), (settings,), ({"Unsaved": unsaved, "Filename": ""},),
            (["/active/foreign", "/active/owned"],),
            ({"Uuid": "foreign", "Connection": "/profile/foreign"},),
            ({"Uuid": active_uuid or owned_uuid, "Connection": "/profile/owned"},),
            (),
        ])
        transport.deactivate = AsyncMock()
        return transport, owned_uuid

    async def test_gio_recovery_only_touches_matching_owned_profile(self):
        transport, owned_uuid = self.gio(connection_settings(certificate()))
        await transport.recover(owned_uuid)
        transport.deactivate.assert_awaited_once_with("/active/owned")
        self.assertEqual(transport.call.call_args.args[:3],
                         ("/profile/owned", transport.DEST + ".Settings.Connection", "Delete"))

    async def test_saved_or_foreign_owner_profile_is_never_removed(self):
        for mutation in ("saved", "user", "missing-owner", "permissions", "service", "type"):
            settings = connection_settings(certificate())
            if mutation == "user":
                settings["user"]["data"]["com.adalettekno.AdaletVPN.owner-uid"] = "foreign-uid"
            if mutation == "missing-owner":
                del settings["user"]
            if mutation == "permissions":
                settings["connection"]["permissions"] = ["user:someone-else:"]
            if mutation == "service":
                settings["vpn"]["service-type"] = "other"
            if mutation == "type":
                settings["connection"]["type"] = "ethernet"
            transport, owned_uuid = self.gio(settings, unsaved=mutation != "saved")
            with self.assertRaises(CodedError):
                await transport.recover(owned_uuid)
            transport.deactivate.assert_not_awaited()
            self.assertNotIn("Delete", [call.args[2] for call in transport.call.call_args_list])

    async def test_no_profile_and_no_active_session_is_already_clean(self):
        transport, owned_uuid = self.gio(connection_settings(certificate()))
        transport.call.side_effect = [([],), ([],)]
        await transport.recover(owned_uuid)
        transport.deactivate.assert_not_awaited()

    async def test_file_backed_profile_is_never_deleted_even_if_unsaved(self):
        settings = connection_settings(certificate())
        transport, owned_uuid = self.gio(settings)
        transport.call.side_effect = [(["/profile/owned"],), (settings,),
                                     ({"Unsaved": True, "Filename": "/system/profile"},)]
        with self.assertRaises(CodedError):
            await transport.recover(owned_uuid)
        transport.deactivate.assert_not_awaited()
        self.assertNotIn("Delete", [call.args[2] for call in transport.call.call_args_list])

    async def test_runtime_profile_with_matching_owner_is_recovered(self):
        settings = connection_settings(certificate())
        transport, owned_uuid = self.gio(settings)
        transport.call.side_effect = [(["/profile/owned"],), (settings,),
            ({"Unsaved": True, "Filename": "/run/NetworkManager/system-connections/trial.nmconnection"},),
            ([],), ()]
        await transport.recover(owned_uuid)
        self.assertEqual(transport.call.call_args.args[2], "Delete")

    def test_runtime_path_is_not_a_general_file_exception(self):
        from adaletvpn.networkmanager import transient_profile
        for filename in (None, "/etc/NetworkManager/system-connections/trial.nmconnection",
                         "/run/NetworkManager/system-connections/../other.nmconnection",
                         "/tmp/trial.nmconnection", "/run/NetworkManager/system-connections/trial.txt"):
            with self.subTest(filename=filename):
                self.assertFalse(transient_profile({"Unsaved": True, "Filename": filename}))
        self.assertFalse(transient_profile({"Unsaved": False,
            "Filename": "/run/NetworkManager/system-connections/trial.nmconnection"}))

    async def test_matching_uuid_with_wrong_active_profile_is_rejected(self):
        settings = connection_settings(certificate())
        transport, owned_uuid = self.gio(settings)
        transport.call.side_effect = [(["/profile/owned"],), (settings,),
                                     ({"Unsaved": True, "Filename": ""},), (["/active/other"],),
                                     ({"Uuid": owned_uuid, "Connection": "/profile/other"},)]
        with self.assertRaises(CodedError):
            await transport.recover(owned_uuid)
        transport.deactivate.assert_not_awaited()
        self.assertNotIn("Delete", [call.args[2] for call in transport.call.call_args_list])

    async def test_worker_dispatches_recovery_without_activation(self):
        transport = SimpleNamespace(recover=AsyncMock(), delete_profile=AsyncMock())
        frames = iter([json.dumps({"id": 1, "method": "recover", "value": "owned-uuid"}), ""])
        async def read():
            return next(frames)
        replies = []
        await serve(transport, read, replies.append)
        transport.recover.assert_awaited_once_with("owned-uuid")
        self.assertEqual(replies, [{"id": 1, "ok": True, "value": None}])
