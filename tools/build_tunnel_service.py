import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/tunnel-build'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                    '-Wl,-z,relro,-z,now', '-o', str(OUT / 'tunnel-pkcs11.so'),
                    str(ROOT / 'native/tunnel_pkcs11.c'), '-ldl'], check=True)

    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wl,--no-as-needed',
                    '-Wl,-l:libgnutls.so.30', '-o', str(OUT / 'openconnect'),
                    str(ROOT / 'native/tls_probe.c'), '-ldl'], check=True)
    for name in ('nm-adaletvpn-service', 'nm-adaletvpn-service.name'):
        shutil.copyfile(ROOT / 'packaging' / name, OUT / name)
    manifest = {name: hashlib.sha256((OUT / name).read_bytes()).hexdigest()
                for name in ('tunnel-pkcs11.so', 'nm-adaletvpn-service',
                             'nm-adaletvpn-service.name')}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Derleme hazır:', OUT)


if __name__ == '__main__':
    main()
