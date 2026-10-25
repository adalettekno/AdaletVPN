# Kurulum

`AdaletVPN.deb` dosyasını yazılım kurucusuyla açın. Kurucu açmazsa, dosyanın bulunduğu dizinde:

```sh
sudo apt install ./AdaletVPN.deb
```

Kurulum bitince AdaletVPN uygulama menüsünde görünür. Masaüstüne kısayol konmaz.

E-imza çubuğunun sürücüsü bu pakette yok. AKİS kuruluysa uygulama sırayla şu dosyalara bakar:

- `/usr/lib/libakisp11.so`
- `/usr/lib/x86_64-linux-gnu/libakisp11.so`
- `/usr/lib/aarch64-linux-gnu/libakisp11.so`

Sürücü başka bir yerdeyse:

```sh
adaletvpn --vpn --module /tam/yol/surucu.so
```

Paket Debian ve Ubuntu tabanlı sistemler içindir. amd64 paketi 64 bit Intel ve AMD içindir. ARM makinede o paketi kurmayın. Kayıtlı şifre için açık bir Secret Service kasası gerekir; GNOME Anahtarlık ve KWallet bunu karşılar.

# Kaldırma

Önce bağlantıyı kesin ve uygulamayı kapatın.

```sh
sudo apt remove adaletvpn
```

Bu, kasadaki sicil, parola ve PIN kayıtlarını silmez. Onları paket dururken uygulamadaki «Kayıtlı bilgileri sil» ile kaldırın.

# Paket derlemek

`cc` ve `dpkg-deb` kurulu olsun. Depo kökünde:

```sh
python3 tools/build_deb.py
```

Paket `dist/amd64/AdaletVPN.deb` olarak yazılır. Mimari farklıysa klasörün adı değişir. Derleme komutu paketi kurmaz.
