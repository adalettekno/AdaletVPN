import json
import unittest
from unittest.mock import AsyncMock, patch

from gi.repository import GLib
from adaletvpn.errors import CodedError
from adaletvpn.networkmanager import GioTransport, NetworkManagerVPN, SupervisedTransport, connection_settings
from adaletvpn.nm_worker import serve
from test_networkmanager import certificate
from test_session_and_ipc import IPCProcess


class LateProfileTests(unittest.IsolatedAsyncioTestCase):
    def setup_transport(self):
        transport = object.__new__(GioTransport)
        transport.GLib = GLib
        transport.profile_path = None
        settings = connection_settings(certificate())
        transport.pending_uuid = settings["connection"]["uuid"]
        profiles = set()
        async def call(path, interface, method, parameters, reply):
            if method == "ListConnections":
                return (list(profiles),)
            if method == "GetSettings":
                return (settings,)
            if method == "GetAll":
                return ({"Unsaved": True, "Filename": ""},)
            if method == "Get":
                return ([],)
            if method == "Delete":
                profiles.remove(path)
                return ()
            raise AssertionError(method)
        transport.call = call
        return transport, profiles, settings

    async def test_repeated_empty_snapshots_never_settle_pending_creation(self):
        transport, profiles, settings = self.setup_transport()
        for _ in range(3):
            with self.assertRaises(CodedError) as caught:
                await transport.delete_profile()
            self.assertEqual(caught.exception.code, "cleanup_unconfirmed")
            self.assertEqual(transport.pending_uuid, settings["connection"]["uuid"])
        profiles.add("/owned/late")
        await transport.delete_profile()
        self.assertEqual(profiles, set())
        self.assertIsNone(transport.pending_uuid)

    async def test_creation_after_first_snapshot_does_not_produce_successful_exit(self):
        transport, profiles, settings = self.setup_transport()
        original = transport.call
        async def call(path, interface, method, parameters, reply):
            if method == "ListConnections":
                snapshot = list(profiles)
                profiles.add("/owned/late")
                return (snapshot,)
            return await original(path, interface, method, parameters, reply)
        transport.call = call
        async def eof():
            return ""
        with self.assertRaises(CodedError):
            await serve(transport, eof, lambda value: None)
        self.assertEqual(transport.pending_uuid, settings["connection"]["uuid"])
        transport.call = original
        await transport.delete_profile()
        self.assertFalse(profiles)

    async def test_fresh_worker_keeps_uncertainty_in_recovery_request(self):
        process = IPCProcess()
        process.returncode = 1
        process.wait.return_value = 1
        old = SupervisedTransport(process)
        with self.assertRaises(CodedError):
            await old.close()
        client = NetworkManagerVPN(transport=old)
        client.owned_uuid = connection_settings(certificate())["connection"]["uuid"]
        client.creation_uncertain = True
        owned_uuid = client.owned_uuid
        with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch.object(
                SupervisedTransport, "request", new_callable=AsyncMock) as request:
            request.side_effect = [CodedError("cleanup_unconfirmed"), None]
            with self.assertRaises(CodedError):
                await client.disconnect()
            self.assertEqual(client.owned_uuid, owned_uuid)
            self.assertTrue(client.creation_uncertain)
            await client.disconnect()
            self.assertEqual([call.args for call in request.await_args_list],
                             [("recover_pending", owned_uuid), ("recover_pending", owned_uuid)])
        self.assertIsNone(client.owned_uuid)
        self.assertFalse(client.creation_uncertain)

    async def test_worker_does_not_downgrade_pending_recovery_to_normal_recovery(self):
        transport, profiles, settings = self.setup_transport()
        transport.pending_uuid = None
        uuid = settings["connection"]["uuid"]
        frames = iter([json.dumps({"id": 1, "method": "recover_pending", "value": uuid}), ""])
        async def read():
            return next(frames)
        replies = []
        await serve(transport, read, replies.append)
        self.assertEqual(replies, [{"id": 1, "ok": False}])
        profiles.add("/owned/late")
        await transport.recover(uuid, require_profile=True)
        self.assertFalse(profiles)
