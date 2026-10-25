import ctypes
import unittest
from unittest.mock import Mock, patch

from adaletvpn.auth_worker import Form, authenticate
from adaletvpn.errors import CodedError
from test_networkmanager import certificate


class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.library = Mock()
        for name, result in {
            "init_ssl": 0, "has_pkcs11_support": 1, "vpninfo_new": 1,
            "set_protocol": 0, "set_client_cert": 0, "parse_url": 0,
            "get_hostname": b"192.0.2.10", "get_dnsname": b"vpn.uyap.gov.tr",
            "get_port": 443, "get_cookie": b"test-cookie", "get_urlpath": b"",
            "get_peer_cert_hash": b"pin-sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        }.items():
            getattr(self.library, "openconnect_" + name).return_value = result

    def run_auth(self, answer):
        def exchange(info):
            callback = self.library.openconnect_vpninfo_new.call_args.args[3]
            return callback(None, ctypes.pointer(Form()))

        self.library.openconnect_obtain_cookie.side_effect = exchange
        with patch("adaletvpn.networkmanager.LIVE_ENABLED", True), patch(
                "adaletvpn.auth_worker.C.CDLL", return_value=self.library), patch(
                "adaletvpn.auth_worker.answer_native_form", side_effect=answer) as respond:
            self.respond = respond
            return authenticate({"gateway": "https://vpn.uyap.gov.tr",
                "certificate": certificate().identifier,
                "username": "ab123", "pin": "test-pin", "password": "test-password"})

    def test_rejected_pin_form_preserves_error_without_unsafe_destructor(self):
        with self.assertRaises(CodedError) as caught:
            self.run_auth(CodedError("pin_low_warning"))
        self.assertEqual(caught.exception.code, "pin_low_warning")
        self.respond.assert_called_once()
        self.library.openconnect_vpninfo_free.assert_not_called()
        self.library.openconnect_get_hostname.assert_not_called()

    def test_wrong_dns_name_or_port_stops_before_credentials(self):
        for dns, port in ((b"other.example", 443), (None, 443), (b"vpn.uyap.gov.tr", 444),
                          (b"vpn1.uyap.gov.tr.attacker.example", 443),
                          (b"vpn3.uyap.gov.tr", 443), (b"vpn1.uyap.gov.tr", 444),
                          (b"vpn2.uyap.gov.tr.attacker.example", 443), (b"vpn2.uyap.gov.tr", 444)):
            with self.subTest(dns=dns, port=port):
                self.library.openconnect_get_dnsname.return_value = dns
                self.library.openconnect_get_port.return_value = port
                with self.assertRaises(CodedError) as caught:
                    self.run_auth(None)
                self.assertEqual(caught.exception.code, "unexpected_server")
                self.respond.assert_not_called()

    def test_success_uses_dns_name_and_frees_native_context(self):
        def answer(form, answers, set_option):
            answers.pin_used = True
            answers.login_used = True

        result = self.run_auth(answer)
        self.assertEqual(result["gateway"], "https://vpn.uyap.gov.tr/")
        self.library.openconnect_vpninfo_free.assert_called_once_with(1)
        self.library.openconnect_get_hostname.assert_not_called()

    def test_redirected_form_and_session_keep_actual_server(self):
        def answer(form, answers, set_option):
            answers.pin_used = True
            answers.login_used = True

        for host in ("vpn1.uyap.gov.tr", "vpn2.uyap.gov.tr"):
            with self.subTest(host=host):
                self.library.openconnect_get_dnsname.return_value = host.encode("ascii")
                self.library.openconnect_get_urlpath.return_value = b"session"
                result = self.run_auth(answer)
                self.respond.assert_called_once()
                self.assertEqual(result["gateway"], "https://" + host + "/session")

    def test_disallowed_server_after_form_cannot_return_session(self):
        def answer(form, answers, set_option):
            answers.pin_used = True
            answers.login_used = True
            self.library.openconnect_get_dnsname.return_value = b"other.example"

        with self.assertRaises(CodedError) as caught:
            self.run_auth(answer)
        self.assertEqual(caught.exception.code, "unexpected_server")
        self.library.openconnect_get_cookie.assert_not_called()

    def test_allowed_redirect_does_not_bypass_tls_rejection(self):
        def answer(form, answers, set_option):
            reject = self.library.openconnect_vpninfo_new.call_args.args[1]
            self.assertEqual(reject(None, b"test-untrusted"), -1)
            raise CodedError("untrusted_server")

        for host in (b"vpn1.uyap.gov.tr", b"vpn2.uyap.gov.tr"):
            with self.subTest(host=host):
                self.library.openconnect_get_dnsname.return_value = host
                with self.assertRaises(CodedError) as caught:
                    self.run_auth(answer)
                self.assertEqual(caught.exception.code, "untrusted_server")

    def test_setup_failure_frees_context_before_exchange(self):
        self.library.openconnect_set_protocol.return_value = -1
        with self.assertRaises(ValueError):
            self.run_auth(None)
        self.library.openconnect_obtain_cookie.assert_not_called()
        self.library.openconnect_vpninfo_free.assert_called_once_with(1)
