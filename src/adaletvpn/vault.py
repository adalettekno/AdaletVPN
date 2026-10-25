import asyncio
import hashlib
import json
import sys

from adaletvpn.auth import credential_problem
from adaletvpn.core import Credentials, ConnectionError


class VaultError(ConnectionError):
    def __init__(self, code='failed'):
        self.code = code
        super().__init__({
            'locked': 'Kasa kilitli. Masaüstünüzün şifre kasasını açın; bu sırada bilgilerinizi elle girerek bağlanabilirsiniz.',
            'unavailable': 'Şifre kasası kullanılamıyor. Uyumlu kasa hizmetini kontrol edin; bilgilerinizi elle girerek bağlanabilirsiniz.',
            'timeout': 'Kasa yanıt vermedi; işlem durduruldu. Kayıt veya silme işleminin tamamlandığı doğrulanamadı.',
            'failed': 'Kasa işlemi tamamlanamadı. Kayıt veya silme işleminin tamamlandığı doğrulanamadı.',
        }.get(code, 'Kasa işlemi tamamlanamadı.'))


def certificate_key(identifier):
    if not isinstance(identifier, str) or not identifier.startswith('pkcs11:') or len(identifier) > 8192:
        raise VaultError()
    return hashlib.sha256(identifier.encode()).hexdigest()


def decode_record(value):
    if not isinstance(value, dict) or set(value) != {'username', 'pin', 'password'}:
        raise VaultError()
    if not all(isinstance(x, str) for x in value.values()):
        raise VaultError()
    if credential_problem(value['username'], value['pin'] or 'not-stored', value['password']):
        raise VaultError()
    return Credentials(value['username'], value['pin'], value['password'])


class CredentialVault:
    async def request(self, operation, identifier=None, credentials=None):
        payload = {'operation': operation}
        if identifier is not None:
            payload['key'] = certificate_key(identifier)
        if credentials is not None:
            payload['record'] = {'username': credentials.username, 'pin': credentials.pin,
                                 'password': credentials.password}
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'adaletvpn.vault_worker',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            raise VaultError() from None
        try:
            output, _ = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), 30)
            if len(output) > 16384:
                raise VaultError()
            value = json.loads(output)
            if isinstance(value, dict) and set(value) == {'error'}:
                raise VaultError(value['error'] if value['error'] in ('locked','unavailable','failed') else 'failed')
            if process.returncode or not isinstance(value, dict) or set(value) != {'result'}:
                raise VaultError()
            return value['result']
        except asyncio.TimeoutError:
            raise VaultError("timeout") from None
        except (ValueError, OSError):
            raise VaultError() from None
        finally:
            payload.clear()
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def load(self, identifier):
        value = await self.request('load', identifier)
        return None if value is None else decode_record(value)

    async def save(self, identifier, credentials):
        if credential_problem(credentials.username, credentials.pin or "not-stored", credentials.password):
            raise VaultError()
        if await self.request('save', identifier, credentials) is not True:
            raise VaultError()

    async def delete(self, identifier=None):
        if await self.request('delete', identifier) is not True:
            raise VaultError()

    async def clear_pin(self, identifier):
        record = await self.load(identifier)
        if record is not None:
            try:
                if record.pin:
                    record.pin = ''
                    await self.save(identifier, record)
            finally:
                record.clear()
