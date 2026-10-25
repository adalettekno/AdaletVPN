import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def project_version():
    import re
    text = (ROOT / 'src/adaletvpn/__init__.py').read_text()
    match = re.search(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', text, re.M)
    if match is None:
        raise ValueError('Uygulama sürümü bulunamadı.')
    return match.group(1)


def build(version=None):
    import re
    version = project_version() if version is None else version
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Sürüm numarası 1.0.0 biçiminde olmalıdır.')
    arch = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    output = ROOT / 'dist' / arch
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'AdaletVPN.deb'
    with tempfile.TemporaryDirectory(prefix='adaletvpn-deb-') as folder:
        stage = Path(folder)
        app = stage / 'usr/lib/adaletvpn'
        app.mkdir(parents=True)
        shutil.copytree(ROOT / 'src/adaletvpn', app / 'src/adaletvpn',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ('fonts', 'app-icons'):
            shutil.copytree(ROOT / 'data' / name, app / 'data' / name)
        service = (ROOT / 'packaging/nm-adaletvpn-service').read_text().replace(
            '/usr/local/libexec/adaletvpn', '/usr/lib/adaletvpn')
        (app / 'nm-adaletvpn-service').write_text(service)
        (app / 'nm-adaletvpn-service').chmod(0o755)
        subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-Wl,-z,relro,-z,now', '-o', str(app / 'tunnel-pkcs11.so'),
                        str(ROOT / 'native/tunnel_pkcs11.c'), '-ldl'], check=True)
        files = {
            'usr/bin/adaletvpn': '#!/usr/bin/python3 -I\nimport os\nimport sys\nos.environ["PYTHONPATH"] = "/usr/lib/adaletvpn/src"\nos.environ["PYTHONDONTWRITEBYTECODE"] = "1"\nsys.dont_write_bytecode = True\nsys.path.insert(0, "/usr/lib/adaletvpn/src")\nfrom adaletvpn.__main__ import main\nif len(sys.argv) == 1:\n    sys.argv.append("--vpn")\nraise SystemExit(main())\n',
            'usr/lib/NetworkManager/VPN/nm-adaletvpn-service.name':
                (ROOT / 'packaging/nm-adaletvpn-service.name').read_text().replace(
                    '/usr/local/libexec/adaletvpn', '/usr/lib/adaletvpn'),
            'usr/share/applications/com.adalettekno.AdaletVPN.desktop':
                '[Desktop Entry]\nType=Application\nName=AdaletVPN\nComment=AdaletVPN, Linux üzerinde e-imza ile UYAP VPN bağlantısı kurmanızı sağlar.\nExec=adaletvpn\nIcon=com.adalettekno.AdaletVPN\nTerminal=false\nCategories=Network;\nStartupNotify=true\nStartupWMClass=com.adalettekno.AdaletVPN\n',
        }
        for name, content in files.items():
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        (stage / 'usr/bin/adaletvpn').chmod(0o755)
        icon = stage / 'usr/share/icons/hicolor/512x512/apps/com.adalettekno.AdaletVPN.png'
        icon.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'data/app-icons/hicolor/512x512/apps/com.adalettekno.AdaletVPN.png', icon)
        metadata = stage / 'usr/share/metainfo/com.adalettekno.AdaletVPN.metainfo.xml'
        metadata.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'packaging/com.adalettekno.AdaletVPN.metainfo.xml', metadata)
        control = stage / 'DEBIAN'
        control.mkdir()
        size = sum(p.stat().st_size for p in (stage / 'usr').rglob('*') if p.is_file())
        (control / 'control').write_text(
            f'Package: adaletvpn\nVersion: {version}\nArchitecture: {arch}\n'
            'Maintainer: Adalet Tekno\nHomepage: https://www.adalettekno.com/\nSection: net\nPriority: optional\n'
            f'Installed-Size: {(size + 1023) // 1024}\n'
            'Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, python3-pil, '
            'gir1.2-gtk-4.0, python3-cryptography (>= 42), libfontconfig1, '
            'network-manager, network-manager-openconnect, openconnect, libopenconnect5, '
            'gnutls-bin, libgnutls30t64 | libgnutls30, libc6 (>= 2.34), libsecret-1-0\n'
            'Recommends: gnome-keyring | kwallet6 | kwalletmanager, pcscd, libccid\n'
            'Description: Linux üzerinde e-imza ile UYAP VPN bağlantısı\n'
            ' AdaletVPN, Linux üzerinde e-imza ile UYAP VPN bağlantısı kurmanızı sağlar.\n')
        checksums = []
        for path in sorted((stage / 'usr').rglob('*')):
            if path.is_file():
                checksums.append(hashlib.md5(path.read_bytes()).hexdigest() + '  ' +
                                 str(path.relative_to(stage)))
        (control / 'md5sums').write_text('\n'.join(checksums) + '\n')
        for path in stage.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)
            elif not path.stat().st_mode & 0o111:
                path.chmod(0o644)
        subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage), str(target)],
                       check=True, env={**os.environ, 'SOURCE_DATE_EPOCH': '1792108800'})
    print(target)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default=None)
    build(parser.parse_args().version)
