import asyncio
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from adaletvpn.core import Certificate, ConnectionError, Credentials
from adaletvpn.networkmanager import NetworkManagerVPN, GioTransport, connection_settings
from adaletvpn.preflight import VPNProfile, validate_certificate
from adaletvpn.nm_worker import serve


def certificate():
    now = datetime.now(timezone.utc)
    return Certificate("pkcs11:token=Test;serial=123;id=%01;type=cert", "Test", "Test", "Test",
                       not_before=now - timedelta(days=1), not_after=now + timedelta(days=1),
                       digital_signature=True, client_auth=True, is_ca=False)


class PreparationTests(unittest.TestCase):
    def test_invalid_certificates_rejected(self):
        item = certificate()
        for changes in ({"not_after": item.not_before}, {"not_before": None},
                        {"details_error": "Okunamadı"}, {"is_ca": True},
                        {"digital_signature": False}, {"client_auth": False},
                        {"identifier": "pkcs11:id=%01;type=cert"}):
            with self.subTest(changes=changes), self.assertRaises(ConnectionError):
                validate_certificate(replace(item, **changes))

    def test_settings_have_no_passwords_and_enforce_tls(self):
        settings = connection_settings(certificate())
        self.assertFalse(settings["connection"]["autoconnect"])
        self.assertEqual(settings["connection"]["permissions"], [])
        self.assertEqual(settings["user"]["data"]["com.adalettekno.AdaletVPN.owner-uid"], str(os.getuid()))
        data = settings["vpn"]["data"]
        self.assertEqual(data["gateway"], "vpn.uyap.gov.tr")
        self.assertEqual(data["prevent_invalid_cert"], "yes")
        self.assertEqual(data["enable_csd_trojan"], "no")
        self.assertEqual(data["cookie-flags"], "2")
        self.assertNotIn("secrets", settings["vpn"])
        self.assertNotIn("key_pass", data)

    def test_gateway_is_restricted(self):
        for address in ("http://vpn.uyap.gov.tr", "https://evil.example", "https://user:pass@vpn.uyap.gov.tr",
                        "https://vpn.uyap.gov.tr?pin=123", "https://vpn.uyap.gov.tr/other"):
            with self.subTest(address=address), self.assertRaises(ConnectionError):
                VPNProfile(gateway=address).validate()

    @patch("adaletvpn.networkmanager.LIVE_ENABLED", False)
    def test_real_transport_disabled_before_bus_access(self):
        with self.assertRaises(ConnectionError):
            GioTransport()

    def test_all_credentials_cleared_and_hidden(self):
        credentials = Credentials("user-secret", "pin-secret", "password-secret")
        for secret in (credentials.username, credentials.pin, credentials.password):
            self.assertNotIn(secret, repr(credentials))
        credentials.clear()
        self.assertEqual((credentials.username, credentials.pin, credentials.password), ("", "", ""))


class FakeAuthenticator:
    async def authenticate(self, certificate, credentials, profile):
        assert credentials.username == "ab123456"
        assert credentials.pin == "test-pin"
        assert credentials.password == "test-password"
        return {"cookie": "test-cookie", "gateway": profile.gateway, "gwcert": "pin-sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}


class FakeTransport:
    def __init__(self):
        self.current_state = 2
        self.closed = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.fail_cleanup = False

    async def activate(self, settings):
        self.started.set()
        await self.release.wait()
        return "/owned/session"

    async def state(self, active):
        return self.current_state

    async def deactivate(self, active):
        if self.fail_cleanup:
            raise ConnectionError("Temizlenemedi")
        self.closed.append(active)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = FakeTransport()
        self.client = NetworkManagerVPN(self.transport, timeout=0.02, authenticator=FakeAuthenticator())
        await self.client.authenticate(certificate(), Credentials("ab123456", "test-pin", "test-password"))

    async def test_ready_then_disconnect_only_owned_connection(self):
        await self.client.connect()
        self.assertTrue(await self.client.is_connected())
        await self.client.disconnect()
        await self.client.disconnect()
        self.assertEqual(self.transport.closed, ["/owned/session"])

    async def test_authentication_clears_credentials_and_only_keeps_session(self):
        credentials = Credentials("ab123456", "test-pin", "test-password")
        await self.client.authenticate(certificate(), credentials)
        self.assertEqual((credentials.username, credentials.pin, credentials.password), ("", "", ""))
        self.assertNotIn("test-pin", repr(self.client.settings))
        self.assertNotIn("test-password", repr(self.client.settings))
        self.assertEqual(self.client.settings["vpn"]["secrets"]["cookie"], "test-cookie")

    async def test_failed_authentication_clears_fields_and_prevents_activation(self):
        from unittest.mock import AsyncMock
        self.client.authenticator.authenticate = AsyncMock(side_effect=ConnectionError("Reddedildi"))
        credentials = Credentials("ab123456", "test-pin", "test-password")
        with self.assertRaises(ConnectionError):
            await self.client.authenticate(certificate(), credentials)
        self.assertEqual((credentials.username, credentials.pin, credentials.password), ("", "", ""))
        self.assertIsNone(self.client.settings)
        self.assertFalse(self.transport.started.is_set())

    async def test_timeout_before_ready_cleans_up(self):
        self.transport.current_state = 1
        with self.assertRaises(ConnectionError):
            await self.client.connect()
        self.assertEqual(self.transport.closed, ["/owned/session"])

    async def test_cancel_waits_for_late_activation_then_cleans_up(self):
        self.transport.release.clear()
        task = asyncio.create_task(self.client.connect())
        await self.transport.started.wait()
        task.cancel()
        self.transport.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.transport.closed, ["/owned/session"])

    async def test_cleanup_failure_keeps_session_for_retry(self):
        await self.client.connect()
        self.transport.fail_cleanup = True
        with self.assertRaises(ConnectionError):
            await self.client.disconnect()
        self.assertEqual(self.client.active, "/owned/session")
        self.transport.fail_cleanup = False
        await self.client.disconnect()
        self.assertIsNone(self.client.active)

    async def test_invalid_credentials_rejected_before_authentication(self):
        with self.assertRaises(ConnectionError):
            await self.client.authenticate(certificate(), Credentials("u", "pin", "password"))
        self.assertIsNone(self.client.settings)

    @patch("adaletvpn.networkmanager.LIVE_ENABLED", False)
    async def test_live_connection_cannot_start(self):
        client = NetworkManagerVPN(authenticator=FakeAuthenticator())
        await client.authenticate(certificate(), Credentials("ab123456", "test-pin", "test-password"))
        with self.assertRaises(ConnectionError):
            await client.connect()

    async def test_disconnect_is_detectable(self):
        await self.client.connect()
        self.transport.current_state = 4
        self.assertFalse(await self.client.is_connected())

    async def test_parent_exit_cleans_up_owned_session(self):
        import json
        lines = iter([json.dumps({"id": 1, "method": "activate", "value": {}}), ""])
        async def read():
            return next(lines)
        replies = []
        await serve(self.transport, read, replies.append)
        self.assertEqual(self.transport.closed, ["/owned/session"])

    async def test_worker_rejects_foreign_session(self):
        import json
        lines = iter([json.dumps({"id": 1, "method": "activate", "value": {}}),
                      json.dumps({"id": 1, "method": "deactivate", "value": "/foreign/session"}), ""])
        async def read():
            return next(lines)
        replies = []
        await serve(self.transport, read, replies.append)
        self.assertFalse(replies[1]["ok"])
        self.assertEqual(self.transport.closed, ["/owned/session"])

    async def test_dbus_profile_is_unsaved_and_vpn_uses_activate_connection(self):
        from gi.repository import GLib
        from unittest.mock import AsyncMock
        transport = object.__new__(GioTransport)
        transport.GLib = GLib
        transport.profile_path = None
        transport.prepare_agent = AsyncMock()
        transport.call = AsyncMock(side_effect=[("/profile/owned",), ("/active/owned",)])
        settings = connection_settings(certificate())
        settings["vpn"]["secrets"] = {"cookie": "test-cookie", "gateway": "https://vpn2.uyap.gov.tr/",
            "gwcert": "pin-sha256:" + "A" * 43 + "="}
        active = await transport.activate(settings)
        self.assertEqual(active, "/active/owned")
        calls = transport.call.call_args_list
        self.assertEqual(calls[0].args[2], "AddConnectionUnsaved")
        self.assertNotIn("secrets", calls[0].args[3].unpack()[0]["vpn"])
        transport.prepare_agent.assert_awaited_once()
        self.assertEqual(calls[1].args[2], "ActivateConnection")
        self.assertEqual(calls[1].args[3].unpack(), ("/profile/owned", "/", "/"))
