from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import shutil
from ctypes.util import find_library

from adaletvpn.core import Certificate, ConnectionError
from adaletvpn.pkcs11 import parse_certificates


@dataclass(frozen=True)
class VPNProfile:
    gateway: str = "https://vpn.uyap.gov.tr"
    protocol: str = "anyconnect"

    def validate(self):
        address = urlsplit(self.gateway)
        try:
            port = address.port
        except ValueError:
            raise ConnectionError("Geçersiz VPN sunucu portu.") from None
        if (address.scheme != "https" or address.hostname != "vpn.uyap.gov.tr"
                or port not in (None, 443) or address.username or address.password
                or address.query or address.fragment or address.path not in ("", "/")
                or self.protocol != "anyconnect"):
            raise ConnectionError("Yalnızca doğrulanmış UYAP HTTPS profili kullanılabilir.")


def validate_certificate(item: Certificate):
    parsed = parse_certificates(item.identifier)
    if len(parsed) != 1 or parsed[0].identifier != item.identifier:
        raise ConnectionError("Tek bir sertifika kimliği gerekli.")
    attributes = dict(part.split("=", 1) for part in item.identifier[7:].split(";"))
    if not attributes.get("serial") or not attributes.get("token"):
        raise ConnectionError("Sertifika cihaz kimliği yeterince belirgin değil.")
    problem = item.connection_problem()
    if problem:
        raise ConnectionError(problem)


def local_checks() -> tuple[tuple[str, bool], ...]:
    from adaletvpn.tunnel_service import isolated_service_installed

    return (
        ("OpenConnect dosyası", shutil.which("openconnect") is not None),
        ("p11tool dosyası", shutil.which("p11tool") is not None),
        ("NetworkManager OpenConnect hizmeti", any(Path(path).is_file() for path in (
            "/usr/libexec/nm-openconnect-service",
            "/usr/lib/NetworkManager/nm-openconnect-service"))),
        ("AdaletVPN tünel hizmeti ve dosya izinleri", isolated_service_installed()),
        ("OpenConnect kütüphanesi", find_library("openconnect") is not None),
        ("GnuTLS kütüphanesi", find_library("gnutls") is not None),
        ("Fontconfig kütüphanesi", find_library("fontconfig") is not None),
        ("İsteğe bağlı şifre hatırlama için libsecret", find_library("secret-1") is not None),
    )
