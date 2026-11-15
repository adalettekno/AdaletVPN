"""Certificate details popover presentation."""

from gi.repository import GLib, Gtk, Pango
from adaletvpn.certificate_list import certificate_name


def show_certificate_info(window):
    item = next(
        (c for c in window.controller.certificates if c.identifier == window.controller.selected_id), None
    )
    if item is None:
        return
    panel = Gtk.Popover()
    panel.set_parent(window.certificate_info)
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
        text.set_markup(
            "<b>"
            + GLib.markup_escape_text(name)
            + "</b>\n"
            + GLib.markup_escape_text(value or "Belirtilmemiş")
        )
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
