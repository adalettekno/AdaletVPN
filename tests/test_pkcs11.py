import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from adaletvpn.core import ConnectionError, Credentials
from adaletvpn.pkcs11 import DisabledVPN, PKCS11Certificates, parse_certificates


URI = "pkcs11:token=Test%20cihaz;serial=123;object=Test%20sertifika;id=%01;type=cert"


class ParsingTests(unittest.TestCase):
    def test_label_and_stable_identifier(self):
        item, = parse_certificates(URI + "\n")
        self.assertEqual(item.identifier, URI)
        self.assertEqual(item.device, "Test cihaz")
        self.assertEqual(item.label, "Test sertifika")

    def test_duplicate_registration_is_collapsed(self):
        self.assertEqual(len(parse_certificates(URI + "\n" + URI)), 1)

    def test_empty_list(self):
        self.assertEqual(parse_certificates("\n"), ())

    def test_invalid_or_secret_bearing_uri_is_rejected(self):
        for value in ("unexpected text", URI + "?pin-value=secret",
                      URI + ";pin-value=secret", URI.replace("type=cert", "type=private"),
                      URI.replace("id=%01;", "")):
            with self.subTest(value=value):
                with self.assertRaises(ConnectionError):
                    parse_certificates(value)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_command_and_pin_environment(self):
        process = AsyncMock()
        process.returncode = 0
        process.communicate.return_value = (URI.encode(), b"")
        with patch.dict(os.environ, {"GNUTLS_PIN": "secret", "GNUTLS_SO_PIN": "secret"}):
            with patch("asyncio.create_subprocess_exec", return_value=process) as spawn:
                items = await PKCS11Certificates(__file__).list_certificates()
        self.assertEqual(len(items), 1)
        args, kwargs = spawn.call_args_list[0]
        self.assertIn("--batch", args)
        self.assertIn("--list-all-certs", args)
        self.assertNotIn("--login", args)
        self.assertNotIn("GNUTLS_PIN", kwargs["env"])
        self.assertNotIn("GNUTLS_SO_PIN", kwargs["env"])
        self.assertEqual(kwargs["stdin"], asyncio.subprocess.DEVNULL)

    async def test_driver_error_does_not_expose_output(self):
        process = AsyncMock()
        process.returncode = -6
        process.communicate.return_value = (b"personal-data", b"secret-error")
        with patch("asyncio.create_subprocess_exec", return_value=process):
            with self.assertRaises(ConnectionError) as caught:
                await PKCS11Certificates(__file__).list_certificates()
        self.assertNotIn("secret-error", str(caught.exception))
        self.assertNotIn("personal-data", str(caught.exception))

    async def test_timeout_kills_and_reaps_process(self):
        process = AsyncMock()
        process.returncode = None
        process.kill = unittest.mock.Mock()
        process.communicate.side_effect = [asyncio.TimeoutError(), (b"", b"")]
        with patch("asyncio.create_subprocess_exec", return_value=process):
            with self.assertRaises(asyncio.TimeoutError):
                await PKCS11Certificates(__file__).list_certificates()
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.await_count, 2)

    async def test_read_only_client_cannot_connect(self):
        client = DisabledVPN()
        with self.assertRaises(ConnectionError):
            await client.authenticate(None, Credentials("test", "test"))
        with self.assertRaises(ConnectionError):
            await client.connect()
