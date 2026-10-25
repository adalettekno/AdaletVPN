import asyncio
from pathlib import Path
import pwd
from uuid import UUID

from adaletvpn.errors import CodedError


def interface_name(owned_uuid):
    return "av" + UUID(owned_uuid).hex[:13]


def passive_tun(device, tun, name, owner, flags):
    return (device.get("Interface") == name and device.get("DeviceType") == 16
            and device.get("ActiveConnection") == "/"
            and device.get("State") in (10, 20, 30)
            and tun.get("Mode") == "tun" and tun.get("Owner") in (-1, owner)
            and not flags & (0x40 | 0x10000))


async def cleanup_owned_tun(transport, owned_uuid):
    name = interface_name(owned_uuid)
    net = Path("/sys/class/net") / name
    if not net.exists():
        return
    try:
        index = (net / "ifindex").read_text().strip()
        V = transport.GLib.Variant
        path, = await transport.call(transport.ROOT, transport.DEST, "GetDeviceByIpIface",
                                     V("(s)", (name,)), "(o)")
        interface = transport.DEST + ".Device"
        async def props(kind):
            value, = await transport.call(path, "org.freedesktop.DBus.Properties", "GetAll",
                                         V("(s)", (kind,)), "(a{sv})")
            return value
        tun = await props(interface + ".Tun")
        device = await props(interface)
        flags = int((net / "flags").read_text().strip(), 16)
        owner = pwd.getpwnam("nm-openconnect").pw_uid


        if (int((net / "owner").read_text().strip()) != owner
                or not passive_tun(device, tun, name, owner, flags)
                or (net / "ifindex").read_text().strip() != index):
            raise CodedError("cleanup_unconfirmed")
        await transport.call(path, interface, "Delete", None, "()")
        for _ in range(20):
            if not net.exists():
                return
            await asyncio.sleep(0.1)
        raise CodedError("cleanup_unconfirmed")
    except Exception:

        if not net.exists():
            return
        raise CodedError("cleanup_unconfirmed") from None
