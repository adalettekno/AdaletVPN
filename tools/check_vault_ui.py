import os
import tempfile
from unittest.mock import AsyncMock
from datetime import datetime, timezone
from adaletvpn.core import Certificate, Credentials
from adaletvpn.ui import Gtk, GLib, MainWindow

folder=tempfile.TemporaryDirectory()
os.environ['XDG_CONFIG_HOME']=folder.name
class Provider:
    async def list_certificates(self):
        return [Certificate('pkcs11:token=Demo;serial=test;id=%01;type=cert','Örnek kart',
            'Örnek kullanıcı','Örnek kurum',not_before=datetime(2020,1,1,tzinfo=timezone.utc),
            not_after=datetime(2040,1,1,tzinfo=timezone.utc))]
app=Gtk.Application(application_id='com.adalettekno.AdaletVPN.VaultUITest')
phase=0
failed=False
count=0

def activate(application):
    global window
    window=MainWindow(application,Provider(),AsyncMock())
    window.vault=AsyncMock()
    window.vault.load.side_effect=lambda *_: Credentials('ab000000','example-pin','example-password')
    window.present()
    GLib.timeout_add(150,check)

def check():
    global phase,failed,count
    count+=1
    try:
        assert count<80
        if window.task and not window.task.done():return True
        if window.vault_task and not window.vault_task.done():return True
        if phase==0:
            assert not window.remember.get_active()
            assert not window.remember_pin.get_active()
            window.remember.set_active(True)
        elif phase==1:
            assert window.username.get_text()=='ab000000'
            assert window.pin.get_text()==''
            assert window.password.get_text()=='example-password'
            window.remember_pin.set_active(True)
        elif phase==2:
            assert window.pin.get_text()=='example-pin'
            window.forget_button.emit('clicked')
        elif phase==3:
            window.vault.delete.assert_awaited_once_with()
            assert not window.remember.get_active()
            assert not window.pin.get_text() and not window.password.get_text()
            assert 'silindi' in window.vault_message.get_text()
            print('Varsayılan kapalı, kasadan doldurma ve kayıt silme arayüzü başarılı.')
            window.close()
            return False
        phase+=1
        return True
    except Exception as error:
        failed=True
        print('Arayüz kontrolü başarısız:',type(error).__name__)
        window.close()
        return False
app.connect('activate',activate)
app.run([])
folder.cleanup()
raise SystemExit(1 if failed else 0)
