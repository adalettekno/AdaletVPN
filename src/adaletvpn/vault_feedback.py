from gi.repository import Gtk, GLib, Pango


class VaultFeedback:
    def __init__(self, window):
        self.window = window
        self.controls = {}
        self.target = "remember"
        self.text = ""
        self.popup = None
        self.timer = None
        window.connect("unrealize", lambda *_: self.dismiss())

    def wrap(self, control, name):
        overlay = Gtk.Overlay()
        control.set_margin_end(0)
        overlay.set_child(control)
        overlay.set_visible(self.window.managed_connection)
        warning = Gtk.MenuButton(icon_name="dialog-warning-symbolic")
        warning.add_css_class("vault-warning")
        warning.set_halign(Gtk.Align.END)
        warning.set_valign(Gtk.Align.START)
        warning.set_tooltip_text("Kasa işlemi tamamlanamadı. Ayrıntıları göster.")
        warning.set_visible(False)
        overlay.add_overlay(warning)
        self.controls[name] = (control, warning)
        return overlay

    def set_target(self, target):
        self.target = target

    def get_text(self):
        return self.text

    def dismiss(self):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.popup is not None:
            popup, self.popup = self.popup, None
            popup.popdown()
            popup.unparent()

    def set_text(self, text, *, error=False):
        self.text = text
        self.dismiss()
        if self.window.closing or self.target not in self.controls:
            return
        control, warning = self.controls[self.target]

        if text.startswith("Kasa işlemi sürüyor"):
            return
        label = Gtk.Label(label=text, wrap=True, xalign=0)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(38)
        panel = Gtk.Popover()
        panel.set_child(label)
        panel.set_position(Gtk.PositionType.TOP)
        if error:
            warning.set_popover(panel)
            warning.set_visible(True)
            return
        warning.set_visible(False)
        warning.set_popover(None)
        panel.set_parent(control)
        panel.set_autohide(False)
        panel.set_can_target(False)
        panel.set_focusable(False)
        self.popup = panel
        panel.popup()

        def expire():
            self.timer = None
            self.dismiss()
            return GLib.SOURCE_REMOVE

        self.timer = GLib.timeout_add_seconds(4, expire)
