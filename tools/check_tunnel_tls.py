import ctypes as C
import json
from pathlib import Path
import resource
import signal


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.alarm(15)
    config = Path("/usr/share/p11-kit/modules/akis.module")
    original = config.read_bytes()
    tls = C.CDLL("libgnutls.so.30")
    init = tls.gnutls_pkcs11_init
    init.argtypes, init.restype = [C.c_uint, C.c_char_p], C.c_int

    if init(0, None) != 0:
        raise RuntimeError("Elle başlatma başarısız")
    oc = C.CDLL("libopenconnect.so.5")
    oc.openconnect_init_ssl.argtypes = []
    oc.openconnect_init_ssl.restype = C.c_int
    result = oc.openconnect_init_ssl()
    credentials = C.c_void_p()
    allocate = tls.gnutls_certificate_allocate_credentials
    allocate.argtypes, allocate.restype = [C.POINTER(C.c_void_p)], C.c_int
    trust = tls.gnutls_certificate_set_x509_system_trust
    trust.argtypes, trust.restype = [C.c_void_p], C.c_int
    free = tls.gnutls_certificate_free_credentials
    free.argtypes, free.restype = [C.c_void_p], None
    if allocate(C.byref(credentials)) != 0:
        raise RuntimeError("TLS güven deposu hazırlanamadı")
    try:
        ca_count = trust(credentials)
        akis_loaded = "libakisp11" in Path("/proc/self/maps").read_text().lower()
        unchanged = config.read_bytes() == original
        print(json.dumps({"ssl_init_result": result, "system_ca_count": ca_count,
                          "akis_loaded": akis_loaded, "system_config_unchanged": unchanged}))
        return 0 if result == 0 and ca_count > 0 and not akis_loaded and unchanged else 1
    finally:
        free(credentials)


if __name__ == "__main__":
    raise SystemExit(main())
