import os
import argparse
from pathlib import Path
import shlex
import shutil

parser = argparse.ArgumentParser(description='AdaletVPN menü kısayolunu kurar.')
parser.add_argument('--module', required=True, type=Path,
                    help='E-imza PKCS#11 sürücüsünün tam yolu')
args = parser.parse_args()
if not args.module.is_absolute() or not args.module.is_file():
    parser.error('--module mevcut bir sürücü dosyasının tam yolu olmalıdır.')

root = Path(__file__).resolve().parents[1]
base = Path(os.environ.get('XDG_DATA_HOME', ''))
if not base.is_absolute():
    base = Path.home() / '.local/share'
icons = base / 'icons/hicolor/512x512/apps'
assets = base / 'adaletvpn'
applications = base / 'applications'
for directory in (icons, assets, applications):
    directory.mkdir(parents=True, exist_ok=True)
icon_name = 'com.adalettekno.AdaletVPN'
shutil.copyfile(root / 'data/app-icons/hicolor/512x512/apps' / f'{icon_name}.png', icons / f'{icon_name}.png')
old_icon = icons / 'tr.adaletvpn.png'
if old_icon.is_file():
    old_icon.unlink()
shutil.copyfile(root / 'data/app-icons/adaletvpn.ico', assets / 'adaletvpn.ico')

launcher = assets / 'launch'
launcher.write_text('#!/bin/sh\nexec /usr/bin/env PYTHONPATH=' + shlex.quote(str(root / 'src')) +
                    ' PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m adaletvpn --vpn --module ' +
                    shlex.quote(str(args.module)) + '\n')
launcher.chmod(0o755)

command = str(launcher).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
old_desktop = applications / 'tr.adaletvpn.desktop'
if old_desktop.is_file():
    old_desktop.unlink()
desktop = applications / 'com.adalettekno.AdaletVPN.desktop'
desktop.write_text('[Desktop Entry]\nType=Application\nName=AdaletVPN\n'
                   'Comment=AdaletVPN, Linux üzerinde e-imza ile UYAP VPN bağlantısı kurmanızı sağlar.\nExec="' + command + '"\n'
                   'Icon=' + icon_name + '\nTerminal=false\nCategories=Network;\n'
                   'StartupNotify=true\nStartupWMClass=' + icon_name + '\n')
print('Menü kısayolu:', desktop)
print('PNG simgesi:', icons / f'{icon_name}.png')
print('ICO kaynağı:', assets / 'adaletvpn.ico')
