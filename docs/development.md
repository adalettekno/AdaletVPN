# Geliştirme ve doğrulama

Python 3.10+ ve GTK 4 gerekir. Üretim bağımlılıkları `tools/build_deb.py` içinde tanımlıdır. CI, Ubuntu 24.04 sistem Python/GTK paketlerini kullanır; gerçek VPN, e-imza veya parola kasasına erişmez.

Sistem GI paketlerini gören bir geliştirme ortamı:

```sh
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements-dev.txt 'cryptography>=42'
.venv/bin/python tools/check_quality.py
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python tools/check_ui.py
```

Son komut masaüstünde örnek verili bir pencere açar. Başsız CI ortamında `dbus-run-session -- xvfb-run -a` ile çalıştırılır. `check_ui.py` geçici tercih dizini ve sahte kart/VPN/kasa kullanır; durum satırını, doğrudan kayıt silme düğmesini, klavyeyi, bağlantı/iptal yaşam döngüsünü, temaları, yazı ölçeklerini ve bağlıyken kapanışı kontrol eder. Yazı ölçeği kontrolleri bütün eski alanların taşmasız olduğunu garanti etmez. Birim testleri Debian paketini de derler, ancak kurmaz.

Ruff kuralları `pyproject.toml` içindedir. Biçim kontrolü yeni ayrılmış bileşenlere uygulanır; liste `tools/check_quality.py` içindedir. Tepsiye küçültme yalnızca kullanıcı isteğiyle yapılır; uçuş animasyonu yoktur.

# Sorumluluklar

- `core.py`: bağlantı durumları, sertifika kontrol kodları, temizlik ve bağlantı izleme sonuçları.
- `connection_view.py`: GTK'dan bağımsız, salt okunur düğme/durum kararları. Kimlik bilgisi içermez.
- `vault_coordinator.py`: kasa görevleri, iptal sırası, geç gelen yanıt koruması ve hatırlama tercihleri. İşlem türü açık `VaultOperation` değeridir.
- `async_bridge.py`: GTK tarafından tetiklenen, genel asyncio API'lerini kullanan sınırlı olay döngüsü adımları.
- `certificate_details.py`: sertifika ayrıntılarının sunumu.
- `ui.py` / `layout.py`: kullanıcı olayları, pencere yaşam döngüsü ve yerleşim. Standart küçültme ile tepsiye küçültme ayrı kullanıcı eylemleridir.

# Tanılama

Beklenmeyen arayüz, görev ve olay döngüsü hataları Python `adaletvpn` günlükleyicisine yazılır. Yalnızca tanımlı işlem aşaması, sabit hata kodu ve kaynak dosyası/satır konumları kaydedilir. İstisna mesajı, kaynak satırı, yerel değişkenler, sicil, PIN, parola ve oturum çerezi kaydedilmez. Destek için terminalden başlatılan uygulamanın standart hata çıktısı kullanılabilir. Günlükler gerçek VPN doğrulamasının yerine geçmez.
