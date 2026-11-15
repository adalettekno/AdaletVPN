"""Real GTK smoke test. Uses fake card/VPN/vault and temporary preferences only."""

import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

from adaletvpn.core import Certificate, Credentials, State
from adaletvpn.demo import DemoVPN
from adaletvpn.ui import Gdk, GLib, Gtk, MainWindow


class Provider:
    async def list_certificates(self):
        return (
            Certificate(
                "pkcs11:token=Test;serial=1;id=%01;type=cert",
                "Test card",
                "CN=Örnek Kullanıcı",
                "CN=Örnek Kurum",
                not_before=datetime(2020, 1, 1, tzinfo=timezone.utc),
                not_after=datetime(2040, 1, 1, tzinfo=timezone.utc),
            ),
        )


class Client(DemoVPN):
    def __init__(self):
        self.connected = False
        self.attempts = 0

    async def authenticate(self, certificate, credentials):
        self.attempts += 1
        await super().authenticate(certificate, credentials)

    async def connect(self):
        await super().connect()
        self.connected = True

    async def disconnect(self):
        await super().disconnect()
        self.connected = False

    async def is_connected(self):
        return self.connected


class Vault:
    def __init__(self):
        self.deleted = False

    async def load(self, identifier):
        return Credentials("ab123456", "", "example-password")

    async def save(self, identifier, credentials):
        pass

    async def clear_pin(self, identifier):
        pass

    async def delete(self):
        self.deleted = True


class Window(MainWindow):
    def sync_tray(self):
        # Desktop tray integration has its own tests; never hide this test window.
        pass


def main():
    failures = []
    window = None
    phase = 0
    ticks = 0
    client = Client()
    vault = Vault()
    app = Gtk.Application(application_id="com.adalettekno.AdaletVPN.SmokeTest")

    def activate(application):
        nonlocal window
        with patch("adaletvpn.ui.CredentialVault", return_value=vault):
            window = Window(application, Provider(), client)
        window.present()
        GLib.timeout_add(120, check)

    def check():
        nonlocal phase, ticks
        ticks += 1
        try:
            assert ticks < 150, "GUI test timed out"
            if window.task and not window.task.done():
                return True
            if window.vault_actions.task and not window.vault_actions.task.done():
                return True
            if phase == 0:
                assert window.controller.selected_id
                focus = window.get_focus()
                assert focus == window.username or focus.is_ancestor(window.username)
                assert window.username.get_position() == 2
                assert not window.username.get_selection_bounds()
                assert window.username_key_pressed(None, Gdk.KEY_Tab, 0, 0)
                focus = window.get_focus()
                assert focus == window.password or focus.is_ancestor(window.password)
                assert window.password_key_pressed(None, Gdk.KEY_Tab, 0, 0)
                focus = window.get_focus()
                assert focus == window.pin or focus.is_ancestor(window.pin)
                assert window.status.get_text() == "Bağlı değil"
                valid, status = window.status_display.compute_bounds(window)
                valid_delete, delete = window.forget_button.compute_bounds(window)
                valid_remember, remember = window.remember.compute_bounds(window)
                valid_action, action = window.connection_actions.compute_bounds(window)
                assert valid and valid_delete and valid_remember and valid_action
                assert delete.get_y() + delete.get_height() <= remember.get_y()
                assert delete.get_x() + delete.get_width() <= status.get_x()
                assert status.get_y() + status.get_height() <= action.get_y()
                assert delete.get_width() == remember.get_width(), (delete.get_width(), remember.get_width())
                assert delete.get_height() == remember.get_height(), (
                    delete.get_height(),
                    remember.get_height(),
                )
                assert window.forget_button.get_label() == "Kaydı sil"
            elif phase == 1:
                window.forget_button.emit("clicked")
            elif phase == 2:
                assert vault.deleted
                window.remember.set_active(True)
            elif phase == 3:
                assert window.username.get_text() == "ab123456"
                assert window.password.get_text() == "example-password"
                window.pin.set_text("123456")
                assert window.connect_button.get_sensitive()
                window.pin.grab_focus()
                assert window.pin_key_pressed(None, Gdk.KEY_Tab, 0, 0)
                assert window.get_focus() == window.connect_button
                window.pin_key_pressed(None, Gdk.KEY_Return, 0, 0)
                window.pin_key_pressed(None, Gdk.KEY_Return, 0, 0)
            elif phase == 4:
                assert client.attempts == 1
                assert window.controller.state is State.CONNECTED
                assert window.status.get_text() == "Bağlı"
                window.pin_key_released(None, Gdk.KEY_Return, 0, 0)
                window.stop_button.emit("clicked")
            elif phase == 5:
                assert window.controller.state is State.DISCONNECTED
                assert not client.connected
                assert window.password.get_text() == "example-password"
                assert not window.pin.get_text()
                window.pin.set_text("123456")
                window.connect_button.emit("clicked")
                window.stop_button.emit("clicked")
            elif phase in (6, 7, 8, 9, 10, 11):
                if phase == 6:
                    assert window.controller.state is State.DISCONNECTED
                    assert window.controller.can_start
                settings = window.get_settings()
                settings.set_property("gtk-application-prefer-dark-theme", phase >= 9)
                settings.set_property("gtk-xft-dpi", (96, 144, 192)[(phase - 6) % 3] * 1024)
            elif phase == 12:
                assert window.status.get_visible()
                window.pin.set_text("123456")
                window.connect_button.emit("clicked")
            elif phase == 13:
                assert window.controller.state is State.CONNECTED
                window.close()
                return GLib.SOURCE_REMOVE
            phase += 1
            return GLib.SOURCE_CONTINUE
        except Exception as error:
            failures.append(error)
            app.quit()
            return GLib.SOURCE_REMOVE

    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"XDG_CONFIG_HOME": folder}):
        app.connect("activate", activate)
        app.run([])
    if failures:
        raise failures[0]
    assert phase == 13
    assert window.loop.is_closed()
    assert not client.connected
    print(
        "GTK: status layout, direct delete button, vault, keyboard, connect/disconnect, themes, scaling and shutdown OK"
    )


if __name__ == "__main__":
    main()
