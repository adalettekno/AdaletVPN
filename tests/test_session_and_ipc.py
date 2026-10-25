import asyncio
import json
import sys
import unittest
from unittest.mock import AsyncMock, Mock

from adaletvpn.errors import CodedError
from adaletvpn.networkmanager import SupervisedTransport
from adaletvpn.session import validate_session


def session():
    return {"cookie": "fake-cookie", "gateway": "https://vpn.uyap.gov.tr/session",
            "gwcert": "pin-sha256:" + "A" * 43 + "="}


class SessionTests(unittest.TestCase):
    def test_verified_redirect_target_is_allowed_with_strict_url_rules(self):
        for authority in ("vpn1.uyap.gov.tr", "vpn1.uyap.gov.tr:443",
                          "vpn2.uyap.gov.tr", "vpn2.uyap.gov.tr:443"):
            value = dict(session(), gateway="https://" + authority + "/session")
            self.assertEqual(validate_session(value), value)
        for address in ("http://vpn1.uyap.gov.tr/", "https://vpn1.uyap.gov.tr:444/",
                        "https://vpn1.uyap.gov.tr.attacker.example/", "https://vpn3.uyap.gov.tr/",
                        "https://vpn2.uyap.gov.tr.attacker.example/", "http://vpn2.uyap.gov.tr/",
                        "https://vpn2.uyap.gov.tr:444/", "https://user@vpn2.uyap.gov.tr/",
                        "https://user@vpn1.uyap.gov.tr/", "https://vpn1.uyap.gov.tr/?secret=x",
                        "https://vpn1.uyap.gov.tr/%0a", "https://vpn1.uyap.gov.tr/#fragment"):
            with self.subTest(address=address), self.assertRaises(CodedError):
                validate_session(dict(session(), gateway=address))

    def test_valid_session(self):
        self.assertEqual(validate_session(session()), session())

    def test_unsafe_gateways_rejected(self):
        for address in ("http://vpn.uyap.gov.tr", "https://attacker.example/",
                        "https://vpn.uyap.gov.tr.attacker.example/", "https://user@vpn.uyap.gov.tr/",
                        "https://vpn.uyap.gov.tr:444/", "https://vpn.uyap.gov.tr/#fragment",
                        "https://vpn.uyap.gov.tr/?query=secret", "https://vpn.uyap.gov.tr/%0d%0a",
                        "https://vpn.uyap.gov.tr/%5cpath", "https://vpn.uyap.gov.tr/%xx",
                        "https://vpn.uyap.gov.tr//other", "https://vpn.uyap.gov.tr\n/"):
            with self.subTest(address=address), self.assertRaises(CodedError):
                validate_session(dict(session(), gateway=address))

    def test_fingerprint_must_be_full_sha256(self):
        for fingerprint in ("sha1:" + "a" * 40, "pin-sha256:AAAA", "pin-sha256:" + "?" * 44,
                            "pin-sha256:" + "A" * 43 + "=\n"):
            with self.subTest(fingerprint=fingerprint), self.assertRaises(CodedError):
                validate_session(dict(session(), gwcert=fingerprint))

    def test_wrong_types_and_extra_fields_rejected_without_echo(self):
        for value in (None, [], {}, dict(session(), cookie="secret\rvalue"),
                      dict(session(), cookie=123), dict(session(), pin="secret")):
            with self.assertRaises(CodedError) as caught:
                validate_session(value)
            self.assertEqual(caught.exception.code, "invalid_session")
            self.assertNotIn("secret", str(caught.exception))


class IPCProcess:
    def __init__(self):
        self.stdin = Mock()
        self.stdin.drain = AsyncMock()
        self.stdout = asyncio.StreamReader()
        self.wait = AsyncMock(return_value=0)
        self.kill = Mock()


class IPCTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pipe_round_trip_and_eof_cleanup(self):

        program = "import sys,json\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps({'id':r['id'],'ok':True,'value':2}),flush=True)\n"
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", program,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        transport = SupervisedTransport(process, reply_timeout=2, close_timeout=2)
        try:
            self.assertEqual(await transport.state("/owned"), 2)
        finally:
            await transport.close()
        self.assertEqual(process.returncode, 0)

    async def test_stalled_real_child_is_reaped_and_cleanup_not_claimed(self):
        program = "import sys,time\nsys.stdin.readline()\ntime.sleep(10)\n"
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", program,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        transport = SupervisedTransport(process, reply_timeout=0.05, close_timeout=0.05)
        with self.assertRaises(CodedError) as caught:
            await transport.state("/owned")
        self.assertEqual(caught.exception.code, "cleanup_unconfirmed")
        self.assertIsNotNone(process.returncode)

    def transport(self, process):
        return SupervisedTransport(process, reply_timeout=0.02, close_timeout=0.02)

    async def test_matching_reply(self):
        process = IPCProcess()
        process.stdout.feed_data(b'{"id":1,"ok":true,"value":2}\n')
        transport = self.transport(process)
        self.assertEqual(await transport.state("/owned"), 2)
        self.assertEqual(json.loads(process.stdin.write.call_args.args[0])["id"], 1)

    async def test_late_or_mismatched_reply_poison_channel(self):
        process = IPCProcess()
        process.stdout.feed_data(b'{"id":99,"ok":true,"value":2}\n')
        transport = self.transport(process)
        with self.assertRaises(CodedError):
            await transport.state("/owned")
        process.stdin.close.assert_called_once()
        with self.assertRaises(CodedError):
            await transport.state("/owned")
        self.assertEqual(process.stdin.write.call_count, 1)

    async def test_timeout_closes_channel_and_waits_for_cleanup(self):
        process = IPCProcess()
        transport = self.transport(process)
        with self.assertRaises(CodedError) as caught:
            await transport.state("/owned")
        self.assertEqual(caught.exception.code, "worker_timeout")
        process.wait.assert_awaited_once()
        self.assertTrue(transport.broken)

    async def test_cancel_does_not_reuse_unread_reply(self):
        process = IPCProcess()
        transport = self.transport(process)
        task = asyncio.create_task(transport.state("/owned"))
        while not process.stdin.write.called:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        process.stdout.feed_data(b'{"id":1,"ok":true,"value":2}\n')
        with self.assertRaises(CodedError):
            await transport.state("/owned")
        self.assertEqual(process.stdin.write.call_count, 1)

    async def test_unconfirmed_cleanup_is_reported(self):
        process = IPCProcess()
        process.wait.side_effect = [asyncio.TimeoutError(), -9]
        transport = self.transport(process)
        with self.assertRaises(CodedError) as caught:
            await transport.state("/owned")
        self.assertEqual(caught.exception.code, "cleanup_unconfirmed")
        process.kill.assert_called_once()
        with self.assertRaises(CodedError):
            await transport.close()

    async def test_eof_and_invalid_types_are_rejected(self):
        for reply in (b'', b'[]\n', b'{"id":true,"ok":true,"value":2}\n',
                      b'{"id":1,"ok":true,"value":true}\n'):
            process = IPCProcess()
            process.stdout.feed_data(reply)
            process.stdout.feed_eof()
            with self.assertRaises(CodedError):
                await self.transport(process).state("/owned")
