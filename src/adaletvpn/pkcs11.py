import asyncio
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

from adaletvpn.core import Certificate, ConnectionError
from adaletvpn.errors import worker_environment


def read_details(item: Certificate, der: bytes) -> Certificate:
    from cryptography import x509

    cert = x509.load_der_x509_certificate(der)
    def extension(kind):
        try:
            return cert.extensions.get_extension_for_class(kind).value
        except x509.ExtensionNotFound:
            return None
    usage = extension(x509.KeyUsage)
    extended = extension(x509.ExtendedKeyUsage)
    constraints = extension(x509.BasicConstraints)
    return replace(item, subject=cert.subject.rfc4514_string(),
                   issuer=cert.issuer.rfc4514_string(),
                   not_before=cert.not_valid_before_utc,
                   not_after=cert.not_valid_after_utc,
                   digital_signature=usage.digital_signature if usage else None,
                   client_auth=(x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH in extended or
                                x509.ObjectIdentifier("2.5.29.37.0") in extended) if extended else None,
                   is_ca=constraints.ca if constraints else None)


def parse_certificates(output: str) -> tuple[Certificate, ...]:
    result = []
    seen = set()
    for line in output.splitlines():
        uri = line.strip()
        if not uri:
            continue
        if not uri.startswith("pkcs11:") or "?" in uri:
            raise ConnectionError("Beklenmeyen sertifika listesi biçimi.")
        fields = {}
        for part in uri[len("pkcs11:"):].split(";"):
            key, separator, value = part.partition("=")
            if not separator or key in fields or key.lower().startswith("pin-"):
                raise ConnectionError("Geçersiz sertifika tanımlayıcısı.")
            fields[key] = unquote(value)
        if fields.get("type") != "cert" or not fields.get("id"):
            raise ConnectionError("Sertifika kimliği okunamadı.")
        if uri in seen:

            continue
        seen.add(uri)
        result.append(Certificate(
            identifier=uri,
            device=fields.get("token") or "E-imza cihazı",
            subject="Sahip bilgisi henüz okunmadı",
            issuer="Düzenleyen kurum henüz okunmadı",
            label=fields.get("object") or "Etiketsiz sertifika",
        ))
    return tuple(result)


class PKCS11Certificates:
    def __init__(self, module: str | None = None, timeout: float = 15):
        if module is None:
            module = next((str(p) for p in (Path("/usr/lib/libakisp11.so"),
                Path("/usr/lib/x86_64-linux-gnu/libakisp11.so"),
                Path("/usr/lib/aarch64-linux-gnu/libakisp11.so")) if p.is_file()), None)
        path = Path(module) if module is not None else None
        if path is not None and (not path.is_absolute() or not path.is_file()):
            raise ValueError("PKCS#11 sürücüsü için mevcut bir dosyanın tam yolunu belirtin.")
        if timeout <= 0:
            raise ValueError("Zaman aşımı sıfırdan büyük olmalı.")
        self.module = str(path.resolve()) if path else None
        self.timeout = timeout

    async def list_certificates(self):
        output = await self._run("--list-all-certs", "--only-urls", "pkcs11:")
        items = parse_certificates(output.decode("utf-8", errors="strict"))
        results = []
        for item in items:
            try:

                der = await self._run("--export", "--outder", item.identifier)
                results.append(read_details(item, der))
            except (ConnectionError, ValueError, ImportError, asyncio.TimeoutError):
                results.append(replace(item, details_error="Sertifika ayrıntıları okunamadı."))
        return tuple(results)

    async def _run(self, *arguments):

        env = worker_environment()
        env["LC_ALL"] = "C"
        try:
            process = await asyncio.create_subprocess_exec(
                "p11tool", *([f"--provider={self.module}"] if self.module else []), "--batch",
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError:
            raise ConnectionError("p11tool başlatılamadı. Kurulu araçları kontrol edin.") from None
        try:
            output, _ = await asyncio.wait_for(process.communicate(), self.timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.communicate()
            raise
        if process.returncode:
            raise ConnectionError(
                "Sertifikalar okunamadı. Cihazı ve sürücüyü kontrol edin. "
                "PIN gerektiren sertifikalar bu modda okunmaz."
            )
        return output


class DisabledVPN:
    async def authenticate(self, certificate, credentials):
        raise ConnectionError("Sertifika okuma modunda VPN bağlantısı kapalı.")

    async def connect(self):
        raise ConnectionError("Sertifika okuma modunda VPN bağlantısı kapalı.")

    async def disconnect(self):
        pass
