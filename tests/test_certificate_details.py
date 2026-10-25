from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from adaletvpn.core import Certificate
from adaletvpn.pkcs11 import PKCS11Certificates, read_details


NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
ITEM = Certificate("pkcs11:token=Test;id=%01;type=cert", "Test cihazı", "", "")


def certificate_der(extensions=()):
    key = ed25519.Ed25519PrivateKey.generate()
    builder = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Örnek Kullanıcı")]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Örnek Kurum")]))
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(NOW - timedelta(days=1))
            .not_valid_after(NOW + timedelta(days=1)))
    for extension in extensions:
        builder = builder.add_extension(extension, critical=True)
    return builder.sign(key, None).public_bytes(Encoding.DER)


class DetailsTests(unittest.TestCase):
    def test_certificate_usage_extensions(self):
        item = read_details(ITEM, certificate_der([
            x509.BasicConstraints(ca=True, path_length=None),
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            x509.KeyUsage(False, False, True, False, False, False, False, False, False),
        ]))
        self.assertTrue(item.is_ca)
        self.assertFalse(item.client_auth)
        self.assertFalse(item.digital_signature)

    def test_subject_issuer_and_dates(self):
        item = read_details(ITEM, certificate_der())
        self.assertIn("Örnek Kullanıcı", item.subject)
        self.assertIn("Örnek Kurum", item.issuer)
        self.assertEqual(item.not_before, NOW - timedelta(days=1))
        self.assertEqual(item.not_after, NOW + timedelta(days=1))
        self.assertEqual(item.identifier, ITEM.identifier)

    def test_validity_boundaries(self):
        item = read_details(ITEM, certificate_der())
        self.assertEqual(item.validity(NOW - timedelta(days=2)), "Henüz geçerli değil")
        self.assertEqual(item.validity(NOW + timedelta(days=2)), "Süresi dolmuş")
        for moment in (item.not_before, NOW, item.not_after):
            self.assertEqual(item.validity(moment), "Geçerlilik tarihleri içinde")
        self.assertEqual(ITEM.validity(NOW), "Tarih bilgisi okunamadı")


class DetailsProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_exports_public_certificate_only(self):
        provider = PKCS11Certificates(__file__)
        with patch.object(provider, "_run", AsyncMock(side_effect=[
                ITEM.identifier.encode(), certificate_der()])) as run:
            item, = await provider.list_certificates()
        self.assertEqual(run.call_args_list[1].args, ("--export", "--outder", ITEM.identifier))
        self.assertIn("Örnek Kullanıcı", item.subject)

    async def test_bad_der_retains_item_with_unknown_validity(self):
        provider = PKCS11Certificates(__file__)
        with patch.object(provider, "_run", AsyncMock(side_effect=[
                ITEM.identifier.encode(), b"invalid certificate"] )):
            item, = await provider.list_certificates()
        self.assertTrue(item.details_error)
        self.assertEqual(item.validity(NOW), "Tarih bilgisi okunamadı")
