import ctypes as C
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gi.repository import GLib
from adaletvpn.auth import FormAnswers, NativeAuthenticator
from adaletvpn.auth_worker import Form, Option, answer_native_form
from adaletvpn.core import Credentials
from adaletvpn.errors import CodedError, MESSAGES
from adaletvpn.networkmanager import GioTransport, connection_settings
from adaletvpn.nm_worker import serve
from adaletvpn.preflight import VPNProfile
from adaletvpn.ui import show_message
from test_networkmanager import certificate

LOW = b"Only a few tries left before locking!"
FINAL = b"This is the final try before locking!"


class PinTests(unittest.TestCase):
    def test_wrong_pin_messages_and_red_rendering(self):
        for banner, code, message in (
            (LOW, "pin_two_left", "Hatalı pin! 2 deneme hakkınız kaldı!"),
            (FINAL, "pin_last_left", "Hatalı pin! Son deneme hakkınız kaldı. Hatalı pin girildiği takdirde elektronik imzanız bloke olacaktır!")):
            policy = FormAnswers(Credentials("ab123", "pin", "password"))
            setter = Mock()
            with self.assertRaises(CodedError) as caught:
                answer_native_form(Form(auth_id=b"pkcs11_pin", error=b"Wrong PIN", banner=banner), policy, setter)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(str(caught.exception), message)
            setter.assert_not_called()
            label = Mock()
            show_message(label, message)
            self.assertIn('foreground="#c01c28"', label.set_markup.call_args.args[0])
            self.assertIn(message, label.set_markup.call_args.args[0])

    def test_manual_retry_requires_matching_warning_and_never_retries_automatically(self):
        for level, banner in (("low", LOW), ("final", FINAL)):
            option = Option(type=2, name=b"pkcs11_pin")
            form = Form(auth_id=b"pkcs11_pin", banner=banner, opts=C.pointer(option))
            setter = Mock(return_value=0)
            policy = FormAnswers(Credentials("ab123", "pin", "password"), level)
            answer_native_form(form, policy, setter)
            form.error = b"Wrong PIN"
            with self.assertRaises(CodedError):
                answer_native_form(form, policy, setter)
            self.assertEqual(setter.call_count, 1)

    def test_external_attempt_reducing_counter_stops_before_pin(self):
        policy = FormAnswers(Credentials("ab123", "pin", "password"), "low")
        setter = Mock()
        with self.assertRaises(CodedError) as caught:
            answer_native_form(Form(auth_id=b"pkcs11_pin", banner=FINAL), policy, setter)
        self.assertEqual(caught.exception.code, "pin_final_warning")
        setter.assert_not_called()

    def test_error_without_card_counter_does_not_invent_two_attempts(self):
        policy = FormAnswers(Credentials("ab123", "pin", "password"))
        with self.assertRaises(CodedError) as caught:
            answer_native_form(Form(auth_id=b"pkcs11_pin", error=b"Wrong PIN"), policy, Mock())
        self.assertEqual(caught.exception.code, "pin_rejected")


class WarningMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_warning_is_scoped_to_certificate_and_consumed_on_manual_retry(self):
        auth = NativeAuthenticator()
        process = AsyncMock()
        process.returncode = 1
        process.communicate.side_effect = [(b'{"error":"pin_two_left"}', b''),
                                           (b'{"error":"auth_failed"}', b'')]
        cert = certificate()
        with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch(
                "asyncio.create_subprocess_exec", return_value=process):
            for _ in range(2):
                with self.assertRaises(CodedError):
                    await auth.authenticate(cert, Credentials("ab123", "pin", "password"), VPNProfile())
        payloads = [json.loads(call.args[0]) for call in process.communicate.call_args_list]
        self.assertIsNone(payloads[0]["pin_warning_ack"])
        self.assertEqual(payloads[1]["pin_warning_ack"], "low")
        self.assertEqual(auth.pin_warnings, {})


class UncertainProfileTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, fail_delete=False):
        settings = connection_settings(certificate())
        settings["vpn"]["secrets"] = {"cookie": "test-cookie", "gateway": "https://vpn.uyap.gov.tr/",
            "gwcert": "pin-sha256:" + "A" * 43 + "="}
        transport = object.__new__(GioTransport)
        transport.GLib = GLib
        transport.profile_path = None
        transport.pending_uuid = None
        profiles = set()
        async def call(path, interface, method, parameters, reply):
            if method == "AddConnectionUnsaved":
                profiles.add("/owned/profile")
                raise CodedError("network_operation")
            if method == "ListConnections":
                return (list(profiles),)
            if method == "GetSettings":
                return (settings,)
            if method == "GetAll":
                return ({"Unsaved": True, "Filename": ""},)
            if method == "Get":
                return ([],)
            if method == "Delete":
                if fail_delete:
                    raise CodedError("network_operation")
                profiles.remove(path)
                return ()
            raise AssertionError(method)
        transport.call = call
        frames = iter([json.dumps({"id": 1, "method": "activate", "value": settings}), ""])
        async def read():
            return next(frames)
        replies = []
        if fail_delete:
            with self.assertRaises(CodedError):
                await serve(transport, read, replies.append)
        else:
            await serve(transport, read, replies.append)
        self.assertFalse(replies[0]["ok"])
        return transport, profiles, settings

    async def test_created_profile_with_lost_reply_is_found_and_removed_on_eof(self):
        transport, profiles, _ = await self.exercise()
        self.assertEqual(profiles, set())
        self.assertIsNone(transport.pending_uuid)

    async def test_failed_cleanup_does_not_report_normal_worker_exit_or_forget_uuid(self):
        transport, profiles, settings = await self.exercise(fail_delete=True)
        self.assertEqual(transport.pending_uuid, settings["connection"]["uuid"])
        self.assertEqual(profiles, {"/owned/profile"})
        transport.recover = AsyncMock()
        await transport.delete_profile()
        transport.recover.assert_awaited_once_with(settings["connection"]["uuid"], require_profile=True)
        self.assertIsNone(transport.pending_uuid)
