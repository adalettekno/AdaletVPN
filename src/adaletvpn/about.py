"""Small application information window."""

from pathlib import Path

from gi.repository import Gtk

from adaletvpn import __version__
from adaletvpn.style import apply_style


def show_about(parent):
    if parent._about_window is not None:
        parent._about_window.present()
        return
    window = Gtk.Window(title="Hakkında", transient_for=parent, modal=False)
    window.set_destroy_with_parent(True)
    window.set_resizable(False)
    window.set_default_size(340, -1)
    apply_style(window)
    window.add_css_class("about-window")
    header = Gtk.HeaderBar()
    header.set_show_title_buttons(False)
    header.set_title_widget(Gtk.Box())
    close_button = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Kapat")
    close_button.add_css_class("titlebutton")
    close_button.set_valign(Gtk.Align.CENTER)
    close_button.update_property([Gtk.AccessibleProperty.LABEL], ["Kapat"])
    close_button.connect("clicked", lambda *_: window.close())
    header.pack_end(close_button)
    window.set_titlebar(header)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for edge in ("top", "bottom", "start", "end"):
        getattr(content, f"set_margin_{edge}")(24)
    logo_path = Path(__file__).resolve().parents[2] / "data/app-icons/adaletvpn-header.svg"
    logo = Gtk.Picture.new_for_filename(str(logo_path))
    logo.set_size_request(180, 36)
    logo.set_halign(Gtk.Align.CENTER)
    logo.set_alternative_text("AdaletVPN")
    content.append(logo)
    name = Gtk.Label(label="Adalet Tekno")
    name.add_css_class("about-developer")
    content.append(name)
    version = Gtk.Label(label=__version__)
    version.add_css_class("about-version")
    version.set_halign(Gtk.Align.CENTER)
    version.set_can_target(True)
    hover = Gtk.EventControllerMotion()
    hover.connect("enter", lambda *_: version.add_css_class("hovered"))
    hover.connect("leave", lambda *_: version.remove_css_class("hovered"))
    version.add_controller(hover)
    content.append(version)
    website = Gtk.LinkButton(uri="https://www.adalettekno.com")
    website.add_css_class("about-website")
    website.update_property([Gtk.AccessibleProperty.LABEL], ["İnternet sitesi"])
    row = Gtk.Box(spacing=12)
    label = Gtk.Label(label="İnternet sitesi", xalign=0)
    label.set_hexpand(True)
    row.append(label)
    external = Gtk.DrawingArea()
    external.set_content_width(18)
    external.set_content_height(18)
    external.set_valign(Gtk.Align.CENTER)
    external.set_can_target(False)

    def draw_external(widget, context, width, height):
        color = widget.get_color()
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        context.set_line_width(1.8)
        context.set_line_join(1)
        context.move_to(8, 4)
        context.line_to(3, 4)
        context.line_to(3, 15)
        context.line_to(14, 15)
        context.line_to(14, 10)
        context.move_to(8, 10)
        context.line_to(15, 3)
        context.move_to(10, 3)
        context.line_to(15, 3)
        context.line_to(15, 8)
        context.stroke()

    external.set_draw_func(draw_external)
    row.append(external)
    website.set_child(row)
    website.set_margin_top(12)
    content.append(website)
    window.set_child(content)
    parent._about_window = window

    def closed(*_):
        parent._about_window = None
        return False

    window.connect("close-request", closed)
    window.present()
