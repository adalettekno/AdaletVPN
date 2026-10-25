import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from adaletvpn.auth import FormAnswers, NativeAuthenticator, credential_problem
from adaletvpn.core import ConnectionError, Credentials
from adaletvpn.errors import CodedError
from adaletvpn.preflight import VPNProfile
from test_networkmanager import certificate


class FormTests(unittest.TestCase):
    def test_error_codes_distinguish_pin_login_and_unknown_forms(self):
        for auth_id, options, error, expected in (
                ("pkcs11_pin", [("pkcs11_pin", 2)], True, "pin_rejected"),
                ("main", [("username", 1), ("password", 2)], True, "login_rejected"),
                ("other", [("otp", 2)], False, "unsupported_form")):
            policy = FormAnswers(Credentials("ab123", "pin", "password"))
            with self.assertRaises(CodedError) as caught:
                policy.answer(auth_id, options, error)
            self.assertEqual(caught.exception.code, expected)
    def test_required_fields_and_ab_prefix(self):
        for username, pin, password in (("12345", "pin", "pass"), ("ab", "pin", "pass"),
                                         ("ab12x", "pin", "pass"), ("ab123", "", "pass"),
                                         ("ab123", "pin", ""), ("ab123", "pin", "pass\n")):
            self.assertIsNotNone(credential_problem(username, pin, password))
        self.assertIsNone(credential_problem("ab123456", "pin", "Türkçe şifre!"))

    def test_pin_and_password_are_used_for_different_forms(self):
        policy = FormAnswers(Credentials("ab123", "pin-secret", "password-secret"))
        self.assertEqual(policy.answer("pkcs11_pin", [("pkcs11_pin", 2)]), {"pkcs11_pin": "pin-secret"})
        self.assertEqual(policy.answer("main", [("username", 1), ("password", 2)]),
                         {"username": "ab123", "password": "password-secret"})

    def test_pin_is_not_retried(self):
        policy = FormAnswers(Credentials("ab123", "pin", "password"))
        policy.answer("pkcs11_pin", [("pkcs11_pin", 2)])
        with self.assertRaises(ConnectionError):
            policy.answer("pkcs11_pin", [("pkcs11_pin", 2)])

    def test_wrong_pin_never_receives_computer_password(self):
        policy = FormAnswers(Credentials("ab123", "pin", "password"))
        with self.assertRaises(ConnectionError):
            policy.answer("pkcs11_pin", [("pkcs11_pin", 2)], error=True)
        self.assertFalse(policy.login_used)

    def test_unknown_fields_are_rejected(self):
        policy = FormAnswers(Credentials("ab123", "pin", "password"))
        with self.assertRaises(ConnectionError):
            policy.answer("other", [("otp", 2)])

    def test_password_is_not_retried(self):
        policy = FormAnswers(Credentials("ab123", "pin", "password"))
        policy.answer("main", [("username", 1), ("password", 2)])
        with self.assertRaises(ConnectionError):
            policy.answer("main", [("username", 1), ("password", 2)])


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_error_codes_are_allowlisted(self):
        import json
        for code in ("pin_rejected", "pin_attempts_low", "login_rejected", "untrusted_server", "unsupported_form", "secret-raw-error"):
            process = AsyncMock()
            process.returncode = 1
            process.communicate.return_value = (json.dumps({"error": code}).encode(), b"")
            with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch(
                    "asyncio.create_subprocess_exec", return_value=process):
                with self.assertRaises(CodedError) as caught:
                    await NativeAuthenticator().authenticate(certificate(), Credentials("ab123", "pin", "password"), VPNProfile())
            self.assertEqual(caught.exception.code, "auth_failed" if code == "secret-raw-error" else code)
            self.assertNotIn("secret-raw-error", str(caught.exception))
    @patch("adaletvpn.networkmanager.LIVE_ENABLED", False)
    async def test_live_lock_prevents_process_creation(self):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            with self.assertRaises(ConnectionError):
                await NativeAuthenticator().authenticate(certificate(), Credentials("ab123", "pin", "password"), VPNProfile())
        spawn.assert_not_called()

    async def test_secrets_only_go_through_pipe(self):
        process = AsyncMock()
        process.returncode = 0
        process.communicate.return_value = (b'{"cookie":"test-cookie","gateway":"https://vpn.uyap.gov.tr/","gwcert":"pin-sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}', b"")
        with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch(
                "asyncio.create_subprocess_exec", return_value=process) as spawn:
            await NativeAuthenticator().authenticate(certificate(), Credentials("ab123", "pin-secret", "password-secret"), VPNProfile())
        args, kwargs = spawn.call_args
        self.assertNotIn("pin-secret", repr(args))
        self.assertNotIn("password-secret", repr(args))
        self.assertNotIn("password-secret", repr(kwargs))
        payload = process.communicate.call_args.args[0]
        self.assertIn(b'"pin": "pin-secret"', payload)
        self.assertIn(b'"password": "password-secret"', payload)

    async def test_timeout_reaps_authentication_worker(self):
        from unittest.mock import Mock
        process = AsyncMock()
        process.returncode = None
        process.kill = Mock()
        process.communicate.side_effect = [asyncio.TimeoutError(), (b"", b"")]
        with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch(
                "asyncio.create_subprocess_exec", return_value=process):
            with self.assertRaisesRegex(ConnectionError, "zaman aşımı"):
                await NativeAuthenticator().authenticate(certificate(), Credentials("ab123", "pin", "password"), VPNProfile())
        process.kill.assert_called_once()
