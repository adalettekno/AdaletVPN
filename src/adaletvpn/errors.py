import os
import resource
from uuid import UUID

from adaletvpn.core import ConnectionError


def worker_environment():
    return {key: value for key, value in os.environ.items()
            if key not in ("GNUTLS_PIN", "GNUTLS_SO_PIN")}


def disable_core_dumps():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def seal_live_worker():
    from adaletvpn.networkmanager import LIVE_ENABLED
    if not LIVE_ENABLED:
        raise SystemExit("Gerçek bağlantı kilitli.")
    disable_core_dumps()


def is_exact_uuid(value):
    return str(UUID(value)) == value


MESSAGES = {
    "tunnel_service_missing": "AdaletVPN tünel yardımcısı kurulu değil veya dosya izinleri uygun değil. PIN gönderilmedi.",
    "auth_worker_crashed_library_load": "Kimlik doğrulama süreci kütüphaneyi yükleme aşamasında çöktü. Tekrar PIN denemeden bu mesajı paylaşın.",
    "auth_worker_crashed_ssl_init": "Kimlik doğrulama süreci kart sürücüsünü başlatma aşamasında çöktü. Tekrar PIN denemeden bu mesajı paylaşın.",
    "auth_worker_crashed_context_create": "Kimlik doğrulama süreci doğrulama oturumunu hazırlama aşamasında çöktü. Tekrar PIN denemeden bu mesajı paylaşın.",
    "auth_worker_crashed_protocol": "Kimlik doğrulama süreci protokolü hazırlama aşamasında çöktü. Tekrar PIN denemeden bu mesajı paylaşın.",
    "auth_worker_crashed_certificate": "Kimlik doğrulama süreci sertifika tanımını hazırlama aşamasında çöktü. Tekrar PIN denemeden bu mesajı paylaşın.",
    "auth_worker_crashed_url": "Kimlik doğrulama süreci sunucu adresini hazırlama aşamasında çöktü. Tekrar PIN denemeden bu mesajı paylaşın.",
    "auth_worker_crashed_exchange": "Kimlik doğrulama süreci sunucu ve kart ile kimlik doğrulama aşamasında çöktü. Tekrar PIN denemeden bu mesajı paylaşın.",
    "auth_worker_crashed_session_read": "Kimlik doğrulama süreci oturum sonucunu okuma aşamasında çöktü. Tekrar PIN denemeden bu mesajı paylaşın.",
    "auth_worker_crashed_cleanup": "Kimlik doğrulama süreci doğrulama kaynaklarını kapatma aşamasında çöktü. Tekrar PIN denemeden bu mesajı paylaşın.",

    "auth_worker_crashed": "Kimlik doğrulama yardımcı süreci beklenmedik biçimde sonlandı. Bu mesaj yanlış PIN anlamına gelmez; yeniden denemeden önce uygulama ve e-imza sürücüsü incelenmeli.",
    "auth_worker_empty": "Kimlik doğrulama yardımcı süreci sonuç bildirmeden kapandı. Yeniden PIN denemesi yapmadan uygulama kontrol edilmeli.",
    "auth_worker_protocol": "Kimlik doğrulama yardımcı sürecinin yanıtı okunamadı. Yeniden PIN denemesi yapmadan uygulama kontrol edilmeli.",
    "watchdog_unavailable": "Süreli kapatma mekanizması hazırlanamadı. VPN başlatılmadı.",
    "pin_two_left": "Hatalı pin! 2 deneme hakkınız kaldı!",
    "pin_last_left": "Hatalı pin! Son deneme hakkınız kaldı. Hatalı pin girildiği takdirde elektronik imzanız bloke olacaktır!",
    "pin_low_warning": "Kartınızda az sayıda PIN denemesi kaldı. PIN gönderilmedi. PIN'inizden eminseniz yeniden girip Bağlan'a basın.",
    "pin_final_warning": "Son PIN deneme hakkınız kaldı. Hatalı PIN girildiği takdirde elektronik imzanız bloke olacaktır! PIN gönderilmedi. PIN'inizden eminseniz yeniden girip Bağlan'a basın.",
    "pin_attempts_low": "Kart kalan PIN denemeleri hakkında uyarı verdi. Kartın kilitlenmesini önlemek için PIN gönderilmedi. Kart durumunu e-imza sağlayıcınızın aracıyla kontrol edin.",
    "pin_rejected": "E-imza PIN işlemi reddedildi. PIN'i ve kart durumunu kontrol edin; otomatik tekrar yapılmadı.",
    "login_rejected": "Kurum kullanıcı adı/parola doğrulaması reddedildi. Bilgileri ve hesabınızı kontrol edin.",
    "unsupported_form": "Sunucu desteklenmeyen bir doğrulama alanı istedi. Bilgiler gönderilmedi.",
    "untrusted_server": "VPN sunucusunun sertifikası doğrulanamadı. Bağlantı durduruldu.",
    "unexpected_server": "Kimlik doğrulama beklenmeyen bir sunucuya yönlendirildi. Bağlantı durduruldu.",
    "auth_failed": "Kimlik doğrulama tamamlanamadı. Cihazı ve sunucu erişimini kontrol edin.",
    "auth_timeout": "Kimlik doğrulama zaman aşımına uğradı.",
    "invalid_session": "Kimlik doğrulama oturum yanıtı güvenlik kontrolünden geçmedi.",
    "worker_timeout": "VPN yardımcı süreci zamanında yanıt vermedi; iletişim kapatıldı.",
    "worker_unavailable": "VPN yardımcı süreciyle iletişim kesildi. Bu iletişim kanalı tekrar kullanılmayacak.",
    "cleanup_unconfirmed": "VPN temizliğinin tamamlandığı doğrulanamadı. Yeniden bağlanmadan önce sistem bağlantı durumunu kontrol edin.",
    "network_operation": "NetworkManager işlemi tamamlanamadı. Yetkilendirmeyi ve ağ hizmetini kontrol edin.",
}


class CodedError(ConnectionError):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in MESSAGES else "auth_failed"
        super().__init__(MESSAGES[self.code])
