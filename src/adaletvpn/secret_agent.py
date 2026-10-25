from adaletvpn.errors import CodedError
from adaletvpn.session import validate_session
from adaletvpn.tunnel_service import OWNED_SERVICES


INTERFACE = "org.freedesktop.NetworkManager.SecretAgent"
PATH = "/org/freedesktop/NetworkManager/SecretAgent"
XML = f'''<node><interface name="{INTERFACE}">
<method name="GetSecrets">
<arg type="a{{sa{{sv}}}}" direction="in"/><arg type="o" direction="in"/>
<arg type="s" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="in"/>
<arg type="a{{sa{{sv}}}}" direction="out"/>
</method>
<method name="CancelGetSecrets"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
<method name="SaveSecrets"><arg type="a{{sa{{sv}}}}" direction="in"/><arg type="o" direction="in"/></method>
<method name="DeleteSecrets"><arg type="a{{sa{{sv}}}}" direction="in"/><arg type="o" direction="in"/></method>
</interface></node>'''


class SessionAgent:
    def __init__(self, transport, settings, profile_path, session):
        self.transport = transport
        self.uuid = settings["connection"]["uuid"]
        self.service = settings["vpn"]["service-type"]
        if self.service not in OWNED_SERVICES:
            raise ValueError("Desteklenmeyen tünel hizmeti")
        self.profile_path = profile_path
        self.session = validate_session(session)
        self.nm_owner = None
        self.registration = None
        self.registered = False
        self.delivered = False

    def clear(self):
        self.session.clear()

    def matches(self, connection, path):
        return (path == self.profile_path
                and connection.get("connection", {}).get("uuid") == self.uuid
                and connection.get("connection", {}).get("type") == "vpn"
                and connection.get("vpn", {}).get("service-type") ==
                self.service)

    def secrets(self, sender, connection, path, setting, flags):
        if sender != self.nm_owner or not self.nm_owner:
            raise PermissionError()
        if not self.matches(connection, path) or setting != "vpn":
            raise LookupError()

        if not self.session or flags & 2 or self.delivered:
            raise CodedError("auth_failed")


        if flags & 1:
            self.delivered = True
        return dict(self.session)

    def dispatch(self, bus, sender, path, interface, method, parameters, invocation):
        try:
            if sender != self.nm_owner or not self.nm_owner:
                raise PermissionError()
            args = parameters.unpack()
            if method == "GetSecrets":
                connection, connection_path, setting, hints, flags = args
                result = self.secrets(sender, connection, connection_path, setting, flags)
                V = self.transport.GLib.Variant
                invocation.return_value(V("(a{sa{sv}})", ({"vpn": {"secrets": V("a{ss}", result)}},)))
                return
            if method == "CancelGetSecrets":
                if args == (self.profile_path, "vpn"):
                    self.clear()
            elif method in ("SaveSecrets", "DeleteSecrets"):
                if not self.matches(args[0], args[1]):
                    raise LookupError()
                if method == "DeleteSecrets":
                    self.clear()

            else:
                raise LookupError()
            invocation.return_value(self.transport.GLib.Variant("()", ()))
        except PermissionError:
            invocation.return_dbus_error(INTERFACE + ".PermissionDenied", "İstek sahibi doğrulanamadı.")
        except LookupError:
            invocation.return_dbus_error(INTERFACE + ".NoSecrets", "Bu profil için oturum yok.")
        except Exception:
            invocation.return_dbus_error(INTERFACE + ".UserCanceled", "Oturum yeniden kullanılamıyor.")

    async def start(self):
        t = self.transport

        self.nm_owner, = t.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetNameOwner", t.GLib.Variant("(s)", (t.DEST,)),
            t.GLib.VariantType.new("(s)"), t.Gio.DBusCallFlags.NONE, 5000, None).unpack()
        self.info = t.Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration = t.bus.register_object(PATH, self.info.interfaces[0], self.dispatch, None, None)
        try:
            await t.call(t.ROOT + "/AgentManager", t.DEST + ".AgentManager",
                "RegisterWithCapabilities", t.GLib.Variant("(su)", ("com.adalettekno.AdaletVPN." + self.uuid, 0)), "()")
            self.registered = True
        except BaseException:
            self.clear()
            t.bus.unregister_object(self.registration)
            self.registration = None
            raise

    async def close(self):
        self.clear()
        try:
            if self.registered:
                await self.transport.call(self.transport.ROOT + "/AgentManager",
                    self.transport.DEST + ".AgentManager", "Unregister", None, "()")
        finally:
            self.registered = False
            if self.registration is not None:
                self.transport.bus.unregister_object(self.registration)
                self.registration = None
