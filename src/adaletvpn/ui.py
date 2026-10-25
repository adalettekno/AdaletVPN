import asyncio
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango, Gdk

from adaletvpn.core import ConnectionError, Controller, Credentials, State
from adaletvpn.demo import DemoCertificates, DemoVPN
from adaletvpn.auth import credential_problem
from adaletvpn.pkcs11 import DisabledVPN
from adaletvpn.errors import MESSAGES
from adaletvpn.vault import CredentialVault, VaultError
from adaletvpn.remember_preferences import write_remember
from adaletvpn.fonts import load_fonts
from adaletvpn.certificate_list import certificate_labels, certificate_name
from adaletvpn.layout import build, power_button
from adaletvpn.style import apply_style
from adaletvpn.tray import APP_ICON, TrayIcon, tray_minimize, tray_view, write_flight_icon
from adaletvpn.flight import ICON_PX, IconFlight, flight_progress, icon_motion, icon_opacity, mix_rect, window_fade


def set_label(label, text):
    if label.get_text() != text:
        label.set_text(text)


def set_markup(label, markup):
    if label.get_label() != markup:
        label.set_markup(markup)


def show_message(label, text):
    if text == "Sertifika bulunamadı!":
        label.set_text("")
    elif text in {message for code, message in MESSAGES.items() if code.startswith("pin_")}:
        root = label.get_root() if isinstance(label, Gtk.Label) else None
        color = "#ff8a92" if getattr(root, "dark_theme", False) else "#c01c28"
        label.set_markup(f'<span foreground="{color}" weight="bold">' + GLib.markup_escape_text(text) + '</span>')
    else:
        label.set_text(text)


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, provider=None, client=None):
        load_fonts()
        super().__init__(application=app, title="AdaletVPN" if client else "AdaletVPN — Sertifikalar" if provider else "AdaletVPN — Deneme")
        icon_root = Path(__file__).resolve().parents[2] / "data/app-icons"

        Gtk.IconTheme.get_for_display(self.get_display()).add_search_path(str(icon_root))
        self.set_icon_name(APP_ICON)
        self._tray_active = False
        self._tray = None
        self._minimize_surface = None
        self._returning_to_tray = False
        self._flying = False
        self._flight = None
        self._flight_tick = None
        self._flight_origin_time = None
        self._flight_finishing = False
        self._in_flight_frame = False
        self.connect("realize", self.hook_minimize)
        self.read_only = provider is not None and client is None
        self.managed_connection = client is not None
        self.set_default_size(500, -1)
        self.controller = Controller(provider, client or DisabledVPN(), timeout=150) if provider else Controller(DemoCertificates(), DemoVPN())
        self.loop = asyncio.new_event_loop()
        self.task = None
        self.closing = False
        self.allow_close = False
        self.cancelling = False
        self.credentials = None
        self.updating_list = False
        self.vault = CredentialVault()
        self.vault_selected = None
        self.vault_task = None
        self.vault_generation = 0

        apply_style(self)
        build(self)
        self.timer = None
        self._ui_snapshot = None
        self.poll_timer = GLib.timeout_add_seconds(5, self.poll_certificates) if self.read_only else None
        self.monitor_task = None
        self.monitor_timer = GLib.timeout_add_seconds(1, self.monitor_connection) if self.managed_connection else None
        self.start(self.scan())

    def update_header_icon_theme(self):
        found, background = self.get_style_context().lookup_color("theme_bg_color")
        if found:
            light = (0.2126 * background.red + 0.7152 * background.green + 0.0722 * background.blue) > 0.5
        else:
            settings = self.get_settings()
            light = not (settings.get_property("gtk-application-prefer-dark-theme")
                         or "dark" in (settings.get_property("gtk-theme-name") or "").lower())
        self.dark_theme = not light
        if light:
            self.remove_css_class("adaletvpn-dark")
        else:
            self.add_css_class("adaletvpn-dark")
        self.refresh()
        return GLib.SOURCE_REMOVE

    def password_key_pressed(self, controller, keyval, keycode, state):
        modifiers = (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK
                     | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK)
        if keyval == Gdk.KEY_Tab and not state & modifiers:
            return self.pin.grab_focus()
        return False

    def pin_key_pressed(self, controller, keyval, keycode, state):
        modifiers = (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK
                     | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK)
        if state & modifiers:
            return False
        if keyval == Gdk.KEY_Tab:
            if self.connect_button.get_sensitive():
                return self.connect_button.grab_focus()
            return False
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if not self.pin_enter_down:
                self.pin_enter_down = True
                if self.connect_button.get_sensitive():
                    self.connect_vpn()
            return True
        return False

    def pin_key_released(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.pin_enter_down = False

    def connection_message_changed(self, label, *_):
        text = label.get_text()
        self.status_display.set_visible_child_name("error" if text else "normal")
        if not text:
            self.connection_error_panel.popdown()
        elif text != self.last_connection_message:
            self.connection_error_panel.popup()
        self.last_connection_message = text

    def show_certificate_info(self, *_):
        item = next((c for c in self.controller.certificates
                     if c.identifier == self.controller.selected_id), None)
        if item is None:
            return
        panel = Gtk.Popover()
        panel.set_parent(self.certificate_info)
        panel.set_position(Gtk.PositionType.BOTTOM)
        panel.set_autohide(True)
        panel.connect("closed", lambda widget: widget.unparent())
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_width(360)
        scroll.set_size_request(360, -1)
        scroll.set_max_content_height(420)
        scroll.set_propagate_natural_height(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for edge in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{edge}")(8)
        scroll.set_child(content)
        panel.set_child(scroll)

        def date_text(value):
            return value.strftime("%d.%m.%Y %H:%M:%S UTC") if value else "Okunamadı"

        def add_field(container, name, value):
            text = Gtk.Label(xalign=0, wrap=True)
            text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            text.set_width_chars(40)
            text.set_max_width_chars(42)
            text.set_markup("<b>" + GLib.markup_escape_text(name) + "</b>\n"
                            + GLib.markup_escape_text(value or "Belirtilmemiş"))
            container.append(text)

        from cryptography import x509
        try:
            issuer = x509.Name.from_rfc4514_string(item.issuer)
            names = issuer.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
            issuer_name = names[0].value if names else item.issuer
        except ValueError:
            issuer_name = item.issuer
        for name, value in [
            ("Sertifika sahibi", certificate_name(item)),
            ("Düzenleyen kurum", issuer_name),
            ("Başlangıç", date_text(item.not_before)),
            ("Bitiş", date_text(item.not_after)),
        ]:
            add_field(content, name, value)
        panel.popup()

    def start(self, coroutine):
        if self.task is not None and not self.task.done():
            coroutine.close()
            return
        show_message(self.message, "")
        self.task = self.loop.create_task(coroutine)
        self.task.add_done_callback(self.finished)
        setattr(self.task, "_adalet_credentials", self.credentials)
        self.ensure_pump()
        MainWindow.pump_once(self)
        self.refresh()

    def pump_once(self):
        if self.loop.is_running():
            return
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        if self.loop._ready:
            self.loop.call_soon(self.loop.stop)
            self.loop.run_forever()

    def finished(self, task):
        current = self.task is task
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except ConnectionError as error:
            if current:
                show_message(self.message, str(error))
        except Exception:
            if current:
                show_message(self.message, "İşlem tamamlanamadı. Tekrar deneyin.")
        finally:
            owned = getattr(task, "_adalet_credentials", None)
            if owned is None:
                if current and self.credentials is not None:
                    self.credentials.clear()
                    self.credentials = None
            elif self.credentials is owned:
                owned.clear()
                self.credentials = None
            else:
                owned.clear()
            if current:
                self.cancelling = False
                self.refresh()
                MainWindow.dismiss_kept_credentials_notice(self)

    def ensure_pump(self):
        if self.timer is None:
            self.timer = GLib.timeout_add(20, self.tick)

    def ui_snapshot(self):
        return (
            self.task is not None and not self.task.done(),
            self.closing,
            self.cancelling,
            self.controller.state,
            bool(self.controller.can_start),
            self.controller.error,
            self.controller.scan_detail,
            self.controller.selected_id,
            tuple((item.identifier, item.validity(), item.connection_problem())
                  for item in self.controller.certificates),
            self.remember.get_active(),
            self.remember_pin.get_active(),
            getattr(self, "dark_theme", False),
        )

    def tick(self):
        try:
            MainWindow.pump_once(self)
            snapshot = self.ui_snapshot()
            if snapshot != self._ui_snapshot:
                self._ui_snapshot = snapshot
                self.refresh()
        except Exception:
            pass
        if asyncio.all_tasks(self.loop) or self.loop._ready:
            return GLib.SOURCE_CONTINUE
        self.timer = None
        return GLib.SOURCE_REMOVE

    def monitor_connection(self):
        if (not self.closing and self.controller.state in (State.CONNECTED, State.UNKNOWN)
                and (self.monitor_task is None or self.monitor_task.done())):
            self.monitor_task = self.loop.create_task(self.check_connection())
            self.ensure_pump()
        return GLib.SOURCE_CONTINUE

    async def check_connection(self):
        try:
            connected = await self.controller.client.is_connected()
            if self.controller.state in (State.CONNECTED, State.UNKNOWN):
                if connected:
                    self.controller.state = State.CONNECTED
                    self.controller.error = None
                    show_message(self.message, "")
                else:
                    await self.controller.connection_lost()
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.controller.state in (State.CONNECTED, State.UNKNOWN):
                self.controller.state = State.UNKNOWN
                self.controller.error = "Bağlantı durumu doğrulanamadı. Bağlantıyı kesmeyi deneyin."

    def poll_certificates(self):
        if not self.closing and (self.task is None or self.task.done()):
            self.start(self.scan())
        return GLib.SOURCE_CONTINUE

    async def scan(self):
        previous = self.controller.selected_id
        try:
            items = await self.controller.scan()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.updating_list = True
            self.certificates.set_model(Gtk.StringList.new(["Sertifika bulunamadı!"]))
            self.certificates.set_selected(0)
            self.certificates.add_css_class("certificate-missing")
            self.updating_list = False
            self.details.set_visible(False)
            raise
        selected = next((i + 1 for i, item in enumerate(items)
                         if item.identifier == previous), 1 if len(items) == 1 else 0)
        if items:
            self.certificates.remove_css_class("certificate-missing")
        else:
            self.certificates.add_css_class("certificate-missing")
        labels = (["Sertifika seçin"] if items else ["Sertifika bulunamadı!"]) + certificate_labels(items)
        self.updating_list = True
        self.certificates.set_model(Gtk.StringList.new(labels))
        self.certificates.set_selected(selected)
        self.updating_list = False
        self.select_certificate()
        if self.managed_connection and self.remember.get_active() and self.controller.selected_id:
            self.start_vault(self.load_credentials())

    def select_certificate(self, *_):
        if self.updating_list:
            return
        index = self.certificates.get_selected()
        if 1 <= index <= len(self.controller.certificates):
            try:
                self.controller.select(self.controller.certificates[index - 1].identifier)
            except ConnectionError:
                return
        else:
            self.controller.selected_id = None
        selected = self.controller.selected_id
        if selected != self.vault_selected:
            self.vault_selected = selected
            self.pin.set_text("")
            self.password.set_text("")
            self.username.set_text("ab")
            if (self.managed_connection and self.remember.get_active() and selected
                    and (self.task is None or self.task.done())):
                self.start_vault(self.load_credentials())
        self.refresh()

    def cancel_vault(self, announce=True):
        self.vault_generation += 1
        if self.vault_task is not None and not self.vault_task.done():
            self.vault_task.cancel()
            if announce:
                self.vault_message.set_text("Kasa işlemi iptal edildi. Yazma veya silme tamamlanmış olabilir; bilgilerinizi elle girerek devam edebilirsiniz.")

    def start_vault(self, coroutine, cleanup=None):
        operation = coroutine.cr_code.co_name
        if operation != "load_credentials":
            self.vault_message.set_target("forget" if operation == "forget_credentials" else
                                          "pin" if operation == "remove_saved_pin" else "remember")
        previous = self.vault_task
        self.cancel_vault(announce=False)
        generation = self.vault_generation
        self.vault_message.set_text("Kasa işlemi sürüyor. Bilgilerinizi elle girerek bağlanabilirsiniz.")
        async def run():
            if previous is not None:
                try:
                    await previous
                except (asyncio.CancelledError, Exception):
                    pass
            if generation != self.vault_generation or self.closing:
                return
            await coroutine
        task = self.vault_task = self.loop.create_task(run())
        if getattr(self, "timer", 1) is None:
            self.ensure_pump()
        def finished_vault(result):
            coroutine.close()
            if cleanup is not None:
                cleanup()
            try:
                result.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                if generation == self.vault_generation:
                    self.vault_message.set_text(str(VaultError()), error=True)
        task.add_done_callback(finished_vault)

    def remember_toggled(self, *_):
        self.vault_message.set_target("remember")
        if not self.managed_connection:
            return
        if self.remember.get_active():
            self.start_vault(self.load_credentials())
        else:
            self.cancel_vault(announce=False)
            self.remember_pin.handler_block_by_func(self.pin_remember_toggled)
            self.remember_pin.set_active(False)
            self.remember_pin.handler_unblock_by_func(self.pin_remember_toggled)
            self.pin.set_text("")
            self.password.set_text("")
            try:
                write_remember(False)
            except OSError:
                self.vault_message.set_text("Hatırlama kapalı; tercih dosyası güncellenemedi.", error=True)
                self.refresh()
                return
            self.vault_message.set_text("Hatırlama kapalı. Eski kayıtları kaldırmak için Kaydedilen bilgileri sil düğmesini kullanın.")
        self.refresh()

    def pin_remember_toggled(self, *_):
        self.vault_message.set_target("pin")
        enabled = self.remember_pin.get_active()
        self.cancel_vault(announce=False)
        try:
            write_remember(self.remember.get_active(), enabled)
        except OSError:
            self.vault_message.set_text("Hatırlama tercihi kaydedilemedi.", error=True)
        if not enabled:
            self.pin.set_text("")
            if self.controller.selected_id:
                self.start_vault(self.remove_saved_pin(self.controller.selected_id))
        elif self.remember.get_active():
            self.start_vault(self.load_credentials())
        self.refresh()

    async def remove_saved_pin(self, identifier):
        try:
            await self.vault.clear_pin(identifier)
            self.vault_message.set_text("Seçili kartın kayıtlı PIN’i kaldırıldı; sicil ve bilgisayar şifresi korundu.")
        except VaultError as error:
            self.vault_message.set_text(str(error), error=True)

    async def load_credentials(self):
        identifier = self.controller.selected_id
        if not identifier:
            self.vault_message.set_text("Önce bir sertifika seçin.")
            return
        record = None
        generation = self.vault_generation
        original = (self.username.get_text(), self.pin.get_text(), self.password.get_text())
        try:
            record = await self.vault.load(identifier)
            if (generation != self.vault_generation or self.closing or self.controller.selected_id != identifier
                    or not self.remember.get_active()):
                return
            if record is None:
                self.vault_message.set_text("Bu kart için kayıt yok. Bilgiler başarılı bağlantıdan sonra kaydedilecek.")
            else:
                if not self.remember_pin.get_active() and record.pin:
                    record.pin = ""
                    await self.vault.save(identifier, record)
                if (generation != self.vault_generation or self.controller.selected_id != identifier
                        or original != (self.username.get_text(), self.pin.get_text(), self.password.get_text())):
                    return
                self.username.set_text(record.username)
                self.pin.set_text(record.pin if self.remember_pin.get_active() else "")
                self.password.set_text(record.password)
        except VaultError as error:
            self.vault_message.set_text(str(error), error=True)
        finally:
            if record is not None:
                record.clear()

    async def forget_credentials(self):
        self.remember.handler_block_by_func(self.remember_toggled)
        self.remember.set_active(False)
        self.remember.handler_unblock_by_func(self.remember_toggled)
        self.remember_pin.handler_block_by_func(self.pin_remember_toggled)
        self.remember_pin.set_active(False)
        self.remember_pin.handler_unblock_by_func(self.pin_remember_toggled)
        try:
            write_remember(False)
        except OSError:
            self.vault_message.set_text("Tercih kaydedilemedi; kasa kayıtlarını silme deneniyor.", error=True)
        self.username.set_text("ab")
        self.pin.set_text("")
        self.password.set_text("")
        try:
            await self.vault.delete()
            self.vault_message.set_text("Tüm bağlantı bilgileri silindi.")
        except VaultError as error:
            self.vault_message.set_text(str(error), error=True)

    async def save_credentials(self, identifier, record):
        try:
            await self.vault.save(identifier, record)
            write_remember(True, bool(record.pin))
            self.vault_message.set_text("Kaydedildi.")
        except VaultError as error:
            self.vault_message.set_text(str(error), error=True)
        except OSError:
            self.vault_message.set_text("Bilgiler kasada; hatırlama tercihi kaydedilemedi.", error=True)
        finally:
            record.clear()

    async def invalidate_credentials(self, identifier, pin_only):
        try:
            if pin_only:
                await self.vault.clear_pin(identifier)
                self.vault_message.set_text("PIN reddedildi; kayıtlı PIN kaldırıldı. PIN’i yeniden girin.", error=True)
            else:
                await self.vault.delete(identifier)
                self.vault_message.set_text("Kurum giriş bilgileri reddedildi; bu kartın kaydı kaldırıldı.", error=True)
        except VaultError as error:
            self.vault_message.set_text(str(error), error=True)

    async def connect_and_remember(self, credentials, identifier, remembered):
        try:
            await self.controller.connect(credentials)
            if remembered is not None:
                self.start_vault(self.save_credentials(identifier, remembered), cleanup=remembered.clear)
                remembered = None
        except ConnectionError:
            code = self.controller.error_code
            if code and code.startswith('pin_'):
                self.pin.set_text("")
            elif code == 'login_rejected':
                self.pin.set_text("")
                self.password.set_text("")
            if remembered is not None and code in ('pin_rejected', 'pin_two_left', 'pin_last_left', 'login_rejected'):

                if code != 'login_rejected':
                    self.remember_pin.handler_block_by_func(self.pin_remember_toggled)
                    self.remember_pin.set_active(False)
                    self.remember_pin.handler_unblock_by_func(self.pin_remember_toggled)
                else:
                    self.remember.handler_block_by_func(self.remember_toggled)
                    self.remember.set_active(False)
                    self.remember.handler_unblock_by_func(self.remember_toggled)
                try:
                    write_remember(code != 'login_rejected', False)
                except OSError:
                    self.vault_message.set_text("Hatırlama bu oturumda kapalı; tercih kaydedilemedi.", error=True)
                self.start_vault(self.invalidate_credentials(identifier, code != 'login_rejected'))
            raise
        finally:
            if remembered is not None:
                remembered.clear()

    def connect_vpn(self, *_):
        if self.read_only or self.closing or not self.controller.can_start:
            return
        if self.task is not None and not self.task.done():
            return
        problem = credential_problem(self.username.get_text(), self.pin.get_text(), self.password.get_text())
        if problem:
            show_message(self.message, problem)
            return
        self.cancel_vault(announce=False)
        self.credentials = Credentials(self.username.get_text(), self.pin.get_text(), self.password.get_text())
        keep_password = self.managed_connection and self.remember.get_active()
        if not (keep_password and self.remember_pin.get_active()):
            self.pin.set_text("")
        if not keep_password:
            self.password.set_text("")
        remembered = (Credentials(self.credentials.username, self.credentials.pin if self.remember_pin.get_active() else "", self.credentials.password)
                      if self.managed_connection and self.remember.get_active() else None)
        self.start(self.connect_and_remember(self.credentials, self.controller.selected_id, remembered))

    def dismiss_kept_credentials_notice(self):
        message = getattr(self, "message", None)
        if (message is None or self.controller.error
                or self.controller.state != State.DISCONNECTED
                or getattr(self.controller, "_needs_cleanup", False)):
            return
        text = message.get_text() if hasattr(message, "get_text") else ""
        if isinstance(text, str) and text.startswith("Bağlantı temizlenemedi"):
            show_message(message, "")

    def stop_vpn(self, *_):
        if self.task is not None and not self.task.done():
            self.cancelling = True
            self.task.cancel()
            self.refresh()
        else:
            self.controller.error = None
            self.start(self.controller.disconnect())

    def refresh(self):
        busy = self.task is not None and not self.task.done()
        connected = self.controller.state in (State.CONNECTED, State.UNKNOWN)
        idle = not busy and not self.closing and self.controller.can_start
        status = self.controller.state.value
        if self.read_only and not busy and self.controller.state == State.DISCONNECTED:
            status = f"{len(self.controller.certificates)} sertifika listelendi"
        dark = getattr(self, "dark_theme", False)
        color = (("#65d58a" if dark else "#22863a") if self.controller.state == State.CONNECTED else
                 ("#c0c0c0" if dark else "#777777") if self.controller.state == State.DISCONNECTED else
                 ("#ff8a92" if dark else "#c01c28") if self.controller.state == State.ERROR else
                 ("#81baff" if dark else "#1c71d8"))
        set_markup(self.status, f'<span foreground="{color}" weight="bold">{GLib.markup_escape_text(status)}</span>')
        self.status.set_wrap(status not in ("Bağlı", "Bağlı değil", "Bağlanıyor"))
        self.status.set_tooltip_text("VPN bağlantı durumu: " + status)
        in_progress = busy or self.controller.state in (State.SCANNING, State.AUTHENTICATING,
                                                       State.CONNECTING, State.DISCONNECTING)
        badge_state = ("working" if in_progress else "online" if self.controller.state == State.CONNECTED
                       else "offline" if self.controller.state == State.DISCONNECTED else "working")
        self.status.get_parent().get_parent().set_css_classes(["vpn-badge", badge_state])
        self.status_spinner.set_spinning(in_progress)
        self.status_spinner.set_visible(in_progress)
        self.status_indicator.set_visible(not in_progress)
        set_markup(self.status_indicator, f'<span foreground="{color}">●</span>')
        needs_stop = busy or connected or not self.controller.can_start
        self.connection_actions.set_visible_child_name("stop" if needs_stop else "connect")
        item = next((c for c in self.controller.certificates
                     if c.identifier == self.controller.selected_id), None)
        if item is None:
            self.details.set_visible(False)
        else:
            validity = item.validity()
            problem = item.connection_problem()
            valid = problem is None
            validity_text = ("Geçerli" if valid else "Doğrulanamadı" if item.details_error
                             else "Kullanım dışı!" if validity == "Süresi dolmuş" else validity)
            self.details.set_from_icon_name("object-select-symbolic" if valid else "window-close-symbolic")
            self.details.set_css_classes(["certificate-status", "valid" if valid else "invalid"])
            validity_text = ("Geçerli — bağlantı için uygun" if valid else
                             "Kullanım dışı! " + problem if validity == "Süresi dolmuş" else
                             problem or validity_text)
            self.details.set_tooltip_text(validity_text)
            self.details.update_property([Gtk.AccessibleProperty.LABEL], [validity_text])
            self.details.set_visible(True)
        scan_warning = getattr(self, "scan_warning", None)
        if scan_warning is not None:
            scan_detail = getattr(self.controller, "scan_detail", "")
            self.scan_problem.set_text(scan_detail)
            scan_warning.set_visible(bool(scan_detail) and not busy)
        reason = (item.connection_problem() if item is not None else "Önce bir sertifika seçin.")
        if not reason:
            reason = credential_problem(self.username.get_text(), self.pin.get_text(), self.password.get_text())
        self.connect_button.set_tooltip_text(reason or "VPN bağlantısını başlat")
        self.certificate_info.set_sensitive(item is not None)
        self.certificate_info.set_visible(item is not None)
        self.remember.set_sensitive(idle and self.managed_connection)
        self.remember_pin.set_sensitive(idle and self.managed_connection and self.remember.get_active())
        self.remember_pin.set_tooltip_text(
            "Öncelikle bilgisayar şifresi kaydedilmelidir!"
            if not self.remember.get_active() else "E-imza şifresini hatırla")
        self.forget_button.set_sensitive(idle and self.managed_connection)
        self.username.set_sensitive(idle)
        self.pin.set_sensitive(idle)
        self.password.set_sensitive(idle)
        self.certificates.set_sensitive(idle)
        self.scan_button.set_sensitive(idle)
        self.connect_button.set_sensitive(not self.read_only and idle and bool(self.controller.selected_id)
                                          and (not self.managed_connection or (item is not None and not item.connection_problem()))
                                          and credential_problem(self.username.get_text(), self.pin.get_text(),
                                                                 self.password.get_text()) is None)
        set_label(self.stop_button.action_label, "İptal" if busy else "Kes")
        self.stop_button.set_busy(busy)
        if self.controller.state == State.CONNECTED and not busy:
            self.stop_button.add_css_class("connected")
        else:
            self.stop_button.remove_css_class("connected")
        self.stop_button.set_tooltip_text("Devam eden işlemi iptal et" if busy else "VPN bağlantısını kes")
        self.stop_button.set_sensitive(not self.closing and not self.cancelling and
                                       (busy or connected or self.controller.state == State.ERROR))
        if self.controller.error:
            show_message(self.message, self.controller.error)
        MainWindow.sync_tray(self)

    def sync_tray(self):
        if not hasattr(self, "set_visible"):
            return
        busy = self.task is not None and not self.task.done()
        active, action = tray_view(
            getattr(self, "_tray_active", False), self.controller.state, busy, getattr(self, "closing", False))
        self._tray_active = active
        if action == "appear":
            if not self.open_tray():
                self._tray_active = False
        elif action == "retire":
            self.close_tray(True)
        elif action == "drop":
            self.close_tray(False)

    def open_tray(self):
        if getattr(self, "_tray", None) is not None:
            return True
        source = Path(__file__).resolve().parents[2] / "data/app-icons/hicolor/512x512/apps" / f"{APP_ICON}.png"
        tray = TrayIcon(self.reveal_from_tray, self.tray_ready, self.tray_failed)
        try:
            started = tray.start(source)
        except Exception:
            return False
        if not started:
            return False
        self._tray = tray
        return True

    def hook_minimize(self, *_):
        surface = self.get_surface()
        if surface is None or surface is self._minimize_surface:
            return
        self._minimize_surface = surface
        surface.connect("notify::state", self.minimize_requested)

    def title_minimize(self, *_):
        tray = getattr(self, "_tray", None)
        if tray_minimize(
                getattr(self, "_tray_active", False), getattr(tray, "ready", False),
                self.controller.state, getattr(self, "closing", False)):
            self.depart_to_tray()
            return
        self.cancel_flight()
        self.minimize()

    def toggle_maximize(self, *_):
        if self.is_maximized():
            self.unmaximize()
        else:
            self.maximize()

    def minimize_requested(self, surface, _param):
        if self._returning_to_tray or getattr(self, "_flying", False):
            return
        try:
            minimized = bool(surface.get_state() & Gdk.ToplevelState.MINIMIZED)
        except Exception:
            return
        tray = getattr(self, "_tray", None)
        if not minimized or not tray_minimize(
                getattr(self, "_tray_active", False), getattr(tray, "ready", False),
                self.controller.state, getattr(self, "closing", False)):
            return
        self._returning_to_tray = True
        self.unminimize()
        self._returning_to_tray = False
        self.depart_to_tray()

    def reveal_from_tray(self):
        if getattr(self, "closing", False):
            return
        self.cancel_flight()
        self.show_from_tray()

    def show_from_tray(self):
        if hasattr(self, "set_opacity"):
            self.set_opacity(1)
        self.set_visible(True)
        self.unminimize()
        self.present()

    def tray_ready(self):
        if not getattr(self, "_tray_active", False) or getattr(self, "closing", False):
            return
        self.depart_to_tray()

    def depart_to_tray(self):
        if getattr(self, "_flying", False) or getattr(self, "closing", False):
            return
        if hasattr(self, "get_visible") and not self.get_visible():
            return
        settings = Gtk.Settings.get_default()
        animate = True if settings is None else bool(settings.get_property("gtk-enable-animations"))
        source = Path(__file__).resolve().parents[2] / "data/app-icons/hicolor/512x512/apps" / f"{APP_ICON}.png"
        icon = write_flight_icon(source, ICON_PX)
        flight = IconFlight.try_open(self, icon) if animate and icon.is_file() else None
        if flight is None:
            self.pulse_tray()
            self.conceal_for_tray()
            return
        self._flying = True
        self._flight = flight
        self._flight_origin_time = None
        self._in_flight_frame = False
        self._flight_tick = self.add_tick_callback(self.flight_frame)

    def pulse_tray(self):
        tray = getattr(self, "_tray", None)
        if tray is not None and getattr(tray, "ready", False):
            tray.pulse()

    def conceal_for_tray(self):
        if not hasattr(self, "set_visible"):
            return
        if hasattr(self, "set_opacity"):
            self.set_opacity(0)
        self.set_visible(False)
        if hasattr(self, "set_opacity"):
            GLib.idle_add(self.restore_hidden_opacity)

    def restore_hidden_opacity(self):
        if hasattr(self, "get_visible") and not self.get_visible() and hasattr(self, "set_opacity"):
            self.set_opacity(1)
        return False

    def flight_frame(self, _widget, clock):
        self._in_flight_frame = True
        try:
            if not self._flying or self._flight is None:
                return False
            now = clock.get_frame_time()
            if self._flight_origin_time is None:
                self._flight_origin_time = now
            progress = flight_progress((now - self._flight_origin_time) / 1000)
            if hasattr(self, "set_opacity"):
                self.set_opacity(window_fade(progress))
            flight = self._flight
            if progress < 0.65:
                flight.place_on_tray()
            motion = icon_motion(progress)
            rect = mix_rect(flight.origin, flight.target, motion)
            try:
                flight.step(rect, icon_opacity(motion))
            except Exception:
                progress = 1
            if progress >= 1 or self.closing or not self._tray_active:
                self.finish_flight(hide=self._tray_active and not self.closing)
                return False
            return True
        finally:
            self._in_flight_frame = False

    def finish_flight(self, hide):
        if self._flight_finishing:
            return
        self._flight_finishing = True
        try:
            self._flying = False
            flight = self._flight
            tick = self._flight_tick
            self._flight = None
            self._flight_tick = None
            self._flight_origin_time = None
            if tick is not None and not self._in_flight_frame:
                self.remove_tick_callback(tick)
            if flight is not None:
                GLib.idle_add(flight.close)
            if hide:
                self.pulse_tray()
                if hasattr(self, "get_visible") and self.get_visible():
                    self.conceal_for_tray()
            elif hasattr(self, "set_opacity"):
                self.set_opacity(1)
        finally:
            self._flight_finishing = False

    def cancel_flight(self):
        if getattr(self, "_flying", False) or getattr(self, "_flight", None) is not None:
            self.finish_flight(hide=False)

    def tray_failed(self):
        self._tray_active = False
        self._tray = None
        if not getattr(self, "closing", False):
            self.set_visible(True)
            self.present()

    def close_tray(self, show_window):
        self.cancel_flight()
        tray = getattr(self, "_tray", None)
        self._tray = None
        if tray is not None:
            tray.stop()
        if show_window and not getattr(self, "closing", False):
            self.show_from_tray()

    def close_requested(self, *_):
        if self.allow_close:
            return False
        if not self.closing:
            self.closing = True
            self.pin.set_text("")
            self.password.set_text("")
            self.loop.create_task(self.shutdown())
            self.ensure_pump()
            self.refresh()
        return True

    async def shutdown(self):
        self.cancel_vault(announce=False)
        if self.vault_task is not None:
            try:
                await self.vault_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.monitor_task is not None and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        if self.task is not None:
            if not self.task.done() and not self.cancelling:
                self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self.controller.disconnect()
        except ConnectionError as error:
            show_message(self.message, str(error))
            self.closing = False
            return
        GLib.idle_add(self.finish_close)

    def finish_close(self):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.poll_timer is not None:
            GLib.source_remove(self.poll_timer)
        if self.monitor_timer is not None:
            GLib.source_remove(self.monitor_timer)
        self.loop.close()
        self.allow_close = True
        self.close()
        return GLib.SOURCE_REMOVE


def run(provider=None, client=None):
    app = Gtk.Application(application_id="com.adalettekno.AdaletVPN" if client else "com.adalettekno.AdaletVPN.Certificates" if provider else "com.adalettekno.AdaletVPN.Demo")

    def activate(application):
        window = application.get_active_window()
        if window is None:
            window = MainWindow(application, provider, client)
        window.present()

    app.connect("activate", activate)
    return app.run([])
