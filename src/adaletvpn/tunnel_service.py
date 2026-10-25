from pathlib import Path
from configparser import ConfigParser, Error

LEGACY_SERVICE = 'org.freedesktop.NetworkManager.openconnect'
ISOLATED_SERVICE = LEGACY_SERVICE + '.adaletvpn'
OWNED_SERVICES = frozenset({LEGACY_SERVICE, ISOLATED_SERVICE})


def isolated_service_installed():
    registration = Path('/usr/lib/NetworkManager/VPN/nm-adaletvpn-service.name')
    try:
        config = ConfigParser(interpolation=None)
        config.read_string(registration.read_text())
        program = config['VPN Connection']['program']
        if program not in ('/usr/local/libexec/adaletvpn/nm-adaletvpn-service',
                           '/usr/lib/adaletvpn/nm-adaletvpn-service'):
            return False
        root = Path(program).parent
        paths = (Path(program), root / 'tunnel-pkcs11.so', registration)
        for path in paths:
            if not path.is_file() or path.is_symlink():
                return False
            for item in (path, *path.parents):
                st = item.stat()
                if st.st_uid != 0 or st.st_mode & 0o022:
                    return False
        return True
    except (OSError, Error, KeyError):
        return False
