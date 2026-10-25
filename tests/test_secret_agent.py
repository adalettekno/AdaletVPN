import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
from gi.repository import GLib, Gio

from adaletvpn.secret_agent import SessionAgent, INTERFACE
from adaletvpn.errors import CodedError
from adaletvpn.networkmanager import connection_settings, GioTransport
from test_networkmanager import certificate
from test_session_and_ipc import session


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = connection_settings(certificate())
        self.transport = SimpleNamespace(GLib=GLib, Gio=Gio, bus=Mock(), call=AsyncMock(),
            ROOT=GioTransport.ROOT, DEST=GioTransport.DEST)
        self.agent = SessionAgent(self.transport, self.settings, "/profile/owned", session())
        self.agent.nm_owner = ":1.42"

    def get(self, sender=":1.42", settings=None, path="/profile/owned", setting="vpn", flags=0):
        return self.agent.secrets(sender, self.settings if settings is None else settings, path, setting, flags)

    def test_only_nm_and_matching_profile_can_read_session(self):
        with self.assertRaises(PermissionError):
            self.get(sender=":1.43")
        for section, key, value in (("connection", "uuid", "foreign"),
                ("connection", "type", "ethernet"), ("vpn", "service-type", "foreign")):
            settings = copy.deepcopy(self.settings)
            settings[section][key] = value
            with self.assertRaises(LookupError):
                self.get(settings=settings)
        for changes in ({"path": "/profile/foreign"}, {"setting": "wifi"}):
            with self.assertRaises(LookupError):
                self.get(**changes)
        self.assertEqual(self.get(), session())

    def test_cached_then_interactive_request_uses_existing_session_once(self):
        self.assertEqual(self.get(flags=4), session())
        self.assertEqual(self.get(flags=5), session())
        with self.assertRaises(CodedError):
            self.get(flags=5)
        self.assertEqual(set(session()), {"cookie", "gateway", "gwcert"})

    def test_new_authentication_and_cleared_session_are_rejected(self):
        with self.assertRaises(CodedError):
            self.get(flags=2)
        self.agent.clear()
        with self.assertRaises(CodedError):
            self.get()

    def test_dbus_result_contains_only_vpn_session_secrets(self):
        typed = {section: {key: GLib.Variant("as" if isinstance(value,list) else
            "b" if isinstance(value,bool) else "a{ss}" if isinstance(value,dict) else "s", value)
            for key,value in values.items()} for section,values in self.settings.items()}
        params = GLib.Variant("(a{sa{sv}}osasu)", (typed, "/profile/owned", "vpn", [], 1))
        invocation = Mock()
        self.agent.dispatch(None, ":1.42", None, INTERFACE, "GetSecrets", params, invocation)
        invocation.return_dbus_error.assert_not_called()
        result = invocation.return_value.call_args.args[0].unpack()[0]
        self.assertEqual(result, {"vpn": {"secrets": session()}})

    async def test_registration_failure_removes_object_and_clears_session(self):
        self.transport.bus.call_sync.return_value = GLib.Variant("(s)", (":1.42",))
        self.transport.bus.register_object.return_value = 7
        self.transport.call.side_effect = CodedError("network_operation")
        with self.assertRaises(CodedError):
            await self.agent.start()
        self.transport.bus.unregister_object.assert_called_once_with(7)
        self.assertEqual(self.agent.session, {})

    async def test_close_clears_even_when_unregister_fails(self):
        self.agent.registered = True
        self.agent.registration = 7
        self.transport.call.side_effect = CodedError("network_operation")
        with self.assertRaises(CodedError):
            await self.agent.close()
        self.assertEqual(self.agent.session, {})
        self.transport.bus.unregister_object.assert_called_once_with(7)

    async def test_activated_state_clears_agent_session(self):
        transport = object.__new__(GioTransport)
        transport.GLib = GLib
        transport.call = AsyncMock(side_effect=[(["/active/owned"],), (2,)])
        transport.agent = self.agent
        self.assertEqual(await transport.state("/active/owned"), 2)
        self.assertEqual(self.agent.session, {})

    async def test_agent_failure_prevents_activation_and_removes_profile(self):
        transport = object.__new__(GioTransport)
        transport.GLib = GLib
        transport.profile_path = None
        transport.call = AsyncMock(side_effect=[("/profile/owned",), ()])
        transport.prepare_agent = AsyncMock(side_effect=CodedError("network_operation"))
        self.settings["vpn"]["secrets"] = session()
        with self.assertRaises(CodedError):
            await transport.activate(self.settings)
        self.assertEqual([call.args[2] for call in transport.call.call_args_list],
                         ["AddConnectionUnsaved", "Delete"])
        self.assertIsNone(transport.profile_path)
