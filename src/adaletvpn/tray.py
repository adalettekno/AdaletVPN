import math
import os
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from adaletvpn.core import State

APP_ICON = "com.adalettekno.AdaletVPN"
CONNECTED_ICON = "com.adalettekno.AdaletVPN-connected"
RING = (101, 213, 138, 255)
TOOLTIP = "AdaletVPN — Bağlı"
WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM = "org.kde.StatusNotifierItem"
ITEM_PATH = "/StatusNotifierItem"
ICON_SIZES = (22, 24, 32, 48, 64)
XML = f'''<node><interface name="{ITEM}">
<property name="Category" type="s" access="read"/>
<property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/>
<property name="Status" type="s" access="read"/>
<property name="WindowId" type="i" access="read"/>
<property name="IconName" type="s" access="read"/>
<property name="IconPixmap" type="a(iiay)" access="read"/>
<property name="IconThemePath" type="s" access="read"/>
<property name="OverlayIconName" type="s" access="read"/>
<property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
<property name="AttentionIconName" type="s" access="read"/>
<property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
<property name="AttentionMovieName" type="s" access="read"/>
<property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/>
<property name="Menu" type="o" access="read"/>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<signal name="NewStatus"><arg type="s"/></signal>
<signal name="NewAttentionIcon"/>
</interface></node>'''


def cache_root():
    base = os.environ.get("XDG_CACHE_HOME", "")
    path = Path(base) if base else Path.home() / ".cache"
    if not path.is_absolute():
        path = Path.home() / ".cache"
    return path / "adaletvpn"


def tray_minimize(active, ready, state, closing):
    return bool(active and ready and not closing and state in (State.CONNECTED, State.UNKNOWN))


def tray_view(active, state, busy, closing):
    if closing:
        return False, "drop" if active else None
    if state == State.CONNECTED and not active and not busy:
        return True, "appear"
    if active and state not in (State.CONNECTED, State.UNKNOWN):
        return False, "retire"
    return active, None


def write_connected_icon(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0 and destination.stat().st_mtime >= source.stat().st_mtime:
        return destination
    from PIL import Image
    image = Image.open(source).convert("RGBA")
    width, height = image.size
    scale = 0.9
    resized = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", image.size, (0, 0, 0, 0))
    canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2), resized)
    pixels = canvas.load()
    center_x = (width - 1) / 2
    center_y = (height - 1) / 2
    outer = min(width, height) / 2 - 6
    inner = outer - 20
    for y in range(height):
        for x in range(width):
            distance = math.hypot(x - center_x, y - center_y)
            if distance < inner - 1.5 or distance > outer + 1.5:
                continue
            cover = 1.0
            if distance < inner + 1.5:
                cover = min(cover, (distance - (inner - 1.5)) / 3)
            if distance > outer - 1.5:
                cover = min(cover, ((outer + 1.5) - distance) / 3)
            cover = max(0.0, min(1.0, cover))
            red, green, blue, alpha = pixels[x, y]
            pixels[x, y] = (
                int(RING[0] * cover + red * (1 - cover)),
                int(RING[1] * cover + green * (1 - cover)),
                int(RING[2] * cover + blue * (1 - cover)),
                max(alpha, int(255 * cover)),
            )
    temporary = destination.with_suffix(".png.tmp")
    canvas.save(temporary, "PNG")
    temporary.replace(destination)
    return destination


def render_flight_icon(source, size):
    from PIL import Image
    image = Image.open(source).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ring_width = 2
    gap = 2
    inner_size = max(1, size - 2 * (ring_width + gap))
    inner = image.resize((inner_size, inner_size), Image.Resampling.LANCZOS)
    offset = (size - inner_size) // 2
    canvas.paste(inner, (offset, offset), inner)
    pixels = canvas.load()
    center = (size - 1) / 2
    outer = size / 2 - 0.5
    inner_edge = outer - ring_width
    for y in range(size):
        for x in range(size):
            distance = math.hypot(x - center, y - center)
            if distance < inner_edge - 1 or distance > outer + 1:
                continue
            cover = 1.0
            if distance < inner_edge:
                cover = min(cover, distance - (inner_edge - 1))
            if distance > outer:
                cover = min(cover, (outer + 1) - distance)
            cover = max(0.0, min(1.0, cover))
            if cover <= 0:
                continue
            red, green, blue, alpha = pixels[x, y]
            pixels[x, y] = (
                int(RING[0] * cover + red * (1 - cover)),
                int(RING[1] * cover + green * (1 - cover)),
                int(RING[2] * cover + blue * (1 - cover)),
                max(alpha, int(255 * cover)),
            )
    return canvas


def write_flight_icon(source, size):
    destination = cache_root() / "icons" / "flight" / f"{size}.png"
    source = Path(source)
    if not source.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = render_flight_icon(source, size)
    temporary = destination.with_suffix(".png.tmp")
    image.save(temporary, "PNG")
    temporary.replace(destination)
    return destination


def render_tray_icon(source, size):
    from PIL import Image, ImageDraw
    image = Image.open(source).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ring_width = max(2, round(size * 0.1))
    gap = max(1, round(size * 0.04))
    inner_size = max(1, size - 2 * (ring_width + gap))
    inner = image.resize((inner_size, inner_size), Image.Resampling.LANCZOS)
    offset = (size - inner_size) // 2
    canvas.paste(inner, (offset, offset), inner)
    draw = ImageDraw.Draw(canvas)
    pad = ring_width // 2
    draw.ellipse((pad, pad, size - 1 - pad, size - 1 - pad), outline=RING, width=ring_width)
    return canvas


def icon_frames(source):
    frames = []
    for size in ICON_SIZES:
        raw = render_tray_icon(source, size).tobytes("raw", "RGBA")
        argb = bytearray()
        for index in range(0, len(raw), 4):
            red, green, blue, alpha = raw[index:index + 4]
            argb.extend((alpha, red, green, blue))
        frames.append((size, size, bytes(argb)))
    return frames


def install_tray_theme(source):
    root = cache_root() / "icons"
    write_connected_icon(source, root / "hicolor/512x512/apps" / f"{CONNECTED_ICON}.png")
    for size in ICON_SIZES:
        destination = root / f"hicolor/{size}x{size}/apps" / f"{CONNECTED_ICON}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        render_tray_icon(source, size).save(destination, "PNG")
    return root


def watcher_present(bus):
    try:
        owned = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", (WATCHER,)), GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE, 500, None)
    except Exception:
        return False
    return bool(owned.unpack()[0])


class TrayIcon:
    def __init__(self, on_activate, on_ready, on_failed):
        self.on_activate = on_activate
        self.on_ready = on_ready
        self.on_failed = on_failed
        self.bus = None
        self.owner_id = 0
        self.registration = 0
        self.service = ""
        self.frames = []
        self.ready = False
        self.stopped = False
        self.status = "Active"
        self.pulse_id = 0

    def start(self, source):
        try:
            self.theme_path = str(install_tray_theme(source))
            self.frames = icon_frames(source)
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            return False
        if not watcher_present(self.bus):
            return False
        self.service = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.owner_id = Gio.bus_own_name_on_connection(
            self.bus, self.service, Gio.BusNameOwnerFlags.NONE, self.acquired, self.lost)
        return True

    def stop(self):
        self.stopped = True
        if self.pulse_id:
            GLib.source_remove(self.pulse_id)
            self.pulse_id = 0
        if self.registration:
            self.bus.unregister_object(self.registration)
            self.registration = 0
        if self.owner_id:
            Gio.bus_unown_name(self.owner_id)
            self.owner_id = 0
        self.ready = False

    def acquired(self, connection, name):
        if self.stopped:
            return
        info = Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration = connection.register_object(
            ITEM_PATH, info.interfaces[0], self.dispatch, self.property, None)
        GLib.idle_add(self.publish)

    def lost(self, connection, name):
        if self.stopped or self.ready:
            return
        self.stop()
        self.on_failed()

    def publish(self):
        if self.stopped or self.registration == 0:
            return False
        self.bus.call(
            WATCHER, WATCHER_PATH, WATCHER, "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self.service,)), None, Gio.DBusCallFlags.NONE, 2000, None,
            self.published, None)
        return False

    def published(self, bus, result, _data):
        if self.stopped:
            return
        try:
            bus.call_finish(result)
        except Exception:
            self.stop()
            self.on_failed()
            return
        self.ready = True
        self.on_ready()

    def pulse(self):
        if self.stopped or not self.ready:
            return
        self.announce("NeedsAttention")
        if self.pulse_id:
            GLib.source_remove(self.pulse_id)
        self.pulse_id = GLib.timeout_add(700, self.settle)

    def settle(self):
        self.pulse_id = 0
        if not self.stopped:
            self.announce("Active")
        return False

    def announce(self, status):
        self.status = status
        if self.bus is None or self.stopped:
            return
        self.bus.emit_signal(None, ITEM_PATH, ITEM, "NewStatus", GLib.Variant("(s)", (status,)))
        if status == "NeedsAttention":
            self.bus.emit_signal(None, ITEM_PATH, ITEM, "NewAttentionIcon", None)
        self.bus.emit_signal(
            None, ITEM_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", (ITEM, {"Status": GLib.Variant("s", status)}, [])))

    def dispatch(self, _bus, _sender, _path, _interface, method, _parameters, invocation):
        if method in ("Activate", "SecondaryActivate"):
            self.on_activate()
        invocation.return_value(GLib.Variant("()", ()))

    def property(self, _bus, _sender, _path, _interface, name):
        empty = GLib.Variant("a(iiay)", [])
        pixmaps = GLib.Variant("a(iiay)", self.frames)
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", APP_ICON),
            "Title": GLib.Variant("s", "AdaletVPN"),
            "Status": GLib.Variant("s", self.status),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", CONNECTED_ICON),
            "IconPixmap": pixmaps,
            "IconThemePath": GLib.Variant("s", self.theme_path),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": empty,
            "AttentionIconName": GLib.Variant("s", CONNECTED_ICON),
            "AttentionIconPixmap": pixmaps,
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", self.frames, TOOLTIP, "")),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", "/"),
        }
        return values[name]
