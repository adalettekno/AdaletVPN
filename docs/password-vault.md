# Şifre kasası

Yeni kullanıcı için iki seçenek de kapalıdır:

- **Sicil ve bilgisayar şifresini hatırla**
- **E-imza PIN’ini de hatırla**

PIN seçeneği ayrıca açılmadıkça PIN kaydedilmez ve kasadan doldurulmaz. Önceki
sürümdeki tek hatırlama tercihi, yeni PIN seçeneğine otomatik onay sayılmaz.
Eski kayıtta PIN varsa, o kart yüklenirken PIN çıkarılarak kayıt güncellenir.
PIN seçeneği kapatıldığında seçili kartın kayıtlı PIN'i kaldırılır; diğer kartlar
seçildiklerinde aynı kontrol uygulanır. Kasa erişilemiyorsa silindiği söylenmez.

Yeni bilgiler başarılı bağlantıdan sonra kasaya yazılır. Kayıt seçili PKCS#11
sertifika kimliğinin SHA-256 özetiyle eşleştirilir. Üç alan kasanın gizli
JSON içeriğindedir; PIN hatırlama kapalıysa PIN alanı boştur. Arama alanları ve
etiketler PIN, parola veya sicil içermez. Otomatik bağlantı veya PIN tekrarı yoktur.

## Bağlantı ve hata davranışı

Kasa işlemi bağlantı görevinden ayrıdır. Beklerken alanlar kullanılabilir. Kullanıcı elle
bilgilerini yazıp Bağlan'a bastığında bekleyen kasa işi iptal edilir. Geç gelen
sonuç, değiştirilmiş alanları veya başka karta ait alanları doldurmaz.

Kayıtlı şifre değiştiğinde bağlantı kapalıyken ilgili alana yeni şifre yazılır.
İlgili hatırlama seçeneği açık bırakılırsa başarılı bağlantıdan sonra kasa kaydı
güncellenir. Önce tüm kayıtları silmek gerekmez.

Kasa yardımcısının üst bekleme süresi 30 saniyedir. İptal/zaman aşımında kasa
sunucusunda yazma veya silme tamamlanmış olabilir; başarı varsayılmaz. Kullanıcı
silme işlemini tekrar isteyebilir. Pencere kapanırken kasa görevi de beklenip
sonlandırılır. Bağlantı sonrası kasa hatası VPN'i kesmez.

Ağ, sunucu, tünel veya yetkilendirme sorunlarında kayıt korunur. Yalnızca açık
kimlik bilgisi reddinde işlem yapılır:

- PIN reddi ve hatalı PIN sonrası kalan hak bildiriminde kayıtlı PIN kaldırılır;
  sicil ve bilgisayar şifresi korunur. PIN hatırlama kapatılır.
- Kurum kullanıcı adı/parolası reddedildiğinde o kartın kaydı kaldırılır ve
  hatırlama kapatılır.
- PIN gönderilmeden verilen düşük hak uyarıları yanlış PIN kabul edilmez.

“Kayıtlı”, “Kasa kilitli”, “Kasa kullanılamıyor” ve zaman aşımı durumları ayrı
mesajlarla gösterilir. Kilitli kayıtlar kendiliğinden açtırılmaz; kullanıcı
masaüstünün kasa uygulamasından açabilir veya elle girişle devam edebilir.
Kaydetme ve silmede kasa sağlayıcısı ayrıca izin isteyebilir.

## Silme ve Linux gereksinimleri

“Kaydedilen bilgileri sil”, bu kullanıcı kasasındaki AdaletVPN kayıtlarını tüm
kartlar için kaldırır. Kilitli kayıtlar da aramaya dahil edilir. İşlem sonunda
kayıt kalmadığı kontrol edilir; başarısız silme gizlenmez. Başka uygulamaların
kayıtları hedeflenmez. Hatırlama seçeneğini kapatmak tek başına tüm kayıtları
silmez; bunun için ayrı silme düğmesi vardır.

Uygulama libsecret ve freedesktop Secret Service kullanır. Uyumlu kasa hizmeti
ve `libsecret-1.so.0` gerekir. Her Linux kurulumunda hazır olacakları garanti
edilemez. Düz metin alternatif kayıt yoktur; kasanın disk şifrelemesi ve kilit
politikası sağlayıcısına aittir.

`$XDG_CONFIG_HOME/adaletvpn/preferences.json` (yoksa `~/.config/adaletvpn`)
yalnızca `remember` ve `remember_pin` açık/kapalı tercihlerini içerir; 0600
izinle atomik yazılır. Sırlar yardımcı sürece özel borularla gider; komut
satırı, ortam değişkeni ve günlük kullanılmaz. Yardımcının core dump'ı kapalıdır.
Python belleğinin fiziksel olarak sıfırlandığı iddia edilmez; geçici referanslar
bırakılır. Demo ve sertifika okuma modları kasaya bağlanmaz.

Kullanıcının isteğiyle önceden hazırlanmış terminal betiğindeki sabit bilgiler
ve ZIP yedekleri bu özellikten ayrıdır; kendiliğinden taşınmaz veya silinmez.

## Doğrulama

161 otomatik test geçti. PIN için ayrı izin, eski tercihlerin aktarımı, ağ
hatasında kayıt koruma, kesin retlerin ayrılması, geciken kayıt işleminin
bağlantıyı bekletmemesi, değişen kart/elle yazılan alanların korunması ve
iptalde yardımcı sürecin kapanması sınandı. GTK örnek penceresinde iki seçeneğin
varsayılan kapalı durumu, ayrı PIN izni, doldurma ve silme kontrol edildi.
Gerçek kasada yalnızca örnek verilerle PIN kaldırıldı, diğer alanların korunduğu
ve test kaydının tamamen silindiği doğrulandı. Gerçek PIN/VPN kullanılmadı.

Kaynaklar:
- https://specifications.freedesktop.org/secret-service/latest/
- https://gnome.pages.gitlab.gnome.org/libsecret/
