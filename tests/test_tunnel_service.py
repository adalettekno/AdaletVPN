import copy
from pathlib import Path
import runpy
import unittest
from unittest.mock import AsyncMock, patch

from adaletvpn.core import Credentials
from adaletvpn.errors import CodedError
from adaletvpn.networkmanager import NetworkManagerVPN, connection_settings
from adaletvpn.secret_agent import SessionAgent
from adaletvpn.tunnel_service import ISOLATED_SERVICE, LEGACY_SERVICE
from test_networkmanager import certificate
from test_session_and_ipc import session


class IsolatedTunnelTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_helper_stops_before_pin_and_clears_credentials(self):
        auth = AsyncMock()
        client = NetworkManagerVPN(authenticator=auth, service=ISOLATED_SERVICE)
        credentials = Credentials('ab123', 'test-pin', 'test-password')
        with patch('adaletvpn.networkmanager.isolated_service_installed', return_value=False):
            with self.assertRaises(CodedError) as caught:
                await client.authenticate(certificate(), credentials)
        self.assertEqual(caught.exception.code, 'tunnel_service_missing')
        auth.authenticate.assert_not_called()
        self.assertEqual(credentials.pin, '')
        self.assertEqual(credentials.password, '')

    async def test_isolated_auth_produces_session_for_selected_service(self):
        auth = AsyncMock()
        auth.authenticate.return_value = session()
        client = NetworkManagerVPN(authenticator=auth, service=ISOLATED_SERVICE)
        with patch('adaletvpn.networkmanager.isolated_service_installed', return_value=True):
            await client.authenticate(certificate(), Credentials('ab123', 'test-pin', 'test-password'))
        self.assertEqual(client.settings['vpn']['service-type'], ISOLATED_SERVICE)
        self.assertEqual(client.settings['vpn']['secrets'], session())

    def test_agent_cannot_deliver_isolated_session_to_legacy_service(self):
        settings = connection_settings(certificate(), service=ISOLATED_SERVICE)
        agent = SessionAgent(None, settings, '/owned', session())
        agent.nm_owner = ':1.2'
        other = copy.deepcopy(settings)
        other['vpn']['service-type'] = LEGACY_SERVICE
        with self.assertRaises(LookupError):
            agent.secrets(':1.2', other, '/owned', 'vpn', 0)
        self.assertEqual(agent.secrets(':1.2', settings, '/owned', 'vpn', 0), session())

    def test_unknown_service_rejected(self):
        with self.assertRaises(ValueError):
            connection_settings(certificate(), service='foreign')

    def test_launcher_rejects_debug_foreign_bus_and_arbitrary_command(self):
        launcher = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                      'packaging/nm-adaletvpn-service'))
        for args in (['--debug'], ['--bus-name', LEGACY_SERVICE],
                     ['--bus-name', ISOLATED_SERVICE + '.evil'], ['/bin/sh']):
            with self.subTest(args=args), patch('sys.argv', ['launcher', *args]):
                with self.assertRaises(RuntimeError):
                    launcher['main']()
