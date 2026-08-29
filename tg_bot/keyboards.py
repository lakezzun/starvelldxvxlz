from __future__ import annotations

from typing import TYPE_CHECKING

from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from tg_bot import cbt
from tg_bot.utils import NotificationTypes

if TYPE_CHECKING:
    from core import App


def _rows(rows: list[list[tuple[str, str]]]) -> K:
    kb = K()
    for row in rows:
        kb.row(*[B(text, callback_data=data) for text, data in row])
    return kb


def cancel() -> K:
    return _rows([[("❌ Отмена", cbt.CLEAR_STATE)]])


def main() -> K:
    return _rows(
        [
            [("🗣️ Язык", cbt.LANGUAGE)],
            [("⚙️ Глобальные переключатели", cbt.GLOBALS)],
            [("🔔 Настройки уведомлений", cbt.NOTIF)],
            [("🤖 Настройки автоответа", cbt.AUTO_RESPONSE)],
            [("📦 Настройки авто-выдачи", cbt.AUTO_DELIVERY)],
            [("🧩 Плагины", f"{cbt.PLUGINS}:0")],
            [("▶️ Далее", cbt.MAIN2)],
        ]
    )


def main2() -> K:
    return _rows(
        [
            [("👋 Приветственное сообщение", cbt.GREETINGS)],
            [("👤 Аккаунт Starvell", cbt.STARVELL)],
            [("📁 Конфиги", cbt.CONFIGS)],
            [("👥 Авторизованные пользователи", f"{cbt.USERS}:0")],
            [("🌐 Прокси", cbt.PROXY)],
            [("📈 Автоподнятие лотов", cbt.AUTO_RAISE)],
            [("🔄 Обновление", cbt.UPDATE)],
            [("📊 Статистика", cbt.STATS)],
            [("◀️ Назад", cbt.MAIN)],
        ]
    )


def language() -> K:
    return _rows(
        [
            [("🇷🇺 Русский ✓", cbt.EMPTY)],
            [("◀️ Назад", cbt.MAIN)],
        ]
    )


def global_switches(cardinal: App) -> K:
    def flag(section: str, key: str, default: str) -> str:
        from utils.config import cfg_get

        on = cfg_get(cardinal.cfg, section, key, default) not in {"0", "false", "no"}
        return "🟢" if on else "🔴"

    return _rows(
        [
            [(f"{flag('AutoResponse', 'enabled', '1')} Автоответчик", f"{cbt.TOGGLE_GS}:ar")],
            [(f"{flag('AutoDelivery', 'enabled', '1')} Авто-выдача", f"{cbt.TOGGLE_GS}:ad")],
            [(f"{flag('AutoRaise', 'enabled', '1')} Автоподнятие", f"{cbt.TOGGLE_GS}:ab")],
            [(f"{flag('Greetings', 'enabled', '0')} Приветствие", f"{cbt.TOGGLE_GS}:gr")],
            [("◀️ Назад", cbt.MAIN)],
        ]
    )


def notifications(tg, chat_id: int) -> K:
    def mark(kind: str, title: str) -> str:
        on = tg.is_notification_enabled(chat_id, kind)
        return f"{'🔔' if on else '🔕'} {title}"

    return _rows(
        [
            [(mark(NotificationTypes.new_message, "Новое сообщение"), f"{cbt.SWITCH_N}:{NotificationTypes.new_message}")],
            [(mark(NotificationTypes.new_order, "Новый заказ"), f"{cbt.SWITCH_N}:{NotificationTypes.new_order}")],
            [(mark(NotificationTypes.bot_start, "Запуск бота"), f"{cbt.SWITCH_N}:{NotificationTypes.bot_start}")],
            [(mark(NotificationTypes.lots_raise, "Автоподнятие лотов"), f"{cbt.SWITCH_N}:{NotificationTypes.lots_raise}")],
            [("◀️ Назад", cbt.MAIN)],
        ]
    )


def starvell(cardinal: App) -> K:
    return _rows(
        [
            [("🍪 Сменить cookie", cbt.SET_COOKIE)],
            [("⏱ Интервалы опроса", cbt.SET_INTERVAL)],
            [("👤 Профиль", cbt.PROFILE)],
            [("◀️ Назад", cbt.MAIN2)],
        ]
    )


def plugins_list(cardinal: App, offset: int = 0) -> K:
    items = list(cardinal.plugins.values())
    kb = K()
    chunk = items[offset : offset + 8]
    for plugin in chunk:
        flag = "🟢" if plugin.enabled else "🔴"
        kb.add(B(f"{flag} {plugin.name} v{plugin.version}", callback_data=f"{cbt.EDIT_PLUGIN}:{plugin.uuid}:{offset}"))
    nav = []
    if offset > 0:
        nav.append(B("⬅️", callback_data=f"{cbt.PLUGINS}:{max(0, offset - 8)}"))
    if offset + 8 < len(items):
        nav.append(B("➡️", callback_data=f"{cbt.PLUGINS}:{offset + 8}"))
    if nav:
        kb.row(*nav)
    kb.add(B("➕ Добавить плагин", callback_data=f"{cbt.UPLOAD_PLUGIN}:{offset}"))
    kb.add(B("◀️ Назад", callback_data=cbt.MAIN))
    return kb


def edit_plugin(cardinal: App, uuid: str, offset: int, confirm_delete: bool = False) -> K:
    plugin = cardinal.plugins[uuid]
    kb = K()
    kb.add(B("🔴 Выключить" if plugin.enabled else "🟢 Включить", callback_data=f"{cbt.TOGGLE_PLUGIN}:{uuid}:{offset}"))
    if getattr(plugin, "settings_page", False):
        kb.add(B("⚙️ Настройки", callback_data=f"{cbt.PLUGIN_SETTINGS}:{uuid}:{offset}"))
    kb.add(B("📟 Команды", callback_data=f"{cbt.PLUGIN_COMMANDS}:{uuid}:{offset}"))
    if confirm_delete:
        kb.row(
            B("✅ Удалить", callback_data=f"{cbt.CONFIRM_DELETE_PLUGIN}:{uuid}:{offset}"),
            B("❌ Нет", callback_data=f"{cbt.CANCEL_DELETE_PLUGIN}:{uuid}:{offset}"),
        )
    else:
        kb.add(B("🗑 Удалить", callback_data=f"{cbt.DELETE_PLUGIN}:{uuid}:{offset}"))
    kb.add(B("⬅️ К списку", callback_data=f"{cbt.PLUGINS}:{offset}"))
    return kb


def users_list(cardinal: App, offset: int = 0) -> K:
    users = list((cardinal.telegram.authorized_users if cardinal.telegram else {}).keys())
    kb = K()
    chunk = users[offset : offset + 8]
    for uid in chunk:
        kb.add(B(f"👤 {uid}", callback_data=f"{cbt.KICK_USER}:{uid}:{offset}"))
    kb.add(B("◀️ Назад", callback_data=cbt.MAIN2))
    return kb


def new_message(chat_id: str) -> K:
    return _rows([[("✉️ Ответить", f"{cbt.SEND_SV}:{chat_id}")]])


def new_order(order_id: str, chat_id: str) -> K:
    rows = []
    if chat_id:
        rows.append([("✉️ Написать", f"{cbt.SEND_SV}:{chat_id}")])
    rows.append([("💸 Возврат", f"{cbt.REFUND}:{order_id}")])
    return _rows(rows)


def confirm_refund(order_id: str) -> K:
    return _rows(
        [
            [("✅ Вернуть", f"{cbt.REFUND_OK}:{order_id}"), ("❌ Отмена", f"{cbt.REFUND_NO}:{order_id}")],
        ]
    )


def configs() -> K:
    return _rows(
        [
            [("⬇️ _main.cfg", f"{cbt.DOWNLOAD_CFG}:main")],
            [("⬇️ auto_response.cfg", f"{cbt.DOWNLOAD_CFG}:ar")],
            [("⬇️ auto_delivery.cfg", f"{cbt.DOWNLOAD_CFG}:ad")],
            [("⬆️ Залить автоответ", cbt.UPLOAD_AR_CFG)],
            [("⬆️ Залить автовыдачу", cbt.UPLOAD_AD_CFG)],
            [("◀️ Назад", cbt.MAIN2)],
        ]
    )


def commands_list(cardinal: App, offset: int = 0) -> K:
    sections = cardinal.AR_CFG.sections()
    kb = K()
    for index, name in enumerate(sections[offset : offset + 8], start=offset):
        flag = "🟢" if cardinal.AR_CFG.get(name, "enabled", fallback="1") not in {"0", "false"} else "🔴"
        kb.add(B(f"{flag} {name}", callback_data=f"{cbt.EDIT_CMD}:{index}:{offset}"))
    nav = []
    if offset > 0:
        nav.append(B("⬅️", callback_data=f"{cbt.CMD_LIST}:{max(0, offset - 8)}"))
    if offset + 8 < len(sections):
        nav.append(B("➡️", callback_data=f"{cbt.CMD_LIST}:{offset + 8}"))
    if nav:
        kb.row(*nav)
    kb.add(B("➕ Добавить команду", callback_data=cbt.ADD_CMD))
    kb.add(B("◀️ Назад", callback_data=cbt.AUTO_RESPONSE))
    return kb


def edit_command(index: int, offset: int, enabled: bool) -> K:
    return _rows(
        [
            [(("🔴 Выключить" if enabled else "🟢 Включить"), f"{cbt.AUTO_RESPONSE}:cmd:{index}:{offset}")],
            [("✏️ Текст ответа", f"{cbt.EDIT_CMD_RESPONSE}:{index}:{offset}")],
            [("🗑 Удалить", f"{cbt.DEL_CMD}:{index}:{offset}")],
            [("◀️ К списку", f"{cbt.CMD_LIST}:{offset}")],
        ]
    )


def ad_lots_list(cardinal: App, offset: int = 0) -> K:
    sections = cardinal.AD_CFG.sections()
    kb = K()
    for index, name in enumerate(sections[offset : offset + 8], start=offset):
        kb.add(B(name[:60], callback_data=f"{cbt.EDIT_AD}:{index}:{offset}"))
    nav = []
    if offset > 0:
        nav.append(B("⬅️", callback_data=f"{cbt.AD_LOTS}:{max(0, offset - 8)}"))
    if offset + 8 < len(sections):
        nav.append(B("➡️", callback_data=f"{cbt.AD_LOTS}:{offset + 8}"))
    if nav:
        kb.row(*nav)
    kb.add(B("➕ Добавить лот", callback_data=cbt.ADD_AD_LOT))
    kb.add(B("📦 Товарные файлы", callback_data=f"{cbt.PRODUCTS_LIST}:0"))
    kb.add(B("◀️ Назад", callback_data=cbt.AUTO_DELIVERY))
    return kb


def edit_ad_lot(index: int, offset: int) -> K:
    return _rows(
        [
            [("✏️ Текст выдачи", f"{cbt.EDIT_AD_RESP}:{index}:{offset}")],
            [("📄 Файл товаров", f"{cbt.AD_SET_FILE}:{index}:{offset}")],
            [("🗑 Удалить", f"{cbt.AD_DEL}:{index}:{offset}")],
            [("◀️ К списку", f"{cbt.AD_LOTS}:{offset}")],
        ]
    )


def products_files(offset: int, total: int) -> K:
    rows = [
        [("⬆️ Загрузить файл", cbt.UPLOAD_PRODUCTS), ("➕ Создать", cbt.CREATE_PRODUCTS)],
    ]
    nav = []
    if offset > 0:
        nav.append(("⬅️", f"{cbt.PRODUCTS_LIST}:{max(0, offset - 20)}"))
    if offset + 20 < total:
        nav.append(("➡️", f"{cbt.PRODUCTS_LIST}:{offset + 20}"))
    if nav:
        rows.append(nav)
    rows.append([("◀️ К лотам", f"{cbt.AD_LOTS}:0")])
    return _rows(rows)
