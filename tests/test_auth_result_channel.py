import asyncio
import json
import sys
import unittest
from unittest.mock import AsyncMock, patch

from adaletvpn.auth import AUTH_STAGES, NativeAuthenticator, decode_worker_output
from adaletvpn.core import Credentials
from adaletvpn.errors import CodedError
from adaletvpn.preflight import VPNProfile
from test_networkmanager import certificate


class ResultChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_crash_reports_only_allowlisted_last_stage(self):
        for stage in AUTH_STAGES:
            with self.assertRaises(CodedError) as caught:
                decode_worker_output(json.dumps({"stage": stage}).encode(), -11)
            self.assertEqual(caught.exception.code, "auth_worker_crashed_" + stage)
        with self.assertRaises(CodedError) as caught:
            decode_worker_output(b'{"stage":"private-data"}', -11)
        self.assertEqual(caught.exception.code, "auth_worker_crashed")
        self.assertNotIn("private-data", str(caught.exception))

    async def test_stage_frames_do_not_change_result_or_allow_duplicate_results(self):
        output = b'{"stage":"ssl_init"}\n{"stage":"cleanup"}\n{"error":"pin_rejected"}\n'
        self.assertEqual(decode_worker_output(output, 1), {"error": "pin_rejected"})
        for suffix in (b'{"error":"auth_failed"}\n', b'{"stage":"cleanup"}\n'):
            with self.assertRaises(CodedError) as caught:
                decode_worker_output(output + suffix, 1)
            self.assertEqual(caught.exception.code, "auth_worker_protocol")

    async def test_driver_output_cannot_pollute_private_result(self):

        script = '''import json,os
from adaletvpn.auth_worker import isolate_result_stream
result = isolate_result_stream()
os.write(1, b"native driver output containing private data\\n")
print("buffered driver output")
print(json.dumps({"error":"auth_failed"}), file=result, flush=True)
'''
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, errors = await asyncio.wait_for(process.communicate(), 5)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(output), {"error": "auth_failed"})
        self.assertNotIn(b"private data", output + errors)

    async def test_crash_empty_and_malformed_are_not_session_validation_failures(self):
        for returncode, output, expected in (
                (-11, b"", "auth_worker_crashed"),
                (1, b"", "auth_worker_empty"),
                (0, b"private driver text\n{}", "auth_worker_protocol"),
                (0, b"\xff", "auth_worker_protocol"),
                (0, b"{}", "invalid_session")):
            with self.subTest(expected=expected, returncode=returncode):
                process = AsyncMock()
                process.returncode = returncode
                process.communicate.return_value = (output, b"")
                with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch(
                        "asyncio.create_subprocess_exec", return_value=process):
                    with self.assertRaises(CodedError) as caught:
                        await NativeAuthenticator().authenticate(certificate(),
                            Credentials("ab123", "fake-pin", "fake-password"), VPNProfile())
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn("private driver text", str(caught.exception))
