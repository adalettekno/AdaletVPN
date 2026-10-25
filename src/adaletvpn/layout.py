import math
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango

from adaletvpn.remember_preferences import read_remember
from adaletvpn.vault_feedback import VaultFeedback


class CircularPowerButton(Gtk.Button):
    def do_contains(self, x, y):
        circle = getattr(self, "power_circle", None)
        if circle is None:
            return False
        valid, bounds = circle.compute_bounds(self)
        if not valid:
            return False
        radius = min(bounds.get_width(), bounds.get_height()) / 2
        dx = x - (bounds.get_x() + bounds.get_width() / 2)
        dy = y - (bounds.get_y() + bounds.get_height() / 2)
        return dx * dx + dy * dy <= radius * radius


def power_button(label):
    button = CircularPowerButton()
    button.add_css_class("connection-action")
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

    content.set_can_target(False)
    content.set_halign(Gtk.Align.CENTER)
    content.set_valign(Gtk.Align.CENTER)
    circle = Gtk.Overlay()
    button.power_circle = circle
    circle.add_css_class("power-circle")
    circle.set_size_request(66, 66)
    icon = Gtk.DrawingArea()
    icon.set_content_width(30)
    icon.set_content_height(30)
    icon.set_halign(Gtk.Align.CENTER)
    icon.set_valign(Gtk.Align.CENTER)

    def draw_power(widget, context, width, height):
        color = widget.get_color()
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        context.translate(width / 2, height / 2)
        context.set_line_width(3.8)
        context.set_line_cap(1)
        context.arc(0, 1, 10, -math.pi / 3, 4 * math.pi / 3)
        context.stroke()
        context.move_to(0, -12)
        context.line_to(0, -2)
        context.stroke()

    icon.set_draw_func(draw_power)
    icon.set_can_target(False)
    circle.set_child(icon)
    arc = Gtk.DrawingArea()
    arc.set_can_target(False)
    arc.set_visible(False)
    arc.set_halign(Gtk.Align.FILL)
    arc.set_valign(Gtk.Align.FILL)
    arc.set_hexpand(True)
    arc.set_vexpand(True)
    button.power_arc = arc
    button._spin_id = None
    button._spin_angle = 0.0
    arc.spin_button = button

    def draw_arc(widget, context, width, height):
        angle = widget.spin_button._spin_angle
        color = widget.get_parent().get_color()
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        context.translate(width / 2, height / 2)
        context.set_line_width(4.5)
        context.set_line_cap(1)
        radius = min(width, height) / 2 - 6
        context.arc(0, 0, radius, angle, angle + math.pi * 0.85)
        context.stroke()

    arc.set_draw_func(draw_arc)
    circle.add_overlay(arc)

    def advance_spin(widget, clock):
        widget._spin_angle = (clock.get_frame_time() % 1000000) / 1000000 * math.tau
        widget.power_arc.queue_draw()
        return GLib.SOURCE_CONTINUE

    def set_busy(widget, busy):
        widget.power_arc.set_visible(bool(busy))
        if busy and widget._spin_id is None:
            widget._spin_id = widget.add_tick_callback(advance_spin)
        elif not busy and widget._spin_id is not None:
            widget.remove_tick_callback(widget._spin_id)
            widget._spin_id = None

    button.set_busy = set_busy.__get__(button)
    content.append(circle)
    button.action_label = Gtk.Label(label=label)
    content.append(button.action_label)
    button.set_child(content)
    return button


def title_button(icon, tooltip, handler):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
    button.add_css_class("titlebutton")
    button.set_valign(Gtk.Align.CENTER)
    button.connect("clicked", handler)
    return button


def install_titlebar(window, heading):
    header = Gtk.HeaderBar()
    header.set_show_title_buttons(False)
    header.set_title_widget(heading)
    header.pack_end(title_button("window-close-symbolic", "Kapat", lambda *_: window.close()))
    header.pack_end(title_button("window-maximize-symbolic", "Ekranı kapla", window.toggle_maximize))
    header.pack_end(title_button("window-minimize-symbolic", "Simge durumuna küçült", window.title_minimize))
    window.set_titlebar(header)


def build(window):
    icon_root = Path(__file__).resolve().parents[2] / "data/app-icons"
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for edge in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{edge}")(24)
    box.set_margin_top(16)
    window.set_child(box)
    heading_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    heading_row.set_halign(Gtk.Align.CENTER)
    heading_icon = Gtk.Picture.new_for_filename(str(icon_root / "adaletvpn-header.svg"))
    heading_icon.set_size_request(180, 36)
    heading_icon.set_can_shrink(True)
    heading_icon.set_alternative_text("AdaletVPN")
    heading_icon.set_valign(Gtk.Align.CENTER)
    window.heading_icon = heading_icon
    heading_row.append(heading_icon)
    install_titlebar(window, heading_row)
    notice = Gtk.Label(label="Deneme modu: Gerçek VPN bağlantısı kurulmaz.\n"
                             "Gerçek sicil numarası veya şifre girmeyin.", xalign=0)
    if window.read_only:
        notice.set_text("Sertifika okuma modu: VPN bağlantısı kapalı.\n"
                        "Yalnızca PIN gerektirmeyen sertifikalar listelenir.")
    if window.managed_connection:
        notice.set_text("Sicil numaranızı ve şifrelerinizi girip Bağlan düğmesine basın.\n"
                        "Gerekirse Linux yetkilendirmesi ayrı bir sistem penceresinde istenir.")
    notice.set_wrap(True)
    if not window.managed_connection:
        box.append(notice)
    window.username = Gtk.Entry(text="ab")
    username_field = Gtk.Overlay()
    username_field.set_child(window.username)
    username_hint = Gtk.Label(label="(sicil no)")
    username_hint.add_css_class("dim-label")
    username_hint.set_halign(Gtk.Align.START)
    username_hint.set_valign(Gtk.Align.CENTER)
    username_hint.set_can_target(False)
    username_field.add_overlay(username_hint)

    def update_username_hint(entry):
        username_hint.set_visible(entry.get_text() == "ab")
        prefix_width, _ = entry.create_pango_layout("ab").get_pixel_size()
        padding = entry.get_style_context().get_padding()
        username_hint.set_margin_start(padding.left + prefix_width + 6)

    window.username.connect("changed", update_username_hint)
    update_username_hint(window.username)
    username_focus = Gtk.EventControllerFocus()
    username_focus.connect("enter", lambda *_: window.username.set_position(-1)
                           if window.username.get_text() == "ab" else None)
    window.username.add_controller(username_focus)
    window.pin = Gtk.PasswordEntry(placeholder_text="E-imza şifresi", show_peek_icon=True)
    window.password = Gtk.PasswordEntry(placeholder_text="Bilgisayar şifresi", show_peek_icon=True)
    password_keys = Gtk.EventControllerKey()
    password_keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    password_keys.connect("key-pressed", window.password_key_pressed)
    window.password.add_controller(password_keys)
    window.pin_enter_down = False
    pin_keys = Gtk.EventControllerKey()
    pin_keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    pin_keys.connect("key-pressed", window.pin_key_pressed)
    pin_keys.connect("key-released", window.pin_key_released)
    window.pin.add_controller(pin_keys)
    window.certificates = Gtk.DropDown.new_from_strings(["Önce sertifikaları tarayın"])
    settings = window.get_settings()
    theme_properties = ["gtk-theme-name", "gtk-application-prefer-dark-theme"]
    if settings.find_property("gtk-interface-color-scheme") is not None:
        theme_properties.append("gtk-interface-color-scheme")
    theme_handlers = [settings.connect("notify::" + name, lambda *_: GLib.idle_add(window.update_header_icon_theme))
                      for name in theme_properties]
    window.connect("map", lambda *_: window.update_header_icon_theme())
    window.connect("unrealize", lambda *_: [settings.disconnect(handler) for handler in theme_handlers])
    window.certificates.connect("notify::selected", window.select_certificate)
    certificate_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    certificate_row.set_halign(Gtk.Align.FILL)
    window.certificates.set_hexpand(True)
    certificate_row.append(window.certificates)
    certificate_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    window.scan_button = Gtk.Button(icon_name="view-refresh-symbolic")
    window.scan_button.set_tooltip_text("Sertifikaları yenile")
    window.scan_button.update_property([Gtk.AccessibleProperty.LABEL], ["Sertifikaları yenile"])
    window.scan_button.connect("clicked", lambda *_: window.start(window.scan()))
    window.scan_warning = Gtk.MenuButton(icon_name="dialog-warning-symbolic")
    window.scan_warning.add_css_class("scan-warning")
    window.scan_warning.set_tooltip_text("Sertifika tarama sorununun ayrıntıları")
    window.scan_warning.set_visible(False)
    window.scan_problem = Gtk.Label(wrap=True, xalign=0)
    window.scan_problem.set_max_width_chars(38)
    window.scan_problem.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    scan_panel = Gtk.Popover()
    scan_panel.set_child(window.scan_problem)
    window.scan_warning.set_popover(scan_panel)
    certificate_actions.append(window.scan_warning)
    window.certificate_info = Gtk.Button(icon_name="dialog-information-symbolic")
    window.certificate_info.set_tooltip_text("Sertifika bilgileri")
    window.certificate_info.update_property([Gtk.AccessibleProperty.LABEL], ["Sertifika bilgileri"])
    window.certificate_info.connect("clicked", window.show_certificate_info)
    certificate_actions.append(window.certificate_info)
    certificate_actions.append(window.scan_button)
    certificate_row.append(certificate_actions)
    box.append(certificate_row)
    window.details = Gtk.Image()
    window.details.set_pixel_size(16)
    window.details.add_css_class("certificate-status")
    window.details.set_visible(False)
    certificate_actions.prepend(window.details)
    if window.read_only:
        hint = Gtk.Label(label="Tarih kontrolü, sertifikanın güvenilirliğini veya UYAP uyumluluğunu doğrulamaz.", xalign=0, wrap=True)
        hint.set_max_width_chars(65)
        box.append(hint)
    form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    form.set_halign(Gtk.Align.FILL)
    form.set_size_request(420, -1)
    form.set_margin_top(12)
    box.append(form)
    window.vault_message = VaultFeedback(window)
    fields = Gtk.Grid(column_spacing=6, row_spacing=12)
    fields.set_visible(not window.read_only)
    form.append(fields)
    for label, widget in (("Sicil numarası", window.username),
                          ("Bilgisayar açılış şifresi", window.password),
                          ("E-imza şifresi", window.pin)):
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
        widget.set_hexpand(True)
    username_field.set_hexpand(True)
    for widget in (window.username, window.password, window.pin):
        widget.set_property("width-chars", 8)
    fields.attach(username_field, 0, 0, 1, 1)
    fields.attach(window.password, 0, 1, 1, 1)
    fields.attach(window.pin, 0, 2, 1, 1)
    for widget in (window.username, window.password, window.pin):
        widget.set_visible(not window.read_only)
        widget.connect("changed", lambda *_: window.refresh())
    window.remember = Gtk.CheckButton(label="Hatırla")
    window.remember.set_tooltip_text("Bilgisayar şifresini hatırla")
    window.remember.update_property([Gtk.AccessibleProperty.LABEL], ["Bilgisayar şifresini hatırla"])
    window.remember.set_halign(Gtk.Align.START)
    window.remember.set_active(window.managed_connection and read_remember())
    window.remember.set_visible(window.managed_connection)
    window.remember.connect("toggled", window.remember_toggled)
    window.remember.set_valign(Gtk.Align.FILL)
    fields.attach(window.vault_message.wrap(window.remember, "remember"), 1, 1, 1, 1)
    window.remember_pin = Gtk.CheckButton(label="Hatırla")
    window.remember_pin.update_property([Gtk.AccessibleProperty.LABEL], ["E-imza şifresini hatırla"])
    window.remember_pin.set_halign(Gtk.Align.START)
    window.remember_pin.set_active(window.managed_connection and read_remember(pin=True))
    window.remember_pin.set_visible(window.managed_connection)
    window.remember_pin.connect("toggled", window.pin_remember_toggled)
    window.remember_pin.set_valign(Gtk.Align.FILL)
    fields.attach(window.vault_message.wrap(window.remember_pin, "pin"), 1, 2, 1, 1)

    def update_remember_style(widget):
        if widget.get_active():
            widget.add_css_class("remember-selected")
        else:
            widget.remove_css_class("remember-selected")

    for widget in (window.remember, window.remember_pin):
        widget.add_css_class("remember-option")
        update_remember_style(widget)
        widget.connect("toggled", update_remember_style)
    window.forget_button = Gtk.Button()
    window.forget_button.add_css_class("forget-action")
    window.forget_button.set_hexpand(False)
    window.forget_button.set_halign(Gtk.Align.FILL)
    window.forget_button.set_valign(Gtk.Align.FILL)
    forget_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    forget_content.set_valign(Gtk.Align.CENTER)
    forget_content.append(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
    forget_content.append(Gtk.Label(label="Kayıtlı\nbilgileri sil", justify=Gtk.Justification.CENTER))
    window.forget_button.set_child(forget_content)
    window.forget_button.set_visible(window.managed_connection)
    window.forget_button.connect("clicked", lambda *_: window.start_vault(window.forget_credentials()))
    fields.attach(window.vault_message.wrap(window.forget_button, "forget"), 2, 1, 1, 2)
    field_heights = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.VERTICAL)
    for widget in (window.password, window.pin, window.remember, window.remember_pin):
        field_heights.add_widget(widget)
    row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    row.set_valign(Gtk.Align.CENTER)
    window.connection_actions = Gtk.Stack()
    window.connection_actions.set_size_request(100, -1)
    window.connect_button = power_button("Bağlan")
    window.connect_button.connect("clicked", window.connect_vpn)
    window.connect_button.set_tooltip_text("VPN bağlantısını başlat")
    window.stop_button = power_button("Kes")
    window.stop_button.connect("clicked", window.stop_vpn)
    window.connection_actions.add_named(window.connect_button, "connect")
    window.connection_actions.add_named(window.stop_button, "stop")
    row.append(window.connection_actions)
    window.status_indicator = Gtk.Label()
    window.status_spinner = Gtk.Spinner()
    normal_status = Gtk.Box(spacing=4)
    normal_status.set_halign(Gtk.Align.CENTER)
    normal_status.set_valign(Gtk.Align.CENTER)
    normal_status.append(window.status_indicator)
    normal_status.append(window.status_spinner)
    status_badge = Gtk.CenterBox()
    status_badge.set_center_widget(normal_status)
    window.status_display = Gtk.Stack()
    window.status_display.set_hhomogeneous(True)
    window.status_display.set_vhomogeneous(True)
    window.status_display.add_named(status_badge, "normal")
    window.status_display.set_halign(Gtk.Align.FILL)
    window.status_display.set_valign(Gtk.Align.CENTER)
    window.status_display.set_size_request(140, 40)
    status_height = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.VERTICAL)
    status_height.add_widget(username_field)
    status_height.add_widget(window.status_display)
    fields.attach(window.status_display, 1, 0, 2, 1)
    fields.attach(row, 3, 0, 1, 3)
    window.status = Gtk.Label(xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER)
    window.status.set_wrap_mode(Pango.WrapMode.WORD)
    window.status.set_max_width_chars(12)
    window.status.add_css_class("caption")
    normal_status.append(window.status)
    window.connection_warning = Gtk.MenuButton()
    window.connection_warning.add_css_class("flat")
    window.connection_warning.add_css_class("vpn-error")
    warning_label = Gtk.Label()
    warning_label.set_markup("<b>⚠ Hata</b>")
    window.connection_warning.set_child(warning_label)
    window.connection_warning.set_tooltip_text("Hata ayrıntılarını göster")
    window.status_display.add_named(window.connection_warning, "error")
    window.connection_error_panel = Gtk.Popover()
    window.connection_error_panel.set_position(Gtk.PositionType.TOP)
    error_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    error_scroll = Gtk.ScrolledWindow()
    error_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    error_scroll.set_size_request(320, -1)
    error_scroll.set_max_content_height(240)
    error_scroll.set_propagate_natural_height(True)
    window.message = Gtk.Label(xalign=0, wrap=True)
    window.message.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    window.message.set_width_chars(36)
    window.message.set_max_width_chars(40)
    error_scroll.set_child(window.message)
    error_content.append(error_scroll)
    error_close = Gtk.Button(label="Kapat", halign=Gtk.Align.END)
    error_close.connect("clicked", lambda *_: window.connection_error_panel.popdown())
    error_content.append(error_close)
    window.connection_error_panel.set_child(error_content)
    window.connection_warning.set_popover(window.connection_error_panel)
    window.last_connection_message = ""
    window.message.connect("notify::label", window.connection_message_changed)
    window.connect("close-request", window.close_requested)
