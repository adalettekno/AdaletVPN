"""Credential-vault workflow, isolated from window and connection rendering."""

import asyncio
from enum import Enum

from adaletvpn.core import ConnectionError
from adaletvpn.vault import VaultError
from adaletvpn.remember_preferences import write_remember
from adaletvpn.diagnostics import Stage, report_failure


class VaultOperation(Enum):
    LOAD = "load"
    SAVE = "remember"
    DELETE = "forget"
    CLEAR_PIN = "pin"
    INVALIDATE = "invalidate"


class VaultCoordinator:
    def __init__(self, view):
        self.view = view
        self.task = None
        self.generation = 0

    def cancel_vault(self, announce=True):
        self.generation += 1
        if self.task is not None and not self.task.done():
            self.task.cancel()
            if announce:
                self.view.vault_message.set_text(
                    "Kasa işlemi iptal edildi. Yazma veya silme tamamlanmış olabilir; bilgilerinizi elle girerek devam edebilirsiniz."
                )

    def start_vault(self, operation, coroutine, cleanup=None):
        if operation is not VaultOperation.LOAD:
            self.view.vault_message.set_target(
                "remember" if operation is VaultOperation.INVALIDATE else operation.value
            )
        previous = self.task
        self.cancel_vault(announce=False)
        generation = self.generation
        self.view.vault_message.set_text("Kasa işlemi sürüyor. Bilgilerinizi elle girerek bağlanabilirsiniz.")

        async def run():
            if previous is not None:
                try:
                    await previous
                except (asyncio.CancelledError, Exception):
                    pass
            if generation != self.generation or self.view.closing:
                return
            await coroutine

        task = self.task = self.view.loop.create_task(run())
        if getattr(self.view, "timer", 1) is None:
            self.view.ensure_pump()

        def finished_vault(result):
            coroutine.close()
            if cleanup is not None:
                cleanup()
            try:
                result.result()
            except asyncio.CancelledError:
                pass
            except Exception as error:
                report_failure(Stage.VAULT_TASK, error)
                if generation == self.generation:
                    self.view.vault_message.set_text(str(VaultError()), error=True)

        task.add_done_callback(finished_vault)

    def remember_toggled(self, *_):
        self.view.vault_message.set_target("remember")
        if not self.view.managed_connection:
            return
        if self.view.remember.get_active():
            self.start_vault(VaultOperation.LOAD, self.load_credentials())
        else:
            self.cancel_vault(announce=False)
            self.view.remember_pin.handler_block_by_func(self.pin_remember_toggled)
            self.view.remember_pin.set_active(False)
            self.view.remember_pin.handler_unblock_by_func(self.pin_remember_toggled)
            self.view.pin.set_text("")
            self.view.password.set_text("")
            try:
                write_remember(False)
            except OSError:
                self.view.vault_message.set_text(
                    "Hatırlama kapalı; tercih dosyası güncellenemedi.", error=True
                )
                self.view.refresh()
                return
            self.view.vault_message.set_text(
                "Hatırlama kapalı. Eski kayıtları kaldırmak için Kaydedilen bilgileri sil düğmesini kullanın."
            )
        self.view.refresh()

    def pin_remember_toggled(self, *_):
        self.view.vault_message.set_target("pin")
        enabled = self.view.remember_pin.get_active()
        self.cancel_vault(announce=False)
        try:
            write_remember(self.view.remember.get_active(), enabled)
        except OSError:
            self.view.vault_message.set_text("Hatırlama tercihi kaydedilemedi.", error=True)
        if not enabled:
            self.view.pin.set_text("")
            if self.view.controller.selected_id:
                self.start_vault(
                    VaultOperation.CLEAR_PIN, self.remove_saved_pin(self.view.controller.selected_id)
                )
        elif self.view.remember.get_active():
            self.start_vault(VaultOperation.LOAD, self.load_credentials())
        self.view.refresh()

    async def remove_saved_pin(self, identifier):
        try:
            await self.view.vault.clear_pin(identifier)
            self.view.vault_message.set_text(
                "Seçili kartın kayıtlı PIN’i kaldırıldı; sicil ve bilgisayar şifresi korundu."
            )
        except VaultError as error:
            self.view.vault_message.set_text(str(error), error=True)

    async def load_credentials(self):
        identifier = self.view.controller.selected_id
        if not identifier:
            self.view.vault_message.set_text("Önce bir sertifika seçin.")
            return
        record = None
        generation = self.generation
        original = (self.view.username.get_text(), self.view.pin.get_text(), self.view.password.get_text())
        try:
            record = await self.view.vault.load(identifier)
            if (
                generation != self.generation
                or self.view.closing
                or self.view.controller.selected_id != identifier
                or not self.view.remember.get_active()
            ):
                return
            if record is None:
                self.view.vault_message.set_text(
                    "Bu kart için kayıt yok. Bilgiler başarılı bağlantıdan sonra kaydedilecek."
                )
            else:
                if not self.view.remember_pin.get_active() and record.pin:
                    record.pin = ""
                    await self.view.vault.save(identifier, record)
                if (
                    generation != self.generation
                    or self.view.controller.selected_id != identifier
                    or original
                    != (
                        self.view.username.get_text(),
                        self.view.pin.get_text(),
                        self.view.password.get_text(),
                    )
                ):
                    return
                self.view.username.set_text(record.username)
                self.view.pin.set_text(record.pin if self.view.remember_pin.get_active() else "")
                self.view.password.set_text(record.password)
        except VaultError as error:
            self.view.vault_message.set_text(str(error), error=True)
        finally:
            if record is not None:
                record.clear()

    async def forget_credentials(self):
        self.view.remember.handler_block_by_func(self.remember_toggled)
        self.view.remember.set_active(False)
        self.view.remember.handler_unblock_by_func(self.remember_toggled)
        self.view.remember_pin.handler_block_by_func(self.pin_remember_toggled)
        self.view.remember_pin.set_active(False)
        self.view.remember_pin.handler_unblock_by_func(self.pin_remember_toggled)
        try:
            write_remember(False)
        except OSError:
            self.view.vault_message.set_text(
                "Tercih kaydedilemedi; kasa kayıtlarını silme deneniyor.", error=True
            )
        self.view.username.set_text("ab")
        self.view.pin.set_text("")
        self.view.password.set_text("")
        try:
            await self.view.vault.delete()
            self.view.vault_message.set_text("Tüm bağlantı bilgileri silindi.")
        except VaultError as error:
            self.view.vault_message.set_text(str(error), error=True)

    async def save_credentials(self, identifier, record):
        try:
            await self.view.vault.save(identifier, record)
            write_remember(True, bool(record.pin))
            self.view.vault_message.set_text("Kaydedildi.")
        except VaultError as error:
            self.view.vault_message.set_text(str(error), error=True)
        except OSError:
            self.view.vault_message.set_text("Bilgiler kasada; hatırlama tercihi kaydedilemedi.", error=True)
        finally:
            record.clear()

    async def invalidate_credentials(self, identifier, pin_only):
        try:
            if pin_only:
                await self.view.vault.clear_pin(identifier)
                self.view.vault_message.set_text(
                    "PIN reddedildi; kayıtlı PIN kaldırıldı. PIN’i yeniden girin.", error=True
                )
            else:
                await self.view.vault.delete(identifier)
                self.view.vault_message.set_text(
                    "Kurum giriş bilgileri reddedildi; bu kartın kaydı kaldırıldı.", error=True
                )
        except VaultError as error:
            self.view.vault_message.set_text(str(error), error=True)

    async def connect_and_remember(self, credentials, identifier, remembered):
        try:
            await self.view.controller.connect(credentials)
            if remembered is not None:
                self.start_vault(
                    VaultOperation.SAVE,
                    self.save_credentials(identifier, remembered),
                    cleanup=remembered.clear,
                )
                remembered = None
        except ConnectionError:
            code = self.view.controller.error_code
            if code and code.startswith("pin_"):
                self.view.pin.set_text("")
            elif code == "login_rejected":
                self.view.pin.set_text("")
                self.view.password.set_text("")
            if remembered is not None and code in (
                "pin_rejected",
                "pin_two_left",
                "pin_last_left",
                "login_rejected",
            ):
                if code != "login_rejected":
                    self.view.remember_pin.handler_block_by_func(self.pin_remember_toggled)
                    self.view.remember_pin.set_active(False)
                    self.view.remember_pin.handler_unblock_by_func(self.pin_remember_toggled)
                else:
                    self.view.remember.handler_block_by_func(self.remember_toggled)
                    self.view.remember.set_active(False)
                    self.view.remember.handler_unblock_by_func(self.remember_toggled)
                try:
                    write_remember(code != "login_rejected", False)
                except OSError:
                    self.view.vault_message.set_text(
                        "Hatırlama bu oturumda kapalı; tercih kaydedilemedi.", error=True
                    )
                self.start_vault(
                    VaultOperation.INVALIDATE,
                    self.invalidate_credentials(identifier, code != "login_rejected"),
                )
            raise
        finally:
            if remembered is not None:
                remembered.clear()
