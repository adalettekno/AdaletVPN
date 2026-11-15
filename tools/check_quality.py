"""One command shared by local development and CI."""

import subprocess
import sys

# Apply formatting incrementally; do not rewrite unrelated legacy/tray code.
FORMATTED = (
    "src/adaletvpn/about.py",
    "tools/check_window_layout.py",
    "src/adaletvpn/async_bridge.py",
    "src/adaletvpn/certificate_details.py",
    "src/adaletvpn/connection_view.py",
    "src/adaletvpn/diagnostics.py",
    "src/adaletvpn/vault_coordinator.py",
    "tests/test_architecture.py",
    "tools/check_ui.py",
    "tools/check_quality.py",
)


def main():
    subprocess.run([sys.executable, "-m", "ruff", "check", "src", "tests", "tools"], check=True)
    subprocess.run([sys.executable, "-m", "ruff", "format", "--check", *FORMATTED], check=True)


if __name__ == "__main__":
    main()
