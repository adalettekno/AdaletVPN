import asyncio
import json
import re
import sys

from adaletvpn.core import ConnectionError
from adaletvpn.errors import CodedError, worker_environment
from adaletvpn.session import validate_session

AUTH_STAGES = {
    "library_load": "kütüphaneyi yükleme",
    "ssl_init": "kart sürücüsünü başlatma",
    "context_create": "doğrulama oturumunu hazırlama",
    "protocol": "protokolü hazırlama",
    "certificate": "sertifika tanımını hazırlama",
    "url": "sunucu adresini hazırlama",
    "exchange": "sunucu ve kart ile kimlik doğrulama",
    "session_read": "oturum sonucunu okuma",
    "cleanup": "doğrulama kaynaklarını kapatma",
}


def decode_worker_output(output, returncode):
    stage = None
    result = None
    received = False
    malformed = len(output) > 65536
    for line in output[:65536].splitlines():
        try:
            frame = json.loads(line)
        except (ValueError, UnicodeError):
            malformed = True
            continue
        if isinstance(frame, dict) and set(frame) == {"stage"}:
            value = frame["stage"]
            if received or not isinstance(value, str) or value not in AUTH_STAGES:
                malformed = True
            else:
                stage = value
        elif received:
            malformed = True
        else:
            result, received = frame, True
    if returncode < 0:
        raise CodedError("auth_worker_crashed_" + stage if stage else "auth_worker_crashed")
    if malformed:
        raise CodedError("auth_worker_protocol")
    if not received:
        raise CodedError("auth_worker_empty")
    return result


def credential_problem(username, pin, password):
    if not re.fullmatch(r"ab[0-9]+", username):
        return "Sicil numarası ab ile başlamalı ve ardından rakamlar gelmeli."
    if not pin:
        return "E-imza şifresi gerekli."
    if not password:
        return "Bilgisayar açılış şifresi gerekli."
    for value in (username, pin, password):
        if len(value.encode("utf-8")) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            return "Giriş alanlarında kontrol karakteri veya çok uzun değer kullanılamaz."
    return None


class FormAnswers:


    def __init__(self, credentials, pin_warning_ack=None):
        problem = credential_problem(credentials.username, credentials.pin, credentials.password)
        if problem:
            raise ConnectionError(problem)
        self.credentials = credentials
        self.pin_used = False
        self.login_used = False
        self.pin_warning_ack = pin_warning_ack

    def answer(self, auth_id, options, error=False, warning=False):
        if auth_id == "pkcs11_pin" and warning:
            raise CodedError("pin_attempts_low")
        if error:
            raise CodedError("pin_rejected" if auth_id == "pkcs11_pin" else "login_rejected")
        if auth_id == "pkcs11_pin":
            if self.pin_used or options != [("pkcs11_pin", 2)]:
                raise CodedError("pin_rejected")
            self.pin_used = True
            return {"pkcs11_pin": self.credentials.pin}
        if self.login_used:
            raise CodedError("login_rejected")
        if sorted(options) != [("password", 2), ("username", 1)]:
            raise CodedError("unsupported_form")
        self.login_used = True
        return {"username": self.credentials.username, "password": self.credentials.password}


class NativeAuthenticator:
    def __init__(self):
        self.pin_warnings = {}

    async def authenticate(self, certificate, credentials, profile):
        from adaletvpn.networkmanager import LIVE_ENABLED
        if not LIVE_ENABLED:
            raise ConnectionError("Gerçek bağlantı denemesi henüz etkinleştirilmedi.")
        problem = credential_problem(credentials.username, credentials.pin, credentials.password)
        if problem:
            raise ConnectionError(problem)
        env = worker_environment()
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "adaletvpn.auth_worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env)
        payload = json.dumps({"certificate": certificate.identifier, "gateway": profile.gateway,
                              "pin_warning_ack": self.pin_warnings.pop(certificate.identifier, None),
                              "username": credentials.username, "pin": credentials.pin,
                              "password": credentials.password}).encode()
        try:
            output, _ = await asyncio.wait_for(process.communicate(payload), 90)
            result = decode_worker_output(output, process.returncode)
            if process.returncode != 0:
                if isinstance(result, dict) and set(result) == {"error"}:
                    raise CodedError(result["error"])
                raise CodedError("auth_failed")
            return validate_session(result)
        except CodedError as error:
            acknowledgement = {"pin_two_left": "low", "pin_last_left": "final",
                               "pin_low_warning": "low", "pin_final_warning": "final"}.get(error.code)
            if acknowledgement:
                self.pin_warnings[certificate.identifier] = acknowledgement
            raise
        except (asyncio.CancelledError, asyncio.TimeoutError) as error:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(process.communicate(), 5)
            except asyncio.TimeoutError:
                raise CodedError("cleanup_unconfirmed") from None
            if isinstance(error, asyncio.CancelledError):
                raise
            raise CodedError("auth_timeout") from None
        except (ValueError, UnicodeError):
            raise CodedError("invalid_session") from None
        finally:
            payload = b""
