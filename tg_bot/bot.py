from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable

import telebot
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import SSLError, Timeout
from telebot.apihelper import ApiTelegramException
from telebot.types import BotCommand, CallbackQuery, Message
from telebot.types import InlineKeyboardMarkup as K

from tg_bot.utils import NotificationTypes, check_password, h
from utils.brand import ACCESS_DENIED, ACCESS_GRANTED, BOT_STARTED
from utils.config import cfg_get
from utils.storage import load_authorized_users, load_notification_settings, save_authorized_users, save_notification_settings

if TYPE_CHECKING:
    from core import App

logger = logging.getLogger("SVC.telegram")
telebot.apihelper.ENABLE_MIDDLEWARE = False

_NETWORK = (SSLError, RequestsConnectionError, Timeout, ConnectionError, TimeoutError)


def _configure_telegram_http(cfg) -> None:
    try:
        telebot.apihelper.proxy = None
        session = telebot.apihelper._get_req_session()
        session.trust_env = False
        session.proxies = {}
        tg_proxy = cfg_get(cfg, "Telegram", "proxy")
        if tg_proxy:
            session.proxies = {"http": tg_proxy, "https": tg_proxy}
            telebot.apihelper.proxy = {"http": tg_proxy, "https": tg_proxy}
    except Exception:
        logger.warning("Не удалось настроить HTTP для Telegram, иду как есть.")


def _retry(fn, *args, attempts: int = 3, **kwargs):
    last: Exception | None = None
    for index in range(attempts):
        try:
            return fn(*args, **kwargs)
        except _NETWORK as exc:
            last = exc
            time.sleep(0.4 * (index + 1))
    if last:
        raise last
    raise RuntimeError("telegram retry")


class TGBot:
    def __init__(self, cardinal: App) -> None:
        self.cardinal = cardinal
        token = cfg_get(cardinal.cfg, "Telegram", "token")
        _configure_telegram_http(cardinal.cfg)
        self.bot = telebot.TeleBot(token, parse_mode="HTML", allow_sending_without_reply=True, num_threads=5)
        self.attempts: dict[int, int] = {}
        self.user_states: dict[int, dict[int, dict[str, Any]]] = {}
        self.file_handlers: dict[str, Callable] = {}
        self.authorized_users = {int(k): v for k, v in load_authorized_users().items()}
        self.notification_settings = load_notification_settings()
        self.commands = {
            "menu": "открыть настройки",
            "profile": "профиль Starvell",
            "logs": "текущий лог",
            "upload_plugin": "загрузить плагин",
            "update": "обновить с GitHub",
            "restart": "перезапустить бота",
            "about": "о боте",
        }

    def get_state(self, chat_id: int, user_id: int) -> dict[str, Any] | None:
        return self.user_states.get(chat_id, {}).get(user_id)

    def set_state(self, chat_id: int, message_id: int, user_id: int, state: str, data: dict | None = None) -> None:
        self.user_states.setdefault(chat_id, {})[user_id] = {"state": state, "mid": message_id, "data": data or {}}

    def clear_state(self, chat_id: int, user_id: int, del_msg: bool = False) -> None:
        state = self.user_states.get(chat_id, {}).pop(user_id, None)
        if del_msg and state and state.get("mid"):
            try:
                self.bot.delete_message(chat_id, state["mid"])
            except Exception:
                pass

    def check_state(self, chat_id: int, user_id: int, state: str) -> bool:
        current = self.get_state(chat_id, user_id)
        return bool(current and current.get("state") == state)

    def is_notification_enabled(self, chat_id: int | str, kind: str) -> bool:
        try:
            return bool(self.notification_settings[str(chat_id)][kind])
        except KeyError:
            return kind in {NotificationTypes.critical, NotificationTypes.bot_start, NotificationTypes.new_order}

    def toggle_notification(self, chat_id: int, kind: str) -> bool:
        key = str(chat_id)
        self.notification_settings.setdefault(key, {})
        self.notification_settings[key][kind] = not self.is_notification_enabled(chat_id, kind)
        save_notification_settings(self.notification_settings)
        return bool(self.notification_settings[key][kind])

    def msg_handler(self, handler, **kwargs) -> None:
        bot = self.bot

        @bot.message_handler(**kwargs)
        def run(message: Message):
            try:
                handler(message)
            except _NETWORK:
                logger.warning("Telegram временно недоступен (сообщение). Повторю при следующем действии.")
            except Exception:
                logger.exception("Ошибка message handler")

    def cbq_handler(self, handler, func, **kwargs) -> None:
        bot = self.bot

        @bot.callback_query_handler(func=func, **kwargs)
        def run(call: CallbackQuery):
            try:
                handler(call)
            except _NETWORK:
                logger.warning("Telegram временно недоступен (кнопка). Нажми ещё раз.")
                try:
                    bot.answer_callback_query(call.id, "Сеть моргнула, нажми ещё раз.", show_alert=False)
                except Exception:
                    pass
            except Exception:
                logger.exception("Ошибка callback handler")
                try:
                    bot.answer_callback_query(call.id)
                except Exception:
                    pass

    def file_handler(self, state: str, handler) -> None:
        self.file_handlers[state] = handler

    def send_notification(self, text: str, keyboard: K | None = None, notification_type: str = NotificationTypes.other) -> None:
        kwargs: dict[str, Any] = {}
        if keyboard is not None:
            kwargs["reply_markup"] = keyboard
        dead: list[str] = []
        for chat_id in list(self.notification_settings):
            if notification_type != NotificationTypes.critical and not self.is_notification_enabled(chat_id, notification_type):
                continue
            try:
                _retry(self.bot.send_message, chat_id, text, **kwargs)
            except ApiTelegramException as exc:
                logger.warning("Не отправилось уведомление в %s: %s", chat_id, exc)
                if exc.error_code in {400, 403}:
                    dead.append(str(chat_id))
            except _NETWORK:
                logger.warning("Уведомление в %s не ушло из-за сети.", chat_id)
            except Exception:
                logger.exception("Уведомление в %s", chat_id)
        for chat_id in dead:
            self.notification_settings.pop(chat_id, None)
        if dead:
            save_notification_settings(self.notification_settings)

    def setup_commands(self) -> None:
        try:
            _retry(self.bot.set_my_commands, [BotCommand(name, desc) for name, desc in self.commands.items()])
        except Exception:
            logger.warning("Не удалось поставить список команд")

    def _ensure_chat_settings(self, chat_id: int) -> None:
        key = str(chat_id)
        if key not in self.notification_settings:
            self.notification_settings[key] = {
                NotificationTypes.new_order: 1,
                NotificationTypes.new_message: 1,
                NotificationTypes.bot_start: 1,
                NotificationTypes.critical: 1,
            }
            save_notification_settings(self.notification_settings)

    def _reg_admin(self, message: Message) -> None:
        if message.chat.type != "private" or message.text is None:
            return
        name = h(message.from_user.first_name or message.from_user.username or "друг")
        if message.text.startswith("/"):
            if message.text.split()[0].lower() in {"/start", "/menu", "/help"}:
                self.bot.send_message(message.chat.id, ACCESS_DENIED.format(name))
            return
        if self.attempts.get(message.from_user.id, 0) >= 5:
            return
        stored = self.cardinal.cfg.get("Telegram", "password", fallback="")
        if check_password(message.text, stored):
            self.authorized_users[message.from_user.id] = {"username": message.from_user.username or ""}
            save_authorized_users({str(k): v for k, v in self.authorized_users.items()})
            self._ensure_chat_settings(message.chat.id)
            self.bot.send_message(message.chat.id, ACCESS_GRANTED)
            logger.info("Доступ к панели: %s (%s)", message.from_user.username, message.from_user.id)
            self.send_notification(
                f"🔓 В панель вошёл @{h(message.from_user.username)} (<code>{message.from_user.id}</code>)",
                notification_type=NotificationTypes.critical,
            )
            return
        self.attempts[message.from_user.id] = self.attempts.get(message.from_user.id, 0) + 1
        self.bot.send_message(message.chat.id, "❌ Неверный пароль.")
        logger.warning("Неудачный вход в панель: %s (%s)", message.from_user.username, message.from_user.id)

    def _ignore_unauthorized(self, call: CallbackQuery) -> None:
        self.bot.answer_callback_query(call.id, "Нет доступа.", show_alert=True)

    def setup(self) -> None:
        self.msg_handler(self._reg_admin, func=lambda m: m.from_user.id not in self.authorized_users)
        self.cbq_handler(self._ignore_unauthorized, lambda c: c.from_user.id not in self.authorized_users)
        self.msg_handler(self._on_file, content_types=["document", "photo"], func=lambda m: m.from_user.id in self.authorized_users)

    def _on_file(self, message: Message) -> None:
        state = self.get_state(message.chat.id, message.from_user.id)
        if not state:
            return
        handler = self.file_handlers.get(state["state"])
        if handler:
            handler(message)

    def run(self) -> None:
        self.setup_commands()
        self.send_notification(
            BOT_STARTED.format(h(self.cardinal.version)),
            notification_type=NotificationTypes.bot_start,
        )
        fails = 0
        while True:
            try:
                _configure_telegram_http(self.cardinal.cfg)
                me = _retry(self.bot.get_me)
                logger.info("Telegram-бот @%s запущен.", me.username)
                self.bot.infinity_polling(logger_level=logging.ERROR, skip_pending=True, timeout=40, long_polling_timeout=40)
                fails = 0
            except _NETWORK:
                fails += 1
                logger.warning("Связь с Telegram пропала (%s). Пробую снова...", fails)
                time.sleep(min(30, 5 * fails))
            except Exception:
                fails += 1
                logger.exception("Telegram polling упал (%s)", fails)
                time.sleep(min(30, 5 * fails))
