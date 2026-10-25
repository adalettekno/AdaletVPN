import sys
from datetime import datetime, timezone

from adaletvpn.core import Certificate, State
from adaletvpn.networkmanager import NetworkManagerVPN
from adaletvpn.ui import Gtk, GLib, MainWindow


class Provider:
    def __init__(self):
        self.items = (
            Certificate("pkcs11:token=Test;serial=1;id=%01;type=cert", "Örnek cihaz 1", "CN=Örnek Kullanıcı", "CN=Örnek Kurum",
                        "İmza sertifikası", datetime(2020, 1, 1, tzinfo=timezone.utc),
                        datetime(2021, 1, 1, tzinfo=timezone.utc)),
            Certificate("pkcs11:token=Test;serial=2;id=%01;type=cert", "Örnek cihaz 2", "CN=İkinci Kullanıcı", "CN=Örnek Kurum",
                        "İkinci sertifika", datetime(2020, 1, 1, tzinfo=timezone.utc),
                        datetime(2040, 1, 1, tzinfo=timezone.utc)),
        )

    async def list_certificates(self):
        return self.items


class FakeAuthenticator:
    async def authenticate(self, certificate, credentials, profile):
        assert credentials.username == "ab123456"
        assert credentials.pin == "123456"
        assert credentials.password == "deneme-parolası"
        return {"cookie": "test-cookie", "gateway": profile.gateway, "gwcert": "pin-sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}


class FakeTransport:
    async def activate(self, settings):
        return "/test/active"

    async def state(self, active):
        return 2

    async def deactivate(self, active):
        pass


app = Gtk.Application(application_id="com.adalettekno.AdaletVPN.UICheck")
provider = Provider()
phase = 0
checks = 0
failure = False


def activate(application):
    global window
    client = NetworkManagerVPN(FakeTransport(), authenticator=FakeAuthenticator()) if "--external" in sys.argv else None
    window = MainWindow(application, provider, client)
    window.present()
    GLib.timeout_add(300, check)


def save_snapshot():
    snapshot = Gtk.Snapshot()
    snapshot.render_background(window.get_style_context(), 0, 0, window.get_width(), window.get_height())
    window.snapshot_child(window.get_child(), snapshot)
    texture = window.get_renderer().render_texture(snapshot.to_node(), None)
    texture.save_to_png("/tmp/adaletvpn-ui-check.png")
    return False


def check():
    global phase, checks, failure
    checks += 1
    try:
        if checks > 50:
            raise AssertionError("Arayüz kontrolü zaman aşımına uğradı")
        if window.task and not window.task.done():
            return True
        if "--external" in sys.argv:
            if phase == 0:
                window.certificates.set_selected(1)
                assert "Kullanım dışı!" in (window.details.get_tooltip_text() or "")
                assert window.details.get_icon_name() == "window-close-symbolic"
                assert not window.connect_button.get_sensitive()
                window.certificates.set_selected(2)
                assert "Geçerli" in (window.details.get_tooltip_text() or "")
                assert "Durum:" not in (window.details.get_tooltip_text() or "")
                assert "Sahip:" not in (window.details.get_tooltip_text() or "")
                assert "Başlangıç:" not in (window.details.get_tooltip_text() or "")
                assert window.certificates.get_selected_item().get_string() == "İkinci Kullanıcı"
                assert window.username.get_parent().get_parent() == window.password.get_parent()
                assert window.remember.get_parent().get_parent() == window.remember_pin.get_parent().get_parent()
                assert "Düzenleyen:" not in (window.details.get_tooltip_text() or "")
                assert window.details.get_icon_name() == "object-select-symbolic"
                assert window.remember.get_label() == "Hatırla"
                assert window.remember_pin.get_label() == "Hatırla"
                assert window.connection_actions.get_visible_child_name() == "connect"
                assert window.status.get_text() == "Bağlı değil"
                assert not window.connect_button.get_sensitive()
                window.username.set_text("123456")
                window.pin.set_text("123456")
                window.password.set_text("deneme-parolası")
                assert not window.connect_button.get_sensitive()
                window.username.set_text("ab123456")
                assert window.connect_button.get_sensitive()
                assert window.pin.get_visible()
                assert window.password.get_visible()
                if "--snapshot" in sys.argv:
                    GLib.timeout_add(100, save_snapshot)
                    phase = 10
                    return True
                window.connect_button.emit("clicked")
                phase = 1
            elif phase == 10:
                window.connect_button.emit("clicked")
                phase = 1
            elif phase == 1:
                assert window.controller.state == State.CONNECTED
                assert window.connection_actions.get_visible_child_name() == "stop"
                assert window.status.get_text() == "Bağlı"
                assert ('#65d58a' if window.dark_theme else '#22863a') in window.status.get_label()
                assert window.pin.get_text() == ""
                assert window.password.get_text() == ""
                window.stop_button.emit("clicked")
                phase = 2
            else:
                assert window.controller.state == State.DISCONNECTED
                print("Üç alan, sicil kontrolü, şifre temizliği ve bağlantı düğmeleri sahte taşıyıcıyla geçti.")
                window.close()
                return False
            return True
        if phase == 0:
            assert len(window.controller.certificates) == 2
            assert window.controller.selected_id is None
            assert not window.connect_button.get_sensitive()
            assert not window.pin.get_visible()
            window.certificates.set_selected(1)
            assert "Kullanım dışı!" in (window.details.get_tooltip_text() or "")
            window.certificates.set_selected(2)
            assert window.certificates.get_selected_item().get_string() == "İkinci Kullanıcı"
            assert "Geçerli" in (window.details.get_tooltip_text() or "")
            assert "Düzenleyen:" not in (window.details.get_tooltip_text() or "")
            window.start(window.scan())
            phase = 1
        elif phase == 1:
            assert window.controller.selected_id == "pkcs11:token=Test;serial=2;id=%01;type=cert"
            provider.items = ()
            window.poll_certificates()
            phase = 2
        elif phase == 2:
            assert not window.controller.certificates
            assert window.controller.selected_id is None
            assert "İkinci Kullanıcı" not in (window.details.get_tooltip_text() or "")
            provider.items = (Certificate("pkcs11:token=Test;serial=3;id=%01;type=cert", "Örnek cihaz", "CN=Örnek Kullanıcı", "CN=Örnek Kurum",
                                         "Süresi dolmuş örnek", datetime(2020, 1, 1, tzinfo=timezone.utc),
                                         datetime(2021, 1, 1, tzinfo=timezone.utc)),)
            window.poll_certificates()
            phase = 3
        elif phase == 3:
            assert window.controller.selected_id == "pkcs11:token=Test;serial=3;id=%01;type=cert"
            assert "Kullanım dışı!" in (window.details.get_tooltip_text() or "")

            if "--snapshot" in sys.argv:
                snapshot = Gtk.Snapshot()
                window.get_child().snapshot_child(window.get_child().get_child(), snapshot)
                node = snapshot.to_node()
                texture = window.get_renderer().render_texture(node, None)
                texture.save_to_png("/tmp/adaletvpn-ui-check.png")
            phase = 4
        else:
            print("GTK kontrolü geçti: seçim, tarih bilgisi, yenileme, cihaz çıkarılması ve yeniden takılması.")
            window.close()
            return False
    except Exception as error:
        failure = True
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        window.close()
        return False
    return True


app.connect("activate", activate)
app.run([])
raise SystemExit(1 if failure else 0)
