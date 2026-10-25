import ctypes as C
import json
import re
import sys

from adaletvpn.errors import disable_core_dumps


class GList(C.Structure):
    pass
GList._fields_ = [('data', C.c_void_p), ('next', C.POINTER(GList)), ('prev', C.POINTER(GList))]


class StoreError(Exception):
    pass


class SecretStore:
    def __init__(self):
        self.secret = C.CDLL('libsecret-1.so.0')
        self.glib = C.CDLL('libglib-2.0.so.0')
        self.gobject = C.CDLL('libgobject-2.0.so.0')
        self.gio = C.CDLL('libgio-2.0.so.0')
        P = C.c_void_p
        E = C.POINTER(P)
        def fn(lib, name, result, args):
            f = getattr(lib, name); f.restype = result; f.argtypes = args; return f
        self.table = fn(self.glib,'g_hash_table_new',P,[P,P])
        self.insert = fn(self.glib,'g_hash_table_insert',C.c_int,[P,C.c_char_p,C.c_char_p])
        self.unref = fn(self.glib,'g_hash_table_unref',None,[P])
        self.error_free = fn(self.glib,'g_error_free',None,[P])
        self.list_free = fn(self.glib,'g_list_free',None,[C.POINTER(GList)])
        self.object_unref = fn(self.gobject,'g_object_unref',None,[P])
        self.store = fn(self.secret,'secret_password_storev_sync',C.c_int,[P,P,C.c_char_p,C.c_char_p,C.c_char_p,P,E])
        self.lookup = fn(self.secret,'secret_password_lookupv_sync',P,[P,P,P,E])
        self.password_free = fn(self.secret,'secret_password_free',None,[P])
        self.search = fn(self.secret,'secret_service_search_sync',C.POINTER(GList),[P,P,P,C.c_int,P,E])
        self.locked = fn(self.secret,'secret_item_get_locked',C.c_int,[P])
        self.remote_error = fn(self.gio,'g_dbus_error_get_remote_error',P,[P])
        self.free = fn(self.glib,'g_free',None,[P])
        self.remove = fn(self.secret,'secret_item_delete_sync',C.c_int,[P,P,E])

    def checked(self, function, *args):
        error = C.c_void_p()
        value = function(*args, C.byref(error))
        if error:
            remote = self.remote_error(error)
            name = C.string_at(remote).decode() if remote else ''
            if remote: self.free(remote)
            self.error_free(error)
            code = ('locked' if name == 'org.freedesktop.Secret.Error.IsLocked' else
                    'unavailable' if name in ('org.freedesktop.DBus.Error.ServiceUnknown',
                    'org.freedesktop.DBus.Error.NameHasNoOwner', 'org.freedesktop.DBus.Error.Spawn.ServiceNotFound') else 'failed')
            raise StoreError(code)
        return value

    def delete_all_matches(self, attributes):

        items = self.checked(self.search, None, None, attributes, 1, None)
        node = items
        try:
            while node:
                if not self.checked(self.remove, node.contents.data, None):
                    raise RuntimeError('delete')
                node = node.contents.next
        finally:
            node = items
            while node:
                self.object_unref(node.contents.data); node = node.contents.next
            self.list_free(items)
        remaining = self.checked(self.search, None, None, attributes, 1, None)
        if remaining:
            node = remaining
            while node:
                self.object_unref(node.contents.data); node = node.contents.next
            self.list_free(remaining)
            raise RuntimeError('remaining')
        return True

    def execute(self, request):
        operation = request.get('operation')
        key = request.get('key')
        expected = {'operation'} | ({'key'} if key is not None else set()) | ({'record'} if operation == 'save' else set())
        if set(request) != expected or operation not in ('save','load','delete'):
            raise ValueError('request')
        if key is not None and (not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key)):
            raise ValueError('key')
        if operation != 'delete' and key is None:
            raise ValueError('key')
        attrs = {b'xdg:schema': b'com.adalettekno.AdaletVPN.Credentials.v1', b'application': b'com.adalettekno.AdaletVPN'}
        if key is not None: attrs[b'certificate'] = key.encode()
        table = self.table(C.cast(self.glib.g_str_hash,C.c_void_p),C.cast(self.glib.g_str_equal,C.c_void_p))
        try:
            for name, value in attrs.items(): self.insert(table,name,value)
            if operation == 'delete': return self.delete_all_matches(table)
            if operation == 'save':
                from adaletvpn.vault import decode_record
                credentials = decode_record(request['record']); credentials.clear()
                encoded = json.dumps(request['record']).encode()
                if not self.checked(self.store,None,table,b'default','AdaletVPN bağlantı bilgileri'.encode(),encoded,None):
                    raise RuntimeError('save')
                return True
            items = self.checked(self.search,None,None,table,1,None)
            node = items
            is_locked = False
            while node:
                is_locked = is_locked or bool(self.locked(node.contents.data))
                self.object_unref(node.contents.data); node = node.contents.next
            self.list_free(items)
            if is_locked: raise StoreError('locked')
            value = self.checked(self.lookup,None,table,None)
            if not value: return None
            try: return json.loads(C.string_at(value).decode())
            finally: self.password_free(value)
        finally:
            self.unref(table)


def main():
    disable_core_dumps()
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384: raise ValueError('size')
        request = json.loads(raw)
        result = SecretStore().execute(request)
        print(json.dumps({'result': result}))
    except StoreError as error:
        print(json.dumps({'error': str(error)}))
        return 1
    except OSError:
        print(json.dumps({'error': 'unavailable'}))
        return 1
    except Exception:
        print(json.dumps({'error': 'failed'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
