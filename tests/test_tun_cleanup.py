import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from gi.repository import GLib

from adaletvpn.errors import CodedError
from adaletvpn.tun_cleanup import interface_name, passive_tun, cleanup_owned_tun


UUID = "12345678-1234-4234-8234-123456789abc"


class TunCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.name = interface_name(UUID)
        self.device = {"Interface": self.name, "DeviceType": 16, "State": 10, "ActiveConnection": "/"}
        self.tun = {"Mode": "tun", "Owner": 980}

    def test_interface_name_is_session_specific_and_within_kernel_limit(self):
        self.assertEqual(len(self.name), 15)
        self.assertNotEqual(self.name, interface_name("22345678-1234-4234-8234-123456789abc"))
        with self.assertRaises(ValueError):
            interface_name("vpn0")

    def test_foreign_active_or_non_tun_device_is_not_deleted(self):
        self.assertTrue(passive_tun(self.device, self.tun, self.name, 980, 0x1091))
        for key, value in (("Interface", "vpn0"), ("State", 100), ("State", 110),
                           ("DeviceType", 1), ("ActiveConnection", "/active/other")):
            self.assertFalse(passive_tun(dict(self.device, **{key: value}), self.tun, self.name, 980, 0))
        self.assertFalse(passive_tun(self.device, dict(self.tun, Owner=0), self.name, 980, 0))
        self.assertFalse(passive_tun(self.device, dict(self.tun, Mode="tap"), self.name, 980, 0))
        for flags in (0x40, 0x10000):
            self.assertFalse(passive_tun(self.device, self.tun, self.name, 980, flags))

    async def test_only_verified_device_path_is_deleted(self):
        t = SimpleNamespace(GLib=GLib, ROOT="/nm", DEST="org.freedesktop.NetworkManager",
            call=AsyncMock(side_effect=[("/device/owned",), (self.tun,), (self.device,), ()]))
        with patch.object(Path, "exists", side_effect=[True, False]), patch.object(
                Path, "read_text", side_effect=["5", "0x1091", "980", "5"]), patch(
                "adaletvpn.tun_cleanup.pwd.getpwnam", return_value=SimpleNamespace(pw_uid=980)):
            await cleanup_owned_tun(t, UUID)
        self.assertEqual(t.call.call_args.args[:3],
                         ("/device/owned", "org.freedesktop.NetworkManager.Device", "Delete"))

    async def test_interface_replacement_blocks_deletion(self):
        t = SimpleNamespace(GLib=GLib, ROOT="/nm", DEST="org.freedesktop.NetworkManager",
            call=AsyncMock(side_effect=[("/device/owned",), (self.tun,), (self.device,)]))
        with patch.object(Path, "exists", return_value=True), patch.object(
                Path, "read_text", side_effect=["5", "0x1091", "980", "6"]), patch(
                "adaletvpn.tun_cleanup.pwd.getpwnam", return_value=SimpleNamespace(pw_uid=980)):
            with self.assertRaises(CodedError):
                await cleanup_owned_tun(t, UUID)
        self.assertNotIn("Delete", [c.args[2] for c in t.call.call_args_list])

    async def test_absent_interface_does_not_touch_networkmanager(self):
        t = SimpleNamespace(call=AsyncMock())
        with patch.object(Path, "exists", return_value=False):
            await cleanup_owned_tun(t, UUID)
        t.call.assert_not_awaited()

    async def test_unknown_nm_owner_requires_matching_kernel_owner(self):
        for kernel_owner, allowed in (("980", True), ("1000", False)):
            with self.subTest(kernel_owner=kernel_owner):
                t = SimpleNamespace(GLib=GLib, ROOT="/nm", DEST="org.freedesktop.NetworkManager",
                    call=AsyncMock(side_effect=[("/device/owned",), (dict(self.tun, Owner=-1),),
                                                (self.device,), ()]))
                with patch.object(Path, "exists", side_effect=[True, False] if allowed else [True, True]), patch.object(
                        Path, "read_text", side_effect=["5", "0x1091", kernel_owner, "5"]), patch(
                        "adaletvpn.tun_cleanup.pwd.getpwnam", return_value=SimpleNamespace(pw_uid=980)):
                    if allowed:
                        await cleanup_owned_tun(t, UUID)
                    else:
                        with self.assertRaises(CodedError):
                            await cleanup_owned_tun(t, UUID)
                self.assertEqual("Delete" in [c.args[2] for c in t.call.call_args_list], allowed)

    async def test_cleanup_failure_keeps_profile_for_retry(self):
        from adaletvpn.networkmanager import GioTransport
        t = object.__new__(GioTransport)
        t.profile_path = "/profile/owned"
        t.interface_uuid = UUID
        t.call = AsyncMock()
        with patch("adaletvpn.networkmanager.cleanup_owned_tun", side_effect=CodedError("cleanup_unconfirmed")):
            with self.assertRaises(CodedError):
                await t.delete_profile()
        self.assertEqual(t.profile_path, "/profile/owned")
        self.assertEqual(t.interface_uuid, UUID)
        t.call.assert_not_awaited()
