import base64
import re
from urllib.parse import urlsplit, unquote

from adaletvpn.errors import CodedError


ALLOWED_VPN_HOSTS = frozenset({"vpn.uyap.gov.tr", "vpn1.uyap.gov.tr", "vpn2.uyap.gov.tr"})
ALLOWED_VPN_AUTHORITIES = frozenset(
    authority for host in ALLOWED_VPN_HOSTS for authority in (host, host + ":443"))


def allowed_auth_endpoint(hostname, port):
    return hostname in {host.encode("ascii") for host in ALLOWED_VPN_HOSTS} and port == 443


def validate_session(value):
    try:
        if not isinstance(value, dict) or set(value) != {"cookie", "gateway", "gwcert"}:
            raise ValueError()
        for text in value.values():
            if not isinstance(text, str) or not text or len(text) > 32768:
                raise ValueError()
            if any(ord(c) < 32 or ord(c) == 127 for c in text):
                raise ValueError()
        gateway = value["gateway"]
        if len(gateway) > 4096 or "\\" in gateway or any(c.isspace() for c in gateway):
            raise ValueError()
        url = urlsplit(gateway)
        if (url.scheme != "https" or url.netloc not in ALLOWED_VPN_AUTHORITIES
                or url.username or url.password or url.port not in (None, 443)
                or url.query or url.fragment):
            raise ValueError()
        if re.search(r"%(?![0-9a-fA-F]{2})", gateway):
            raise ValueError()
        path = unquote(url.path, errors="strict")
        if "\\" in path or path.startswith("//") or any(ord(c) < 32 or ord(c) == 127 for c in path):
            raise ValueError()
        fingerprint = value["gwcert"]
        if not fingerprint.startswith("pin-sha256:"):
            raise ValueError()
        encoded = fingerprint[len("pin-sha256:"):]
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != encoded:
            raise ValueError()
        return dict(value)
    except (ValueError, TypeError, UnicodeError):
        raise CodedError("invalid_session") from None
