from adaletvpn.style import load_css
from adaletvpn.ui import Gtk, GLib, power_button

app = Gtk.Application(application_id="com.adalettekno.AdaletVPN.PowerHitCheck")
failures = []


def activate(application):
    window = Gtk.ApplicationWindow(application=application)
    button = power_button("Bağlan")
    window.set_child(button)
    window.set_default_size(100, 110)
    provider = load_css()
    Gtk.StyleContext.add_provider_for_display(window.get_display(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window.present()

    def check():
        try:
            valid, bounds = button.power_circle.compute_bounds(window)
            assert valid
            cx = bounds.get_x() + bounds.get_width() / 2
            cy = bounds.get_y() + bounds.get_height() / 2
            radius = min(bounds.get_width(), bounds.get_height()) / 2
            for y in range(window.get_height()):
                for x in range(window.get_width()):
                    px, py = x + 0.5, y + 0.5
                    expected = (px - cx) ** 2 + (py - cy) ** 2 <= radius ** 2
                    target = window.pick(px, py, Gtk.PickFlags.DEFAULT)
                    while target is not None and target != button:
                        target = target.get_parent()
                    assert (target == button) == expected, (px, py, expected)
            assert button.get_focusable()
            print("Tüm noktalarda fare hedefi daireyle eşleşiyor; yazı ve boşluklar dışarıda.")
        except Exception as error:
            failures.append(error)
        finally:
            window.close()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(300, check)


app.connect("activate", activate)
app.run([])
if failures:
    raise failures[0]
