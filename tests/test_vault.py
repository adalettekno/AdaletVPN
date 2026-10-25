import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from adaletvpn.core import Credentials, ConnectionError
from adaletvpn.vault import CredentialVault, VaultError, certificate_key, decode_record
from adaletvpn.ui import MainWindow
from adaletvpn.vault_worker import SecretStore


class VaultTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        pref = patch('adaletvpn.ui.write_remember')
        pref.start(); self.addCleanup(pref.stop)

    def window(self):
        w=SimpleNamespace(controller=SimpleNamespace(connect=AsyncMock(),selected_id='pkcs11:serial=A',error_code=None),
            vault=SimpleNamespace(save=AsyncMock(),delete=AsyncMock(),load=AsyncMock(),clear_pin=AsyncMock()),
            vault_message=Mock(),remember=Mock(),remember_pin=Mock(),closing=False,
            username=Mock(),pin=Mock(),password=Mock(),vault_generation=0,vault_task=None,
            loop=asyncio.get_running_loop())
        w.remember.get_active.return_value=True
        w.remember_pin.get_active.return_value=False
        for field in (w.username,w.pin,w.password):field.get_text.return_value=''
        for method in ('start_vault','cancel_vault','save_credentials','invalidate_credentials',
                       'remember_toggled','pin_remember_toggled'):
            setattr(w,method,getattr(MainWindow,method).__get__(w))
        return w

    async def finish(self,w):
        if w.vault_task:await w.vault_task
        await asyncio.sleep(0)

    def test_card_keys_and_record_validation(self):
        self.assertNotEqual(certificate_key('pkcs11:serial=A'),certificate_key('pkcs11:serial=B'))
        for value in ({'username':'ab1','pin':'a','password':'b','extra':1},
                      {'username':'ab1','pin':'a\n','password':'b'},
                      {'username':'ab1','pin':None,'password':'b'}):
            with self.assertRaises(VaultError):decode_record(value)
        record=decode_record({'username':'ab1','pin':'','password':'secret-password'})
        self.assertEqual(record.pin,'');self.assertNotIn('secret',repr(record))

    async def test_disabled_option_never_saves(self):
        w=self.window()
        await MainWindow.connect_and_remember(w,Credentials('ab1','p','s'),'card',None)
        w.vault.save.assert_not_called();w.vault.delete.assert_not_called()

    async def test_save_only_after_success_and_clear_copy(self):
        w=self.window();record=Credentials('ab1','','s')
        async def save(identifier,value):
            w.controller.connect.assert_awaited_once()
            self.assertEqual(value.pin,'')
        w.vault.save.side_effect=save
        await MainWindow.connect_and_remember(w,Credentials('ab1','p','s'),'card',record)
        await self.finish(w)
        w.vault.save.assert_awaited_once();self.assertEqual(record.password,'')

    async def test_network_error_preserves_record_and_does_not_retry(self):
        w=self.window();w.controller.connect.side_effect=ConnectionError('network')
        with self.assertRaises(ConnectionError):
            await MainWindow.connect_and_remember(w,Credentials('ab1','p','s'),'card',Credentials('ab1','p','s'))
        w.controller.connect.assert_awaited_once()
        w.vault.delete.assert_not_called();w.vault.clear_pin.assert_not_called()

    async def test_pin_rejection_removes_only_pin_and_disables_pin_preference(self):
        w=self.window();w.controller.error_code='pin_last_left'
        w.controller.connect.side_effect=ConnectionError('pin')
        with self.assertRaises(ConnectionError):
            await MainWindow.connect_and_remember(w,Credentials('ab1','p','s'),'card',Credentials('ab1','p','s'))
        await self.finish(w)
        w.vault.clear_pin.assert_awaited_once_with('card');w.vault.delete.assert_not_called()
        w.remember_pin.set_active.assert_called_with(False)

    async def test_login_rejection_removes_card_record(self):
        w=self.window();w.controller.error_code='login_rejected'
        w.controller.connect.side_effect=ConnectionError('login')
        with self.assertRaises(ConnectionError):
            await MainWindow.connect_and_remember(w,Credentials('ab1','p','s'),'card',Credentials('ab1','p','s'))
        await self.finish(w)
        w.vault.delete.assert_awaited_once_with('card')

    async def test_slow_save_does_not_block_connection_completion(self):
        w=self.window();entered=asyncio.Event()
        async def save(*args):entered.set();await asyncio.Future()
        w.vault.save.side_effect=save
        record=Credentials('ab1','','s')
        await asyncio.wait_for(MainWindow.connect_and_remember(w,Credentials('ab1','p','s'),'card',record),.1)
        await entered.wait()
        w.cancel_vault()
        with self.assertRaises(asyncio.CancelledError):await self.finish(w)
        await asyncio.sleep(0)
        self.assertEqual(record.password,'')

    async def test_late_load_cannot_fill_changed_card_or_edited_field(self):
        for change in ('card','typed'):
            w=self.window();record=Credentials('ab1','','s')
            async def load(identifier):
                if change=='card':w.controller.selected_id='pkcs11:serial=B'
                else:w.username.get_text.return_value='ab2'
                return record
            w.vault.load.side_effect=load
            await MainWindow.load_credentials(w)
            w.username.set_text.assert_not_called();self.assertEqual(record.password,'')

    async def test_old_stored_pin_not_filled_without_new_consent(self):
        w=self.window();w.vault.load.return_value=Credentials('ab1','old-pin','s')
        async def save(identifier,record):self.assertEqual(record.pin,'')
        w.vault.save.side_effect=save
        await MainWindow.load_credentials(w)
        w.pin.set_text.assert_called_once_with('')
        w.vault.save.assert_awaited_once()

    async def test_failed_delete_does_not_report_success(self):
        w=self.window();w.vault.delete.side_effect=VaultError()
        await MainWindow.forget_credentials(w)
        self.assertIn('doğrulanamadı',w.vault_message.set_text.call_args.args[0])

    async def test_cancel_reaps_worker(self):
        process=SimpleNamespace(returncode=None,communicate=AsyncMock(side_effect=asyncio.CancelledError()),kill=Mock(),wait=AsyncMock())
        with patch('asyncio.create_subprocess_exec',AsyncMock(return_value=process)):
            with self.assertRaises(asyncio.CancelledError):await CredentialVault().load('pkcs11:serial=A')
        process.kill.assert_called_once();process.wait.assert_awaited_once()

    async def test_worker_locked_status_preserved(self):
        process=SimpleNamespace(returncode=1,communicate=AsyncMock(return_value=(b'{"error":"locked"}',None)))
        with patch('asyncio.create_subprocess_exec',AsyncMock(return_value=process)):
            with self.assertRaises(VaultError) as caught:await CredentialVault().load('pkcs11:serial=A')
        self.assertEqual(caught.exception.code,'locked')

    async def test_clear_pin_preserves_username_and_password(self):
        vault=CredentialVault();record=Credentials('ab1','p','s')
        vault.load=AsyncMock(return_value=record)
        async def save(identifier,value):self.assertEqual((value.username,value.pin,value.password),('ab1','','s'))
        vault.save=AsyncMock(side_effect=save)
        await vault.clear_pin('card');vault.save.assert_awaited_once();self.assertEqual(record.password,'')


class PreferenceTests(unittest.TestCase):
    def test_default_off_and_old_preference_does_not_opt_into_pin(self):
        from adaletvpn.remember_preferences import read_remember, write_remember
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'preferences.json'
            with patch('adaletvpn.remember_preferences.preference_path',return_value=path):
                self.assertFalse(read_remember());self.assertFalse(read_remember(pin=True))
                path.write_text('{"remember":true}')
                self.assertTrue(read_remember());self.assertFalse(read_remember(pin=True))
                write_remember(True,True)
                self.assertTrue(read_remember(pin=True))
                self.assertEqual(json.loads(path.read_text()),{'remember':True,'remember_pin':True})
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
                write_remember(False);self.assertFalse(read_remember(pin=True))
