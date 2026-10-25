import argparse

from adaletvpn import __version__


def main():
    parser = argparse.ArgumentParser(description="AdaletVPN masaüstü arayüzü")
    parser.add_argument("--version", action="version", version=f"AdaletVPN {__version__}")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--demo", action="store_true", help="Örnek sertifikalarla deneme arayüzünü aç")
    modes.add_argument("--certificates", action="store_true", help="Gerçek sertifikaları salt okunur modda listele")
    modes.add_argument("--check", action="store_true", help="Bağlantı başlatmadan kurulu dosyaları kontrol et")
    modes.add_argument("--vpn", action="store_true", help="Gerçek VPN bağlantı arayüzünü aç")
    modes.add_argument("--isolated-vpn", action="store_true", help="Uygulamaya özel tünel hizmetiyle gerçek deneme")
    parser.add_argument("--module", help="PKCS#11 sürücüsünün tam yolu")
    args = parser.parse_args()
    client = None
    if args.vpn or args.isolated_vpn:
        from adaletvpn.networkmanager import LIVE_ENABLED, NetworkManagerVPN
        if not LIVE_ENABLED:
            parser.error("Gerçek VPN denemesi henüz etkinleştirilmedi. --check veya --certificates kullanın.")
        from adaletvpn.tunnel_service import ISOLATED_SERVICE
        client = NetworkManagerVPN(service=ISOLATED_SERVICE)
    if args.check:
        from adaletvpn.preflight import local_checks
        from adaletvpn.networkmanager import LIVE_ENABLED
        for label, available in local_checks():
            print(f"{'Mevcut' if available else 'Eksik'}: {label}")
        print("Gerçek deneme etkin; bağlantı kullanıcı tarafından başlatılır." if LIVE_ENABLED else "Gerçek bağlantı kapalı.")
        print("Dosyaların mevcut olması bağlantı hazırlığının tamamlandığını göstermez.")
        return 0
    provider = None
    if args.certificates or args.vpn or args.isolated_vpn:
        from adaletvpn.pkcs11 import PKCS11Certificates
        try:
            provider = PKCS11Certificates(args.module)
        except ValueError as error:
            parser.error(str(error))
    elif args.module:
        parser.error("--module yalnızca --certificates ile kullanılabilir.")
    try:
        from adaletvpn.ui import run
    except (ImportError, ValueError):
        parser.exit(1, "Arayüz için GTK 4 ve Python gi desteği gerekli. Hiçbir paket kurulmadı.\n")
    return run(provider, client)


if __name__ == "__main__":
    raise SystemExit(main())
