import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from adaletvpn.errors import CodedError
from adaletvpn.networkmanager import NetworkManagerVPN, SupervisedTransport, connection_settings
from adaletvpn.watchdog import arm_watchdog, run_guard, write_status
from test_networkmanager import certificate
from test_session_and_ipc import IPCProcess


class WatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_then_only_owned_uuid_is_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            owned = str(uuid4())
            transport = Mock(recover=AsyncMock())
            task = asyncio.create_task(run_guard(transport, owned, path, .04, .1, .01, .04))
            await asyncio.sleep(.01)
            self.assertEqual(json.loads(path.read_text())["state"], "armed")
            transport.recover.assert_not_awaited()
            self.assertTrue(await task)
            transport.recover.assert_awaited_once_with(owned, require_profile=True)
            self.assertEqual(json.loads(path.read_text())["state"], "cleaned")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    async def test_temporarily_missing_profile_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            transport = Mock(recover=AsyncMock(side_effect=[CodedError("cleanup_unconfirmed"), None]))
            self.assertTrue(await run_guard(transport, str(uuid4()), path, .01, .1, .01, .04))
            self.assertEqual(transport.recover.await_count, 2)

    async def test_service_failure_is_bounded_and_not_reported_as_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            transport = Mock(recover=AsyncMock(side_effect=CodedError("network_operation")))
            self.assertFalse(await run_guard(transport, str(uuid4()), path, .01, .04, .01, .02))
            self.assertEqual(json.loads(path.read_text())["state"], "unconfirmed")

    async def test_hung_cleanup_is_bounded(self):
        async def hang(*args, **kwargs):
            await asyncio.Future()
        with tempfile.TemporaryDirectory() as directory:
            transport = Mock(recover=AsyncMock(side_effect=hang))
            self.assertFalse(await asyncio.wait_for(run_guard(transport, str(uuid4()),
                Path(directory) / "status.json", .01, .04, .01, .02), 1))

    @patch("adaletvpn.networkmanager.LIVE_ENABLED", False)
    async def test_lock_prevents_guard_launch(self):
        with patch("adaletvpn.watchdog.subprocess.Popen") as spawn:
            with self.assertRaises(CodedError):
                await arm_watchdog(str(uuid4()))
        spawn.assert_not_called()

    async def test_arm_requires_ready_status_and_detaches_without_secret_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            owned = str(uuid4())
            def spawn(args, **kwargs):
                write_status(args[-1], owned, "armed")
                return Mock(poll=Mock(return_value=None))
            with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch(
                    "adaletvpn.watchdog.TRIAL_DIRECTORY", Path(directory)), patch(
                    "adaletvpn.watchdog.subprocess.Popen", side_effect=spawn) as popen:
                status = await arm_watchdog(owned)
            args, kwargs = popen.call_args
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertNotIn("GNUTLS_PIN", kwargs["env"])
            self.assertEqual(json.loads(status.read_text())["uuid"], owned)

    async def test_connection_does_not_start_automatic_disconnect_guard(self):
        transport = SupervisedTransport(IPCProcess())
        client = NetworkManagerVPN(transport=transport)
        client.settings = connection_settings(certificate())
        with patch("adaletvpn.watchdog.arm_watchdog", new_callable=AsyncMock,
                   side_effect=AssertionError("Otomatik kesme başlatılmamalı")) as arm, patch.object(
                transport, "activate", new_callable=AsyncMock, return_value="/owned/active") as activate, patch.object(
                transport, "state", new_callable=AsyncMock, return_value=2), patch.object(
                transport, "deactivate", new_callable=AsyncMock) as deactivate:
            await client.connect()
            self.assertTrue(await client.is_connected())
            arm.assert_not_awaited()
            activate.assert_awaited_once()
            deactivate.assert_not_awaited()

    async def test_guard_exiting_before_ready_prevents_arming(self):
        with tempfile.TemporaryDirectory() as directory, patch(
                "adaletvpn.networkmanager.LIVE_ENABLED", True), patch(
                "adaletvpn.watchdog.TRIAL_DIRECTORY", Path(directory)), patch(
                "adaletvpn.watchdog.subprocess.Popen", return_value=Mock(poll=Mock(return_value=1))):
            with self.assertRaises(CodedError) as caught:
                await arm_watchdog(str(uuid4()))
        self.assertEqual(caught.exception.code, "watchdog_unavailable")

    async def test_detached_fake_guard_survives_launcher_exit(self):

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = root / "status.json"
            info = root / "child.json"
            owned = str(uuid4())
            child = root / "child.py"
            child.write_text('''import asyncio,json,os,sys
from pathlib import Path
from adaletvpn.watchdog import run_guard
class Fake:
    async def recover(self, owned_uuid, require_profile=False):
        assert owned_uuid == sys.argv[1] and require_profile
Path(sys.argv[3]).write_text(json.dumps({"pid":os.getpid(),"sid":os.getsid(0)}))
asyncio.run(run_guard(Fake(), sys.argv[1], sys.argv[2], .15, .1, .01, .05))
''')
            launcher = "import subprocess,sys; subprocess.Popen(sys.argv[1:],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)"
            process = await asyncio.create_subprocess_exec(sys.executable, "-c", launcher,
                sys.executable, str(child), owned, str(status), str(info))
            self.assertEqual(await asyncio.wait_for(process.wait(), 2), 0)
            for _ in range(200):
                if status.exists() and json.loads(status.read_text())["state"] == "cleaned":
                    break
                await asyncio.sleep(.01)
            self.assertEqual(json.loads(status.read_text())["state"], "cleaned")
            identifiers = json.loads(info.read_text())
            self.assertEqual(identifiers["pid"], identifiers["sid"])
