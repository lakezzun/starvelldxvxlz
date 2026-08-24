from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message

from tg_bot import cbt, keyboards as kb
from tg_bot.utils import h
from utils.brand import DESC_CFG
from utils.config import MAIN_CFG_PATH, ROOT

if TYPE_CHECKING:
    from core import App

CONFIG_MAP = {
    "ar": ROOT / "configs" / "auto_response.cfg",
    "ad": ROOT / "configs" / "auto_delivery.cfg",
}


def _redact_main() -> Path:
    raw = MAIN_CFG_PATH.read_text(encoding="utf-8") if MAIN_CFG_PATH.exists() else ""
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("session_cookie") or stripped.startswith("password") or stripped.startswith("token"):
            key = stripped.split("=", 1)[0].strip()
            lines.append(f"{key} = ***")
        else:
            lines.append(line)
    tmp = ROOT / "storage" / "cache" / "_main.redacted.cfg"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp


def init_config_loader_cp(cardinal: App) -> None:
    tg = cardinal.telegram
    if not tg:
        return
    bot = tg.bot

    def open_loader(call: CallbackQuery) -> None:
        bot.edit_message_text(
            DESC_CFG,
            call.message.chat.id,
            call.message.id,
            reply_markup=kb.configs(),
        )
        bot.answer_callback_query(call.id)

    def download(call: CallbackQuery) -> None:
        kind = call.data.split(":", 1)[1]
        bot.answer_callback_query(call.id)
        back = kb._rows([[("⬅️ Назад", cbt.CONFIGS)]])
        if kind == "main":
            path = _redact_main()
            caption = "configs/_main.cfg (секреты скрыты)"
        else:
            path = CONFIG_MAP.get(kind)
            caption = str(path.relative_to(ROOT)) if path else ""
            if not path or not path.exists() or not path.read_text(encoding="utf-8").strip():
                bot.send_message(call.message.chat.id, "Файл пуст или не найден.", reply_markup=back)
                return
        with open(path, "rb") as handle:
            bot.send_document(call.message.chat.id, handle, caption=caption, reply_markup=back)

    def ask_upload(call: CallbackQuery) -> None:
        kind = "ar" if call.data == cbt.UPLOAD_AR_CFG else "ad"
        label = "auto_response.cfg" if kind == "ar" else "auto_delivery.cfg"
        msg = bot.send_message(call.message.chat.id, f"Пришлите файл <code>{label}</code>.", reply_markup=kb.cancel())
        state = cbt.UPLOAD_AR_CFG if kind == "ar" else cbt.UPLOAD_AD_CFG
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, state)
        bot.answer_callback_query(call.id)

    def save_upload(message: Message, kind: str) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        doc = message.document
        if not doc:
            bot.send_message(message.chat.id, "Нужен файл.")
            return
        info = bot.get_file(doc.file_id)
        raw = bot.download_file(info.file_path)
        path = CONFIG_MAP[kind]
        path.write_bytes(raw)
        cardinal._load_feature_configs()
        bot.send_message(message.chat.id, f"✅ {h(path.name)} загружен и применён.")

    tg.cbq_handler(open_loader, lambda c: c.data == cbt.CONFIGS)
    tg.cbq_handler(download, lambda c: c.data.startswith(f"{cbt.DOWNLOAD_CFG}:"))
    tg.cbq_handler(ask_upload, lambda c: c.data in {cbt.UPLOAD_AR_CFG, cbt.UPLOAD_AD_CFG})
    tg.file_handler(cbt.UPLOAD_AR_CFG, lambda m: save_upload(m, "ar"))
    tg.file_handler(cbt.UPLOAD_AD_CFG, lambda m: save_upload(m, "ad"))
