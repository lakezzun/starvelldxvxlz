from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message

from tg_bot import cbt, keyboards as kb
from tg_bot.utils import h
from utils.config import ROOT

if TYPE_CHECKING:
    from core import App

PRODUCTS = ROOT / "storage" / "products"
DEFAULT_RESPONSE = "Спасибо за покупку!\n\nВаш товар:\n$product"


def init_auto_delivery_cp(cardinal: App) -> None:
    tg = cardinal.telegram
    if not tg:
        return
    bot = tg.bot
    PRODUCTS.mkdir(parents=True, exist_ok=True)

    def _exists(index: int, message: Message) -> bool:
        if 0 <= index < len(cardinal.AD_CFG.sections()):
            return True
        bot.edit_message_text(
            "Лот не найден. Обновите список.",
            message.chat.id,
            message.id,
            reply_markup=kb.ad_lots_list(cardinal, 0),
        )
        return False

    def open_list(call: CallbackQuery) -> None:
        offset = int(call.data.split(":")[1]) if ":" in call.data else 0
        bot.edit_message_text(
            "<b>Автовыдача</b>\n\nСекция — <code>publicId</code> лота Starvell или его название.\n"
            "В тексте ответа можно <code>$product</code> — строка из товарного файла.",
            call.message.chat.id,
            call.message.id,
            reply_markup=kb.ad_lots_list(cardinal, offset),
        )
        bot.answer_callback_query(call.id)

    def ask_add(call: CallbackQuery) -> None:
        msg = bot.send_message(
            call.message.chat.id,
            "Пришлите <code>publicId</code> или точное название лота Starvell.",
            reply_markup=kb.cancel(),
        )
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.ADD_AD_LOT)
        bot.answer_callback_query(call.id)

    def add_lot(message: Message) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        lot = (message.text or "").strip()
        if not lot:
            bot.send_message(message.chat.id, "Пустое имя лота.")
            return
        if cardinal.AD_CFG.has_section(lot):
            bot.send_message(message.chat.id, "Этот лот уже в автовыдаче.")
            return
        cardinal.AD_CFG.add_section(lot)
        cardinal.AD_CFG.set(lot, "response", DEFAULT_RESPONSE)
        cardinal.AD_CFG.set(lot, "productsFileName", "")
        cardinal.save_config(cardinal.AD_CFG, "configs/auto_delivery.cfg")
        index = len(cardinal.AD_CFG.sections()) - 1
        bot.send_message(
            message.chat.id,
            f"✅ Лот <code>{h(lot)}</code> привязан.",
            reply_markup=kb._rows([[("⚙️ Настроить", f"{cbt.EDIT_AD}:{index}:0"), ("⬅️ Список", f"{cbt.AD_LOTS}:0")]]),
        )

    def open_edit(call: CallbackQuery) -> None:
        parts = call.data.split(":")
        index, offset = int(parts[1]), int(parts[2] if len(parts) > 2 else 0)
        if not _exists(index, call.message):
            bot.answer_callback_query(call.id)
            return
        section = cardinal.AD_CFG.sections()[index]
        obj = cardinal.AD_CFG[section]
        bot.edit_message_text(
            f"<b>[{h(section)}]</b>\n\n"
            f"Файл товаров: <code>{h(obj.get('productsFileName') or 'нет')}</code>\n\n"
            f"Ответ:\n<code>{h(obj.get('response', ''))}</code>",
            call.message.chat.id,
            call.message.id,
            reply_markup=kb.edit_ad_lot(index, offset),
        )
        bot.answer_callback_query(call.id)

    def ask_response(call: CallbackQuery) -> None:
        parts = call.data.split(":")
        msg = bot.send_message(
            call.message.chat.id,
            "Новый текст автовыдачи. Используйте <code>$product</code> для товара из файла.",
            reply_markup=kb.cancel(),
        )
        tg.set_state(
            call.message.chat.id,
            msg.id,
            call.from_user.id,
            cbt.EDIT_AD_RESP,
            {"index": int(parts[1]), "offset": int(parts[2] if len(parts) > 2 else 0)},
        )
        bot.answer_callback_query(call.id)

    def save_response(message: Message) -> None:
        state = tg.get_state(message.chat.id, message.from_user.id) or {}
        data = state.get("data") or {}
        tg.clear_state(message.chat.id, message.from_user.id, True)
        index = int(data.get("index", -1))
        offset = int(data.get("offset", 0))
        if index < 0 or index >= len(cardinal.AD_CFG.sections()):
            bot.send_message(message.chat.id, "Лот не найден.")
            return
        section = cardinal.AD_CFG.sections()[index]
        cardinal.AD_CFG.set(section, "response", (message.text or "").strip())
        cardinal.save_config(cardinal.AD_CFG, "configs/auto_delivery.cfg")
        bot.send_message(
            message.chat.id,
            "✅ Текст автовыдачи сохранён.",
            reply_markup=kb._rows([[("⬅️ К лоту", f"{cbt.EDIT_AD}:{index}:{offset}")]]),
        )

    def ask_file(call: CallbackQuery) -> None:
        parts = call.data.split(":")
        files = sorted(p.name for p in PRODUCTS.glob("*") if p.is_file())
        listing = "\n".join(f"• <code>{h(name)}</code>" for name in files[:40]) or "Пока нет файлов."
        msg = bot.send_message(
            call.message.chat.id,
            f"Имя файла из <code>storage/products/</code> или «-» чтобы отвязать.\n\n{listing}",
            reply_markup=kb.cancel(),
        )
        tg.set_state(
            call.message.chat.id,
            msg.id,
            call.from_user.id,
            cbt.AD_SET_FILE,
            {"index": int(parts[1]), "offset": int(parts[2] if len(parts) > 2 else 0)},
        )
        bot.answer_callback_query(call.id)

    def save_file(message: Message) -> None:
        state = tg.get_state(message.chat.id, message.from_user.id) or {}
        data = state.get("data") or {}
        tg.clear_state(message.chat.id, message.from_user.id, True)
        index = int(data.get("index", -1))
        offset = int(data.get("offset", 0))
        if index < 0 or index >= len(cardinal.AD_CFG.sections()):
            bot.send_message(message.chat.id, "Лот не найден.")
            return
        name = (message.text or "").strip()
        if name == "-":
            name = ""
        section = cardinal.AD_CFG.sections()[index]
        cardinal.AD_CFG.set(section, "productsFileName", name)
        cardinal.save_config(cardinal.AD_CFG, "configs/auto_delivery.cfg")
        bot.send_message(
            message.chat.id,
            "✅ Файл товаров сохранён.",
            reply_markup=kb._rows([[("⬅️ К лоту", f"{cbt.EDIT_AD}:{index}:{offset}")]]),
        )

    def delete(call: CallbackQuery) -> None:
        parts = call.data.split(":")
        index, offset = int(parts[1]), int(parts[2] if len(parts) > 2 else 0)
        if not _exists(index, call.message):
            bot.answer_callback_query(call.id)
            return
        section = cardinal.AD_CFG.sections()[index]
        cardinal.AD_CFG.remove_section(section)
        cardinal.save_config(cardinal.AD_CFG, "configs/auto_delivery.cfg")
        call.data = f"{cbt.AD_LOTS}:{offset}"
        open_list(call)

    def open_products(call: CallbackQuery) -> None:
        offset = int(call.data.split(":")[1]) if ":" in call.data else 0
        files = sorted(p.name for p in PRODUCTS.glob("*") if p.is_file())
        listing = "\n".join(f"• <code>{h(name)}</code>" for name in files[offset : offset + 20]) or "Файлов нет."
        bot.edit_message_text(
            f"<b>Товарные файлы</b>\n<code>storage/products/</code>\n\n{listing}",
            call.message.chat.id,
            call.message.id,
            reply_markup=kb.products_files(offset, len(files)),
        )
        bot.answer_callback_query(call.id)

    def ask_upload(call: CallbackQuery) -> None:
        msg = bot.send_message(call.message.chat.id, "Пришлите текстовый файл товаров (по одной позиции на строку).", reply_markup=kb.cancel())
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.UPLOAD_PRODUCTS)
        bot.answer_callback_query(call.id)

    def save_upload(message: Message) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        doc = message.document
        if not doc:
            bot.send_message(message.chat.id, "Нужен файл.")
            return
        info = bot.get_file(doc.file_id)
        raw = bot.download_file(info.file_path)
        name = Path(doc.file_name or "products.txt").name
        dest = PRODUCTS / name
        dest.write_bytes(raw)
        bot.send_message(message.chat.id, f"✅ Файл <code>{h(name)}</code> сохранён в storage/products/.")

    def ask_create(call: CallbackQuery) -> None:
        msg = bot.send_message(call.message.chat.id, "Имя нового файла, например <code>stars.txt</code>:", reply_markup=kb.cancel())
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.CREATE_PRODUCTS)
        bot.answer_callback_query(call.id)

    def create_file(message: Message) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        name = Path((message.text or "").strip()).name
        if not name:
            bot.send_message(message.chat.id, "Пустое имя.")
            return
        dest = PRODUCTS / name
        if not dest.exists():
            dest.write_text("", encoding="utf-8")
        bot.send_message(message.chat.id, f"✅ Файл <code>{h(name)}</code> готов. Загрузите в него товары документом или правьте на диске.")

    tg.cbq_handler(open_list, lambda c: c.data == cbt.AD_LOTS or c.data.startswith(f"{cbt.AD_LOTS}:"))
    tg.cbq_handler(ask_add, lambda c: c.data == cbt.ADD_AD_LOT)
    tg.msg_handler(add_lot, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.ADD_AD_LOT))
    tg.cbq_handler(open_edit, lambda c: c.data.startswith(f"{cbt.EDIT_AD}:"))
    tg.cbq_handler(ask_response, lambda c: c.data.startswith(f"{cbt.EDIT_AD_RESP}:"))
    tg.msg_handler(save_response, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.EDIT_AD_RESP))
    tg.cbq_handler(ask_file, lambda c: c.data.startswith(f"{cbt.AD_SET_FILE}:"))
    tg.msg_handler(save_file, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.AD_SET_FILE))
    tg.cbq_handler(delete, lambda c: c.data.startswith(f"{cbt.AD_DEL}:"))
    tg.cbq_handler(open_products, lambda c: c.data == cbt.PRODUCTS_LIST or c.data.startswith(f"{cbt.PRODUCTS_LIST}:"))
    tg.cbq_handler(ask_upload, lambda c: c.data == cbt.UPLOAD_PRODUCTS)
    tg.file_handler(cbt.UPLOAD_PRODUCTS, save_upload)
    tg.cbq_handler(ask_create, lambda c: c.data == cbt.CREATE_PRODUCTS)
    tg.msg_handler(create_file, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.CREATE_PRODUCTS))
