#!/usr/bin/python3 -I

import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path('/usr/local/libexec/adaletvpn')
TARGETS = {
    'nm-adaletvpn-service': (ROOT / 'nm-adaletvpn-service', 0o755),
    'tunnel-pkcs11.so': (ROOT / 'tunnel-pkcs11.so', 0o644),
    'nm-adaletvpn-service.name':
        (Path('/usr/lib/NetworkManager/VPN/nm-adaletvpn-service.name'), 0o644),
}


def safe_directory(path):
    for item in (path, *path.parents):
        st = item.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise RuntimeError('Güvensiz kurulum dizini: ' + str(item))


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Kurulum yönetici yetkisi gerektirir.')
    if len(sys.argv) != 3 or sys.argv[1] not in ('--install', '--remove'):
        raise RuntimeError('Kullanım: --install|--remove DERLEME_DİZİNİ')
    source = Path(sys.argv[2])
    manifest = json.loads((source / 'manifest.json').read_text())
    if set(manifest) != set(TARGETS):
        raise RuntimeError('Kurulum listesi uyuşmuyor.')
    contents = {name: (source / name).read_bytes() for name in TARGETS}
    for name, data in contents.items():
        if hashlib.sha256(data).hexdigest() != manifest[name]:
            raise RuntimeError('Derleme özeti uyuşmuyor: ' + name)
    if sys.argv[1] == '--remove':

        for name, (path, _) in TARGETS.items():
            safe_directory(path.parent)
            if path.is_symlink() or path.read_bytes() != contents[name]:
                raise RuntimeError('Kurulu dosya değişmiş; kaldırılmadı: ' + name)
        for path, _ in reversed(list(TARGETS.values())):
            path.unlink()
        ROOT.rmdir()
        print('AdaletVPN hizmet dosyaları kaldırıldı.')
        return
    created_dirs, created_files = [], []
    try:
        for parent in (ROOT.parent, ROOT):
            if not parent.exists():
                safe_directory(parent.parent)
                parent.mkdir(mode=0o755)
                created_dirs.append(parent)
            safe_directory(parent)
        for path, _ in TARGETS.values():
            safe_directory(path.parent)
            if path.exists() or path.is_symlink():
                raise RuntimeError('Mevcut dosyanın üzerine yazılmadı: ' + str(path))
        for name, (path, mode) in TARGETS.items():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            created_files.append(path)
            with os.fdopen(fd, 'wb') as f:
                f.write(contents[name]); f.flush()
                os.fchmod(f.fileno(), mode); os.fsync(f.fileno())
        print('AdaletVPN hizmeti kuruldu. AKİS ayarı değiştirilmedi.')
    except BaseException:
        for path in reversed(created_files):
            path.unlink()
        for path in reversed(created_dirs):
            path.rmdir()
        raise


if __name__ == '__main__':
    main()
