import asyncio

from adaletvpn.core import Certificate, Credentials


class DemoCertificates:
    async def list_certificates(self):
        await asyncio.sleep(0.2)
        return (
            Certificate("demo:1", "Örnek cihaz 1", "Deneme sertifikası 1", "Örnek kurum"),
            Certificate("demo:2", "Örnek cihaz 2", "Deneme sertifikası 2", "Örnek kurum"),
        )


class DemoVPN:
    async def authenticate(self, certificate: Certificate, credentials: Credentials) -> None:
        await asyncio.sleep(0.3)

    async def connect(self) -> None:
        await asyncio.sleep(0.5)

    async def disconnect(self) -> None:
        await asyncio.sleep(0.1)
