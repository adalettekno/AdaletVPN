import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, Sequence


class State(Enum):
    DISCONNECTED = "Bağlı değil"
    SCANNING = "Sertifikalar taranıyor"
    AUTHENTICATING = "Kimlik doğrulanıyor"
    CONNECTING = "Bağlanıyor"
    CONNECTED = "Bağlı"
    UNKNOWN = "Doğrulanamıyor"
    DISCONNECTING = "Bağlantı kesiliyor"
    ERROR = "Hata"


@dataclass(frozen=True)
class Certificate:

    identifier: str
    device: str
    subject: str
    issuer: str
    label: str = ""
    not_before: datetime | None = None
    not_after: datetime | None = None
    details_error: str = ""
    digital_signature: bool | None = None
    client_auth: bool | None = None
    is_ca: bool | None = None

    def connection_problem(self) -> str | None:
        if self.details_error or self.not_before is None or self.not_after is None:
            return "Sertifika ayrıntıları doğrulanamadı."
        if self.validity() != "Geçerlilik tarihleri içinde":
            return "Sertifika geçerlilik tarihleri dışında."
        if self.is_ca:
            return "Kök veya ara kurum sertifikasıyla bağlantı kurulamaz."
        if self.digital_signature is False:
            return "Sertifika imzalama kullanımına izin vermiyor."
        if self.client_auth is False:
            return "Sertifika istemci kimlik doğrulamasına izin vermiyor."
        return None

    def validity(self, now: datetime | None = None) -> str:
        if self.not_before is None or self.not_after is None:
            return "Tarih bilgisi okunamadı"
        now = now or datetime.now(timezone.utc)
        if now < self.not_before:
            return "Henüz geçerli değil"
        if now > self.not_after:
            return "Süresi dolmuş"
        return "Geçerlilik tarihleri içinde"


@dataclass
class Credentials:
    username: str = field(repr=False)
    pin: str = field(repr=False)
    password: str = field(default="", repr=False)

    def clear(self) -> None:

        self.username = ""
        self.pin = ""
        self.password = ""


class CertificateProvider(Protocol):
    async def list_certificates(self) -> Sequence[Certificate]:

        ...


class VPNClient(Protocol):
    async def authenticate(self, certificate: Certificate, credentials: Credentials) -> None:

        ...

    async def connect(self) -> None:

        ...

    async def disconnect(self) -> None:

        ...


class ConnectionError(Exception):
    pass


class Controller:
    def __init__(self, provider: CertificateProvider, client: VPNClient, timeout: float = 30):
        if timeout <= 0:
            raise ValueError("Zaman aşımı sıfırdan büyük olmalı.")
        self.provider = provider
        self.client = client
        self.timeout = timeout
        self.state = State.DISCONNECTED
        self.error: str | None = None
        self.error_code = None
        self.scan_detail = ""
        self.certificates: tuple[Certificate, ...] = ()
        self.selected_id: str | None = None
        self._busy = False
        self._needs_cleanup = False

    @property
    def can_start(self) -> bool:

        return (not self._busy and not self._needs_cleanup
                and self.state not in (State.CONNECTED, State.UNKNOWN, State.DISCONNECTING))

    def _require_idle(self) -> None:
        if not self.can_start:
            raise ConnectionError("Önce devam eden bağlantı işlemini sonlandırın.")

    async def scan(self) -> tuple[Certificate, ...]:
        self._require_idle()
        previous_certificates = self.certificates
        previous_selected = self.selected_id
        previous_state = self.state
        previous_error = self.error
        previous_detail = self.scan_detail
        self._busy = True
        self.state = State.SCANNING
        self.scan_detail = ""
        self.error = None
        self.certificates = ()
        self.selected_id = None
        try:
            self.certificates = tuple(await asyncio.wait_for(
                self.provider.list_certificates(), self.timeout
            ))
            identifiers = [item.identifier for item in self.certificates]
            if any(not item for item in identifiers) or len(set(identifiers)) != len(identifiers):
                raise ValueError("Geçersiz sertifika kimliği")
            if len(self.certificates) == 1:
                self.selected_id = self.certificates[0].identifier
            if not self.certificates:
                self.scan_detail = "Takılı cihazda okunabilir sertifika bulunamadı. Kartı ve okuyucuyu kontrol edin."
            self.state = State.DISCONNECTED
            return self.certificates
        except asyncio.CancelledError:
            self.certificates = previous_certificates
            self.selected_id = previous_selected
            self.state = previous_state
            self.error = previous_error
            self.scan_detail = previous_detail
            raise
        except Exception as error:
            self.scan_detail = (str(error) if isinstance(error, ConnectionError) else
                                "Sertifika taraması zaman aşımına uğradı. Cihazı yeniden takıp deneyin."
                                if isinstance(error, asyncio.TimeoutError) else
                                "Sertifika listesi okunamadı. Kart sürücüsünü ve cihazı kontrol edin.")
            self.certificates = ()
            self.state = State.ERROR
            self.error = "Sertifika bulunamadı!"
            raise ConnectionError(self.error) from None
        finally:
            self._busy = False

    def select(self, identifier: str) -> None:
        self._require_idle()
        if not any(item.identifier == identifier for item in self.certificates):
            raise ConnectionError("Seçilen sertifika listede bulunamadı.")
        self.selected_id = identifier

    async def connect(self, credentials: Credentials) -> None:

        acquired = False
        try:
            self._require_idle()
            if not self.selected_id:
                raise ConnectionError("Bağlanmak için bir sertifika seçin.")
            if getattr(self.client, "requires_full_credentials", False):
                from adaletvpn.auth import credential_problem
                problem = credential_problem(credentials.username, credentials.pin, credentials.password)
                if problem:
                    raise ConnectionError(problem)
            if not credentials.username.strip() or not credentials.pin:
                raise ConnectionError("Kullanıcı adı ve e-imza PIN'i gerekli.")
            self._busy = acquired = True
            self.error = None
            self.error_code = None
            self.state = State.AUTHENTICATING
            try:
                await asyncio.wait_for(self._establish(credentials), self.timeout)
                self.state = State.CONNECTED
            except asyncio.CancelledError:
                await self._cleanup()
                raise
            except Exception as exc:
                from adaletvpn.errors import CodedError
                self.error_code = exc.code if isinstance(exc, CodedError) else None
                message = (str(exc) if isinstance(exc, CodedError) else
                           "Bağlantı zaman aşımına uğradı." if isinstance(exc, asyncio.TimeoutError)
                           else "Bağlantı kurulamadı. Cihazı ve kimlik bilgilerini kontrol edin.")
                cleaned = await self._cleanup()
                self.state = State.ERROR
                if cleaned:
                    self.error = message
                raise ConnectionError(self.error) from None
        finally:
            credentials.clear()
            if acquired:
                self._busy = False

    async def _establish(self, credentials: Credentials) -> None:
        available = await self.provider.list_certificates()
        matches = [item for item in available if item.identifier == self.selected_id]
        if len(matches) != 1:
            self.selected_id = None
            raise ConnectionError("Seçilen sertifika artık kullanılamıyor.")
        self._needs_cleanup = True
        await self.client.authenticate(matches[0], credentials)
        credentials.clear()
        self.state = State.CONNECTING
        await self.client.connect()

    async def _cleanup(self) -> bool:
        self.state = State.DISCONNECTING
        if self._needs_cleanup:
            try:
                await asyncio.wait_for(self.client.disconnect(), self.timeout)
            except asyncio.CancelledError:
                self.state = State.ERROR
                self.error = "Bağlantıyı kesme işlemi iptal edildi. Temizlik doğrulanmadı; bağlantıyı kesmeyi tekrar deneyin."
                raise
            except Exception:
                self.state = State.ERROR
                self.error = "Bağlantı temizlenemedi. Bağlantıyı kesmeyi tekrar deneyin."
                return False
            self._needs_cleanup = False
        self.state = State.DISCONNECTED
        self.error = None
        return True

    async def disconnect(self) -> None:
        if self._busy:
            raise ConnectionError("Önce devam eden işlemi iptal edip tamamlanmasını bekleyin.")
        self._busy = True
        try:
            if not await self._cleanup():
                raise ConnectionError(self.error)
        finally:
            self._busy = False

    async def connection_lost(self) -> None:

        if self.state not in (State.CONNECTED, State.UNKNOWN):
            return
        await self.disconnect()
        self.state = State.ERROR
        self.error = "VPN bağlantısı beklenmedik şekilde kesildi."
