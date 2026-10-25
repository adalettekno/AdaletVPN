from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


_CSS = Path(__file__).with_name("adaletvpn.css")


def load_css():
    provider = Gtk.CssProvider()
    provider.load_from_path(str(_CSS))
    return provider


def apply_style(window):
    window.add_css_class("adaletvpn")
    window.certificate_css = load_css()
    Gtk.StyleContext.add_provider_for_display(
        window.get_display(), window.certificate_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
