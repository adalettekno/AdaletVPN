import asyncio
import json
import os
import re
import sys
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from uuid import uuid4

from adaletvpn.core import ConnectionError
from adaletvpn.auth import NativeAuthenticator, credential_problem
from adaletvpn.errors import CodedError, is_exact_uuid, worker_environment
from adaletvpn.session import validate_session
from adaletvpn.preflight import VPNProfile, validate_certificate
from adaletvpn.tun_cleanup import interface_name, cleanup_owned_tun
from adaletvpn.tunnel_service import LEGACY_SERVICE, ISOLATED_SERVICE, OWNED_SERVICES, isolated_service_installed


LIVE_ENABLED = True


def transient_profile(properties):
    if properties.get("Unsaved") is not True:
        return False
    filename = properties.get("Filename")
    if filename == "":
        return True
    if not isinstance(filename, str):
        return False
    path = PurePosixPath(filename)
    return (str(path.parent) == "/run/NetworkManager/system-connections"
            and path.suffix == ".nmconnection" and ".." not in path.parts)


def connection_settings(certificate, profile=None, service=LEGACY_SERVICE):
    if service not in OWNED_SERVICES:
        raise ValueError("Desteklenmeyen tünel hizmeti")
    profile = profile or VPNProfile()
    profile.validate()
    validate_certificate(certificate)
    owned_uuid = str(uuid4())
    return {
        "connection": {
            "id": "AdaletVPN geçici oturum", "uuid": owned_uuid, "type": "vpn",
            "interface-name": interface_name(owned_uuid),


            "autoconnect": False, "permissions": [],
        },
        "user": {"data": {"com.adalettekno.AdaletVPN.owner-uid": str(os.getuid())}},
        "vpn": {
            "service-type": service,
            "data": {
                "gateway": urlsplit(profile.gateway).hostname, "protocol": profile.protocol,
                "usercert": certificate.identifier, "prevent_invalid_cert": "yes",
                "enable_csd_trojan": "no", "cookie-flags": "2", "gateway-flags": "2",
                "gwcert-flags": "2", "resolve-flags": "2",
            },
        },
    }


class NetworkManagerVPN:
    requires_full_credentials = True
    def __init__(self, transport=None, timeout=120, authenticator=None, service=LEGACY_SERVICE):
        if service not in OWNED_SERVICES:
            raise ValueError("Desteklenmeyen tünel hizmeti")
        self.service = service
        self.transport = transport
        self.authenticator = authenticator or NativeAuthenticator()
        self.timeout = timeout
        self.active = None
        self.settings = None
        self.owned_uuid = None
        self.creation_uncertain = False

    async def authenticate(self, certificate, credentials):
        self.settings = None
        try:
            problem = credential_problem(credentials.username, credentials.pin, credentials.password)
            if problem:
                raise ConnectionError(problem)
            if self.service == ISOLATED_SERVICE and self.transport is None and not isolated_service_installed():
                raise CodedError("tunnel_service_missing")
            settings = connection_settings(certificate, service=self.service)
            session = await self.authenticator.authenticate(certificate, credentials, VPNProfile())
            settings["vpn"]["secrets"] = validate_session(session)
            self.settings = settings
        finally:
            credentials.clear()

    async def connect(self):
        if isinstance(self.transport, SupervisedTransport) and (
                self.transport.broken or self.transport.retirement is not None):
            await self.disconnect()
        if self.transport is None:
            if not LIVE_ENABLED:
                raise ConnectionError("Gerçek bağlantı denemesi henüz etkinleştirilmedi.")
            self.transport = SupervisedTransport()
        if self.settings is None or self.active is not None:
            raise ConnectionError("Bağlantı hazırlığı eksik veya oturum zaten açık.")
        self.owned_uuid = self.settings["connection"]["uuid"]
        self.creation_uncertain = True
        request = asyncio.create_task(self.transport.activate(self.settings))
        try:

            self.active = await asyncio.shield(request)
            self.creation_uncertain = False
            await asyncio.wait_for(self._wait_ready(), self.timeout)
        except asyncio.CancelledError:
            try:
                self.active = await request
                self.creation_uncertain = False
            finally:
                await self.disconnect()
            raise
        except Exception as error:
            await self.disconnect()
            if isinstance(error, CodedError):
                raise
            raise CodedError("network_operation") from None
        finally:
            self.settings = None

    async def _wait_ready(self):
        while True:
            state = await self.transport.state(self.active)
            if state == 2:
                return
            if state in (0, 3, 4):
                raise ConnectionError("VPN bağlantısı etkinleşmeden sonlandı.")
            await asyncio.sleep(0.2)

    async def is_connected(self):
        return self.active is not None and await self.transport.state(self.active) == 2

    async def disconnect(self):
        self.settings = None
        if isinstance(self.transport, SupervisedTransport) and self.transport.broken:
            await self._finish_stopped_worker()
            self.active = None
        elif self.active is not None:
            try:
                await self.transport.deactivate(self.active)
            except asyncio.CancelledError:
                raise
            except Exception:
                if not (isinstance(self.transport, SupervisedTransport) and self.transport.broken):
                    raise
                await self._finish_stopped_worker()
            self.active = None
        if isinstance(self.transport, SupervisedTransport):
            await self.transport.close()
            self.transport = None
        self.owned_uuid = None
        self.creation_uncertain = False

    async def _finish_stopped_worker(self):
        try:
            await self.transport.close()
        except CodedError:
            process = self.transport.process
            if not self.owned_uuid or process is None or process.returncode is None:
                raise
            await self._recover_owned()

    async def _recover_owned(self):
        recovery = SupervisedTransport()
        try:
            method = "recover_pending" if self.creation_uncertain else "recover"
            await recovery.request(method, self.owned_uuid)
        finally:
            await recovery.close()

        self.transport = None


async def run_with_glib(work):
    from gi.repository import GLib

    async def pump():
        context = GLib.MainContext.default()
        while True:
            while context.pending():
                context.iteration(False)
            await asyncio.sleep(0.01)

    runner = asyncio.create_task(pump())
    try:
        return await work
    finally:
        runner.cancel()
        try:
            await runner
        except asyncio.CancelledError:
            pass


class GioTransport:


    DEST = "org.freedesktop.NetworkManager"
    ROOT = "/org/freedesktop/NetworkManager"

    def __init__(self):
        if not LIVE_ENABLED:
            raise ConnectionError("Gerçek bağlantı denemesi henüz etkinleştirilmedi.")
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.profile_path = None
        self.pending_uuid = None
        self.interface_uuid = None
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None)
        self.bus = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
            Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)

    async def call(self, path, interface, method, parameters, reply):
        future = asyncio.get_running_loop().create_future()
        def complete(bus, result):
            try:
                value = bus.call_finish(result).unpack()
            except Exception:
                if not future.done():
                    future.set_exception(CodedError("network_operation"))
            else:
                if not future.done():
                    future.set_result(value)
        self.bus.call(self.DEST, path, interface, method, parameters,
                      self.GLib.VariantType.new(reply), self.Gio.DBusCallFlags.NONE,
                      30000, None, complete)
        return await future

    async def activate(self, settings):
        session = validate_session(settings.get("vpn", {}).get("secrets"))
        owned_uuid = settings["connection"]["uuid"]
        if settings["connection"].get("interface-name") != interface_name(owned_uuid):
            raise CodedError("cleanup_unconfirmed")


        profile = {**settings, "vpn": {key: value for key, value in settings["vpn"].items()
                                      if key != "secrets"}}
        self.pending_uuid = settings["connection"]["uuid"]
        V = self.GLib.Variant
        typed = {}
        for section, values in profile.items():
            typed[section] = {key: V("b" if isinstance(value, bool) else
                                    "as" if isinstance(value, list) else
                                    "a{ss}" if isinstance(value, dict) else "s", value)
                              for key, value in values.items()}
        result = await self.call(self.ROOT + "/Settings", self.DEST + ".Settings",
                                 "AddConnectionUnsaved", V("(a{sa{sv}})", (typed,)), "(o)")
        self.profile_path = result[0]
        self.interface_uuid = owned_uuid
        self.pending_uuid = None
        try:
            await self.prepare_agent(profile, session)
            result = await self.call(self.ROOT, self.DEST, "ActivateConnection",
                                    V("(ooo)", (self.profile_path, "/", "/")), "(o)")
            return result[0]
        except BaseException:
            await self.delete_profile()
            raise
        finally:
            session.clear()

    async def prepare_agent(self, settings, session):
        from adaletvpn.secret_agent import SessionAgent
        self.agent = SessionAgent(self, settings, self.profile_path, session)
        await self.agent.start()

    async def delete_profile(self):
        if getattr(self, "agent", None) is not None:
            agent, self.agent = self.agent, None
            try:
                await agent.close()
            except Exception:


                pass
        if getattr(self, "pending_uuid", None) is not None:
            pending_uuid = self.pending_uuid
            self.pending_uuid = None
            try:
                await self.recover(pending_uuid, require_profile=True)
            except BaseException:
                self.pending_uuid = pending_uuid
                raise
        if self.profile_path is not None:
            if getattr(self, "interface_uuid", None) is not None:
                await cleanup_owned_tun(self, self.interface_uuid)
            await self.call(self.profile_path, self.DEST + ".Settings.Connection", "Delete", None, "()")
            self.profile_path = None
            self.interface_uuid = None

    async def state(self, active):
        connections = await self.call(self.ROOT, "org.freedesktop.DBus.Properties", "Get",
                                     self.GLib.Variant("(ss)", (self.DEST, "ActiveConnections")), "(v)")
        if active not in connections[0]:
            if getattr(self, "agent", None) is not None:
                self.agent.clear()
            return 4
        result = await self.call(active, "org.freedesktop.DBus.Properties", "Get",
                                 self.GLib.Variant("(ss)", (self.DEST + ".Connection.Active", "State")), "(v)")
        state = result[0]
        if state in (2, 3, 4) and getattr(self, "agent", None) is not None:
            self.agent.clear()
        return state

    async def deactivate(self, active):
        if await self.state(active) != 4:
            await self.call(self.ROOT, self.DEST, "DeactivateConnection",
                            self.GLib.Variant("(o)", (active,)), "()")
        async def wait_stopped():
            while await self.state(active) != 4:
                await asyncio.sleep(0.2)
        await asyncio.wait_for(wait_stopped(), 15)
        await self.delete_profile()

    async def recover(self, owned_uuid, require_profile=False):

        if not isinstance(owned_uuid, str) or not is_exact_uuid(owned_uuid):
            raise CodedError("cleanup_unconfirmed")
        V = self.GLib.Variant
        paths, = await self.call(self.ROOT + "/Settings", self.DEST + ".Settings",
                                 "ListConnections", None, "(ao)")
        owned_path = None
        owned_interface = False
        for path in paths:
            settings, = await self.call(path, self.DEST + ".Settings.Connection",
                                       "GetSettings", None, "(a{sa{sv}})")
            connection = settings.get("connection", {})
            if connection.get("uuid") != owned_uuid:
                continue
            vpn = settings.get("vpn", {})
            properties, = await self.call(path, "org.freedesktop.DBus.Properties", "GetAll",
                                         V("(s)", (self.DEST + ".Settings.Connection",)), "(a{sv})")
            if (owned_path is not None or not transient_profile(properties)
                    or connection.get("type") != "vpn"
                    or connection.get("id") != "AdaletVPN geçici oturum"
                    or connection.get("interface-name") not in (None, interface_name(owned_uuid))
                    or connection.get("permissions", []) != []
                    or settings.get("user", {}).get("data", {}).get("com.adalettekno.AdaletVPN.owner-uid") != str(os.getuid())
                    or vpn.get("service-type") not in OWNED_SERVICES
                    or vpn.get("data", {}).get("gateway") not in
                    (VPNProfile().gateway, urlsplit(VPNProfile().gateway).hostname)):
                raise CodedError("cleanup_unconfirmed")
            owned_path = path
            owned_interface = connection.get("interface-name") == interface_name(owned_uuid)


        if require_profile and owned_path is None:
            raise CodedError("cleanup_unconfirmed")
        connections, = await self.call(self.ROOT, "org.freedesktop.DBus.Properties", "Get",
                                       V("(ss)", (self.DEST, "ActiveConnections")), "(v)")
        for active in connections:
            properties, = await self.call(active, "org.freedesktop.DBus.Properties", "GetAll",
                                         V("(s)", (self.DEST + ".Connection.Active",)), "(a{sv})")
            if properties.get("Uuid") != owned_uuid:
                continue
            if owned_path is None or properties.get("Connection") != owned_path:
                raise CodedError("cleanup_unconfirmed")

            await self.deactivate(active)
        if owned_path is not None:
            if owned_interface:
                await cleanup_owned_tun(self, owned_uuid)
            await self.call(owned_path, self.DEST + ".Settings.Connection", "Delete", None, "()")


class SupervisedTransport:


    def __init__(self, process=None, reply_timeout=75, close_timeout=90):
        if process is None and not LIVE_ENABLED:
            raise ConnectionError("Gerçek bağlantı denemesi henüz etkinleştirilmedi.")
        if reply_timeout <= 0 or close_timeout <= 0:
            raise ValueError("Zaman aşımı sıfırdan büyük olmalı.")
        self.process = process
        self.lock = asyncio.Lock()
        self.reply_timeout = reply_timeout
        self.close_timeout = close_timeout
        self.broken = False
        self.sequence = 0
        self.retirement = None

    async def _retire(self):
        self.broken = True
        process = self.process
        if process is None:
            return

        process.stdin.close()
        try:
            code = await asyncio.wait_for(process.wait(), self.close_timeout)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                pass
            raise CodedError("cleanup_unconfirmed") from None
        if code != 0:
            raise CodedError("cleanup_unconfirmed")
        self.process = None

    async def close(self):
        if self.retirement is None:
            self.broken = True
            self.retirement = asyncio.create_task(self._retire())

            self.retirement.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        await asyncio.shield(self.retirement)

    async def request(self, method, value):
        async with self.lock:
            if self.broken:
                raise CodedError("worker_unavailable")
            if method not in ("activate", "state", "deactivate", "recover", "recover_pending"):
                raise CodedError("worker_unavailable")
            if self.process is None:
                env = worker_environment()
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "adaletvpn.nm_worker",
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, env=env)
            self.sequence += 1
            sequence = self.sequence

            async def exchange():
                frame = (json.dumps({"id": sequence, "method": method, "value": value}) + "\n").encode()
                if len(frame) > 65536:
                    raise ValueError()
                self.process.stdin.write(frame)
                await self.process.stdin.drain()
                reply = await self.process.stdout.readline()
                result = json.loads(reply)
                if (not isinstance(result, dict) or type(result.get("id")) is not int
                        or result["id"] != sequence or type(result.get("ok")) is not bool):
                    raise ValueError()
                if not result["ok"]:
                    raise CodedError("network_operation")
                answer = result.get("value")
                if method == "activate" and (not isinstance(answer, str) or
                        not re.fullmatch(r"/org/freedesktop/NetworkManager/ActiveConnection/[0-9]+", answer)):
                    raise ValueError()
                if method == "state" and (type(answer) is not int or answer not in range(5)):
                    raise ValueError()
                if method in ("deactivate", "recover", "recover_pending") and answer is not None:
                    raise ValueError()
                return answer

            try:
                return await asyncio.wait_for(exchange(), self.reply_timeout)
            except asyncio.CancelledError:
                await self.close()
                raise
            except asyncio.TimeoutError:
                await self.close()
                raise CodedError("worker_timeout") from None
            except CodedError:
                await self.close()
                raise
            except (ValueError, OSError, UnicodeError):
                await self.close()
                raise CodedError("worker_unavailable") from None

    async def activate(self, settings):
        return await self.request("activate", settings)

    async def state(self, active):
        return await self.request("state", active)

    async def deactivate(self, active):
        await self.request("deactivate", active)
        await self.close()
