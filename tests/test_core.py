import asyncio
import unittest

from adaletvpn.core import Certificate, ConnectionError, Controller, Credentials, State


FIRST = Certificate("device-1/cert-1", "Test cihazı 1", "Test kullanıcısı", "Test kurumu")
SECOND = Certificate("device-2/cert-1", "Test cihazı 2", "Test kullanıcısı", "Test kurumu")


class FakeProvider:
    def __init__(self):
        self.items = [FIRST]
        self.failure = False

    async def list_certificates(self):
        if self.failure:
            raise RuntimeError("Hassas cihaz çıktısı")
        return tuple(self.items)


class FakeVPN:
    def __init__(self):
        self.calls = []
        self.auth_error = None
        self.cleanup_error = False
        self.started = asyncio.Event()
        self.ready = asyncio.Event()
        self.ready.set()

    async def authenticate(self, certificate, credentials):
        self.calls.append("authenticate")
        if self.auth_error:
            raise self.auth_error

    async def connect(self):
        self.calls.append("connect")
        self.started.set()
        await self.ready.wait()

    async def disconnect(self):
        self.calls.append("disconnect")
        if self.cleanup_error:
            raise RuntimeError("Hassas süreç çıktısı")


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_error_code_reaches_interface(self):
        from adaletvpn.errors import CodedError
        await self.controller.scan()
        self.vpn.auth_error = CodedError("untrusted_server")
        with self.assertRaises(ConnectionError) as caught:
            await self.controller.connect(self.credentials())
        self.assertEqual(str(caught.exception), str(self.vpn.auth_error))
        self.assertEqual(self.controller.error, str(self.vpn.auth_error))
    async def asyncSetUp(self):
        self.provider = FakeProvider()
        self.vpn = FakeVPN()
        self.controller = Controller(self.provider, self.vpn, timeout=1)

    def credentials(self):
        return Credentials("test-user", "test-pin")

    async def test_no_certificate_prevents_connection(self):
        self.provider.items = []
        self.assertEqual(await self.controller.scan(), ())
        credentials = self.credentials()
        with self.assertRaises(ConnectionError):
            await self.controller.connect(credentials)
        self.assertEqual(self.vpn.calls, [])
        self.assertEqual(credentials.pin, "")

    async def test_single_certificate_is_selected(self):
        await self.controller.scan()
        self.assertEqual(self.controller.selected_id, FIRST.identifier)
        credentials = self.credentials()
        await self.controller.connect(credentials)
        self.assertEqual(self.controller.state, State.CONNECTED)
        self.assertEqual(credentials.pin, "")
        self.assertEqual(credentials.username, "")
        await self.controller.disconnect()
        await self.controller.disconnect()
        self.assertEqual(self.controller.state, State.DISCONNECTED)
        self.assertEqual(self.vpn.calls, ["authenticate", "connect", "disconnect"])

    async def test_multiple_certificates_require_explicit_selection(self):
        self.provider.items = [FIRST, SECOND]
        await self.controller.scan()
        with self.assertRaises(ConnectionError):
            await self.controller.connect(self.credentials())
        with self.assertRaises(ConnectionError):
            self.controller.select("unknown")
        self.controller.select(SECOND.identifier)
        await self.controller.connect(self.credentials())
        self.assertEqual(self.controller.state, State.CONNECTED)

    async def test_removed_device_prevents_authentication(self):
        await self.controller.scan()
        self.provider.items = []
        with self.assertRaises(ConnectionError):
            await self.controller.connect(self.credentials())
        self.assertEqual(self.vpn.calls, [])
        self.assertIsNone(self.controller.selected_id)
        self.assertEqual(self.controller.state, State.ERROR)

    async def test_rescan_discards_old_selection(self):
        await self.controller.scan()
        self.provider.items = [FIRST, SECOND]
        await self.controller.scan()
        self.assertIsNone(self.controller.selected_id)

    async def test_cancelled_scan_restores_the_previous_list(self):
        await self.controller.scan()
        selected = self.controller.selected_id
        started = asyncio.Event()

        async def block():
            started.set()
            await asyncio.Future()

        self.provider.list_certificates = block
        task = asyncio.create_task(self.controller.scan())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.controller.certificates, (FIRST,))
        self.assertEqual(self.controller.selected_id, selected)
        self.assertEqual(self.controller.state, State.DISCONNECTED)
        self.assertIsNone(self.controller.error)
        self.assertEqual(self.controller.scan_detail, "")
        self.assertFalse(self.controller._busy)

    async def test_cancelled_rescan_restores_the_previous_error(self):
        self.provider.failure = True
        with self.assertRaises(ConnectionError):
            await self.controller.scan()
        detail = self.controller.scan_detail
        self.provider.failure = False
        started = asyncio.Event()

        async def block():
            started.set()
            await asyncio.Future()

        self.provider.list_certificates = block
        task = asyncio.create_task(self.controller.scan())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.controller.state, State.ERROR)
        self.assertEqual(self.controller.error, "Sertifika bulunamadı!")
        self.assertEqual(self.controller.scan_detail, detail)
        self.assertEqual(self.controller.certificates, ())
        self.assertFalse(self.controller._busy)

    async def test_scan_failure_is_safe_and_recoverable(self):
        self.provider.failure = True
        with self.assertRaises(ConnectionError) as caught:
            await self.controller.scan()
        self.assertNotIn("Hassas", str(caught.exception))
        self.assertEqual(self.controller.state, State.ERROR)
        self.provider.failure = False
        await self.controller.scan()
        self.assertEqual(self.controller.state, State.DISCONNECTED)

    async def test_ambiguous_identifiers_are_rejected(self):
        self.provider.items = [FIRST, FIRST]
        with self.assertRaises(ConnectionError):
            await self.controller.scan()
        self.assertEqual(self.controller.certificates, ())

    async def test_empty_credentials_do_not_start_vpn(self):
        await self.controller.scan()
        for credentials in [Credentials(" ", "pin"), Credentials("user", "")]:
            with self.assertRaises(ConnectionError):
                await self.controller.connect(credentials)
            self.assertEqual(credentials.pin, "")
        self.assertEqual(self.vpn.calls, [])

    async def test_wrong_pin_and_permission_denial_are_not_retried(self):
        await self.controller.scan()
        for error in [ValueError("test-pin"), PermissionError("test-user")]:
            self.vpn.calls.clear()
            self.vpn.auth_error = error
            credentials = self.credentials()
            with self.assertRaises(ConnectionError) as caught:
                await self.controller.connect(credentials)
            self.assertNotIn("test-pin", str(caught.exception))
            self.assertNotIn("test-user", self.controller.error)
            self.assertEqual(credentials.pin, "")
            self.assertEqual(self.vpn.calls, ["authenticate", "disconnect"])

    async def test_connecting_is_not_connected_and_reentry_is_rejected(self):
        await self.controller.scan()
        self.vpn.ready.clear()
        task = asyncio.create_task(self.controller.connect(self.credentials()))
        await self.vpn.started.wait()
        try:
            self.assertEqual(self.controller.state, State.CONNECTING)
            with self.assertRaises(ConnectionError):
                await self.controller.connect(self.credentials())
            with self.assertRaises(ConnectionError):
                await self.controller.scan()
            with self.assertRaises(ConnectionError):
                self.controller.select(FIRST.identifier)
        finally:
            self.vpn.ready.set()
            await task
        self.assertEqual(self.vpn.calls.count("connect"), 1)

    async def test_cancellation_cleans_up(self):
        await self.controller.scan()
        self.vpn.ready.clear()
        credentials = self.credentials()
        task = asyncio.create_task(self.controller.connect(credentials))
        await self.vpn.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.controller.state, State.DISCONNECTED)
        self.assertEqual(credentials.pin, "")
        self.assertEqual(self.vpn.calls[-1], "disconnect")

    async def test_timeout_cleans_up(self):
        await self.controller.scan()
        self.controller.timeout = 0.01
        self.vpn.ready.clear()
        with self.assertRaisesRegex(ConnectionError, "zaman aşımı"):
            await self.controller.connect(self.credentials())
        self.assertEqual(self.controller.state, State.ERROR)
        self.assertEqual(self.vpn.calls[-1], "disconnect")

    async def test_failed_cleanup_blocks_reconnect_until_retry(self):
        await self.controller.scan()
        await self.controller.connect(self.credentials())
        self.vpn.cleanup_error = True
        with self.assertRaises(ConnectionError):
            await self.controller.disconnect()
        with self.assertRaises(ConnectionError):
            await self.controller.connect(self.credentials())
        self.vpn.cleanup_error = False
        await self.controller.disconnect()
        await self.controller.connect(self.credentials())
        self.assertEqual(self.controller.state, State.CONNECTED)

    async def test_unexpected_disconnect(self):
        await self.controller.scan()
        await self.controller.connect(self.credentials())
        await self.controller.connection_lost()
        self.assertEqual(self.controller.state, State.ERROR)
        self.assertEqual(self.vpn.calls[-1], "disconnect")

    def test_credentials_repr_omits_secrets(self):
        self.assertNotIn("test-pin", repr(self.credentials()))
        self.assertNotIn("test-user", repr(self.credentials()))


if __name__ == "__main__":
    unittest.main()
