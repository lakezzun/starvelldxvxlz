from __future__ import annotations

from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message

from tg_bot import cbt, keyboards as kb
from tg_bot.utils import h
from utils.brand import DESC_AR

if TYPE_CHECKING:
    from core import App


def init_auto_response_cp(cardinal: App) -> None:
    tg = cardinal.telegram
    if not tg:
        return
    bot = tg.bot

    def _exists(index: int, message: Message) -> bool:
        if 0 <= index < len(cardinal.AR_CFG.sections()):
            return True
        bot.edit_message_text(
            "Команда не найдена. Обновите список.",
            message.chat.id,
            message.id,
            reply_markup=kb.commands_list(cardinal, 0),
        )
        return False

    def open_list(call: CallbackQuery) -> None:
        offset = int(call.data.split(":")[1]) if ":" in call.data else 0
        bot.edit_message_text(
            f"{DESC_AR}\n\nКоманды из чата Starvell. Несколько триггеров через <code>|</code>:\n"
            "<code>привет|здравствуйте</code>",
            call.message.chat.id,
            call.message.id,
            reply_markup=kb.commands_list(cardinal, offset),
        )
        bot.answer_callback_query(call.id)

    def ask_add(call: CallbackQuery) -> None:
        msg = bot.send_message(call.message.chat.id, "Название команды (или несколько через |):", reply_markup=kb.cancel())
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.ADD_CMD)
        bot.answer_callback_query(call.id)

    def add_command(message: Message) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        raw = (message.text or "").strip().lower().replace("\n", "")
        aliases = [part.strip() for part in raw.split("|") if part.strip()]
        if not aliases:
            bot.send_message(message.chat.id, "Пустая команда.")
            return
        for alias in aliases:
            if alias in cardinal.AR_CFG.sections() or any(
                alias in [x.strip() for x in section.split("|")] for section in cardinal.AR_CFG.sections()
            ):
                bot.send_message(message.chat.id, f"Команда <code>{h(alias)}</code> уже есть.")
                return
        cardinal.AR_CFG.add_section(raw)
        cardinal.AR_CFG.set(raw, "response", "Этой команде нужно задать текст ответа.")
        cardinal.AR_CFG.set(raw, "enabled", "1")
        cardinal.save_config(cardinal.AR_CFG, "configs/auto_response.cfg")
        index = len(cardinal.AR_CFG.sections()) - 1
        bot.send_message(
            message.chat.id,
            f"✅ Команда <code>{h(raw)}</code> добавлена. Теперь задайте текст ответа.",
            reply_markup=kb._rows([[("⚙️ Настроить", f"{cbt.EDIT_CMD}:{index}:0"), ("⬅️ Список", f"{cbt.CMD_LIST}:0")]]),
        )

    def open_edit(call: CallbackQuery) -> None:
        _, index_s, offset_s = (call.data.split(":") + ["0", "0"])[:3]
        index, offset = int(index_s), int(offset_s)
        if not _exists(index, call.message):
            bot.answer_callback_query(call.id)
            return
        section = cardinal.AR_CFG.sections()[index]
        obj = cardinal.AR_CFG[section]
        enabled = obj.get("enabled", "1") not in {"0", "false"}
        bot.edit_message_text(
            f"<b>[{h(section)}]</b>\n\n"
            f"Состояние: <b>{'вкл' if enabled else 'выкл'}</b>\n"
            f"Ответ:\n<code>{h(obj.get('response', ''))}</code>",
            call.message.chat.id,
            call.message.id,
            reply_markup=kb.edit_command(index, offset, enabled),
        )
        bot.answer_callback_query(call.id)

    def ask_response(call: CallbackQuery) -> None:
        _, index_s, offset_s = (call.data.split(":") + ["0", "0"])[:3]
        msg = bot.send_message(call.message.chat.id, "Новый текст ответа:", reply_markup=kb.cancel())
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.EDIT_CMD_RESPONSE, {"index": int(index_s), "offset": int(offset_s)})
        bot.answer_callback_query(call.id)

    def save_response(message: Message) -> None:
        state = tg.get_state(message.chat.id, message.from_user.id) or {}
        data = state.get("data") or {}
        tg.clear_state(message.chat.id, message.from_user.id, True)
        index = int(data.get("index", -1))
        offset = int(data.get("offset", 0))
        if index < 0 or index >= len(cardinal.AR_CFG.sections()):
            bot.send_message(message.chat.id, "Команда не найдена.")
            return
        section = cardinal.AR_CFG.sections()[index]
        cardinal.AR_CFG.set(section, "response", (message.text or "").strip())
        cardinal.save_config(cardinal.AR_CFG, "configs/auto_response.cfg")
        bot.send_message(
            message.chat.id,
            "✅ Текст ответа сохранён.",
            reply_markup=kb._rows([[("⬅️ К команде", f"{cbt.EDIT_CMD}:{index}:{offset}")]]),
        )

    def toggle(call: CallbackQuery) -> None:
        parts = call.data.split(":")
        index, offset = int(parts[2]), int(parts[3] if len(parts) > 3 else 0)
        if not _exists(index, call.message):
            bot.answer_callback_query(call.id)
            return
        section = cardinal.AR_CFG.sections()[index]
        enabled = cardinal.AR_CFG.get(section, "enabled", fallback="1") not in {"0", "false"}
        cardinal.AR_CFG.set(section, "enabled", "0" if enabled else "1")
        cardinal.save_config(cardinal.AR_CFG, "configs/auto_response.cfg")
        call.data = f"{cbt.EDIT_CMD}:{index}:{offset}"
        open_edit(call)

    def delete(call: CallbackQuery) -> None:
        _, index_s, offset_s = (call.data.split(":") + ["0", "0"])[:3]
        index, offset = int(index_s), int(offset_s)
        if not _exists(index, call.message):
            bot.answer_callback_query(call.id)
            return
        section = cardinal.AR_CFG.sections()[index]
        cardinal.AR_CFG.remove_section(section)
        cardinal.save_config(cardinal.AR_CFG, "configs/auto_response.cfg")
        call.data = f"{cbt.CMD_LIST}:{offset}"
        open_list(call)

    tg.cbq_handler(open_list, lambda c: c.data == cbt.CMD_LIST or c.data.startswith(f"{cbt.CMD_LIST}:"))
    tg.cbq_handler(ask_add, lambda c: c.data == cbt.ADD_CMD)
    tg.msg_handler(add_command, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.ADD_CMD))
    tg.cbq_handler(open_edit, lambda c: c.data.startswith(f"{cbt.EDIT_CMD}:"))
    tg.cbq_handler(ask_response, lambda c: c.data.startswith(f"{cbt.EDIT_CMD_RESPONSE}:"))
    tg.msg_handler(save_response, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.EDIT_CMD_RESPONSE))
    tg.cbq_handler(toggle, lambda c: c.data.startswith(f"{cbt.AUTO_RESPONSE}:cmd:"))
    tg.cbq_handler(delete, lambda c: c.data.startswith(f"{cbt.DEL_CMD}:"))
