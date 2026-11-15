import asyncio
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Gdk

try:
    gi.require_version("GdkX11", "4.0")
    from gi.repository import GdkX11
except (ImportError, ValueError):
    GdkX11 = None

from adaletvpn.core import CertificateValidity, ConnectionError, Controller, Credentials, State
from adaletvpn.connection_view import ConnectionView
from adaletvpn import async_bridge
from adaletvpn.diagnostics import Stage, report_failure
from adaletvpn.demo import DemoCertificates, DemoVPN
from adaletvpn.auth import credential_problem
from adaletvpn.pkcs11 import DisabledVPN
from adaletvpn.errors import MESSAGES
from adaletvpn.vault import CredentialVault
from adaletvpn.vault_coordinator import VaultCoordinator, VaultOperation
from adaletvpn.fonts import load_fonts
from adaletvpn.certificate_list import certificate_labels
from adaletvpn.certificate_details import show_certificate_info
from adaletvpn.layout import build, power_button
from adaletvpn.style import apply_style
from adaletvpn.tray import APP_ICON, TrayIcon


def set_label(label, text):
    if label.get_text() != text:
        label.set_text(text)


def set_markup(label, markup):
    if label.get_label() != markup:
        label.set_markup(markup)


def show_message(label, text):
    if text == "Sertifika bulunamadı!":
        label.set_text("")
    else:
        if text in {message for code, message in MESSAGES.items() if code.startswith("pin_")}:
            label.add_css_class("pin-warning")
        else:
            label.remove_css_class("pin-warning")
        label.set_text(text)



class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, provider=None, client=None):
        load_fonts()
        super().__init__(application=app, title="AdaletVPN" if client else "AdaletVPN — Sertifikalar" if provider else "AdaletVPN — Deneme")
        icon_root = Path(__file__).resolve().parents[2] / "data/app-icons"

        Gtk.IconTheme.get_for_display(self.get_display()).add_search_path(str(icon_root))
        self.set_icon_name(APP_ICON)
        self._tray = None
        self._tray_requested = False
        self._about_window = None
        self.read_only = provider is not None and client is None
        self.managed_connection = client is not None
        self.set_default_size(500, -1)
        self.controller = Controller(provider, client or DisabledVPN(), timeout=150) if provider else Controller(DemoCertificates(), DemoVPN())
        self.loop = async_bridge.create_loop()
        self.task = None
        self.closing = False
        self.allow_close = False
        self.cancelling = False
        self.credentials = None
        self.updating_list = False
        self.vault = CredentialVault()
        self.vault_selected = None
        self.vault_actions = VaultCoordinator(self)
        self._initial_focus_pending = True
        self._initial_focus_waiting = False

        apply_style(self)
        build(self)
        self.timer = None
        self._ui_snapshot = None
        self.poll_timer = GLib.timeout_add_seconds(5, self.poll_certificates) if self.read_only else None
        self.monitor_task = None
        self.monitor_timer = GLib.timeout_add_seconds(1, self.monitor_connection) if self.managed_connection else None
        self.start(self.scan())

    def focus_initial_username(self):
        if not getattr(self, "_initial_focus_pending", False):
            return GLib.SOURCE_REMOVE
        if self.closing or self.read_only:
            self._initial_focus_pending = False
            return GLib.SOURCE_REMOVE
        if not self.get_mapped() or (self.task is not None and not self.task.done()):
            return GLib.SOURCE_REMOVE
        task = self.vault_actions.task
        if task is not None and not task.done():
            if not self._initial_focus_waiting:
                self._initial_focus_waiting = True

                def loaded(_task):
                    self._initial_focus_waiting = False
                    GLib.idle_add(self.focus_initial_username)

                task.add_done_callback(loaded)
            return GLib.SOURCE_REMOVE
        self._initial_focus_pending = False
        focus = self.get_focus()
        editing_secret = focus is not None and any(
            focus == field or focus.is_ancestor(field) for field in (self.password, self.pin))
        if (not editing_secret and self.username.get_text() == "ab"
                and not self.password.get_text() and not self.pin.get_text()
                and self.username.get_sensitive()):
            self.username.grab_focus()
            self.username.select_region(-1, -1)
            self.username.set_position(-1)
        return GLib.SOURCE_REMOVE

    def username_key_pressed(self, controller, keyval, keycode, state):
        modifiers = (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK
                     | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK)
        if keyval == Gdk.KEY_Tab and not state & modifiers:
            return self.password.grab_focus()
        return False

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
        show_certificate_info(self)

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
        async_bridge.pump_once(self.loop)

    def finished(self, task):
        current = self.task is task
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except ConnectionError as error:
            if current:
                show_message(self.message, str(error))
        except Exception as error:
            report_failure(Stage.UI_TASK, error)
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
                if getattr(self, "_initial_focus_pending", False):
                    GLib.idle_add(self.focus_initial_username)

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
            tuple((item.identifier, item.validity_code(), item.problem_code())
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
        except Exception as error:
            report_failure(Stage.UI_REFRESH, error)
        if async_bridge.has_pending_tasks(self.loop):
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
        await self.controller.check_connection()
        if self.controller.state == State.CONNECTED and not self.controller.error:
            show_message(self.message, "")

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
            self.vault_actions.start_vault(VaultOperation.LOAD, self.vault_actions.load_credentials())

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
            self.controller.clear_selection()
        selected = self.controller.selected_id
        if selected != self.vault_selected:
            self.vault_selected = selected
            self.pin.set_text("")
            self.password.set_text("")
            self.username.set_text("ab")
            if (self.managed_connection and self.remember.get_active() and selected
                    and (self.task is None or self.task.done())):
                self.vault_actions.start_vault(VaultOperation.LOAD, self.vault_actions.load_credentials())
        self.refresh()

    def connect_vpn(self, *_):
        if self.read_only or self.closing or not self.controller.can_start:
            return
        if self.task is not None and not self.task.done():
            return
        problem = credential_problem(self.username.get_text(), self.pin.get_text(), self.password.get_text())
        if problem:
            show_message(self.message, problem)
            return
        self.vault_actions.cancel_vault(announce=False)
        self.credentials = Credentials(self.username.get_text(), self.pin.get_text(), self.password.get_text())
        keep_password = self.managed_connection and self.remember.get_active()
        if not (keep_password and self.remember_pin.get_active()):
            self.pin.set_text("")
        if not keep_password:
            self.password.set_text("")
        remembered = (Credentials(self.credentials.username, self.credentials.pin if self.remember_pin.get_active() else "", self.credentials.password)
                      if self.managed_connection and self.remember.get_active() else None)
        self.start(self.vault_actions.connect_and_remember(self.credentials, self.controller.selected_id, remembered))

    def dismiss_kept_credentials_notice(self):
        message = getattr(self, "message", None)
        if (message is None or self.controller.error
                or self.controller.state != State.DISCONNECTED
                or self.controller.needs_cleanup):
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
            self.controller.clear_error()
            self.start(self.controller.disconnect())

    def refresh(self):
        busy = self.task is not None and not self.task.done()
        credentials_valid = credential_problem(
            self.username.get_text(), self.pin.get_text(), self.password.get_text()) is None
        view = ConnectionView.from_controller(
            self.controller, busy=busy, closing=self.closing, cancelling=self.cancelling,
            read_only=self.read_only, managed=self.managed_connection,
            credentials_valid=credentials_valid)
        idle = view.editable
        status = view.status_message
        set_label(self.status, status)
        self.status.set_css_classes(["caption", "connection-status", view.tone])
        self.status.set_wrap(self.controller.state not in (
            State.CONNECTED, State.DISCONNECTED, State.CONNECTING))
        self.status.set_tooltip_text("VPN bağlantı durumu: " + status)
        self.status.get_parent().get_parent().set_css_classes(["vpn-badge", view.badge])
        self.status_spinner.set_spinning(view.in_progress)
        self.status_spinner.set_visible(view.in_progress)
        self.status_indicator.set_visible(not view.in_progress)
        set_label(self.status_indicator, "●")
        self.status_indicator.set_css_classes(["connection-status", view.tone])
        self.connection_actions.set_visible_child_name("stop" if view.show_stop else "connect")
        item = next((c for c in self.controller.certificates
                     if c.identifier == self.controller.selected_id), None)
        if item is None:
            self.details.set_visible(False)
        else:
            validity_code = item.validity_code()
            validity = validity_code.value
            problem = item.connection_problem()
            valid = problem is None
            validity_text = ("Geçerli" if valid else "Doğrulanamadı" if item.details_error
                             else "Kullanım dışı!" if validity_code is CertificateValidity.EXPIRED else validity)
            self.details.set_from_icon_name("object-select-symbolic" if valid else "window-close-symbolic")
            self.details.set_css_classes(["certificate-status", "valid" if valid else "invalid"])
            validity_text = ("Geçerli — bağlantı için uygun" if valid else
                             "Kullanım dışı! " + problem if validity_code is CertificateValidity.EXPIRED else
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
        self.connect_button.set_sensitive(view.can_connect)
        set_label(self.stop_button.action_label, "İptal" if busy else "Kes")
        self.stop_button.set_busy(busy)
        if self.controller.state == State.CONNECTED and not busy:
            self.stop_button.add_css_class("connected")
        else:
            self.stop_button.remove_css_class("connected")
        self.stop_button.set_tooltip_text("Devam eden işlemi iptal et" if busy else "VPN bağlantısını kes")
        self.stop_button.set_sensitive(view.can_stop)
        if self.controller.error:
            show_message(self.message, self.controller.error)
        MainWindow.sync_tray(self)

    def sync_tray(self):
        if not hasattr(self, "set_visible"):
            return
        if self.closing:
            self.close_tray(False)
        elif self._tray is not None:
            self._tray.set_state(self.controller.state)

    def minimize_to_tray(self, *_):
        if self.closing:
            return
        self._tray_requested = True
        if not self.open_tray():
            self.tray_failed()
        elif self._tray.ready:
            self.tray_ready()

    def show_about(self, *_):
        from adaletvpn.about import show_about
        show_about(self)

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
        tray.set_state(self.controller.state)
        return True

    def title_minimize(self, *_):
        self._tray_requested = False
        self.minimize()

    def toggle_maximize(self, *_):
        if self.is_maximized():
            self.unmaximize()
        else:
            self.maximize()

    def reveal_from_tray(self):
        if getattr(self, "closing", False):
            return
        self._tray_requested = False
        # StatusNotifier Activate carries no event timestamp. On X11, present()
        # can reuse stale user time and KWin then leaves the window behind.
        timestamp = None
        if GdkX11 is not None and isinstance(self.get_display(), GdkX11.X11Display):
            self.realize()
            surface = self.get_surface()
            timestamp = GdkX11.x11_get_server_time(surface)
            # KWin checks user time when the hidden window is first mapped.
            surface.set_user_time(timestamp)
        self.show_from_tray(timestamp)

    def show_from_tray(self, timestamp=None):
        if hasattr(self, "set_opacity"):
            self.set_opacity(1)
        self.set_visible(True)
        self.unminimize()
        if timestamp is None:
            self.present()
        else:
            self.present_with_time(timestamp)

    def tray_ready(self):
        if self._tray_requested and not self.closing and self._tray is not None and self._tray.ready:
            self._tray_requested = False
            self.set_visible(False)

    def tray_failed(self):
        requested = self._tray_requested
        self._tray_requested = False
        tray, self._tray = self._tray, None
        if tray is not None:
            tray.stop()
        if not self.closing:
            self.show_from_tray()
            if requested:
                show_message(self.message, "Sistem tepsisi kullanılamıyor. Pencere açık bırakıldı.")

    def close_tray(self, show_window):
        self._tray_requested = False
        tray, self._tray = self._tray, None
        if tray is not None:
            tray.stop()
        if show_window and not self.closing:
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
        self.vault_actions.cancel_vault(announce=False)
        if self.vault_actions.task is not None:
            try:
                await self.vault_actions.task
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
