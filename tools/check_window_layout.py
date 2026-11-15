"""GTK checks for stable certificate geometry, title controls and About."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from adaletvpn.ui import GLib, Gtk, MainWindow
from adaletvpn import __version__
from check_ui import Provider, Client, Vault


class SlowProvider(Provider):
    async def list_certificates(self):
        await asyncio.sleep(0.7)
        return await super().list_certificates()


def main():
    app = Gtk.Application(application_id="com.adalettekno.AdaletVPN.LayoutCheck")
    failures = []
    window = None
    baseline = None
    phase = 0
    ticks = 0

    def geometry():
        valid, rect = window.certificates.compute_bounds(window)
        assert valid
        values = [
            window.get_width(),
            window.get_height(),
            rect.get_x(),
            rect.get_y(),
            rect.get_width(),
            rect.get_height(),
        ]
        for widget in (window.details, window.scan_warning, window.certificate_info, window.scan_button):
            valid, slot = widget.get_parent().compute_bounds(window)
            assert valid
            values.extend((slot.get_x(), slot.get_y(), slot.get_width(), slot.get_height()))
        return tuple(values)

    def capture(widget, name):
        if "--snapshots" in sys.argv:
            folder = Path("/tmp/adaletvpn-window-review")
            folder.mkdir(exist_ok=True)
            subprocess.run(
                ["import", "-window", str(widget.get_surface().get_xid()), str(folder / (name + ".png"))],
                check=True,
            )

    def activate(application):
        nonlocal window
        with patch("adaletvpn.ui.CredentialVault", return_value=Vault()):
            window = MainWindow(application, SlowProvider(), Client())
        window.present()
        GLib.timeout_add(150, check)

    def check():
        nonlocal baseline, phase, ticks
        ticks += 1
        try:
            assert ticks < 100
            if phase == 0:
                baseline = geometry()
                capture(window, "scanning")
                phase = 1
                return True
            if window.task and not window.task.done():
                return True
            if phase == 1:
                assert geometry() == baseline, (baseline, geometry())
                assert window.get_visible()
                valid, tray_rect = window.tray_button.compute_bounds(window)
                valid_about, about_rect = window.about_button.compute_bounds(window)
                assert valid and valid_about
                assert tray_rect.get_width() == tray_rect.get_height(), (
                    tray_rect.get_width(),
                    tray_rect.get_height(),
                )
                assert tray_rect.get_width() == about_rect.get_width(), (
                    tray_rect.get_width(),
                    about_rect.get_width(),
                )
                _, cert_rect = window.certificates.compute_bounds(window)
                _, valid_rect = window.details.get_parent().compute_bounds(window)
                _, info_rect = window.certificate_info.get_parent().compute_bounds(window)
                _, scan_rect = window.scan_button.get_parent().compute_bounds(window)
                gaps = (
                    valid_rect.get_x() - cert_rect.get_x() - cert_rect.get_width(),
                    info_rect.get_x() - valid_rect.get_x() - valid_rect.get_width(),
                    scan_rect.get_x() - info_rect.get_x() - info_rect.get_width(),
                )
                assert gaps == (8, 8, 8), gaps
                assert window._tray is None
                capture(window, "loaded")
                window.certificates.set_model(Gtk.StringList.new(["Çok uzun sertifika sahibi " * 12]))
            elif phase == 2:
                assert geometry() == baseline, (baseline, geometry())
                window.show_about()
            elif phase == 3:
                about = window._about_window
                assert about is not None and about.get_visible()
                box = about.get_child()
                logo = box.get_first_child()
                assert logo.get_alternative_text() == "AdaletVPN"
                name = logo.get_next_sibling()
                version = name.get_next_sibling()
                link = version.get_next_sibling()
                assert name.get_text() == "Adalet Tekno"
                assert version.get_text() == __version__
                assert link.get_child().get_first_child().get_text() == "İnternet sitesi"
                assert link.get_uri() == "https://www.adalettekno.com"
                window.show_about()
                assert window._about_window is about
                capture(about, "about")
                about.close()
                assert window._about_window is None
                window.certificates.set_model(Gtk.StringList.new(["Sertifika bulunamadı!"]))
                window.controller.certificates = ()
                window.controller.clear_selection()
                window.refresh()
            elif phase == 4:
                assert geometry() == baseline, (baseline, geometry())
                capture(window, "missing")
                window.close()
                return False
            phase += 1
            return True
        except Exception as error:
            failures.append(error)
            app.quit()
            return False

    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"XDG_CONFIG_HOME": folder}):
        app.connect("activate", activate)
        app.run([])
    if failures:
        raise failures[0]
    assert phase == 4
    print("GTK: stable geometry before/after scan, long/missing certificates and About OK")


if __name__ == "__main__":
    main()
