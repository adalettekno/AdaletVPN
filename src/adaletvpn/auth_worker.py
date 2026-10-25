import ctypes as C
import json
import locale
import os
import sys

from adaletvpn.auth import FormAnswers
from adaletvpn.errors import CodedError
from adaletvpn.session import allowed_auth_endpoint, validate_session
from adaletvpn.core import Credentials
from adaletvpn.pkcs11 import parse_certificates
from adaletvpn.preflight import VPNProfile


class Option(C.Structure):
    pass


Option._fields_ = [("next", C.POINTER(Option)), ("type", C.c_int),
                  ("name", C.c_char_p), ("label", C.c_char_p),
                  ("value", C.c_char_p), ("flags", C.c_uint), ("reserved", C.c_void_p)]


class Form(C.Structure):
    _fields_ = [(name, C.c_char_p) for name in ("banner", "message", "error", "auth_id", "method", "action")] + [
        ("opts", C.POINTER(Option)), ("authgroup_opt", C.c_void_p), ("authgroup_selection", C.c_int)]


def answer_native_form(form, answers, set_option):

    auth_id = (form.auth_id or b"").decode("utf-8")
    if auth_id == "pkcs11_pin" and form.banner:
        level = {b"Only a few tries left before locking!": "low",
                 b"This is the final try before locking!": "final"}.get(form.banner)
        if level is None:
            raise CodedError("pin_attempts_low")
        if form.error:
            raise CodedError("pin_two_left" if level == "low" else "pin_last_left")
        if answers.pin_warning_ack != level:
            raise CodedError("pin_low_warning" if level == "low" else "pin_final_warning")
    options, pointers = [], {}
    current = form.opts
    count = 0
    while current:
        count += 1
        if count > 32:
            raise CodedError("unsupported_form")
        option = current.contents
        if not option.flags & 1 and option.type != 4:
            name = (option.name or b"").decode("utf-8")
            if name in pointers:
                raise CodedError("unsupported_form")
            options.append((name, option.type))
            pointers[name] = current
        current = option.next
    result = answers.answer(auth_id, options, bool(form.error))
    for name, value in result.items():
        if set_option(pointers[name], value.encode("utf-8")):
            raise CodedError("auth_failed")


def authenticate(request, report_stage=lambda stage: None):

    from adaletvpn.networkmanager import LIVE_ENABLED
    if not LIVE_ENABLED:
        raise ValueError("Gerçek bağlantı kilitli")
    profile = VPNProfile(gateway=request["gateway"])
    profile.validate()
    certificates = parse_certificates(request["certificate"])
    if len(certificates) != 1:
        raise ValueError("Tek sertifika gerekli")
    credentials = Credentials(request.pop("username"), request.pop("pin"), request.pop("password"))
    answers = FormAnswers(credentials, request.pop("pin_warning_ack", None))

    locale.setlocale(locale.LC_MESSAGES, "C")
    failure_code = "auth_failed"
    report_stage("library_load")
    library = C.CDLL("libopenconnect.so.5")

    def bind(name, restype, *argtypes):
        fn = getattr(library, "openconnect_" + name)
        fn.restype, fn.argtypes = restype, list(argtypes)
        return fn

    ptr, string, integer = C.c_void_p, C.c_char_p, C.c_int
    validate_type = C.CFUNCTYPE(integer, ptr, string)
    form_type = C.CFUNCTYPE(integer, ptr, C.POINTER(Form))
    progress_type = C.CFUNCTYPE(None, ptr, integer, string)
    set_option = bind("set_option_value", integer, C.POINTER(Option), string)

    get_dnsname = bind("get_dnsname", string, ptr)
    get_port = bind("get_port", integer, ptr)

    @validate_type
    def reject_untrusted(_data, _reason):
        nonlocal failure_code
        failure_code = "untrusted_server"
        return -1

    @progress_type
    def discard_progress(_data, _level, _format):
        pass

    @form_type
    def process_form(_data, pointer):
        nonlocal failure_code
        try:
            form = pointer.contents
            if not allowed_auth_endpoint(get_dnsname(info), get_port(info)):
                failure_code = "unexpected_server"
                return -1
            answer_native_form(form, answers, set_option)
            return 0
        except CodedError as error:
            failure_code = error.code
            return -1
        except Exception:
            failure_code = "unsupported_form"
            return -1

    new = bind("vpninfo_new", ptr, string, validate_type, ptr, form_type, progress_type, ptr)
    free = bind("vpninfo_free", None, ptr)
    report_stage("ssl_init")
    if bind("init_ssl", integer)() or not bind("has_pkcs11_support", integer)():
        raise ValueError("PKCS#11 desteği yok")
    report_stage("context_create")
    info = new(b"AdaletVPN", reject_untrusted, None, process_form, discard_progress, None)
    if not info:
        raise ValueError("Kimlik doğrulama başlatılamadı")
    exchange_started = False
    exchange_succeeded = False
    try:
        report_stage("protocol")
        if bind("set_protocol", integer, ptr, string)(info, b"anyconnect"):
            raise ValueError("Protokol desteklenmiyor")
        report_stage("certificate")
        if bind("set_client_cert", integer, ptr, string, string)(info, request["certificate"].encode(), None):
            raise ValueError("Sertifika seçilemedi")
        report_stage("url")
        if bind("parse_url", integer, ptr, string)(info, profile.gateway.encode()):
            raise ValueError("Sunucu adresi geçersiz")
        report_stage("exchange")
        exchange_started = True
        if bind("obtain_cookie", integer, ptr)(info):
            raise CodedError(failure_code)
        exchange_succeeded = True
        report_stage("session_read")
        hostname = get_dnsname(info)
        port = bind("get_port", integer, ptr)(info)
        if not allowed_auth_endpoint(hostname, port):
            raise CodedError("unexpected_server")
        cookie = bind("get_cookie", string, ptr)(info)
        fingerprint = bind("get_peer_cert_hash", string, ptr)(info)
        path = bind("get_urlpath", string, ptr)(info) or b""
        if not cookie or not fingerprint or not answers.pin_used or not answers.login_used:
            raise ValueError("Kimlik doğrulama akışı tamamlanmadı")
        return validate_session({"cookie": cookie.decode(), "gateway": "https://" + hostname.decode("ascii") + "/" + path.decode().lstrip("/"),
                                 "gwcert": fingerprint.decode()})
    finally:
        credentials.clear()
        report_stage("cleanup")


        if not exchange_started or exchange_succeeded:
            free(info)


def isolate_result_stream():

    sys.stdout.flush()
    result = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    return result


if __name__ == "__main__":
    from adaletvpn.errors import seal_live_worker
    seal_live_worker()
    result_stream = isolate_result_stream()
    try:
        response = authenticate(json.loads(sys.stdin.buffer.read(16384)),
            lambda stage: print(json.dumps({"stage": stage}), file=result_stream, flush=True))
        print(json.dumps(response), file=result_stream, flush=True)
    except CodedError as error:
        print(json.dumps({"error": error.code}), file=result_stream, flush=True)
        raise SystemExit(1)
    except Exception:
        print(json.dumps({"error": "auth_failed"}), file=result_stream, flush=True)
        raise SystemExit(1)
