from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message

from tg_bot import cbt, keyboards as kb
from tg_bot.utils import h
from utils.brand import DESC_PL
from utils.config import ROOT
from utils.storage import load_disabled_plugins, save_disabled_plugins

if TYPE_CHECKING:
    from core import App

logger = logging.getLogger("SVC.plugins")


def init_plugins_cp(cardinal: App) -> None:
    tg = cardinal.telegram
    if not tg:
        return
    bot = tg.bot

    def exists(uuid: str, message) -> bool:
        if uuid in cardinal.plugins:
            return True
        try:
            bot.edit_message_text("Плагин не найден. Обновите список.", message.chat.id, message.id, reply_markup=kb.plugins_list(cardinal, 0))
        except Exception:
            pass
        return False

    def open_list(call: CallbackQuery) -> None:
        offset = int(call.data.split(":")[1])
        bot.edit_message_text(
            DESC_PL,
            call.message.chat.id,
            call.message.id,
            reply_markup=kb.plugins_list(cardinal, offset),
        )
        bot.answer_callback_query(call.id)

    def open_edit(call: CallbackQuery) -> None:
        _, uuid, offset = call.data.split(":")[:3]
        offset = int(offset)
        if not exists(uuid, call.message):
            bot.answer_callback_query(call.id)
            return
        plugin = cardinal.plugins[uuid]
        text = (
            f"<b>{h(plugin.name)}</b> v{h(plugin.version)}\n\n"
            f"{h(plugin.description)}\n\n"
            f"UUID: <code>{h(plugin.uuid)}</code>\n"
            f"Автор: {h(plugin.credits)}\n"
            f"Файл: <code>{h(plugin.path.name)}</code>\n"
            f"Состояние: <b>{'вкл' if plugin.enabled else 'выкл'}</b>"
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.id, reply_markup=kb.edit_plugin(cardinal, uuid, offset))
        bot.answer_callback_query(call.id)

    def toggle(call: CallbackQuery) -> None:
        _, uuid, offset = call.data.split(":")[:3]
        if not exists(uuid, call.message):
            bot.answer_callback_query(call.id)
            return
        cardinal.toggle_plugin(uuid)
        call.data = f"{cbt.EDIT_PLUGIN}:{uuid}:{offset}"
        open_edit(call)

    def commands(call: CallbackQuery) -> None:
        _, uuid, offset = call.data.split(":")[:3]
        if not exists(uuid, call.message):
            bot.answer_callback_query(call.id)
            return
        plugin = cardinal.plugins[uuid]
        lines = [f"/{cmd} — {h(desc)}" for cmd, desc in (plugin.commands or {}).items()] or ["Нет команд."]
        bot.edit_message_text(
            f"<b>Команды {h(plugin.name)}</b>\n\n" + "\n".join(lines),
            call.message.chat.id,
            call.message.id,
            reply_markup=kb._rows([[("⬅️ Назад", f"{cbt.EDIT_PLUGIN}:{uuid}:{offset}")]]),
        )
        bot.answer_callback_query(call.id)

    def ask_delete(call: CallbackQuery) -> None:
        _, uuid, offset = call.data.split(":")[:3]
        bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=kb.edit_plugin(cardinal, uuid, int(offset), True))
        bot.answer_callback_query(call.id)

    def cancel_delete(call: CallbackQuery) -> None:
        _, uuid, offset = call.data.split(":")[:3]
        bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=kb.edit_plugin(cardinal, uuid, int(offset), False))
        bot.answer_callback_query(call.id)

    def delete_plugin(call: CallbackQuery) -> None:
        _, uuid, offset = call.data.split(":")[:3]
        if not exists(uuid, call.message):
            bot.answer_callback_query(call.id)
            return
        plugin = cardinal.plugins[uuid]
        if plugin.delete_handler:
            try:
                plugin.delete_handler(cardinal, call)
            except Exception:
                logger.exception("delete_handler %s", plugin.name)
        try:
            os.remove(plugin.path)
        except FileNotFoundError:
            pass
        cardinal._unload_plugin(uuid)
        disabled = load_disabled_plugins()
        disabled.discard(uuid)
        save_disabled_plugins(disabled)
        call.data = f"{cbt.PLUGINS}:{offset}"
        open_list(call)

    def ask_upload(obj: CallbackQuery | Message) -> None:
        if isinstance(obj, CallbackQuery):
            offset = int(obj.data.split(":")[1])
            result = bot.send_message(obj.message.chat.id, "Пришлите файл плагина <code>.py</code>.", reply_markup=kb.cancel())
            tg.set_state(obj.message.chat.id, result.id, obj.from_user.id, cbt.UPLOAD_PLUGIN, {"offset": offset})
            bot.answer_callback_query(obj.id)
        else:
            result = bot.send_message(obj.chat.id, "Пришлите файл плагина <code>.py</code>.", reply_markup=kb.cancel())
            tg.set_state(obj.chat.id, result.id, obj.from_user.id, cbt.UPLOAD_PLUGIN, {"offset": 0})

    def save_upload(message: Message) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        doc = message.document
        if not doc or not (doc.file_name or "").lower().endswith(".py"):
            bot.send_message(message.chat.id, "Нужен файл .py")
            return
        info = bot.get_file(doc.file_id)
        raw = bot.download_file(info.file_path)
        name = Path(doc.file_name).name
        dest = ROOT / "plugins" / name
        dest.write_bytes(raw)
        try:
            cardinal.load_plugin_file(dest)
            bot.send_message(message.chat.id, f"✅ Плагин <code>{h(name)}</code> загружен и включён.")
        except Exception as exc:
            bot.send_message(
                message.chat.id,
                f"Файл сохранён в <code>plugins/{h(name)}</code>, но загрузка с ходу не вышла: {h(exc)}\nПерезапусти бота командой /restart.",
            )

    tg.cbq_handler(open_list, lambda c: c.data.startswith(f"{cbt.PLUGINS}:"))
    tg.cbq_handler(open_edit, lambda c: c.data.startswith(f"{cbt.EDIT_PLUGIN}:"))
    tg.cbq_handler(toggle, lambda c: c.data.startswith(f"{cbt.TOGGLE_PLUGIN}:"))
    tg.cbq_handler(commands, lambda c: c.data.startswith(f"{cbt.PLUGIN_COMMANDS}:"))
    tg.cbq_handler(ask_delete, lambda c: c.data.startswith(f"{cbt.DELETE_PLUGIN}:"))
    tg.cbq_handler(cancel_delete, lambda c: c.data.startswith(f"{cbt.CANCEL_DELETE_PLUGIN}:"))
    tg.cbq_handler(delete_plugin, lambda c: c.data.startswith(f"{cbt.CONFIRM_DELETE_PLUGIN}:"))
    tg.cbq_handler(ask_upload, lambda c: c.data.startswith(f"{cbt.UPLOAD_PLUGIN}:"))
    tg.msg_handler(ask_upload, commands=["upload_plugin"])
    tg.file_handler(cbt.UPLOAD_PLUGIN, save_upload)
