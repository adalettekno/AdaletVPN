import asyncio
import unittest

from adaletvpn.core import Controller, Credentials, State
from adaletvpn.demo import DemoCertificates, DemoVPN


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_demo_connect_and_disconnect(self):
        controller = Controller(DemoCertificates(), DemoVPN())
        items = await controller.scan()
        self.assertEqual(len(items), 2)
        self.assertIsNone(controller.selected_id)
        controller.select(items[1].identifier)
        credentials = Credentials("örnek", "örnek-pin")
        await controller.connect(credentials)
        self.assertEqual(controller.state, State.CONNECTED)
        self.assertEqual(credentials.pin, "")
        await controller.disconnect()
        self.assertEqual(controller.state, State.DISCONNECTED)

    async def test_demo_can_be_cancelled_during_authentication(self):
        controller = Controller(DemoCertificates(), DemoVPN())
        items = await controller.scan()
        controller.select(items[0].identifier)
        credentials = Credentials("örnek", "örnek-pin")
        task = asyncio.create_task(controller.connect(credentials))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(controller.state, State.DISCONNECTED)
        self.assertEqual(credentials.pin, "")
