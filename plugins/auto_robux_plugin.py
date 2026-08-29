from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from threading import Lock, RLock, Thread
from typing import TYPE_CHECKING, Any, Iterator, Literal, Union

try:
    import requests
except ModuleNotFoundError:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

try:
    import pydantic
    from pydantic import BaseModel, Field
except ImportError:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "pip", "install", "pydantic"])
    import pydantic
    from pydantic import BaseModel, Field

try:
    from pydantic import validator
except ImportError:  # pragma: no cover
    validator = None  # type: ignore[misc, assignment]


def _pydantic_v2() -> bool:
    ver = getattr(pydantic, "VERSION", None) or getattr(pydantic, "__version__", "1")
    raw = str(ver).split(".")[0]
    try:
        return int(raw) >= 2
    except ValueError:
        return False


_PD_V2 = _pydantic_v2()


def _next_action_field():
    if _PD_V2:
        from pydantic import field_validator

        return field_validator("next_action", mode="before")
    return validator("next_action", pre=True, allow_reuse=True)
from types import SimpleNamespace

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K
from tg_bot import cbt as CBT
from tg_bot.utils import h as _html_escape
from utils.config import ROOT

from starvell.events import NewMessageEvent, NewOrderEvent
from starvell.types import Message as SvMessage
from starvell.types import Order as SvOrder

if TYPE_CHECKING:
    from core import App as Cardinal


class _Utils:
    @staticmethod
    def escape(value: Any) -> str:
        return _html_escape(value)

    @staticmethod
    def add_navigation_buttons(
        keyboard: K,
        start_from: int,
        items_amount: int,
        extra_items: int,
        items_total: int,
        callback_text: str,
        extra: list | None = None,
    ) -> None:
        extra_part = ""
        if extra:
            extra_part = ":" + ":".join(str(item) for item in extra)
        arrows: list[B] = []
        if start_from > 0:
            arrows.append(
                B(
                    "◀️",
                    callback_data=f"{callback_text}:{start_from - items_amount}{extra_part}",
                )
            )
        if extra_items == items_amount and start_from + extra_items < items_total:
            arrows.append(
                B(
                    "▶️",
                    callback_data=f"{callback_text}:{start_from + extra_items}{extra_part}",
                )
            )
        if len(arrows) == 1:
            keyboard.add(arrows[0])
        elif len(arrows) > 1:
            keyboard.row(*arrows)


utils = _Utils()


class _Skb:
    @staticmethod
    def CLEAR_STATE_BTN() -> K:
        return K().row(B("❌ Отмена", callback_data=CBT.CLEAR_STATE))


skb = _Skb()


if _PD_V2:
    class _Model(BaseModel):
        model_config = {"extra": "allow"}
else:
    class _Model(BaseModel):
        class Config:
            extra = "allow"
            smart_union = True

        @classmethod
        def model_validate(cls, obj: Any):
            return cls.parse_obj(obj)

        def model_dump(self, **kwargs: Any) -> dict:
            return self.dict(**kwargs)

        def model_dump_json(self, **kwargs: Any) -> str:
            return self.json(**kwargs)


NAME = "Auto Robux Plugin"
VERSION = "1.0.4"
DESCRIPTION = "Автовыдача Robux через rbcode.net / swizzyer для StarvellDxvxlz"
UUID = "a6532f1d-07fc-46cc-97d3-7f97b7143894"
CREDITS = "@swizzyer, порт @dxvxlz"
SETTINGS_PAGE = True
SETTINGS_FILE_PATH = ROOT / "storage" / "auto_robux_plugin" / "settings.json"
MESSAGES_FILE_PATH = ROOT / "storage" / "auto_robux_plugin" / "messages.json"
DB_FILE_PATH = ROOT / "storage" / "auto_robux_plugin" / "storage.db"


class Logging:
    def __init__(
        self,
        logger_name: str = "SVC.auto_robux_plugin",
        logger_prefix: str = "[AUTO ROBUX PLUGIN]",
    ) -> None:
        self._logger_prefix = logger_prefix
        self._logger = logging.getLogger(logger_name)

    def log(self, level: int, message: str, **kwargs: Any) -> None:
        self._logger.log(
            level, f"$MAGENTA{self._logger_prefix}$RESET {message}", **kwargs
        )

    def debug(self, message: str, **kwargs: Any) -> None:
        self.log(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        self.log(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self.log(logging.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        self.log(logging.ERROR, message, **kwargs)


logger = Logging()


DEFAULT_REFUND_KEYWORDS = [
    "верни деньги",
    "верните деньги",
    "вернуть деньги",
    "верни средства",
    "верните средства",
    "хочу возврат",
    "нужен возврат",
    "сделай возврат",
    "сделайте возврат",
    "оформи возврат",
    "оформите возврат",
    "возврат средств",
    "возврат денег",
    "отмени заказ",
    "отмените заказ",
    "отмена заказа",
    "отменить заказ",
    "не нужен заказ",
    "не нужны робуксы",
    "передумал",
    "передумала",
    "ошибся аккаунтом",
    "ошибся почтой",
    "не тот аккаунт",
    "не та почта",
    "refund",
    "money back",
    "cancel order",
]


class Settings(_Model):
    if _PD_V2:
        model_config = {"extra": "ignore"}
    else:
        class Config(_Model.Config):
            extra = "ignore"

    on: bool = False
    api_key: str | None = None
    notify_success: list[int] = Field(default_factory=list)
    notify_failure: list[int] = Field(default_factory=list)
    warn_invalid_login: bool = False
    refund_on_passkey: bool = False
    ignore_russian_password: bool = False
    auto_recover_cancelled: bool = False
    use_managed_pool: bool = False
    refund_on_timeout: bool = False
    refund_timeout_minutes: int = 50
    refund_on_no_stock: bool = False
    refund_on_service_down: bool = False
    refund_on_roblox_blocked: bool = False
    refund_on_order_failed: bool = False
    refund_on_attempts: bool = False
    refund_max_attempts: int = 10
    refund_on_request: bool = False
    refund_request_keywords: list[str] = Field(
        default_factory=lambda: list(DEFAULT_REFUND_KEYWORDS)
    )
    push_approval_repeat_minutes: int = 0
    deadline_on: bool = False
    deadline_minutes: int = 60
    deadline_warn_on: bool = False
    deadline_warn_minutes: int = 25
    deadline_refund_on: bool = False
    deadline_refund_minutes: int = 45
    deadline_buyer_warn_on: bool = False
    deadline_buyer_warn_minutes: int = 5
    deadline_repeat_minutes: int = 5
    lot_bindings: dict = Field(default_factory=dict)
    lot_titles: dict = Field(default_factory=dict)

    def bind_lot(self, offer_id: str, title: str, robux: int) -> None:
        key = str(offer_id).strip()
        if not key:
            return
        self.lot_bindings[key] = int(robux)
        self.lot_titles[key] = (title or key).strip() or key
        self.save()

    def unbind_lot(self, offer_id: str) -> None:
        key = str(offer_id)
        self.lot_bindings.pop(key, None)
        self.lot_titles.pop(key, None)
        self.save()

    @classmethod
    def load(cls) -> "Settings":
        if not SETTINGS_FILE_PATH.exists():
            instance = cls()
            instance.save()
            return instance
        with open(SETTINGS_FILE_PATH, "r") as f:
            data = json.load(f)
            return cls.model_validate(data)

    def toggle_notify(self, field_name: str, chat_id: int) -> bool:
        ids = getattr(self, field_name)
        if chat_id in ids:
            ids.remove(chat_id)
            enabled = False
        else:
            ids.append(chat_id)
            enabled = True
        self.save()
        return enabled

    def save(self) -> None:
        SETTINGS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE_PATH, "w") as f:
            f.write(self.model_dump_json(indent=2))


settings = Settings.load()


@dataclass(frozen=True)
class MessageSpec:
    key: str
    category: str
    label: str
    default: str
    variables: tuple[str, ...] = ()


MESSAGE_SPECS: list[MessageSpec] = [
    MessageSpec(
        "greeting",
        "success",
        "Приветствие / запрос логина",
        "🤖 Привет! Спасибо за заказ на $total робуксов.\n\n"
        "→ Для выдачи пришли логин своего аккаунта Roblox.",
        ("$total", "$robux", "$quantity", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "order_queued",
        "success",
        "Заказ поставлен в очередь",
        "⏳ Заказ на $total робуксов принят и поставлен в очередь.\n"
        "Начнём, как только завершим текущий заказ.",
        ("$total", "$robux", "$quantity", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "ask_password",
        "success",
        "Запрос пароля",
        "🔑 Теперь пришли пароль от аккаунта Roblox.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "creating_order",
        "success",
        "Создание заказа",
        "⏳ Создаю заказ и подключаюсь к Roblox...",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "choose_one",
        "success",
        "Выбор одного варианта (2FA)",
        "$prompt\n$options\n\n→ Пришли номер нужного варианта.",
        ("$prompt", "$options"),
    ),
    MessageSpec(
        "choose_many",
        "success",
        "Выбор игр",
        "🔑 Roblox просит подтвердить аккаунт.\n"
        "Выбери $count игры из $options_count, в которые ты ИГРАЛ за последние 7 дней:"
        "\n\n$options\n\n"
        "→ Пришли РОВНО $count номера в любом формате: 1, 3, 5 или 1 3 5 или 135",
        ("$count", "$options_count", "$options"),
    ),
    MessageSpec(
        "input_authenticator",
        "success",
        "Запрос кода из приложения-аутентификатора",
        "🔐 Введи 6-значный код из приложения-аутентификатора Roblox.$extra",
        ("$extra",),
    ),
    MessageSpec(
        "input_email",
        "success",
        "Запрос кода с почты (почта известна)",
        "📧 Введи 6-значный код, отправленный на почту $hint.$extra",
        ("$hint", "$extra"),
    ),
    MessageSpec(
        "input_email_nohint",
        "success",
        "Запрос кода с почты (почта неизвестна)",
        "📧 Введи 6-значный код, отправленный на почту.$extra",
        ("$extra",),
    ),
    MessageSpec(
        "input_recovery",
        "success",
        "Запрос резервного кода",
        "🔑 Введи резервный код Roblox (8–16 символов).$extra",
        ("$extra",),
    ),
    MessageSpec(
        "input_retry_warning",
        "success",
        "Приписка о неподошедшем коде",
        "\n⚠️ Код не подошёл. Попытка $attempt из $max.",
        ("$attempt", "$max"),
    ),
    MessageSpec(
        "push_approval",
        "success",
        "Подтверждение входа в приложении",
        "📱 Перезайди в Roblox с мобильного устройства.\n"
        'В появившемся окне нажми кнопку "Approve".',
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "processing",
        "success",
        "Данные приняты, идёт обработка",
        "⏳ Данные приняты, проверяю аккаунт и завершаю заказ.\n"
        "Это может занять до нескольких минут — подожди, пожалуйста.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "processing_short",
        "success",
        "Короткое «идёт обработка»",
        "⏳ Идёт обработка, подожди немного...",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "success",
        "success",
        "Заказ выполнен",
        "🎉 Готово!\n\n• Аккаунт: $account\n• Начислено: $robux робуксов\n\n→ Если не трудно, оставь отзыв, упомянув, что заказ был выполнен автоматически 🙏",
        ("$account", "$robux", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "invalid_login",
        "failure",
        "Неверный логин (русский/пробелы)",
        "❌ Логин должен быть на английском и без пробелов.\n\n→ Пришли ещё раз.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "credentials_retry",
        "failure",
        "Roblox отклонил логин/пароль",
        "❌ Roblox отклонил логин или пароль.\n\n→ Пришли логин Roblox ещё раз.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "credentials_retry_incorrect",
        "failure",
        "Логин/пароль неверны (подтверждено)",
        "❌ Логин или пароль указаны неверно.\n\n"
        "→ Пришли правильные логин и пароль от аккаунта Roblox ещё раз.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "credentials_retry_short",
        "failure",
        "Краткий повторный запрос логина",
        "🔑 Пришли логин Roblox ещё раз.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "choose_one_invalid",
        "failure",
        "Неверный номер (выбор варианта)",
        "❌ Пришли номер от 1 до $n.",
        ("$n",),
    ),
    MessageSpec(
        "choose_many_invalid",
        "failure",
        "Неверный набор номеров (выбор игр)",
        "❌ Нужно прислать РОВНО $count номера от 1 до $n.\nНапример: $example",
        ("$count", "$n", "$example"),
    ),
    MessageSpec(
        "input_invalid_format",
        "failure",
        "Неверный формат ввода",
        "❌ Неверный формат. $prompt",
        ("$prompt",),
    ),
    MessageSpec(
        "respond_input_invalid",
        "failure",
        "Roblox отклонил введённые данные",
        "❌ Неверный ввод. Попробуй ещё раз.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "service_unavailable_password",
        "failure",
        "Сервис недоступен (повтор пароля)",
        "⚠️ Сервис недоступен.\n\n→ Пришли пароль ещё раз через минуту.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "service_unavailable_retry",
        "failure",
        "Сервис недоступен (повтор позже)",
        "⚠️ Сервис недоступен, попробуй ещё раз чуть позже.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "generic_error_later",
        "failure",
        "Произошла ошибка, попробуй позже",
        "⚠️ Произошла ошибка, попробуй позже.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "no_pack",
        "failure",
        "Не найден пак для заказа",
        "❌ Не нашёл у себя пак на $robux робуксов.\n"
        "Пожалуйста, дождись продавца — он вручную обработает твой заказ.",
        ("$robux", "$total", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "order_not_found",
        "failure",
        "Заказ не найден в сервисе",
        "❌ Заказ не найден.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "items_unavailable",
        "failure",
        "Товар недоступен в Roblox",
        "❌ Товар недоступен в Roblox.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "duplicate_order",
        "failure",
        "По аккаунту уже есть активный заказ",
        "❌ По этому аккаунту уже есть активный заказ.\nПопробуй позже.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "partial",
        "failure",
        "Заказ выдан частично",
        "⚠️ Выдано частично: $robux робуксов.\n"
        "Ожидай продавца — он вручную докинет тебе остаток.",
        ("$robux", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "relink_request",
        "failure",
        "Докид остатка: запрос логина",
        "⚠️ Зачислено пока только $robux из $total робуксов.\n"
        "Остаток докинем на этот же заказ — повторно оплачивать ничего не нужно.\n\n"
        "→ Пришли логин своего аккаунта Roblox ещё раз.",
        ("$robux", "$total", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "relink_unavailable",
        "failure",
        "Докид остатка невозможен",
        "⚠️ Зачислено $robux из $total робуксов.\n"
        "Дозачислить остаток автоматически не вышло — дождись продавца, "
        "он докинет остаток вручную.",
        ("$robux", "$total", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "manual_completion",
        "failure",
        "Требуется ручное завершение",
        "⚠️ Оплата прошла, но автоматическая выдача остановилась.\n"
        "Продавец уже уведомлён и завершит заказ вручную.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "charge_pending",
        "failure",
        "Ожидание ответа платёжной системы",
        "⏳ Уточняем статус оплаты у платёжной системы.\n"
        "Это может занять некоторое время — не создавай новый заказ, "
        "я напишу, как будет результат.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "recovery_in_progress",
        "failure",
        "Идёт автоматическое довыполнение",
        "⏳ Оплата прошла, дозачисляю робуксы автоматически.\n"
        "Ничего делать не нужно — напишу, как всё будет готово.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "pack_too_large",
        "failure",
        "Слишком крупный заказ для одной покупки",
        "❌ Такой объём нельзя выдать одной покупкой.\n"
        "Пожалуйста, дождись продавца — он обработает твой заказ вручную.",
        ("$robux", "$total", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_no_funded_account",
        "failure",
        "Перезапрос: нет доступного аккаунта выдачи",
        "⌛ Выдача временно недоступна — нет свободного аккаунта.\n\n"
        "→ Попробуй прислать логин аккаунта Roblox через 10 минут.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_concurrent_orders",
        "failure",
        "Перезапрос: слишком много активных заказов",
        "⌛ По этому аккаунту Roblox уже слишком много активных заказов.\n\n"
        "→ Дождись их завершения и пришли логин ещё раз.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "failed",
        "failure",
        "Заказ не выполнен",
        "❌ Не удалось выполнить заказ.\n• $reason",
        ("$reason", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "cancelled",
        "failure",
        "Заказ отменён",
        "❌ Заказ отменён.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "expired",
        "failure",
        "Время на подтверждение истекло",
        "⌛ Время на подтверждение истекло.\n"
        "Дождись продавца — он вручную обработает твой заказ.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "done_generic",
        "failure",
        "Заказ завершён (прочее)",
        "✅ Заказ завершён.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_default",
        "failure",
        "Перезапрос: логин/пароль не подошли",
        "❌ Логин или пароль не подошли несколько раз. Давай попробуем заново.\n\n"
        "→ Пришли логин своего аккаунта Roblox.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_cancelled",
        "failure",
        "Перезапрос: заказ отменён",
        "❌ Произошёл сбой, заказ был отменён, но я его восстановил, всё впорядке. Давай попробуем заново.\n\n"
        "→ Пришли логин своего аккаунта Roblox.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_timeout",
        "failure",
        "Перезапрос: верификация истекла",
        "❌ Верификация заняла слишком много времени и истекла.\n\n"
        "→ Давай попробуем заново — пришли логин своего аккаунта Roblox.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_internal_error",
        "failure",
        "Перезапрос: внутренняя ошибка",
        "❌ Не удалось выполнить заказ из-за технической ошибки на стороне сервиса."
        "\n\n→ Давай попробуем заново — пришли логин своего аккаунта Roblox.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_input_invalid",
        "failure",
        "Перезапрос: некорректные данные входа",
        "❌ Не удалось выполнить заказ.\n"
        "Проверь, можешь ли ты войти в свой аккаунт по паролю.\n\n"
        "→ Если войти не получается — сбрось пароль, а затем пришли логин своего "
        "аккаунта Roblox ещё раз.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_account_blocked",
        "failure",
        "Перезапрос: аккаунт временно заблокирован",
        "❌ Roblox временно заблокировал твой аккаунт — слишком много попыток ввода "
        "неправильного пароля.\n\n→ Чтобы разблокировать, сбрось пароль на аккаунте, "
        "а затем пришли логин своего аккаунта Roblox ещё раз.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_delivery_unavailable",
        "failure",
        "Перезапрос: выдача временно недоступна",
        "⌛ Выдача робуксов на этот аккаунт временно недоступна.\n"
        "Чаще всего это связано с тем, что пароль несколько раз вводили неправильно "
        "— нужно немного подождать (в среднем 1 час).\n\n"
        "→ Попробуй прислать логин аккаунта Roblox позже.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_captcha_unavailable",
        "failure",
        "Перезапрос: проблемы с капчей",
        "🛠 В сервисе временные проблемы с капчей.\n\n"
        "→ Попробуй прислать логин аккаунта Roblox через 15 минут.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_service_disabled",
        "failure",
        "Перезапрос: технические работы",
        "🛠 Авто-выдача сейчас на технических работах.\n\n"
        "→ Попробуй прислать логин аккаунта Roblox немного позже.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_insufficient_funds",
        "failure",
        "Перезапрос: временно нет наличия",
        "⌛ Наличие робуксов временно закончилось.\n\n"
        "→ Попробуй прислать логин аккаунта Roblox через 10 минут.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_method_not_supported",
        "failure",
        "Перезапрос: вход по ключу доступа (Passkey)",
        "❌ На твоём аккаунте включён вход по ключу доступа (Passkey), который мы не "
        "можем обработать.\n\n→ Убери вход по Passkey в настройках безопасности "
        "Roblox, а затем пришли логин аккаунта Roblox ещё раз.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "refund_passkey",
        "failure",
        "Возврат: вход по Passkey",
        "❌ Не удалось обработать заказ — на аккаунте включён вход по ключу доступа "
        "(Passkey).\nОформлен возврат средств.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "refund_timeout",
        "failure",
        "Возврат: покупатель долго не отвечал",
        "⌛ От тебя не было ответа больше $minutes минут, поэтому заказ отменён "
        "и оформлен возврат средств.\n\n"
        "→ Если робуксы всё ещё нужны — оформи заказ заново.",
        ("$minutes", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "refund_no_stock",
        "failure",
        "Возврат: нет наличия робуксов",
        "❌ Наличие робуксов закончилось, выдать заказ не получилось.\n"
        "Оформлен возврат средств.\n\n"
        "→ Попробуй оформить заказ позже.",
        ("$reason", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "refund_service_down",
        "failure",
        "Возврат: сервис выдачи недоступен",
        "🛠 Сервис авто-выдачи сейчас не работает, выполнить заказ не получилось.\n"
        "Оформлен возврат средств.\n\n"
        "→ Попробуй оформить заказ позже.",
        ("$reason", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "refund_roblox_blocked",
        "failure",
        "Возврат: вход в Roblox ограничен",
        "❌ Roblox ограничил вход на твой аккаунт, поэтому выдать робуксы не вышло.\n"
        "Оформлен возврат средств.\n\n"
        "→ Сбрось пароль на аккаунте и оформи заказ заново чуть позже.",
        ("$reason", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "refund_failed",
        "failure",
        "Возврат: заказ не удалось выполнить",
        "❌ Не удалось выполнить заказ автоматически.\nОформлен возврат средств.",
        ("$reason", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "refund_attempts",
        "failure",
        "Возврат: превышен лимит попыток ввода",
        "❌ Данные вводились неверно $attempts раз, дальше продолжать не получится.\n"
        "Оформлен возврат средств.\n\n"
        "→ Проверь логин и пароль от аккаунта Roblox и оформи заказ заново.",
        ("$attempts", "$max", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "refund_requested",
        "failure",
        "Возврат: по просьбе покупателя",
        "✅ Оформил возврат средств по твоей просьбе.\n"
        "Робуксы не выдавались — деньги вернутся на баланс Starvell.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "deadline_buyer_warning",
        "failure",
        "Дедлайн: предупреждение покупателю",
        "⏳ На выполнение заказа осталось $minutes минут.\n"
        "Если не успеем — придётся оформить возврат средств.\n\n"
        "→ Поторопись, пожалуйста.",
        ("$minutes", "$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "refund_deadline",
        "failure",
        "Возврат: не уложились в срок",
        "⌛ Заказ не получилось выполнить за отведённое время, "
        "поэтому оформлен возврат средств.\n\n"
        "→ Если робуксы всё ещё нужны — оформи заказ заново.",
        ("$account", "$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_subscription",
        "failure",
        "Перезапрос: выдача временно недоступна",
        "❌ Выдача временно недоступна.\n\n"
        "→ Давай попробуем заново — пришли логин своего аккаунта Roblox.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_auth",
        "failure",
        "Перезапрос: временная ошибка сервиса",
        "❌ Временная ошибка сервиса.\n\n"
        "→ Давай попробуем заново — пришли логин своего аккаунта Roblox.",
        ("$order_id", "$buyer"),
    ),
    MessageSpec(
        "restart_create_generic",
        "failure",
        "Перезапрос: прочая ошибка создания",
        "❌ $reason\n\n→ Давай попробуем заново — пришли логин своего аккаунта Roblox.",
        ("$reason", "$order_id", "$buyer"),
    ),
]
MESSAGE_BY_KEY: dict[str, MessageSpec] = {s.key: s for s in MESSAGE_SPECS}
MESSAGE_DEFAULTS: dict[str, str] = {s.key: s.default for s in MESSAGE_SPECS}

VARIABLE_DOCS: dict[str, str] = {
    "$account": "логин (никнейм) аккаунта Roblox покупателя",
    "$robux": "количество робуксов (для пака — номинал, при выдаче — фактически начислено)",
    "$total": "итоговое количество робуксов в заказе (номинал × количество)",
    "$quantity": "количество паков в заказе",
    "$order_id": "ID заказа на Starvell",
    "$buyer": "никнейм покупателя на Starvell",
    "$reason": "причина проблемы/ошибки (текст от сервиса)",
    "$prompt": "текст запроса от Roblox / список вариантов",
    "$options": "пронумерованный список вариантов выбора",
    "$options_count": "общее количество доступных вариантов (игр)",
    "$count": "сколько вариантов нужно выбрать",
    "$n": "количество доступных номеров для выбора",
    "$example": "пример правильного ответа",
    "$hint": "подсказка с адресом почты, куда отправлен код",
    "$extra": "приписка о неподошедшем коде (номер попытки)",
    "$attempt": "номер текущей попытки ввода",
    "$max": "максимальное число попыток ввода",
    "$minutes": "сколько минут ждали ответа покупателя",
    "$attempts": "сколько неудачных попыток ввода было сделано",
}


class Messages(_Model):
    if _PD_V2:
        model_config = {"extra": "ignore"}
    else:
        class Config(_Model.Config):
            extra = "ignore"

    overrides: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def load(cls) -> "Messages":
        if not MESSAGES_FILE_PATH.exists():
            instance = cls()
            instance.save()
            return instance
        with open(MESSAGES_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def save(self) -> None:
        MESSAGES_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MESSAGES_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))

    def text(self, key: str) -> str:
        return self.overrides.get(key) or MESSAGE_DEFAULTS[key]

    def is_custom(self, key: str) -> bool:
        return key in self.overrides

    def set(self, key: str, value: str) -> None:
        self.overrides[key] = value
        self.save()

    def reset(self, key: str) -> None:
        self.overrides.pop(key, None)
        self.save()

    def reset_category(self, category: str) -> int:
        keys = [
            s.key
            for s in MESSAGE_SPECS
            if s.category == category and self.is_custom(s.key)
        ]
        for key in keys:
            self.overrides.pop(key, None)
        self.save()
        return len(keys)

    def reset_all(self) -> int:
        count = len(self.overrides)
        self.overrides.clear()
        self.save()
        return count

    def custom_count(self, category: str | None = None) -> int:
        if category is None:
            return len(self.overrides)
        return sum(
            1 for s in MESSAGE_SPECS if s.category == category and self.is_custom(s.key)
        )


messages = Messages.load()


class OrderStatus(str, Enum):
    PENDING_VERIFICATION = "pending_verification"
    VERIFYING = "verifying"
    REQUIRES_ACTION = "requires_action"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIALLY_DELIVERED = "partially_delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class NextActionType(str, Enum):
    WAIT = "wait"
    CHOOSE_ONE = "choose_one"
    CHOOSE_MANY = "choose_many"
    PROVIDE_INPUT = "provide_input"
    CREDENTIALS_RETRY = "credentials_retry"
    PUSH_APPROVAL = "push_approval"


class InputFormat(str, Enum):
    DIGITS = "digits"
    RECOVERY_CODE = "recovery_code"


class StepAction(str, Enum):
    RESEND = "resend"
    CANCEL = "cancel"


class FailureCategory(str, Enum):
    BUYER_ACTION_REQUIRED = "buyer_action_required"
    SELLER_ACTION_REQUIRED = "seller_action_required"
    TEMPORARY_UNAVAILABLE = "temporary_unavailable"
    PERMANENT_FAILURE = "permanent_failure"


class VerificationMode(str, Enum):
    CONVERSATIONAL = "conversational"


class SituationState(str, Enum):
    AWAITING_LINK_OPEN = "awaiting_link_open"
    BUYER_LOGIN_IN_PROGRESS = "buyer_login_in_progress"
    CREDENTIALS_REJECTED = "credentials_rejected"
    AWAITING_2FA = "awaiting_2fa"
    BUYER_DISCONNECTED = "buyer_disconnected"
    PREPARING_DELIVERY = "preparing_delivery"
    DELIVERING = "delivering"
    REAUTH_AVAILABLE = "reauth_available"
    CHARGE_VERDICT_PENDING = "charge_verdict_pending"
    RECOVERY_IN_PROGRESS = "recovery_in_progress"
    MANUAL_COMPLETION_REQUIRED = "manual_completion_required"
    RELINK_AVAILABLE = "relink_available"
    FINISHED = "finished"


class SituationActor(str, Enum):
    BUYER = "buyer"
    SELLER = "seller"
    PLATFORM = "platform"
    NOBODY = "nobody"


class SituationActionType(str, Enum):
    OPEN_LINK = "open_link"
    RETRY_PASSWORD = "retry_password"
    ENTER_2FA_CODE = "enter_2fa_code"
    TOP_UP_MS_BALANCE = "top_up_ms_balance"
    WAIT = "wait"
    RELINK = "relink"
    CONTACT_SUPPORT = "contact_support"


class SituationChannel(str, Enum):
    EMAIL = "email"
    AUTHENTICATOR = "authenticator"
    GAME = "game"
    PASSKEY = "passkey"
    RECOVERY = "recovery"
    METHOD_SELECT = "method_select"


class ChargeState(str, Enum):
    NOT_CHARGED = "not_charged"
    CHARGING = "charging"
    CHARGED = "charged"
    UNKNOWN = "unknown"


class AnnouncementSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Language(str, Enum):
    EN = "en"
    RU = "ru"
    ZH = "zh"


class Currency(str, Enum):
    USD = "USD"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ErrorCode(str, Enum):
    AUTHENTICATION_REQUIRED = "authentication_required"
    INVALID_API_KEY = "invalid_api_key"
    EXPIRED_API_KEY = "expired_api_key"
    REVOKED_API_KEY = "revoked_api_key"
    SCOPE_INSUFFICIENT = "scope_insufficient"
    API_KEY_IN_URL_FORBIDDEN = "api_key_in_url_forbidden"
    HTTPS_REQUIRED = "https_required"
    CORS_NOT_SUPPORTED = "cors_not_supported"
    INVALID_JSON = "invalid_json"
    INVALID_REQUEST = "invalid_request"
    REQUEST_BODY_TOO_LARGE = "request_body_too_large"
    MISSING_PARAMETER = "missing_parameter"
    INVALID_PARAMETER = "invalid_parameter"
    INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"
    IDEMPOTENCY_KEY_REQUIRED = "idempotency_key_required"
    IDEMPOTENCY_KEY_IN_USE = "idempotency_key_in_use"
    IDEMPOTENCY_REQUEST_IN_PROGRESS = "idempotency_request_in_progress"
    METADATA_TOO_LARGE = "metadata_too_large"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    MODE_CREDENTIALS_MISMATCH = "mode_credentials_mismatch"
    WEBHOOK_URL_INVALID = "webhook_url_invalid"
    AMOUNT_TOO_LARGE = "amount_too_large"
    ITEM_PRICING_MISMATCH = "item_pricing_mismatch"
    ORDER_NOT_FOUND = "order_not_found"
    WEBHOOK_ENDPOINT_NOT_FOUND = "webhook_endpoint_not_found"
    EVENT_NOT_FOUND = "event_not_found"
    API_KEY_NOT_FOUND = "api_key_not_found"
    ORDER_ALREADY_CANCELLED = "order_already_cancelled"
    ORDER_ALREADY_COMPLETED = "order_already_completed"
    ORDER_CANNOT_BE_CANCELLED = "order_cannot_be_cancelled"
    DUPLICATE_RECENT_ORDER = "duplicate_recent_order"
    ORDER_NOT_RELINKABLE = "order_not_relinkable"
    RELINK_WINDOW_EXPIRED = "relink_window_expired"
    RELINK_REQUIRES_MANUAL_REVIEW = "relink_requires_manual_review"
    SESSION_ALREADY_HAS_ACTIVE_ORDER = "session_already_has_active_order"
    VERIFICATION_STATE_CHANGED = "verification_state_changed"
    VERIFICATION_NOT_READY = "verification_not_ready"
    VERIFICATION_ALREADY_RESPONDED = "verification_already_responded"
    VERIFY_CANCELLED = "verify.cancelled"
    VERIFICATION_METHOD_NOT_SUPPORTED = "verification_method_not_supported"
    VERIFICATION_INPUT_INVALID = "verification_input_invalid"
    VERIFICATION_STEP_EXPIRED = "verification_step_expired"
    VERIFICATION_SESSION_EXPIRED = "verification_session_expired"
    VERIFICATION_ORCHESTRATOR_LOST = "verification_orchestrator_lost"
    VERIFICATION_TIMEOUT = "verification_timeout"
    PROMPT_TIMEOUT = "prompt_timeout"
    VERIFICATION_NOT_COMPLETED = "verification_not_completed"
    CHARGE_OUTCOME_UNKNOWN = "charge_outcome_unknown"
    INVALID_CREDENTIALS_EXHAUSTED = "invalid_credentials_exhausted"
    BUYER_CONCURRENT_ORDERS_LIMIT_EXCEEDED = "buyer_concurrent_orders_limit_exceeded"
    PREMIUM_SUB_QUANTITY_LIMIT = "premium_sub_quantity_limit"
    ACCOUNT_TEMPORARILY_BLOCKED = "account_temporarily_blocked"
    BUYER_DAILY_LIMIT_REACHED = "buyer_daily_limit_reached"
    DELIVERY_TEMPORARILY_UNAVAILABLE = "delivery_temporarily_unavailable"
    ITEMS_NOT_CURRENTLY_AVAILABLE = "items_not_currently_available"
    ITEMS_NOT_AVAILABLE_IN_REGION = "items_not_available_in_region"
    ITEMS_INVALID = "items_invalid"
    UNKNOWN_PRODUCT = "unknown_product"
    PARTIAL_DELIVERY_MANUAL_TOPUP = "partial_delivery_manual_topup"
    SUBSCRIPTION_REQUIRED = "subscription_required"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    TRANSACTIONS_QUOTA_EXCEEDED = "transactions_quota_exceeded"
    SERVICE_TEMPORARILY_DISABLED = "service_temporarily_disabled"
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_METHOD_EXPIRED = "payment_method_expired"
    PAYMENT_DECLINED = "payment_declined"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    NO_BILLING_ADDRESS = "no_billing_address"
    NO_FUNDED_ACCOUNT = "no_funded_account"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    MS_ACCOUNT_THROTTLED = "ms_account_throttled"
    INTERNAL_ERROR = "internal_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    REQUEST_TIMEOUT = "request_timeout"
    CAPTCHA_SERVICE_UNAVAILABLE = "captcha_service_unavailable"
    SITUATION_OPEN_LINK = "situation_open_link"
    SITUATION_BUYER_LOGIN_IN_PROGRESS = "situation_buyer_login_in_progress"
    SITUATION_RETRY_PASSWORD = "situation_retry_password"
    SITUATION_ENTER_2FA_EMAIL = "situation_enter_2fa_email"
    SITUATION_ENTER_2FA_AUTHENTICATOR = "situation_enter_2fa_authenticator"
    SITUATION_ENTER_2FA_GAME = "situation_enter_2fa_game"
    SITUATION_ENTER_2FA_PASSKEY = "situation_enter_2fa_passkey"
    SITUATION_ENTER_2FA_RECOVERY = "situation_enter_2fa_recovery"
    SITUATION_ENTER_2FA_METHOD_SELECT = "situation_enter_2fa_method_select"
    SITUATION_BUYER_DISCONNECTED = "situation_buyer_disconnected"
    SITUATION_PREPARING_DELIVERY = "situation_preparing_delivery"
    SITUATION_DELIVERING = "situation_delivering"
    SITUATION_REAUTH_OPEN_LINK = "situation_reauth_open_link"
    SITUATION_REAUTH_TOP_UP = "situation_reauth_top_up"
    SITUATION_REAUTH_WAIT_ACCOUNT = "situation_reauth_wait_account"
    SITUATION_CHARGE_VERDICT_PENDING = "situation_charge_verdict_pending"
    SITUATION_RECOVERY_IN_PROGRESS = "situation_recovery_in_progress"
    SITUATION_RELINK_AVAILABLE = "situation_relink_available"
    SITUATION_MANUAL_COMPLETION = "situation_manual_completion"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> "ErrorCode":
        return cls.UNKNOWN


TERMINAL_STATUSES = frozenset(
    {
        OrderStatus.COMPLETED,
        OrderStatus.PARTIALLY_DELIVERED,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    }
)


ROBUX_RE = re.compile(r"(\d+)\s*(?:робуксов|роб|robux|r\$)", re.IGNORECASE)


def _dot(on: bool) -> str:
    return "🟢" if on else "🔴"


def _lot_token(offer_id: str) -> str:
    return hashlib.md5(str(offer_id).encode("utf-8")).hexdigest()[:12]

OPTION_TRANSLATIONS = [
    (("email", "e-mail", "почт"), "Код на почту"),
    (("recovery", "backup", "резерв"), "Резервный код"),
    (("authenticator", "totp", "аутентиф"), "Код из приложения-аутентификатора"),
    (
        ("push", "mobile", "roblox app", "приложен"),
        "Подтверждение в Roblox с мобильного устройства",
    ),
]
RUSSIAN_RE = re.compile(r"[а-яё]", re.IGNORECASE)
LATIN_TO_CYRILLIC_HOMOGLYPHS = {
    "A": "А",
    "B": "В",
    "C": "С",
    "E": "Е",
    "H": "Н",
    "K": "К",
    "M": "М",
    "O": "О",
    "P": "Р",
    "T": "Т",
    "X": "Х",
    "Y": "У",
    "a": "а",
    "c": "с",
    "e": "е",
    "o": "о",
    "p": "р",
    "x": "х",
    "y": "у",
}
HOMOGLYPH_SWAP_TABLE = str.maketrans(
    {
        **LATIN_TO_CYRILLIC_HOMOGLYPHS,
        **{cyr: lat for lat, cyr in LATIN_TO_CYRILLIC_HOMOGLYPHS.items()},
    }
)
_CRED_LOGIN_LABEL = (
    r"(?:н[иеё]кн[еэ]йм\w*|н[иеё]к\w*|л[ао]гин\w*|л[ао]г|акк\w*|юз[еэ]р\w*|имя"
    r"|username|nickname|login\w*|nick|user|name|acc\w*)"
)
_CRED_LOGIN_LABEL_RU = (
    r"(?:н[иеё]кн[еэ]йм\w*|н[иеё]к\w*|л[ао]гин\w*|л[ао]г|акк\w*|юз[еэ]р\w*|имя)"
)
_CRED_PASS_LABEL = (
    r"(?:п[ао]р[ао]л\w*|п[ао]ссв[ао]рд\w*|п[ао]св[ао]рд\w*|п[ао]сс\w*"
    r"|password\w*|passwd|pass|pwd|п[ао]р)"
)
_CRED_SEP = r"[\s:=\-–—>|]+"
CRED_LOGIN_ANCHOR_RE = re.compile(rf"\b{_CRED_LOGIN_LABEL}{_CRED_SEP}", re.IGNORECASE)
CRED_PASS_ANCHOR_RE = re.compile(rf"\b{_CRED_PASS_LABEL}{_CRED_SEP}", re.IGNORECASE)
CRED_LOGIN_VALUE_RE = re.compile(r"[A-Za-z0-9_]+")
CRED_PAIR_RE = re.compile(
    r"^@?([A-Za-z0-9_]+)(?:\s*[/:|,;]\s*|\s+[-–—]\s+)(\S.*)$"
)
CRED_LIST_MARKER_RE = re.compile(r"^\d+[.):\]]$")
CRED_WORDLESS_RE = re.compile(r"^[^\w]+$")
CYRILLIC_WORD_RE = re.compile(r"^[а-яё]+$", re.IGNORECASE)
CRED_QUOTES = " \t\r\n'\"«»`“”‘’*"
CRED_CHATTER_TRIM = CRED_QUOTES + ".,!?;:"
LOGIN_MIN_LENGTH = 3
LOGIN_MAX_LENGTH = 20
LOGIN_LABEL_ONLY_RE = re.compile(rf"^{_CRED_LOGIN_LABEL_RU}[\s:=\-]*$", re.IGNORECASE)
PASS_LABEL_ONLY_RE = re.compile(rf"^{_CRED_PASS_LABEL}[\s:=\-]*$", re.IGNORECASE)
LOGIN_LABEL_RE = re.compile(rf"^{_CRED_LOGIN_LABEL_RU}[\s:=\-]+", re.IGNORECASE)
PASS_LABEL_RE = re.compile(rf"^{_CRED_PASS_LABEL}\b[\s:=\-]+", re.IGNORECASE)
LOGIN_LABEL_SUFFIX_RE = re.compile(
    rf"[\s:=\-]+{_CRED_LOGIN_LABEL_RU}\s*$", re.IGNORECASE
)
PASS_LABEL_SUFFIX_RE = re.compile(rf"[\s:=\-]+{_CRED_PASS_LABEL}\s*$", re.IGNORECASE)
POLL_INTERVAL = 10.0
EXTEND_THRESHOLD = 1800.0
EXTEND_COOLDOWN = 600.0
LOOKUP_BATCH = 50
MAX_UNITS_PER_ORDER = 15
MAX_PREMIUM_UNITS_PER_ORDER = 3
RELINK_MAX_LIFETIME = 6 * 3600
PLUGIN_STARTED_AT = time.time()

TIMEOUT_FAILURE_CODES = frozenset(
    {
        ErrorCode.VERIFICATION_TIMEOUT.value,
        ErrorCode.VERIFICATION_SESSION_EXPIRED.value,
        ErrorCode.PROMPT_TIMEOUT.value,
    }
)
STEP_LABELS = {
    NextActionType.CHOOSE_ONE: "выбор способа 2FA",
    NextActionType.CHOOSE_MANY: "выбор игр",
    NextActionType.PROVIDE_INPUT: "ввод кода",
    NextActionType.CREDENTIALS_RETRY: "повторный логин/пароль",
    NextActionType.PUSH_APPROVAL: "подтверждение в приложении Roblox",
}
ACTIONABLE_STEPS = frozenset(
    {
        NextActionType.CHOOSE_ONE,
        NextActionType.CHOOSE_MANY,
        NextActionType.PROVIDE_INPUT,
        NextActionType.CREDENTIALS_RETRY,
    }
)
HANDLED_SITUATIONS = frozenset(
    {
        SituationState.REAUTH_AVAILABLE,
        SituationState.MANUAL_COMPLETION_REQUIRED,
        SituationState.CHARGE_VERDICT_PENDING,
        SituationState.RECOVERY_IN_PROGRESS,
    }
)
RESTART_FAILURE_MESSAGE_KEYS = {
    ErrorCode.INTERNAL_ERROR.value: "restart_internal_error",
    ErrorCode.VERIFICATION_INPUT_INVALID.value: "restart_input_invalid",
    ErrorCode.VERIFICATION_METHOD_NOT_SUPPORTED.value: "restart_method_not_supported",
    ErrorCode.ACCOUNT_TEMPORARILY_BLOCKED.value: "restart_account_blocked",
    ErrorCode.DELIVERY_TEMPORARILY_UNAVAILABLE.value: "restart_internal_error",
    ErrorCode.CAPTCHA_SERVICE_UNAVAILABLE.value: "restart_captcha_unavailable",
    ErrorCode.SERVICE_TEMPORARILY_DISABLED.value: "restart_service_disabled",
    ErrorCode.INSUFFICIENT_FUNDS.value: "restart_insufficient_funds",
    ErrorCode.NO_FUNDED_ACCOUNT.value: "restart_no_funded_account",
    ErrorCode.NO_BILLING_ADDRESS.value: "restart_no_funded_account",
    ErrorCode.MS_ACCOUNT_THROTTLED.value: "restart_no_funded_account",
    ErrorCode.VERIFICATION_ORCHESTRATOR_LOST.value: "restart_auth",
    ErrorCode.SERVICE_UNAVAILABLE.value: "restart_auth",
    ErrorCode.SUBSCRIPTION_REQUIRED.value: "restart_subscription",
    ErrorCode.SUBSCRIPTION_EXPIRED.value: "restart_subscription",
    ErrorCode.TRANSACTIONS_QUOTA_EXCEEDED.value: "restart_subscription",
    ErrorCode.BUYER_CONCURRENT_ORDERS_LIMIT_EXCEEDED.value: "restart_concurrent_orders",
    ErrorCode.BUYER_DAILY_LIMIT_REACHED.value: "restart_concurrent_orders",
    ErrorCode.INVALID_CREDENTIALS_EXHAUSTED.value: "restart_default",
    ErrorCode.VERIFICATION_NOT_COMPLETED.value: "restart_timeout",
}
RESTART_FAILURE_CODES = frozenset(RESTART_FAILURE_MESSAGE_KEYS)
NEVER_RECREATE_CODES = frozenset(
    {
        ErrorCode.CHARGE_OUTCOME_UNKNOWN.value,
        ErrorCode.PARTIAL_DELIVERY_MANUAL_TOPUP.value,
    }
)
SAFE_CHARGE_STATES = frozenset({ChargeState.NOT_CHARGED})
CHARGE_LOCK_SITUATIONS = frozenset(
    {
        SituationState.PREPARING_DELIVERY,
        SituationState.DELIVERING,
        SituationState.CHARGE_VERDICT_PENDING,
        SituationState.RECOVERY_IN_PROGRESS,
        SituationState.MANUAL_COMPLETION_REQUIRED,
    }
)
REFUND_NO_STOCK_CODES = frozenset(
    {
        ErrorCode.INSUFFICIENT_FUNDS.value,
        ErrorCode.NO_FUNDED_ACCOUNT.value,
        ErrorCode.NO_BILLING_ADDRESS.value,
        ErrorCode.PAYMENT_DECLINED.value,
        ErrorCode.PAYMENT_METHOD_EXPIRED.value,
        ErrorCode.PAYMENT_FAILED.value,
        ErrorCode.ITEMS_NOT_CURRENTLY_AVAILABLE.value,
        ErrorCode.ITEMS_NOT_AVAILABLE_IN_REGION.value,
        ErrorCode.MS_ACCOUNT_THROTTLED.value,
    }
)
REFUND_SERVICE_DOWN_CODES = frozenset(
    {
        ErrorCode.SERVICE_TEMPORARILY_DISABLED.value,
        ErrorCode.SERVICE_UNAVAILABLE.value,
        ErrorCode.CAPTCHA_SERVICE_UNAVAILABLE.value,
        ErrorCode.INTERNAL_ERROR.value,
        ErrorCode.REQUEST_TIMEOUT.value,
        ErrorCode.VERIFICATION_ORCHESTRATOR_LOST.value,
        ErrorCode.SUBSCRIPTION_REQUIRED.value,
        ErrorCode.SUBSCRIPTION_EXPIRED.value,
        ErrorCode.TRANSACTIONS_QUOTA_EXCEEDED.value,
        ErrorCode.RATE_LIMIT_EXCEEDED.value,
        ErrorCode.DELIVERY_TEMPORARILY_UNAVAILABLE.value,
    }
)
REFUND_ROBLOX_BLOCKED_CODES = frozenset(
    {
        ErrorCode.ACCOUNT_TEMPORARILY_BLOCKED.value,
    }
)
REFUND_ORDER_FAILED_CODES = frozenset(
    {
        ErrorCode.ITEMS_INVALID.value,
        ErrorCode.UNKNOWN_PRODUCT.value,
        ErrorCode.ITEM_PRICING_MISMATCH.value,
        ErrorCode.PREMIUM_SUB_QUANTITY_LIMIT.value,
        ErrorCode.INVALID_CREDENTIALS_EXHAUSTED.value,
        ErrorCode.VERIFICATION_INPUT_INVALID.value,
        ErrorCode.VERIFICATION_NOT_COMPLETED.value,
        ErrorCode.VERIFICATION_TIMEOUT.value,
        ErrorCode.VERIFICATION_SESSION_EXPIRED.value,
        ErrorCode.PROMPT_TIMEOUT.value,
        ErrorCode.DUPLICATE_RECENT_ORDER.value,
        ErrorCode.SESSION_ALREADY_HAS_ACTIVE_ORDER.value,
        ErrorCode.BUYER_CONCURRENT_ORDERS_LIMIT_EXCEEDED.value,
        ErrorCode.BUYER_DAILY_LIMIT_REACHED.value,
        ErrorCode.ORDER_NOT_FOUND.value,
        ErrorCode.UNKNOWN.value,
    }
)
REFUND_CATEGORIES: list[tuple[str, frozenset, str]] = [
    ("refund_on_no_stock", REFUND_NO_STOCK_CODES, "refund_no_stock"),
    ("refund_on_service_down", REFUND_SERVICE_DOWN_CODES, "refund_service_down"),
    ("refund_on_roblox_blocked", REFUND_ROBLOX_BLOCKED_CODES, "refund_roblox_blocked"),
    ("refund_on_order_failed", REFUND_ORDER_FAILED_CODES, "refund_failed"),
]
REFUND_REASON_LABELS = {
    "refund_passkey": "на аккаунте включён вход по Passkey",
    "refund_timeout": "покупатель долго не отвечал",
    "refund_no_stock": "нет наличия робуксов или средств",
    "refund_service_down": "сервис выдачи недоступен",
    "refund_roblox_blocked": "вход в Roblox ограничен",
    "refund_failed": "заказ не удалось выполнить",
    "refund_attempts": "превышен лимит неудачных попыток ввода",
    "refund_requested": "покупатель попросил возврат",
    "refund_deadline": "не укладываемся в срок выполнения",
}
DEADLINE_WARN = "warn"
DEADLINE_BUYER_WARN = "buyer_warn"
DEADLINE_REFUND = "refund"
DEADLINE_URGENT = "urgent"
DEADLINE_FINAL = "final"
PERMANENT_ITEM_CODES = frozenset(
    {
        ErrorCode.ITEMS_INVALID,
        ErrorCode.ITEMS_NOT_CURRENTLY_AVAILABLE,
        ErrorCode.ITEMS_NOT_AVAILABLE_IN_REGION,
        ErrorCode.UNKNOWN_PRODUCT,
        ErrorCode.ITEM_PRICING_MISMATCH,
    }
)
TRANSIENT_CODES = frozenset(
    {
        ErrorCode.SERVICE_UNAVAILABLE,
        ErrorCode.INTERNAL_ERROR,
        ErrorCode.REQUEST_TIMEOUT,
        ErrorCode.MS_ACCOUNT_THROTTLED,
    }
)
RESPOND_ATTEMPTS = 3
RESPOND_RETRY_DELAY = 3.0


CBT_TOGGLE = "arp_toggle"
CBT_TOGGLE_INVALID = "arp_toggle_invalid"
CBT_TOGGLE_RU_PASS = "arp_toggle_ru_pass"
CBT_TOGGLE_RECOVER = "arp_toggle_recover"
CBT_TOGGLE_POOL = "arp_toggle_pool"
CBT_SET_KEY = "arp_set_key"
CBT_PACKS = "arp_packs"
CBT_PACK = "arp_pack"
CBT_PACK_ADD = "arp_pack_add"
CBT_PACK_DEL = "arp_pack_del"
CBT_PACK_RM = "arp_pack_rm"
CBT_PACK_RM_YES = "arp_pack_rm_yes"
CBT_PACK_NEW = "arp_pack_new"
CBT_MORE = "arp_more"
CBT_LOTS = "arp_lots"
CBT_LADD = "arp_ladd"
CBT_LSEL = "arp_lsel"
CBT_LSET = "arp_lset"
CBT_LDEL = "arp_ldel"
CBT_LYES = "arp_lyes"
CBT_HISTORY = "arp_history"
CBT_STATS = "arp_stats"
CBT_STATS_CUSTOM = "arp_stats_custom"
CBT_ORDER = "arp_order"
CBT_NOTIFY = "arp_notify"
CBT_NOTIFY_TOGGLE = "arp_notify_toggle"
CBT_SEARCH = "arp_search"
CBT_MESSAGES = "arp_messages"
CBT_MSG_CAT = "arp_msg_cat"
CBT_MSG = "arp_msg"
CBT_MSG_EDIT = "arp_msg_edit"
CBT_MSG_RESET = "arp_msg_reset"
CBT_MSG_RESET_CAT = "arp_msg_reset_cat"
CBT_MSG_RESET_ALL = "arp_msg_reset_all"
CBT_REFUNDS = "arp_refunds"
CBT_DEADLINE = "arp_deadline"
CBT_TOGGLE_FIELD = "arp_field"
CBT_NUM = "arp_num"
CBT_REFUND_WORDS = "arp_refund_words"
CBT_REFUND_WORDS_RESET = "arp_refund_words_reset"
CBT_NOOP = "arp_noop"
STATE_SET_KEY = "arp_await_api_key"
STATE_NEW_PACK = "arp_await_pack_nominal"
STATE_SEARCH = "arp_await_search"
STATE_STATS_RANGE = "arp_await_stats_range"
STATE_EDIT_MSG = "arp_await_message"
STATE_REFUND_NUM = "arp_await_refund_num"
STATE_REFUND_WORDS = "arp_await_refund_words"
PACKS_PER_PAGE = 8
HISTORY_PER_PAGE = 8
MESSAGES_PER_PAGE = 8
LOTS_PER_PAGE = 8
MESSAGE_CATEGORY_LABELS = {"success": "Успешные", "failure": "Неуспешные"}
REFUND_TOGGLE_ROWS: list[tuple[str, str, str]] = [
    ("refund_on_timeout", "Простой покупателя", "refund_timeout_minutes"),
    ("refund_on_no_stock", "Нет наличия робуксов", ""),
    ("refund_on_service_down", "Сервис не работает", ""),
    ("refund_on_roblox_blocked", "Вход в Roblox ограничен", ""),
    ("refund_on_order_failed", "Заказ не выполнен", ""),
    ("refund_on_passkey", "Вход по Passkey", ""),
    ("refund_on_attempts", "Лимит попыток ввода", "refund_max_attempts"),
    ("refund_on_request", "По просьбе покупателя", "keywords"),
]
DEADLINE_TOGGLE_ROWS: list[tuple[str, str, str]] = [
    ("deadline_on", "Контроль времени", ""),
    ("deadline_warn_on", "Предупреждать продавца", "deadline_warn_minutes"),
    ("deadline_refund_on", "Авто-возврат по дедлайну", "deadline_refund_minutes"),
    ("deadline_buyer_warn_on", "Предупреждать покупателя", "deadline_buyer_warn_minutes"),
]
DEADLINE_NUMBER_ROWS = ["deadline_minutes", "deadline_repeat_minutes"]
REFUND_TOGGLE_FIELDS = frozenset(field_name for field_name, _, _ in REFUND_TOGGLE_ROWS)
TOGGLE_FIELDS = REFUND_TOGGLE_FIELDS | frozenset(
    field_name for field_name, _, _ in DEADLINE_TOGGLE_ROWS
)
NUMBER_FIELDS: dict[str, tuple[str, str, int, int, str]] = {
    "refund_timeout_minutes": (
        "Простой покупателя",
        "Сколько минут ждать ответа покупателя, прежде чем оформить возврат.",
        1,
        1440,
        "мин",
    ),
    "refund_max_attempts": (
        "Лимит попыток ввода",
        "Сколько неудачных попыток ввода данных допустимо до возврата.",
        1,
        100,
        "",
    ),
    "push_approval_repeat_minutes": (
        "Повтор просьбы Approve",
        "Через сколько минут повторить просьбу нажать «Approve» в приложении Roblox.\n"
        "<code>0</code> — не повторять (просьба уходит один раз).",
        0,
        1440,
        "мин",
    ),
    "deadline_minutes": (
        "Регламент площадки",
        "Сколько минут даётся на выполнение заказа по правилам Starvell.\n"
        "Используется только для обратного отсчёта в уведомлениях.",
        5,
        1440,
        "мин",
    ),
    "deadline_warn_minutes": (
        "Предупреждение продавцу",
        "Через сколько минут после появления заказа прислать предупреждение в Telegram.",
        1,
        1440,
        "мин",
    ),
    "deadline_refund_minutes": (
        "Авто-возврат по дедлайну",
        "Через сколько минут после появления заказа оформить возврат, "
        "если заказ так и не выполнен.\n"
        "Ставь заметно меньше регламента, чтобы остался запас.",
        1,
        1440,
        "мин",
    ),
    "deadline_buyer_warn_minutes": (
        "Предупреждение покупателю",
        "За сколько минут до авто-возврата предупредить покупателя в чате Starvell.",
        1,
        120,
        "мин",
    ),
    "deadline_repeat_minutes": (
        "Повтор срочного уведомления",
        "Как часто повторять срочное уведомление, когда дедлайн подошёл, "
        "а возврат оформить нельзя.",
        1,
        60,
        "мин",
    ),
}

AUTH_ERROR_CODES = frozenset(
    {
        ErrorCode.AUTHENTICATION_REQUIRED,
        ErrorCode.INVALID_API_KEY,
        ErrorCode.EXPIRED_API_KEY,
        ErrorCode.REVOKED_API_KEY,
        ErrorCode.SCOPE_INSUFFICIENT,
        ErrorCode.API_KEY_NOT_FOUND,
    }
)


HISTORY_FILTERS = ["all", "active", "completed", "failed"]
HISTORY_FILTER_LABELS = {
    "all": "Все",
    "active": "В работе",
    "completed": "Выполнены",
    "failed": "Проблемные",
}
MANUAL_COMPLETION_STATUS = "manual_completion_required"
HISTORY_FILTER_SQL = {
    "all": "",
    "active": "WHERE pending = 1",
    "completed": "WHERE status = 'completed'",
    "failed": "WHERE pending = 0 AND status IN "
    "('failed', 'cancelled', 'expired', 'partially_delivered', "
    "'manual_completion_required')",
}
STATS_PERIODS = ["day", "yesterday", "week", "month", "all"]
STATS_PERIOD_LABELS = {
    "day": "Сегодня",
    "yesterday": "Вчера",
    "week": "Неделя",
    "month": "Месяц",
    "all": "Всё время",
}


@dataclass(frozen=True)
class BaseSku:
    key: str
    label: str
    product_id: str
    availability_id: str
    price: float
    robux: int
    sku_id: str = "0010"
    premium: bool = False


BASE_SKUS: dict[str, BaseSku] = {
    "80": BaseSku("80", "80 R$", "9NH6SMMZQHM9", "B2LV8XQBLF65", 0.99, 80),
    "500": BaseSku("500", "500 R$", "9PH0VHQ4CNFF", "B4C4VKBLV4FQ", 4.99, 500),
    "1000": BaseSku("1000", "1000 R$", "9NRQLWSN0K89", "9R5ZB9W0LL0R", 9.99, 1000),
    "2000": BaseSku("2000", "2000 R$", "9NH22L8775FQ", "9VBC6N2Q2XFQ", 19.99, 2000),
    "prem450": BaseSku(
        "prem450",
        "450 R$ + Premium",
        "9NT8XD0WZ4JT",
        "B4242B1938M7",
        4.99,
        450,
        premium=True,
    ),
    "prem1000": BaseSku(
        "prem1000",
        "1000 R$ + Premium",
        "9PJSPHF65QVG",
        "B0TVV89MS1RV",
        9.99,
        1000,
        premium=True,
    ),
    "prem2200": BaseSku(
        "prem2200",
        "2200 R$ + Premium",
        "9PJKVXL2N2LZ",
        "9Z337JQ19LRQ",
        19.99,
        2200,
        premium=True,
    ),
}


DEFAULT_PACKS: dict[int, list[tuple[str, int]]] = {
    80: [("80", 1)],
    200: [("80", 3)],
    400: [("500", 1)],
    500: [("500", 1)],
    800: [("500", 1), ("80", 4)],
    1000: [("1000", 1)],
    1200: [("1000", 1), ("80", 3)],
    1700: [("1000", 1), ("500", 1), ("80", 3)],
    2000: [("2000", 1)],
    2100: [("2000", 1), ("80", 2)],
    2500: [("2000", 1), ("500", 1)],
    3600: [("2000", 1), ("1000", 1), ("500", 1), ("80", 2)],
    4500: [("2000", 2), ("500", 1)],
    10000: [("2000", 5)],
    22500: [("2000", 11), ("500", 1)],
}


def swap_homoglyphs(text: str) -> str:
    return text.translate(HOMOGLYPH_SWAP_TABLE)


def _code_str(code) -> str:
    return code.value if isinstance(code, Enum) else str(code)


def _loose_enum(enum_cls):
    def _coerce(value):
        if isinstance(value, enum_cls):
            return value
        try:
            return enum_cls(value)
        except (ValueError, TypeError):
            return value

    if _PD_V2:
        from typing import Annotated

        from pydantic import BeforeValidator

        return Annotated[Any, BeforeValidator(_coerce)]

    class _Loose:
        @classmethod
        def __get_validators__(cls):
            yield cls.validate

        @classmethod
        def validate(cls, value):
            return _coerce(value)

    _Loose.__name__ = f"{enum_cls.__name__}OrStr"
    return _Loose


OrderStatusT = _loose_enum(OrderStatus)
CurrencyT = _loose_enum(Currency)
LanguageT = _loose_enum(Language)
FailureCategoryT = _loose_enum(FailureCategory)
SubscriptionStatusT = _loose_enum(SubscriptionStatus)
InputFormatT = _loose_enum(InputFormat)
StepActionT = _loose_enum(StepAction)
VerificationModeT = _loose_enum(VerificationMode)
SituationStateT = _loose_enum(SituationState)
SituationActorT = _loose_enum(SituationActor)
SituationActionTypeT = _loose_enum(SituationActionType)
SituationChannelT = _loose_enum(SituationChannel)
ChargeStateT = _loose_enum(ChargeState)
AnnouncementSeverityT = _loose_enum(AnnouncementSeverity)


class _ApiModel(_Model):
    pass


class I18nMessage(_ApiModel):
    en: str = ""
    ru: str = ""
    zh: str = ""

    def get(self, language: Language | str | None) -> str:
        key = language.value if isinstance(language, Language) else (language or "en")
        return getattr(self, key, self.en) or self.en


class Credentials(_ApiModel):
    username: str
    password: str

    def to_dict(self) -> dict:
        return self.model_dump()


class OrderItem(_ApiModel):
    product_id: str
    sku_id: str
    availability_id: str
    quantity: int = 1
    product_name: str | None = None
    amount: float | None = None

    def to_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


class RemainingQuota(_ApiModel):
    transactions_used: int
    transactions_limit: int


class QuotaAction(_ApiModel):
    consumed: int
    released: int
    remaining_after: RemainingQuota


class OrderResult(_ApiModel):
    username: str = ""
    user_id: int = 0
    robux_credited: int = 0


class OrderDelivery(_ApiModel):
    measured: bool = False
    robux_ordered: int | None = None
    robux_delivered: int | None = None
    username: str | None = None
    user_id: int | None = None


class OrderRelink(_ApiModel):
    available: bool = False
    url: str | None = None
    expires_at: int | None = None


class RelinkItem(_ApiModel):
    product_id: str = ""
    product_name: str = ""
    undelivered_quantity: int = 0


class RelinkSummary(_ApiModel):
    available: bool = False
    undelivered_items: list[RelinkItem] = Field(default_factory=list)
    expected_additional_robux: int | None = None


class FailureReason(_ApiModel):
    code: str
    category: FailureCategoryT | None = None
    retryable: bool = False
    user_message: I18nMessage | None = None


class SituationNextAction(_ApiModel):
    type: SituationActionTypeT
    channel: SituationChannelT | None = None
    url: str | None = None
    message: I18nMessage | None = None


class SituationMoney(_ApiModel):
    charge_state: ChargeStateT = ChargeState.NOT_CHARGED


class SituationLastFailure(_ApiModel):
    code: str
    category: FailureCategoryT | None = None
    retryable: bool = False
    user_message: I18nMessage | None = None
    occurred_at: int | None = None


class Situation(_ApiModel):
    state: SituationStateT
    state_changed_at: int | None = None
    actor: SituationActorT | None = None
    deadline: int | None = None
    outcome_if_deadline_passes: OrderStatusT | None = None
    next_action: SituationNextAction | None = None
    money: SituationMoney = Field(default_factory=SituationMoney)
    last_failure: SituationLastFailure | None = None


class ChoiceOption(_ApiModel):
    id: str
    label: I18nMessage | str | None = None
    image_url: str | None = None


class InputSpec(_ApiModel):
    format: InputFormatT
    min_length: int
    max_length: int


class SelectSpec(_ApiModel):
    exactly: int


class NextActionWait(_ApiModel):
    type: Literal[NextActionType.WAIT] = NextActionType.WAIT
    version: int
    poll_after_ms: int
    prompt: I18nMessage | None = None


class NextActionChooseOne(_ApiModel):
    type: Literal[NextActionType.CHOOSE_ONE] = NextActionType.CHOOSE_ONE
    version: int
    prompt: I18nMessage | None = None
    options: list[ChoiceOption] = Field(default_factory=list)
    expires_at: int
    poll_after_ms: int


class NextActionChooseMany(_ApiModel):
    type: Literal[NextActionType.CHOOSE_MANY] = NextActionType.CHOOSE_MANY
    version: int
    prompt: I18nMessage | None = None
    image_url: str | None = None
    image_expires_at: int | None = None
    options: list[ChoiceOption] = Field(default_factory=list)
    select: SelectSpec
    expires_at: int
    poll_after_ms: int

    @property
    def select_exactly(self) -> int:
        return self.select.exactly


class NextActionProvideInput(_ApiModel):
    type: Literal[NextActionType.PROVIDE_INPUT] = NextActionType.PROVIDE_INPUT
    version: int
    prompt: I18nMessage | None = None
    email_hint: str | None = None
    input: InputSpec
    attempt: int | None = None
    max_attempts: int | None = None
    actions: list[StepActionT] = Field(default_factory=list)
    expires_at: int
    poll_after_ms: int


class NextActionCredentialsRetry(_ApiModel):
    type: Literal[NextActionType.CREDENTIALS_RETRY] = NextActionType.CREDENTIALS_RETRY
    version: int
    prompt: I18nMessage | None = None
    reason: str | None = None
    attempt: int
    max_attempts: int
    expires_at: int
    poll_after_ms: int


class NextActionPushApproval(_ApiModel):
    type: Literal[NextActionType.PUSH_APPROVAL] = NextActionType.PUSH_APPROVAL
    version: int
    prompt: I18nMessage | None = None
    actions: list[StepActionT] = Field(default_factory=list)
    expires_at: int
    poll_after_ms: int


NextAction = Union[
    NextActionWait,
    NextActionChooseOne,
    NextActionChooseMany,
    NextActionProvideInput,
    NextActionCredentialsRetry,
    NextActionPushApproval,
]
_NEXT_ACTION_MODELS = {
    "wait": NextActionWait,
    "choose_one": NextActionChooseOne,
    "choose_many": NextActionChooseMany,
    "provide_input": NextActionProvideInput,
    "credentials_retry": NextActionCredentialsRetry,
    "push_approval": NextActionPushApproval,
}


def _parse_next_action(value: Any) -> Any:
    if value is None or isinstance(
        value,
        (
            NextActionWait,
            NextActionChooseOne,
            NextActionChooseMany,
            NextActionProvideInput,
            NextActionCredentialsRetry,
            NextActionPushApproval,
        ),
    ):
        return value
    if not isinstance(value, dict):
        return value
    model = _NEXT_ACTION_MODELS.get(str(value.get("type") or ""))
    if model is None:
        return value
    if _PD_V2:
        return model.model_validate(value)
    return model.parse_obj(value)


class Verification(_ApiModel):
    mode: VerificationModeT
    expires_at: int | None = None
    next_action: NextAction | None = None
    url: str | None = None
    display_label: str | None = None

    @_next_action_field()
    def _verification_next_action(cls, value):
        return _parse_next_action(value)


class Order(_ApiModel):
    id: str
    status: OrderStatusT
    current_step: str = ""
    cancellable: bool = False
    items: list[OrderItem] = Field(default_factory=list)
    amount_total: float = 0.0
    currency: CurrencyT = Currency.USD
    roblox_username: str = ""
    verification: Verification | None = None
    created_at: int
    updated_at: int
    language: LanguageT | None = None
    result: OrderResult | None = None
    delivery: OrderDelivery | None = None
    relink: OrderRelink | None = None
    situation: Situation | None = None
    delivered_robux: int | None = None
    ordered_robux: int | None = None
    failure_reason: FailureReason | None = None
    quota_action: QuotaAction | None = None
    metadata: dict = Field(default_factory=dict)
    payment_reference: str | None = None
    last_activity_at: int | None = None
    last_event_sequence: int | None = None

    @property
    def next_action(self) -> "NextAction | None":
        return self.verification.next_action if self.verification else None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_completed(self) -> bool:
        return self.status is OrderStatus.COMPLETED

    @property
    def situation_state(self) -> SituationState | str | None:
        return self.situation.state if self.situation else None

    @property
    def charge_state(self) -> ChargeState | str:
        if self.situation is not None:
            return self.situation.money.charge_state
        if self.payment_reference:
            return ChargeState.CHARGED
        return ChargeState.NOT_CHARGED

    @property
    def charge_free(self) -> bool:
        return self.charge_state in SAFE_CHARGE_STATES

    @property
    def last_failure(self) -> SituationLastFailure | FailureReason | None:
        if self.situation and self.situation.last_failure:
            return self.situation.last_failure
        return self.failure_reason

    @property
    def credited_robux(self) -> int | None:
        if self.delivery and self.delivery.measured:
            return self.delivery.robux_delivered
        if self.delivered_robux is not None:
            return self.delivered_robux
        return None

    @property
    def target_robux(self) -> int | None:
        if self.delivery and self.delivery.robux_ordered is not None:
            return self.delivery.robux_ordered
        return self.ordered_robux

    @property
    def relink_available(self) -> bool:
        return bool(self.relink and self.relink.available)

    @property
    def session_expires_at(self) -> int | None:
        return self.verification.expires_at if self.verification else None


class OrderList(_ApiModel):
    data: list[Order] = Field(default_factory=list)
    has_more: bool = False
    next_cursor: str | None = None


class LookupOrdersResponse(_ApiModel):
    data: list[Order] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)


class RelinkConversationalResponse(_ApiModel):
    next_action: NextAction | None = None
    relink: RelinkSummary | None = None

    @_next_action_field()
    def _relink_next_action(cls, value):
        return _parse_next_action(value)


class Subscription(_ApiModel):
    plan_id: str
    plan_name: str
    status: SubscriptionStatusT
    transactions_used: int
    transactions_limit: int
    transactions_remaining: int
    end_date: int
    manage_url: str
    cost_per_order: int


class Wallet(_ApiModel):
    balance: float = 0.0
    held: float = 0.0
    available: float = 0.0
    currency: CurrencyT = Currency.USD


class WalletQuote(_ApiModel):
    price: float = 0.0
    currency: CurrencyT = Currency.USD
    balance: float = 0.0
    held: float = 0.0
    available: float = 0.0
    affordable: bool = False
    shortfall: float = 0.0
    pool_enabled: bool = False


class ServiceAnnouncement(_ApiModel):
    active: bool = False
    text: str | None = None
    severity: AnnouncementSeverityT = AnnouncementSeverity.INFO


class _RateLimiter:
    def __init__(self) -> None:
        self._lock = Lock()
        self._until = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                remaining = self._until - time.time()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 2.0))

    def trip(self, seconds: float) -> None:
        with self._lock:
            self._until = max(self._until, time.time() + seconds)


_rate_limiter = _RateLimiter()
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_DEFAULT_WAIT = 60.0


class SwizzyerError(Exception):
    def __init__(
        self,
        code: ErrorCode | str,
        message: I18nMessage | None,
        *,
        http_status: int,
        details: dict | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}
        text = message.en if isinstance(message, I18nMessage) else str(message)
        super().__init__(f"[{http_status} {_code_str(code)}] {text}")

    @classmethod
    def from_response(cls, resp: requests.Response) -> "SwizzyerError":
        try:
            err = (resp.json() or {}).get("error", {})
        except ValueError:
            err = {}
        message = err.get("message")
        return cls(
            code=ErrorCode(err.get("code", "unknown")),
            message=I18nMessage.model_validate(message) if message else None,
            http_status=resp.status_code,
            details=err.get("details"),
        )


class SwizzyerAPI:
    BASE_URL = "https://rbcode.net"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        proxies: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "swizzyer-python/1.0",
            }
        )
        if proxies:
            self.session.proxies.update(proxies)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        for attempt in range(RATE_LIMIT_MAX_RETRIES):
            _rate_limiter.wait()
            resp = self.session.request(
                method,
                f"{self.base_url}{path}",
                json=json,
                params=params,
                headers=headers or None,
                timeout=self.timeout,
            )
            if resp.status_code != 429:
                break
            wait_for = self._retry_after_seconds(resp)
            _rate_limiter.trip(wait_for)
            logger.warning(
                f"Rate limit (429): глобальная пауза {wait_for:.0f}с "
                f"(попытка {attempt + 1}/{RATE_LIMIT_MAX_RETRIES})"
            )
        if not resp.ok:
            raise SwizzyerError.from_response(resp)
        return resp.json()

    @staticmethod
    def _retry_after_seconds(resp: requests.Response) -> float:
        header = resp.headers.get("Retry-After")
        if header:
            try:
                return max(1.0, float(header))
            except ValueError:
                pass
        try:
            ra = (resp.json() or {}).get("error", {}).get("retry_after")
            if ra is not None:
                return max(1.0, float(ra))
        except (ValueError, AttributeError):
            pass
        return RATE_LIMIT_DEFAULT_WAIT

    @staticmethod
    def _new_idempotency_key() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _language_value(language: Language | str | None) -> str | None:
        if language is None:
            return None
        return language.value if isinstance(language, Language) else language

    def create_order(
        self,
        credentials: Credentials,
        items: list[OrderItem],
        *,
        language: Language | str | None = None,
        metadata: dict[str, str] | None = None,
        use_managed_pool: bool = False,
        idempotency_key: str | None = None,
    ) -> Order:
        body: dict = {
            "mode": VerificationMode.CONVERSATIONAL.value,
            "credentials": credentials.to_dict(),
            "items": [i.to_dict() for i in items],
        }
        language_value = self._language_value(language)
        if language_value is not None:
            body["language"] = language_value
        if use_managed_pool:
            body["use_managed_pool"] = True
        if metadata:
            body["metadata"] = metadata
        data = self._request(
            "POST",
            "/v1/orders",
            json=body,
            idempotency_key=idempotency_key or self._new_idempotency_key(),
        )
        return Order.model_validate(data)

    def relink_order(
        self,
        order_id: str,
        credentials: Credentials,
        *,
        language: Language | str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> RelinkConversationalResponse:
        body: dict = {
            "mode": VerificationMode.CONVERSATIONAL.value,
            "credentials": credentials.to_dict(),
        }
        language_value = self._language_value(language)
        if language_value is not None:
            body["language"] = language_value
        if metadata:
            body["metadata"] = metadata
        data = self._request(
            "POST",
            f"/v1/orders/{order_id}/relink",
            json=body,
            idempotency_key=idempotency_key or self._new_idempotency_key(),
        )
        return RelinkConversationalResponse.model_validate(data)

    def get_order(self, order_id: str) -> Order:
        return Order.model_validate(self._request("GET", f"/v1/orders/{order_id}"))

    def list_orders(
        self,
        *,
        status: OrderStatus | str | None = None,
        created_after: int | str | None = None,
        roblox_username: str | None = None,
        external_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> OrderList:
        params: dict = {}
        if status is not None:
            params["status"] = (
                status.value if isinstance(status, OrderStatus) else status
            )
        if created_after is not None:
            params["created_after"] = created_after
        if roblox_username is not None:
            params["roblox_username"] = roblox_username
        if external_id is not None:
            params["external_id"] = external_id
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        return OrderList.model_validate(
            self._request("GET", "/v1/orders", params=params)
        )

    def lookup_orders(self, ids: list[str]) -> LookupOrdersResponse:
        data = self._request("POST", "/v1/orders/lookup", json={"ids": ids})
        return LookupOrdersResponse.model_validate(data)

    def cancel_order(
        self, order_id: str, *, idempotency_key: str | None = None
    ) -> Order:
        return Order.model_validate(
            self._request(
                "POST",
                f"/v1/orders/{order_id}/cancel",
                idempotency_key=idempotency_key,
            )
        )

    def extend_order(
        self, order_id: str, *, idempotency_key: str | None = None
    ) -> Order:
        return Order.model_validate(
            self._request(
                "POST",
                f"/v1/orders/{order_id}/extend",
                idempotency_key=idempotency_key,
            )
        )

    def _respond(self, order_id: str, body: dict, idempotency_key: str | None) -> Order:
        return Order.model_validate(
            self._request(
                "POST",
                f"/v1/orders/{order_id}/verification/respond",
                json=body,
                idempotency_key=idempotency_key or self._new_idempotency_key(),
            )
        )

    def respond_choice_one(
        self,
        order_id: str,
        if_version: int,
        choice_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> Order:
        return self._respond(
            order_id,
            {"if_version": if_version, "choice_id": choice_id},
            idempotency_key,
        )

    def respond_choice_many(
        self,
        order_id: str,
        if_version: int,
        choice_ids: list[str],
        *,
        idempotency_key: str | None = None,
    ) -> Order:
        return self._respond(
            order_id,
            {"if_version": if_version, "choice_ids": choice_ids},
            idempotency_key,
        )

    def respond_input(
        self,
        order_id: str,
        if_version: int,
        value: str,
        *,
        idempotency_key: str | None = None,
    ) -> Order:
        return self._respond(
            order_id, {"if_version": if_version, "input": value}, idempotency_key
        )

    def respond_credentials(
        self,
        order_id: str,
        if_version: int,
        credentials: Credentials,
        *,
        idempotency_key: str | None = None,
    ) -> Order:
        return self._respond(
            order_id,
            {"if_version": if_version, "credentials": credentials.to_dict()},
            idempotency_key,
        )

    def get_subscription(self) -> Subscription:
        return Subscription.model_validate(self._request("GET", "/v1/subscription"))

    def get_wallet(self) -> Wallet:
        return Wallet.model_validate(self._request("GET", "/v1/wallet"))

    def quote_wallet(self, items: list[OrderItem]) -> WalletQuote:
        return WalletQuote.model_validate(
            self._request(
                "POST",
                "/v1/wallet/quote",
                json={"items": [i.to_dict() for i in items]},
            )
        )

    def get_announcement(self) -> ServiceAnnouncement:
        return ServiceAnnouncement.model_validate(
            self._request("GET", "/v1/announcement")
        )


class Database:
    def __init__(self, path: Path | str = DB_FILE_PATH) -> None:
        self.path = Path(path)
        if self.path.parent.name:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self.transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_versions "
                "(name TEXT PRIMARY KEY, version INTEGER NOT NULL)"
            )
        self.orders = OrdersDB(self)
        self.packs = PacksDB(self)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

class BaseDB(ABC):
    def __init__(self, db: Database) -> None:
        self._db = db
        self._migrate()

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def schema(self) -> str: ...

    @property
    def migrations(self) -> list[str]:
        return []

    def _migrate(self) -> None:
        with self._db.transaction() as conn:
            conn.executescript(self.schema)
            row = conn.execute(
                "SELECT version FROM schema_versions WHERE name = ?", (self.name,)
            ).fetchone()
            current = row["version"] if row else 0
            target = len(self.migrations)
            for stmt in self.migrations[current:target]:
                conn.executescript(stmt)
            if row is None:
                conn.execute(
                    "INSERT INTO schema_versions (name, version) VALUES (?, ?)",
                    (self.name, target),
                )
            elif target != current:
                conn.execute(
                    "UPDATE schema_versions SET version = ? WHERE name = ?",
                    (target, self.name),
                )

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._db.transaction() as conn:
            return conn.execute(sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._db.lock:
            return self._db.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._db.lock:
            return list(self._db.conn.execute(sql, params).fetchall())


class Stage(str, Enum):
    QUEUED = "queued"
    AWAITING_LOGIN = "awaiting_login"
    AWAITING_PASSWORD = "awaiting_password"
    AWAITING_CRED_LOGIN = "awaiting_cred_login"
    AWAITING_CRED_PASSWORD = "awaiting_cred_password"
    VERIFYING = "verifying"
    DONE = "done"


STAGE_LABELS = {
    Stage.QUEUED: "в очереди",
    Stage.AWAITING_LOGIN: "ожидает логин",
    Stage.AWAITING_PASSWORD: "ожидает пароль",
    Stage.AWAITING_CRED_LOGIN: "повторный логин",
    Stage.AWAITING_CRED_PASSWORD: "повторный пароль",
    Stage.VERIFYING: "проверка 2FA",
}


@dataclass
class OrderRecord:
    funpay_order_id: str
    chat_id: str
    buyer_id: str
    buyer_username: str
    robux_amount: int
    quantity: int = 1
    stage: Stage = Stage.QUEUED
    roblox_username: str | None = None
    swizzyer_order_id: str | None = None
    await_version: int | None = None
    status: str | None = None
    pending: bool = True
    created_at: int = 0
    updated_at: int = 0
    data: dict = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "OrderRecord":
        return cls(
            funpay_order_id=row["funpay_order_id"],
            chat_id=str(row["chat_id"] or ""),
            buyer_id=str(row["buyer_id"] or ""),
            buyer_username=row["buyer_username"],
            robux_amount=row["robux_amount"],
            quantity=row["quantity"],
            stage=Stage(row["stage"]),
            roblox_username=row["roblox_username"],
            swizzyer_order_id=row["swizzyer_order_id"],
            await_version=row["await_version"],
            status=row["status"],
            pending=bool(row["pending"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            data=json.loads(row["data"] or "{}"),
        )


class OrdersDB(BaseDB):
    name = "orders"
    schema = """
    CREATE TABLE IF NOT EXISTS orders (
        funpay_order_id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        buyer_id TEXT NOT NULL,
        buyer_username TEXT NOT NULL,
        robux_amount INTEGER NOT NULL,
        stage TEXT NOT NULL,
        roblox_username TEXT,
        swizzyer_order_id TEXT,
        await_version INTEGER,
        status TEXT,
        pending INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        data TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_orders_pending ON orders (pending);
    CREATE INDEX IF NOT EXISTS idx_orders_chat ON orders (chat_id);
    CREATE INDEX IF NOT EXISTS idx_orders_swizzyer ON orders (swizzyer_order_id);
    """
    migrations = ["ALTER TABLE orders ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1;"]

    def save(self, record: OrderRecord) -> None:
        now = int(time.time())
        if not record.created_at:
            record.created_at = now
        record.updated_at = now
        self.execute(
            """
            INSERT INTO orders (funpay_order_id, chat_id, buyer_id, buyer_username, robux_amount,
                                quantity, stage, roblox_username, swizzyer_order_id, await_version,
                                status, pending, created_at, updated_at, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(funpay_order_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                buyer_id = excluded.buyer_id,
                buyer_username = excluded.buyer_username,
                robux_amount = excluded.robux_amount,
                quantity = excluded.quantity,
                stage = excluded.stage,
                roblox_username = excluded.roblox_username,
                swizzyer_order_id = excluded.swizzyer_order_id,
                await_version = excluded.await_version,
                status = excluded.status,
                pending = excluded.pending,
                updated_at = excluded.updated_at,
                data = excluded.data
            """,
            (
                record.funpay_order_id,
                record.chat_id,
                record.buyer_id,
                record.buyer_username,
                record.robux_amount,
                record.quantity,
                record.stage.value,
                record.roblox_username,
                record.swizzyer_order_id,
                record.await_version,
                record.status,
                1 if record.pending else 0,
                record.created_at,
                record.updated_at,
                json.dumps(record.data, ensure_ascii=False),
            ),
        )

    def get(self, funpay_order_id: str) -> OrderRecord | None:
        row = self.fetchone(
            "SELECT * FROM orders WHERE funpay_order_id = ?", (funpay_order_id,)
        )
        return OrderRecord.from_row(row) if row else None

    def active_by_buyer(self, buyer_id: str) -> OrderRecord | None:
        row = self.fetchone(
            "SELECT * FROM orders WHERE buyer_id = ? AND pending = 1 AND stage != ? "
            "ORDER BY created_at ASC LIMIT 1",
            (buyer_id, Stage.QUEUED.value),
        )
        return OrderRecord.from_row(row) if row else None

    def next_queued_by_buyer(self, buyer_id: str) -> OrderRecord | None:
        row = self.fetchone(
            "SELECT * FROM orders WHERE buyer_id = ? AND pending = 1 AND stage = ? "
            "ORDER BY created_at ASC LIMIT 1",
            (buyer_id, Stage.QUEUED.value),
        )
        return OrderRecord.from_row(row) if row else None

    def queued_buyer_ids(self) -> list[str]:
        rows = self.fetchall(
            "SELECT DISTINCT buyer_id FROM orders WHERE pending = 1 AND stage = ?",
            (Stage.QUEUED.value,),
        )
        return [str(row["buyer_id"]) for row in rows]

    def verifying(self) -> list[OrderRecord]:
        rows = self.fetchall(
            "SELECT * FROM orders WHERE pending = 1 AND stage = ? AND swizzyer_order_id IS NOT NULL",
            (Stage.VERIFYING.value,),
        )
        return [OrderRecord.from_row(r) for r in rows]

    def pending_all(self) -> list[OrderRecord]:
        rows = self.fetchall(
            "SELECT * FROM orders WHERE pending = 1 AND stage != ?",
            (Stage.DONE.value,),
        )
        return [OrderRecord.from_row(r) for r in rows]

    def active_waiting(self) -> list[OrderRecord]:
        rows = self.fetchall(
            "SELECT * FROM orders WHERE pending = 1 AND stage NOT IN (?, ?)",
            (Stage.QUEUED.value, Stage.DONE.value),
        )
        return [OrderRecord.from_row(r) for r in rows]

    def history(
        self, flt: str = "all", *, limit: int = 10, offset: int = 0
    ) -> list[OrderRecord]:
        where = HISTORY_FILTER_SQL.get(flt, "")
        rows = self.fetchall(
            f"SELECT * FROM orders {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [OrderRecord.from_row(r) for r in rows]

    def history_count(self, flt: str = "all") -> int:
        where = HISTORY_FILTER_SQL.get(flt, "")
        row = self.fetchone(f"SELECT COUNT(*) AS c FROM orders {where}")
        return row["c"] if row else 0

    @staticmethod
    def _search_clause(query: str) -> tuple[str, tuple]:
        like = f"%{query}%"
        clause = (
            "WHERE funpay_order_id LIKE ? OR swizzyer_order_id LIKE ? "
            "OR buyer_username LIKE ? OR roblox_username LIKE ?"
        )
        return clause, (like, like, like, like)

    def search(
        self, query: str, *, limit: int = 10, offset: int = 0
    ) -> list[OrderRecord]:
        clause, params = self._search_clause(query)
        rows = self.fetchall(
            f"SELECT * FROM orders {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [OrderRecord.from_row(r) for r in rows]

    def search_count(self, query: str) -> int:
        clause, params = self._search_clause(query)
        row = self.fetchone(f"SELECT COUNT(*) AS c FROM orders {clause}", params)
        return row["c"] if row else 0

    def count(self, *, pending: bool | None = None) -> int:
        if pending is None:
            row = self.fetchone("SELECT COUNT(*) AS c FROM orders")
        else:
            row = self.fetchone(
                "SELECT COUNT(*) AS c FROM orders WHERE pending = ?",
                (1 if pending else 0,),
            )
        return row["c"] if row else 0

    def stats(self, start: int, end: int) -> dict:
        row = self.fetchone(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(status = 'completed'), 0) AS completed,
                COALESCE(SUM(status = 'partially_delivered'), 0) AS partial,
                COALESCE(SUM(status IN ('failed', 'cancelled', 'expired',
                    'manual_completion_required')), 0) AS failed,
                COALESCE(SUM(status = 'refunded'), 0) AS refunded,
                COALESCE(SUM(pending = 1), 0) AS pending,
                COALESCE(SUM(json_extract(data, '$.robux_credited')), 0) AS robux,
                MIN(created_at) AS first
            FROM orders
            WHERE created_at >= ? AND created_at < ?
            """,
            (start, end),
        )
        keys = (
            "total",
            "completed",
            "partial",
            "failed",
            "refunded",
            "pending",
            "robux",
            "first",
        )
        return {k: (row[k] if row else None) for k in keys}


@dataclass
class PackComponent:
    base_key: str
    quantity: int

    @property
    def base(self) -> BaseSku | None:
        return BASE_SKUS.get(self.base_key)


@dataclass
class Pack:
    robux: int
    components: list[PackComponent] = field(default_factory=list)

    @property
    def total_price(self) -> float:
        return round(
            sum(c.base.price * c.quantity for c in self.components if c.base), 2
        )

    @property
    def total_robux(self) -> int:
        return sum(c.base.robux * c.quantity for c in self.components if c.base)

    @property
    def has_premium(self) -> bool:
        return any(c.base.premium for c in self.components if c.base)

    @property
    def total_units(self) -> int:
        return sum(c.quantity for c in self.components if c.base)

    @property
    def premium_units(self) -> int:
        return sum(c.quantity for c in self.components if c.base and c.base.premium)

    def quantity_of(self, base_key: str) -> int:
        for c in self.components:
            if c.base_key == base_key:
                return c.quantity
        return 0

    def to_items(self, multiplier: int = 1) -> list[OrderItem]:
        items = []
        for c in self.components:
            base = c.base
            if not base or c.quantity < 1:
                continue
            items.append(
                OrderItem(
                    product_id=base.product_id,
                    sku_id=base.sku_id,
                    availability_id=base.availability_id,
                    quantity=c.quantity * multiplier,
                    product_name=base.label,
                    amount=base.price,
                )
            )
        return items


def _packs_seed_sql() -> str:
    rows = []
    for robux, comps in DEFAULT_PACKS.items():
        payload = json.dumps([[k, q] for k, q in comps]).replace("'", "''")
        rows.append(f"({robux}, '{payload}')")
    return (
        "INSERT OR IGNORE INTO packs (robux, components) VALUES "
        + ", ".join(rows)
        + ";"
    )


class PacksDB(BaseDB):
    name = "packs"
    schema = """
    CREATE TABLE IF NOT EXISTS packs (
        robux INTEGER PRIMARY KEY,
        components TEXT NOT NULL DEFAULT '[]'
    );
    """
    migrations = [_packs_seed_sql()]

    @staticmethod
    def _row_to_pack(row: sqlite3.Row) -> Pack:
        comps = [PackComponent(k, q) for k, q in json.loads(row["components"] or "[]")]
        return Pack(robux=row["robux"], components=comps)

    def get(self, robux: int) -> Pack | None:
        row = self.fetchone("SELECT * FROM packs WHERE robux = ?", (robux,))
        return self._row_to_pack(row) if row else None

    def list(self) -> list[Pack]:
        rows = self.fetchall("SELECT * FROM packs ORDER BY robux ASC")
        return [self._row_to_pack(r) for r in rows]

    def save(self, pack: Pack) -> None:
        payload = json.dumps([[c.base_key, c.quantity] for c in pack.components])
        self.execute(
            "INSERT INTO packs (robux, components) VALUES (?, ?) "
            "ON CONFLICT(robux) DO UPDATE SET components = excluded.components",
            (pack.robux, payload),
        )

    def delete(self, robux: int) -> None:
        self.execute("DELETE FROM packs WHERE robux = ?", (robux,))

    def add_component(self, robux: int, base_key: str, delta: int = 1) -> Pack:
        if base_key not in BASE_SKUS:
            return self.get(robux) or Pack(robux)
        pack = self.get(robux) or Pack(robux)
        for c in pack.components:
            if c.base_key == base_key:
                c.quantity = max(0, c.quantity + delta)
                break
        else:
            if delta > 0:
                pack.components.append(PackComponent(base_key, delta))
        pack.components = [c for c in pack.components if c.quantity > 0]
        self.save(pack)
        return pack


db = Database()


class AutoRobuxMenu:
    def __init__(self, cardinal: Cardinal) -> None:
        self.cardinal = cardinal
        self.tg = cardinal.telegram
        self.bot = cardinal.telegram.bot
        self._searches: dict[int, str] = {}
        self._lot_cache: list[tuple[str, str]] = []

    def _proxies(self) -> dict[str, str] | None:
        raw = getattr(getattr(self.cardinal, "account", None), "proxy", None)
        if not raw:
            return None
        if isinstance(raw, dict):
            return raw
        url = str(raw)
        return {"http": url, "https": url}

    def register(self) -> None:
        self.tg.cbq_handler(
            self.open_settings,
            lambda c: c.data.startswith(f"{CBT.PLUGIN_SETTINGS}:{UUID}"),
        )
        self.tg.cbq_handler(self.toggle, lambda c: c.data.startswith(f"{CBT_TOGGLE}:"))
        self.tg.cbq_handler(
            self.toggle_invalid,
            lambda c: c.data.startswith(f"{CBT_TOGGLE_INVALID}:"),
        )
        self.tg.cbq_handler(
            self.toggle_ru_pass,
            lambda c: c.data.startswith(f"{CBT_TOGGLE_RU_PASS}:"),
        )
        self.tg.cbq_handler(
            self.open_refunds, lambda c: c.data.startswith(f"{CBT_REFUNDS}:")
        )
        self.tg.cbq_handler(
            self.open_deadline, lambda c: c.data.startswith(f"{CBT_DEADLINE}:")
        )
        self.tg.cbq_handler(
            self.toggle_field, lambda c: c.data.startswith(f"{CBT_TOGGLE_FIELD}:")
        )
        self.tg.cbq_handler(
            self.act_number, lambda c: c.data.startswith(f"{CBT_NUM}:")
        )
        self.tg.cbq_handler(
            self.act_refund_words, lambda c: c.data.startswith(f"{CBT_REFUND_WORDS}:")
        )
        self.tg.cbq_handler(
            self.reset_refund_words,
            lambda c: c.data.startswith(f"{CBT_REFUND_WORDS_RESET}:"),
        )
        self.tg.cbq_handler(
            self.toggle_recover,
            lambda c: c.data.startswith(f"{CBT_TOGGLE_RECOVER}:"),
        )
        self.tg.cbq_handler(
            self.toggle_pool,
            lambda c: c.data.startswith(f"{CBT_TOGGLE_POOL}:"),
        )
        self.tg.cbq_handler(
            self.open_notify, lambda c: c.data.startswith(f"{CBT_NOTIFY}:")
        )
        self.tg.cbq_handler(
            self.toggle_notify, lambda c: c.data.startswith(f"{CBT_NOTIFY_TOGGLE}:")
        )
        self.tg.cbq_handler(
            self.act_set_key, lambda c: c.data.startswith(f"{CBT_SET_KEY}:")
        )
        self.tg.cbq_handler(
            self.open_packs, lambda c: c.data.startswith(f"{CBT_PACKS}:")
        )
        self.tg.cbq_handler(self.open_pack, lambda c: c.data.startswith(f"{CBT_PACK}:"))
        self.tg.cbq_handler(
            self.pack_add, lambda c: c.data.startswith(f"{CBT_PACK_ADD}:")
        )
        self.tg.cbq_handler(
            self.pack_del, lambda c: c.data.startswith(f"{CBT_PACK_DEL}:")
        )
        self.tg.cbq_handler(
            self.pack_remove, lambda c: c.data.startswith(f"{CBT_PACK_RM}:")
        )
        self.tg.cbq_handler(
            self.pack_remove_confirm, lambda c: c.data.startswith(f"{CBT_PACK_RM_YES}:")
        )
        self.tg.cbq_handler(
            self.open_history, lambda c: c.data.startswith(f"{CBT_HISTORY}:")
        )
        self.tg.cbq_handler(
            self.open_stats, lambda c: c.data.startswith(f"{CBT_STATS}:")
        )
        self.tg.cbq_handler(
            self.act_stats_custom,
            lambda c: c.data.startswith(f"{CBT_STATS_CUSTOM}:"),
        )
        self.tg.cbq_handler(
            self.open_order, lambda c: c.data.startswith(f"{CBT_ORDER}:")
        )
        self.tg.cbq_handler(
            self.act_search, lambda c: c.data.startswith(f"{CBT_SEARCH}:")
        )
        self.tg.cbq_handler(
            self.open_messages, lambda c: c.data.startswith(f"{CBT_MESSAGES}:")
        )
        self.tg.cbq_handler(
            self.open_msg_category, lambda c: c.data.startswith(f"{CBT_MSG_CAT}:")
        )
        self.tg.cbq_handler(self.open_msg, lambda c: c.data.startswith(f"{CBT_MSG}:"))
        self.tg.cbq_handler(
            self.act_edit_msg, lambda c: c.data.startswith(f"{CBT_MSG_EDIT}:")
        )
        self.tg.cbq_handler(
            self.reset_msg, lambda c: c.data.startswith(f"{CBT_MSG_RESET}:")
        )
        self.tg.cbq_handler(
            self.reset_msg_category,
            lambda c: c.data.startswith(f"{CBT_MSG_RESET_CAT}:"),
        )
        self.tg.cbq_handler(
            self.reset_msg_all, lambda c: c.data.startswith(f"{CBT_MSG_RESET_ALL}:")
        )
        self.tg.cbq_handler(
            self.act_new_pack, lambda c: c.data.startswith(f"{CBT_PACK_NEW}:")
        )
        self.tg.cbq_handler(self.open_more, lambda c: c.data.startswith(f"{CBT_MORE}:"))
        self.tg.cbq_handler(self.open_lots, lambda c: c.data.startswith(f"{CBT_LOTS}:"))
        self.tg.cbq_handler(self.open_lot_picker, lambda c: c.data.startswith(f"{CBT_LADD}:"))
        self.tg.cbq_handler(self.select_lot, lambda c: c.data.startswith(f"{CBT_LSEL}:"))
        self.tg.cbq_handler(self.set_lot_pack, lambda c: c.data.startswith(f"{CBT_LSET}:"))
        self.tg.cbq_handler(self.ask_unbind_lot, lambda c: c.data.startswith(f"{CBT_LDEL}:"))
        self.tg.cbq_handler(self.unbind_lot, lambda c: c.data.startswith(f"{CBT_LYES}:"))
        self.tg.cbq_handler(self.noop, lambda c: c.data == CBT_NOOP)
        self.tg.msg_handler(
            self.set_key,
            func=lambda m: self.tg.check_state(
                m.chat.id, m.from_user.id, STATE_SET_KEY
            ),
        )
        self.tg.msg_handler(
            self.do_search,
            func=lambda m: self.tg.check_state(m.chat.id, m.from_user.id, STATE_SEARCH),
        )
        self.tg.msg_handler(
            self.do_stats_range,
            func=lambda m: self.tg.check_state(
                m.chat.id, m.from_user.id, STATE_STATS_RANGE
            ),
        )
        self.tg.msg_handler(
            self.save_msg,
            func=lambda m: self.tg.check_state(
                m.chat.id, m.from_user.id, STATE_EDIT_MSG
            ),
        )
        self.tg.msg_handler(
            self.new_pack,
            func=lambda m: self.tg.check_state(
                m.chat.id, m.from_user.id, STATE_NEW_PACK
            ),
        )
        self.tg.msg_handler(
            self.save_number,
            func=lambda m: self.tg.check_state(
                m.chat.id, m.from_user.id, STATE_REFUND_NUM
            ),
        )
        self.tg.msg_handler(
            self.save_refund_words,
            func=lambda m: self.tg.check_state(
                m.chat.id, m.from_user.id, STATE_REFUND_WORDS
            ),
        )

    def _client(self, api_key: str | None = None) -> SwizzyerAPI:
        return SwizzyerAPI(
            api_key or settings.api_key, proxies=self._proxies()
        )

    def keyboard(self, offset: int) -> K:
        kb = K()
        kb.add(
            B(
                f"{_dot(settings.on)} Выдача {'включена' if settings.on else 'выключена'}",
                callback_data=f"{CBT_TOGGLE}:{offset}",
            )
        )
        kb.add(
            B(
                "🔑 API ключ" if settings.api_key else "🔑 Указать API ключ",
                callback_data=f"{CBT_SET_KEY}:{offset}",
            )
        )
        kb.row(
            B(f"📦 Лоты · {len(settings.lot_bindings)}", callback_data=f"{CBT_LOTS}:0"),
            B("💎 Паки", callback_data=f"{CBT_PACKS}:0"),
        )
        kb.row(
            B("🔔 Уведомления", callback_data=f"{CBT_NOTIFY}:{offset}"),
            B("💬 Сообщения", callback_data=f"{CBT_MESSAGES}:{offset}"),
        )
        kb.row(
            B("💸 Возвраты", callback_data=f"{CBT_REFUNDS}:{offset}"),
            B(
                f"{_dot(settings.deadline_on)} Время",
                callback_data=f"{CBT_DEADLINE}:{offset}",
            ),
        )
        kb.row(
            B("📜 История", callback_data=f"{CBT_HISTORY}:0:all"),
            B("📊 Статистика", callback_data=f"{CBT_STATS}:day"),
        )
        kb.add(B("⚙️ Ещё настройки", callback_data=f"{CBT_MORE}:{offset}"))
        kb.add(B("◀️ Назад", callback_data=f"{CBT.EDIT_PLUGIN}:{UUID}:{offset}"))
        return kb

    def text(self) -> str:
        header = "💎 <b>Auto Robux</b>\n<i>Автовыдача Robux через rbcode.net</i>"
        bound = len(settings.lot_bindings)
        packs_n = len(db.packs.list())
        done = db.orders.history_count(flt="completed")
        pending = db.orders.count(pending=True)
        status = (
            f"{_dot(settings.on)} Выдача {'включена' if settings.on else 'выключена'}\n"
            f"{'🔑 Ключ задан' if settings.api_key else '🔑 Ключ не задан'}\n"
            f"📦 Привязано лотов: <code>{bound}</code>\n"
            f"💎 Паков: <code>{packs_n}</code>\n"
            f"✅ Выполнено: <code>{done}</code>\n"
            f"⏳ В работе: <code>{pending}</code>"
        )
        if not settings.api_key:
            return (
                f"{header}\n\n<blockquote>{status}</blockquote>\n\n"
                "Сначала укажи API ключ, создай паки и привяжи лоты Starvell."
            )
        try:
            sub = self._client().get_subscription()
        except SwizzyerError as e:
            if e.code in AUTH_ERROR_CODES:
                return (
                    f"{header}\n\n<blockquote>{status}</blockquote>\n\n"
                    f"❌ API ключ недействителен.\n"
                    f"Код: <code>{utils.escape(_code_str(e.code))}</code>"
                )
            message = e.message.get("ru") if e.message else ""
            return (
                f"{header}\n\n<blockquote>{status}</blockquote>\n\n"
                f"⚠️ Не удалось получить подписку.\n"
                f"Код: <code>{utils.escape(_code_str(e.code))}</code>"
                + (f"\n{utils.escape(message)}" if message else "")
            )
        except requests.RequestException:
            return (
                f"{header}\n\n<blockquote>{status}</blockquote>\n\n"
                "⚠️ Сервис rbcode.net сейчас недоступен."
            )
        left = sub.transactions_remaining
        return (
            f"{header}{self._announcement_block()}\n\n"
            f"<b>Статус</b>\n<blockquote>{status}</blockquote>\n\n"
            f"<b>Подписка</b>\n<blockquote>"
            f"• {utils.escape(sub.plan_name)}\n"
            f"• Транзакции: <code>{sub.transactions_used}</code> / "
            f"<code>{sub.transactions_limit}</code> · осталось <code>{left}</code>"
            f"</blockquote>"
            + self._wallet_block()
        )

    def _announcement_block(self) -> str:
        try:
            announcement = self._client().get_announcement()
        except (SwizzyerError, requests.RequestException):
            return ""
        if not announcement.active or not announcement.text:
            return ""
        icons = {
            AnnouncementSeverity.INFO: "ℹ️",
            AnnouncementSeverity.WARNING: "⚠️",
            AnnouncementSeverity.CRITICAL: "🚨",
        }
        icon = icons.get(announcement.severity, "ℹ️")
        return f"\n\n{icon} <i>{utils.escape(announcement.text)}</i>"

    def _wallet_block(self) -> str:
        if not settings.use_managed_pool:
            return ""
        try:
            wallet = self._client().get_wallet()
        except SwizzyerError as e:
            return (
                f"\n<b>Кошелёк</b>\n<blockquote>"
                f"⚠️ Не удалось получить баланс: "
                f"<code>{utils.escape(_code_str(e.code))}</code>"
                f"</blockquote>"
            )
        except requests.RequestException:
            return ""
        return (
            f"\n<b>Кошелёк</b>\n"
            f"<blockquote>"
            f"• Баланс: <code>{wallet.balance:.2f} $</code>\n"
            f"• В резерве: <code>{wallet.held:.2f} $</code>\n"
            f"• Доступно: <code>{wallet.available:.2f} $</code>"
            f"</blockquote>"
        )

    def open_settings(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        self.bot.edit_message_text(
            self.text(),
            c.message.chat.id,
            c.message.id,
            reply_markup=self.keyboard(offset),
        )
        self.bot.answer_callback_query(c.id)

    def more_text(self) -> str:
        return (
            "⚙️ <b>Ещё настройки</b>\n\n"
            "<blockquote>Тонкая настройка выдачи. Основные кнопки — на главном экране.</blockquote>"
        )

    def more_keyboard(self, offset: int) -> K:
        kb = K()
        kb.add(
            B(
                f"{_dot(settings.warn_invalid_login)} Сообщать о неверном логине",
                callback_data=f"{CBT_TOGGLE_INVALID}:{offset}",
            )
        )
        kb.add(
            B(
                f"{_dot(settings.ignore_russian_password)} Игнорировать русские пароли",
                callback_data=f"{CBT_TOGGLE_RU_PASS}:{offset}",
            )
        )
        kb.add(
            B(
                f"{_dot(settings.auto_recover_cancelled)} Автовосстановление отмены",
                callback_data=f"{CBT_TOGGLE_RECOVER}:{offset}",
            )
        )
        kb.add(
            B(
                f"{_dot(settings.use_managed_pool)} Пул Swizzyer (кошелёк)",
                callback_data=f"{CBT_TOGGLE_POOL}:{offset}",
            )
        )
        repeat = settings.push_approval_repeat_minutes
        kb.add(
            B(
                "Повтор Approve: " + (f"{repeat} мин" if repeat > 0 else "выкл"),
                callback_data=f"{CBT_NUM}:push_approval_repeat_minutes:more:{offset}",
            )
        )
        kb.add(B("◀️ Назад", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}:{offset}"))
        return kb

    def _render_more(self, c: CallbackQuery, offset: int) -> None:
        self.bot.edit_message_text(
            self.more_text(),
            c.message.chat.id,
            c.message.id,
            reply_markup=self.more_keyboard(offset),
        )

    def open_more(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        self._render_more(c, offset)
        self.bot.answer_callback_query(c.id)

    def _binding_key(self, token: str) -> str | None:
        for offer_id in settings.lot_bindings:
            if _lot_token(offer_id) == token:
                return offer_id
        return None

    def _load_starvell_lots(self) -> list[tuple[str, str]]:
        account = getattr(self.cardinal, "account", None)
        if account is None:
            return []
        try:
            lots = account.get_lots()
        except Exception:
            logger.debug("не удалось получить лоты Starvell", exc_info=True)
            return []
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for lot in lots or []:
            offer_id = str(getattr(lot, "id", "") or "").strip()
            title = str(getattr(lot, "title", "") or offer_id).strip() or offer_id
            if not offer_id or offer_id in seen:
                continue
            seen.add(offer_id)
            result.append((offer_id, title))
        self._lot_cache = result
        return result

    def lots_text(self) -> str:
        items = list(settings.lot_bindings.items())
        if not items:
            body = "Пока ни один лот не привязан."
        else:
            lines = []
            for offer_id, robux in items:
                title = settings.lot_titles.get(offer_id) or offer_id
                lines.append(f"• {utils.escape(title)} → <code>{robux} R$</code>")
            body = "\n".join(lines)
        return (
            "📦 <b>Привязка лотов</b>\n\n"
            "Привяжи лот Starvell к паку робуксов. Заказ по этому лоту сразу "
            "пойдёт в автовыдачу — без угадывания по названию.\n\n"
            f"<blockquote>{body}</blockquote>\n\n"
            "<i>Если лот не привязан, бот по-прежнему ищет число робуксов в названии.</i>"
        )

    def lots_keyboard(self, page: int) -> K:
        kb = K()
        items = list(settings.lot_bindings.items())
        chunk = items[page : page + LOTS_PER_PAGE]
        for offer_id, robux in chunk:
            title = settings.lot_titles.get(offer_id) or offer_id
            label = f"📌 {title[:28]} → {robux} R$"
            kb.add(
                B(
                    label,
                    callback_data=f"{CBT_LDEL}:{_lot_token(offer_id)}:{page}",
                )
            )
        utils.add_navigation_buttons(
            kb, page, LOTS_PER_PAGE, len(chunk), len(items), CBT_LOTS
        )
        kb.add(B("➕ Привязать лот", callback_data=f"{CBT_LADD}:0"))
        kb.add(B("◀️ Назад", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}:0"))
        return kb

    def open_lots(self, c: CallbackQuery) -> None:
        page = int(c.data.split(":")[-1] or 0)
        self.bot.edit_message_text(
            self.lots_text(),
            c.message.chat.id,
            c.message.id,
            reply_markup=self.lots_keyboard(page),
        )
        self.bot.answer_callback_query(c.id)

    def open_lot_picker(self, c: CallbackQuery) -> None:
        page = int(c.data.split(":")[-1] or 0)
        lots = self._load_starvell_lots()
        if not db.packs.list():
            self.bot.answer_callback_query(
                c.id, "Сначала создай хотя бы один пак робуксов.", show_alert=True
            )
            return
        if not lots:
            self.bot.answer_callback_query(
                c.id, "Лоты Starvell не загрузились. Проверь cookie.", show_alert=True
            )
            return
        chunk = lots[page : page + LOTS_PER_PAGE]
        kb = K()
        for index, (offer_id, title) in enumerate(chunk, start=page):
            bound = settings.lot_bindings.get(offer_id)
            mark = f" · {bound} R$" if bound else ""
            kb.add(
                B(
                    f"{title[:40]}{mark}",
                    callback_data=f"{CBT_LSEL}:{index}:{page}",
                )
            )
        utils.add_navigation_buttons(
            kb, page, LOTS_PER_PAGE, len(chunk), len(lots), CBT_LADD
        )
        kb.add(B("◀️ К привязкам", callback_data=f"{CBT_LOTS}:0"))
        self.bot.edit_message_text(
            "📦 <b>Какой лот привязать?</b>\n\n"
            "Нажми лот, затем выбери пак робуксов.",
            c.message.chat.id,
            c.message.id,
            reply_markup=kb,
        )
        self.bot.answer_callback_query(c.id)

    def select_lot(self, c: CallbackQuery) -> None:
        _, index_s, page_s = c.data.split(":")
        index, page = int(index_s), int(page_s)
        if not self._lot_cache:
            self._load_starvell_lots()
        if index < 0 or index >= len(self._lot_cache):
            self.bot.answer_callback_query(c.id, "Лот не найден, открой список ещё раз.", show_alert=True)
            return
        offer_id, title = self._lot_cache[index]
        packs = db.packs.list()
        kb = K()
        for pack in packs:
            kb.add(
                B(
                    f"{pack.robux} R$ → покупатель {pack.total_robux}",
                    callback_data=f"{CBT_LSET}:{index}:{pack.robux}:{page}",
                )
            )
        kb.add(B("◀️ К лотам", callback_data=f"{CBT_LADD}:{page}"))
        self.bot.edit_message_text(
            f"💎 <b>Пак для лота</b>\n\n"
            f"<blockquote>{utils.escape(title)}</blockquote>\n"
            f"<code>{utils.escape(offer_id)}</code>",
            c.message.chat.id,
            c.message.id,
            reply_markup=kb,
        )
        self.bot.answer_callback_query(c.id)

    def set_lot_pack(self, c: CallbackQuery) -> None:
        _, index_s, robux_s, page_s = c.data.split(":")
        index, robux, page = int(index_s), int(robux_s), int(page_s)
        if not self._lot_cache:
            self._load_starvell_lots()
        if index < 0 or index >= len(self._lot_cache):
            self.bot.answer_callback_query(c.id, "Лот не найден.", show_alert=True)
            return
        offer_id, title = self._lot_cache[index]
        settings.bind_lot(offer_id, title, robux)
        self.bot.edit_message_text(
            self.lots_text(),
            c.message.chat.id,
            c.message.id,
            reply_markup=self.lots_keyboard(0),
        )
        self.bot.answer_callback_query(c.id, f"Привязан пак {robux} R$")

    def ask_unbind_lot(self, c: CallbackQuery) -> None:
        _, token, page_s = c.data.split(":")
        page = int(page_s)
        offer_id = self._binding_key(token)
        if not offer_id:
            self.bot.edit_message_text(
                self.lots_text(),
                c.message.chat.id,
                c.message.id,
                reply_markup=self.lots_keyboard(page),
            )
            self.bot.answer_callback_query(c.id, "Привязка уже снята.")
            return
        title = settings.lot_titles.get(offer_id) or offer_id
        robux = settings.lot_bindings.get(offer_id)
        kb = K()
        kb.row(
            B("✅ Отвязать", callback_data=f"{CBT_LYES}:{token}:{page}"),
            B("❌ Нет", callback_data=f"{CBT_LOTS}:{page}"),
        )
        self.bot.edit_message_text(
            "📦 <b>Отвязать лот?</b>\n\n"
            f"<blockquote>{utils.escape(title)}\n→ пак <code>{robux} R$</code></blockquote>",
            c.message.chat.id,
            c.message.id,
            reply_markup=kb,
        )
        self.bot.answer_callback_query(c.id)

    def unbind_lot(self, c: CallbackQuery) -> None:
        _, token, page_s = c.data.split(":")
        page = int(page_s)
        offer_id = self._binding_key(token)
        if offer_id:
            settings.unbind_lot(offer_id)
        self.bot.edit_message_text(
            self.lots_text(),
            c.message.chat.id,
            c.message.id,
            reply_markup=self.lots_keyboard(page),
        )
        self.bot.answer_callback_query(c.id, "Лот отвязан")

    def noop(self, c: CallbackQuery) -> None:
        self.bot.answer_callback_query(c.id, "swizzyer.com", show_alert=False)

    def toggle(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        settings.on = not settings.on
        settings.save()
        self.bot.edit_message_text(
            self.text(),
            c.message.chat.id,
            c.message.id,
            reply_markup=self.keyboard(offset),
        )
        self.bot.answer_callback_query(c.id)

    def toggle_invalid(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        settings.warn_invalid_login = not settings.warn_invalid_login
        settings.save()
        self._render_more(c, offset)
        self.bot.answer_callback_query(c.id)

    def toggle_ru_pass(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        settings.ignore_russian_password = not settings.ignore_russian_password
        settings.save()
        self._render_more(c, offset)
        self.bot.answer_callback_query(c.id)

    def toggle_recover(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        settings.auto_recover_cancelled = not settings.auto_recover_cancelled
        settings.save()
        self._render_more(c, offset)
        self.bot.answer_callback_query(c.id)

    def toggle_pool(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        settings.use_managed_pool = not settings.use_managed_pool
        settings.save()
        self._render_more(c, offset)
        self.bot.answer_callback_query(
            c.id,
            "Заказы пойдут на аккаунты Swizzyer, стоимость робуксов спишется с кошелька"
            if settings.use_managed_pool
            else "Заказы пойдут на ваши Microsoft-аккаунты",
            show_alert=settings.use_managed_pool,
        )

    @staticmethod
    def refunds_text() -> str:
        return (
            "💸 <b>Автоматические возвраты</b>\n\n"
            "<blockquote>Выбери, в каких случаях бот сам оформляет возврат средств "
            "покупателю. Текст, который при этом получит покупатель, "
            "настраивается в разделе «Сообщения».</blockquote>\n"
            "<blockquote>🛡 Возврат никогда не оформляется, если сервис уже списал "
            "деньги или выдал часть робуксов — в таком случае просто придёт "
            "уведомление.</blockquote>"
        )

    @staticmethod
    def _number_button(field_name: str, screen: str, offset: int) -> B:
        unit = NUMBER_FIELDS[field_name][4]
        value = getattr(settings, field_name)
        return B(
            f"{value}" + (f" {unit}" if unit else ""),
            callback_data=f"{CBT_NUM}:{field_name}:{screen}:{offset}",
        )

    @classmethod
    def _toggle_rows(cls, rows: list, screen: str, offset: int, kb: K) -> None:
        for field_name, label, extra in rows:
            enabled = getattr(settings, field_name, False)
            row = [
                B(
                    f"{_dot(enabled)} {label}",
                    callback_data=f"{CBT_TOGGLE_FIELD}:{field_name}:{screen}:{offset}",
                )
            ]
            if extra == "keywords":
                row.append(B("Слова", callback_data=f"{CBT_REFUND_WORDS}:{offset}"))
            elif extra:
                row.append(cls._number_button(extra, screen, offset))
            kb.row(*row)

    @classmethod
    def refunds_keyboard(cls, offset: int) -> K:
        kb = K()
        cls._toggle_rows(REFUND_TOGGLE_ROWS, "refunds", offset, kb)
        kb.add(B("◀️ Назад", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}:{offset}"))
        return kb

    @staticmethod
    def deadline_text() -> str:
        if not settings.deadline_on:
            state = "<blockquote>Контроль времени выключен.</blockquote>"
        else:
            points = []
            if settings.deadline_warn_on:
                points.append(
                    f"<code>{settings.deadline_warn_minutes} мин</code> — "
                    f"предупреждение тебе"
                )
            if settings.deadline_refund_on:
                if settings.deadline_buyer_warn_on:
                    before = max(
                        0,
                        settings.deadline_refund_minutes
                        - settings.deadline_buyer_warn_minutes,
                    )
                    points.append(
                        f"<code>{before} мин</code> — предупреждение покупателю"
                    )
                points.append(
                    f"<code>{settings.deadline_refund_minutes} мин</code> — "
                    f"авто-возврат"
                )
            points.append(
                f"<code>{settings.deadline_minutes} мин</code> — регламент площадки"
            )
            state = "<blockquote>" + "\n".join("• " + p for p in points) + "</blockquote>"
            if (
                settings.deadline_refund_on
                and settings.deadline_refund_minutes >= settings.deadline_minutes
            ):
                state += (
                    "\n⚠️ Возврат стоит позже регламента — запаса времени не остаётся."
                )
        return (
            "⏱ <b>Время выполнения заказа</b>\n\n"
            "<blockquote>Отсчёт идёт от появления заказа на Starvell, "
            "заказы в очереди считаются наравне с остальными.</blockquote>\n"
            f"{state}\n"
            "<blockquote>🛡 Если к моменту возврата деньги уже списаны или робуксы "
            "выданы частично, возврат не делается — вместо него приходит срочное "
            "уведомление с обратным отсчётом, пока не выйдет регламент.</blockquote>"
        )

    @classmethod
    def deadline_keyboard(cls, offset: int) -> K:
        kb = K()
        cls._toggle_rows(DEADLINE_TOGGLE_ROWS, "deadline", offset, kb)
        for field_name in DEADLINE_NUMBER_ROWS:
            label = NUMBER_FIELDS[field_name][0]
            kb.row(
                B(label, callback_data=CBT_NOOP),
                cls._number_button(field_name, "deadline", offset),
            )
        kb.add(B("◀️ Назад", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}:{offset}"))
        return kb

    def _render_screen(
        self, screen: str, chat_id: int, message_id: int, offset: int
    ) -> None:
        if screen == "deadline":
            text, kb = self.deadline_text(), self.deadline_keyboard(offset)
        elif screen == "more":
            text, kb = self.more_text(), self.more_keyboard(offset)
        else:
            text, kb = self.refunds_text(), self.refunds_keyboard(offset)
        self.bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)

    def _render_refunds(self, chat_id: int, message_id: int, offset: int) -> None:
        self._render_screen("refunds", chat_id, message_id, offset)

    def open_refunds(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        self._render_screen("refunds", c.message.chat.id, c.message.id, offset)
        self.bot.answer_callback_query(c.id)

    def open_deadline(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        self._render_screen("deadline", c.message.chat.id, c.message.id, offset)
        self.bot.answer_callback_query(c.id)

    def toggle_field(self, c: CallbackQuery) -> None:
        _, field_name, screen, offset = c.data.split(":")
        if field_name not in TOGGLE_FIELDS:
            self.bot.answer_callback_query(c.id, "Настройка не найдена", show_alert=True)
            return
        setattr(settings, field_name, not getattr(settings, field_name))
        settings.save()
        self._render_screen(screen, c.message.chat.id, c.message.id, int(offset))
        self.bot.answer_callback_query(c.id)

    def act_number(self, c: CallbackQuery) -> None:
        _, field_name, back, offset = c.data.split(":")
        spec = NUMBER_FIELDS.get(field_name)
        if not spec:
            self.bot.answer_callback_query(c.id, "Настройка не найдена", show_alert=True)
            return
        label, hint, minimum, maximum, unit = spec
        result = self.bot.send_message(
            c.message.chat.id,
            f"<b> -|- {utils.escape(label)}</b>\n\n"
            f"<blockquote>{hint}</blockquote>\n\n"
            f"Текущее значение: <code>{getattr(settings, field_name)}</code>\n"
            f"Допустимо: от <code>{minimum}</code> до <code>{maximum}</code>"
            + (f" ({unit})" if unit else ""),
            reply_markup=skb.CLEAR_STATE_BTN(),
        )
        self.tg.set_state(
            result.chat.id,
            result.id,
            c.from_user.id,
            STATE_REFUND_NUM,
            {"field": field_name, "back": back, "offset": offset},
        )
        self.bot.answer_callback_query(c.id)

    @staticmethod
    def _back_button(back: str, offset: str) -> K:
        targets = {
            "refunds": f"{CBT_REFUNDS}:{offset}",
            "deadline": f"{CBT_DEADLINE}:{offset}",
            "more": f"{CBT_MORE}:{offset}",
        }
        target = targets.get(back, f"{CBT.PLUGIN_SETTINGS}:{UUID}:{offset}")
        return K().add(B("◀️ Назад", callback_data=target))

    def save_number(self, m: Message) -> None:
        data = self.tg.get_state(m.chat.id, m.from_user.id)["data"]
        self.tg.clear_state(m.chat.id, m.from_user.id, True)
        field_name, back, offset = data["field"], data["back"], data["offset"]
        back_kb = self._back_button(back, offset)
        spec = NUMBER_FIELDS.get(field_name)
        if not spec:
            self.bot.send_message(m.chat.id, "❌ Настройка не найдена.")
            return
        label, _, minimum, maximum, unit = spec
        raw = (m.text or "").strip()
        if not raw.lstrip("-").isdigit():
            self.bot.send_message(
                m.chat.id, "❌ Нужно целое число.", reply_markup=back_kb
            )
            return
        value = int(raw)
        if not minimum <= value <= maximum:
            self.bot.send_message(
                m.chat.id,
                f"❌ Значение должно быть от <code>{minimum}</code> "
                f"до <code>{maximum}</code>.",
                reply_markup=back_kb,
            )
            return
        setattr(settings, field_name, value)
        settings.save()
        self.bot.send_message(
            m.chat.id,
            f"✅ «{label}» — <code>{value}</code>" + (f" {unit}" if unit else ""),
            reply_markup=back_kb,
        )

    def act_refund_words(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        current = "\n".join(settings.refund_request_keywords) or "—"
        kb = K()
        kb.add(B("♻️ По умолчанию", callback_data=f"{CBT_REFUND_WORDS_RESET}:{offset}"))
        kb.add(B("❌ Отмена", callback_data=CBT.CLEAR_STATE))
        result = self.bot.send_message(
            c.message.chat.id,
            "<b> -|- Слова для возврата по просьбе</b>\n\n"
            "<blockquote>Если сообщение покупателя содержит любую из этих фраз "
            "и выдача ещё не запущена — бот оформит возврат.</blockquote>\n\n"
            f"<b>-| Сейчас ({len(settings.refund_request_keywords)})</b>\n"
            f"<blockquote><code>{utils.escape(current)}</code></blockquote>\n\n"
            "Отправь новый список — по одной фразе в строке или через запятую.",
            reply_markup=kb,
        )
        self.tg.set_state(
            result.chat.id, result.id, c.from_user.id, STATE_REFUND_WORDS, {"offset": offset}
        )
        self.bot.answer_callback_query(c.id)

    def save_refund_words(self, m: Message) -> None:
        offset = self.tg.get_state(m.chat.id, m.from_user.id)["data"]["offset"]
        self.tg.clear_state(m.chat.id, m.from_user.id, True)
        back_kb = K().add(B("◀️ К возвратам", callback_data=f"{CBT_REFUNDS}:{offset}"))
        raw = m.text or ""
        words = [
            part.strip().lower()
            for chunk in raw.splitlines()
            for part in chunk.split(",")
            if part.strip()
        ]
        if not words:
            self.bot.send_message(
                m.chat.id, "❌ Пустой список.", reply_markup=back_kb
            )
            return
        settings.refund_request_keywords = list(dict.fromkeys(words))
        settings.save()
        self.bot.send_message(
            m.chat.id,
            f"✅ Сохранено фраз: <code>{len(settings.refund_request_keywords)}</code>",
            reply_markup=back_kb,
        )

    def reset_refund_words(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        self.tg.clear_state(c.message.chat.id, c.from_user.id, False)
        settings.refund_request_keywords = list(DEFAULT_REFUND_KEYWORDS)
        settings.save()
        self._render_refunds(c.message.chat.id, c.message.id, offset)
        self.bot.answer_callback_query(c.id, "Список сброшен")

    def notify_text(self) -> str:
        return (
            "🔔 <b>Уведомления</b>\n\n"
            "<blockquote>Какие события слать в этот Telegram-чат.</blockquote>"
        )

    def notify_keyboard(self, chat_id: int, offset: int) -> K:
        kb = K()
        kb.add(
            B(
                f"{_dot(chat_id in settings.notify_success)} Успешная выдача",
                callback_data=f"{CBT_NOTIFY_TOGGLE}:success:{offset}",
            ),
            B(
                f"{_dot(chat_id in settings.notify_failure)} Проблемы с выдачей",
                callback_data=f"{CBT_NOTIFY_TOGGLE}:failure:{offset}",
            ),
        )
        kb.add(B("◀️ Назад", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}:{offset}"))
        return kb

    def open_notify(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        chat_id = c.message.chat.id
        self.bot.edit_message_text(
            self.notify_text(),
            chat_id,
            c.message.id,
            reply_markup=self.notify_keyboard(chat_id, offset),
        )
        self.bot.answer_callback_query(c.id)

    def toggle_notify(self, c: CallbackQuery) -> None:
        _, kind, offset = c.data.split(":")
        chat_id = c.message.chat.id
        field_name = "notify_success" if kind == "success" else "notify_failure"
        settings.toggle_notify(field_name, chat_id)
        self.bot.edit_message_text(
            self.notify_text(),
            chat_id,
            c.message.id,
            reply_markup=self.notify_keyboard(chat_id, int(offset)),
        )
        self.bot.answer_callback_query(c.id)

    def act_set_key(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        result = self.bot.send_message(
            c.message.chat.id,
            "<b> -|- API ключ</b>\n\n"
            "<b>-| Где взять ключ</b>\n"
            "<blockquote>"
            '1. Открой <a href="https://swizzyer.com/dashboard/api/keys">'
            "swizzyer.com/dashboard/api/keys</a>\n"
            "2. «Создать API-ключ»\n"
            "3. Права: «Чтение и запись»\n"
            "4. «Создать ключ»"
            "</blockquote>\n\n"
            "Отправь полученный ключ в формате <code>swz_live_...</code>",
            reply_markup=skb.CLEAR_STATE_BTN(),
        )
        self.tg.set_state(
            result.chat.id, result.id, c.from_user.id, STATE_SET_KEY, {"offset": offset}
        )
        self.bot.answer_callback_query(c.id)

    def set_key(self, m: Message) -> None:
        offset = self.tg.get_state(m.chat.id, m.from_user.id)["data"]["offset"]
        self.tg.clear_state(m.chat.id, m.from_user.id, True)
        back_kb = K().add(
            B("◀️ Назад", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}:{offset}")
        )
        key = (m.text or "").strip()
        if not key:
            self.bot.send_message(m.chat.id, "❌ Пустой ключ.", reply_markup=back_kb)
            return
        try:
            self._client(key).get_subscription()
        except SwizzyerError as e:
            if e.code in AUTH_ERROR_CODES:
                self.bot.send_message(
                    m.chat.id,
                    f"❌ Неверный API ключ.\nКод: <code>{utils.escape(_code_str(e.code))}</code>",
                    reply_markup=back_kb,
                )
                return
            logger.warning(f"Ключ принят, но запрос подписки вернул ошибку: {e}")
        except requests.RequestException:
            self.bot.send_message(
                m.chat.id,
                "⚠️ Не удалось проверить ключ — сервис недоступен. Ключ сохранён, проверьте позже.",
                reply_markup=back_kb,
            )
            settings.api_key = key
            settings.save()
            return
        settings.api_key = key
        settings.save()
        self.bot.send_message(m.chat.id, "✅ API ключ сохранён.", reply_markup=back_kb)

    def _packs_text(self) -> str:
        n = len(db.packs.list())
        return (
            "💎 <b>Паки робуксов</b>\n\n"
            f"• Всего паков: <code>{n}</code>\n\n"
            "<i>номинал • цена → сколько получит покупатель</i>\n"
        )

    def _packs_kb(self, offset: int) -> K:
        packs = db.packs.list()
        page = packs[offset : offset + PACKS_PER_PAGE]
        kb = K()
        for pack in page:
            prem = " ✦" if pack.has_premium else ""
            kb.add(
                B(
                    f"{pack.robux} R$ • ${pack.total_price:.2f} → {pack.total_robux}{prem}",
                    callback_data=f"{CBT_PACK}:{pack.robux}:{offset}",
                )
            )
        utils.add_navigation_buttons(
            kb, offset, PACKS_PER_PAGE, len(page), len(packs), CBT_PACKS
        )
        kb.add(B("➕ Создать пак", callback_data=f"{CBT_PACK_NEW}:{offset}"))
        kb.add(B("◀️ Назад", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}:0"))
        return kb

    def _pack_text(self, pack: Pack) -> str:
        if pack.components:
            lines = []
            for c in pack.components:
                if not c.base:
                    continue
                lines.append(
                    f"• {c.base.label} ×{c.quantity} — ${c.base.price * c.quantity:.2f}"
                )
            composition = "\n".join(lines)
        else:
            composition = "<i>пусто — добавь товары кнопками ниже</i>"
        return (
            f"<b> -|- Пак {pack.robux} R$</b>\n\n"
            f"<b>-| Состав:</b>\n<blockquote>{composition}</blockquote>\n\n"
            f"• Цена: <code>${pack.total_price:.2f}</code>\n"
            f"• Покупатель получит: <code>{pack.total_robux} R$</code>"
            + (
                "\n\n✦ <b><i>Включает Roblox Premium</i></b>"
                if pack.has_premium
                else ""
            )
        )

    def _pack_kb(self, pack: Pack, offset: int, ask_delete: bool = False) -> K:
        kb = K()
        for base in BASE_SKUS.values():
            qty = pack.quantity_of(base.key)
            minus = (
                B(
                    "➖",
                    callback_data=f"{CBT_PACK_DEL}:{pack.robux}:{base.key}:{offset}",
                )
                if qty
                else B(" ", callback_data=CBT_NOOP)
            )
            label = B(f"{base.label} ×{qty}", callback_data=CBT_NOOP)
            plus = B(
                "➕", callback_data=f"{CBT_PACK_ADD}:{pack.robux}:{base.key}:{offset}"
            )
            kb.row(minus, label, plus)
        if ask_delete:
            kb.row(
                B("✅ Да", callback_data=f"{CBT_PACK_RM_YES}:{pack.robux}:{offset}"),
                B("❌ Нет", callback_data=f"{CBT_PACK}:{pack.robux}:{offset}"),
            )
        else:
            kb.add(
                B("🗑 Удалить пак", callback_data=f"{CBT_PACK_RM}:{pack.robux}:{offset}")
            )
        kb.add(B("◀️ К пакам", callback_data=f"{CBT_PACKS}:{offset}"))
        return kb

    def open_packs(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        self.bot.edit_message_text(
            self._packs_text(),
            c.message.chat.id,
            c.message.id,
            reply_markup=self._packs_kb(offset),
        )
        self.bot.answer_callback_query(c.id)

    def open_pack(self, c: CallbackQuery) -> None:
        _, robux, offset = c.data.split(":")
        self._show_pack(c.message.chat.id, c.message.id, int(robux), int(offset))
        self.bot.answer_callback_query(c.id)

    def _show_pack(
        self,
        chat_id: int,
        msg_id: int,
        robux: int,
        offset: int,
        ask_delete: bool = False,
    ) -> None:
        pack = db.packs.get(robux) or Pack(robux)
        self.bot.edit_message_text(
            self._pack_text(pack),
            chat_id,
            msg_id,
            reply_markup=self._pack_kb(pack, offset, ask_delete),
        )

    def pack_add(self, c: CallbackQuery) -> None:
        _, robux, base_key, offset = c.data.split(":")
        db.packs.add_component(int(robux), base_key, 1)
        self._show_pack(c.message.chat.id, c.message.id, int(robux), int(offset))
        self.bot.answer_callback_query(c.id)

    def pack_del(self, c: CallbackQuery) -> None:
        _, robux, base_key, offset = c.data.split(":")
        db.packs.add_component(int(robux), base_key, -1)
        self._show_pack(c.message.chat.id, c.message.id, int(robux), int(offset))
        self.bot.answer_callback_query(c.id)

    def pack_remove(self, c: CallbackQuery) -> None:
        _, robux, offset = c.data.split(":")
        self._show_pack(
            c.message.chat.id, c.message.id, int(robux), int(offset), ask_delete=True
        )
        self.bot.answer_callback_query(c.id)

    def pack_remove_confirm(self, c: CallbackQuery) -> None:
        _, robux, offset = c.data.split(":")
        db.packs.delete(int(robux))
        self.bot.edit_message_text(
            self._packs_text(),
            c.message.chat.id,
            c.message.id,
            reply_markup=self._packs_kb(int(offset)),
        )
        self.bot.answer_callback_query(c.id, "Пак удалён")

    def act_new_pack(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        result = self.bot.send_message(
            c.message.chat.id,
            "<b> -|- Новый пак</b>\n\n"
            "Введи номинал пака — число робуксов, как в названии лота на Starvell.\n"
            "<blockquote>Например: <code>1500</code></blockquote>",
            reply_markup=skb.CLEAR_STATE_BTN(),
        )
        self.tg.set_state(
            result.chat.id,
            result.id,
            c.from_user.id,
            STATE_NEW_PACK,
            {"offset": offset},
        )
        self.bot.answer_callback_query(c.id)

    def new_pack(self, m: Message) -> None:
        offset = self.tg.get_state(m.chat.id, m.from_user.id)["data"]["offset"]
        self.tg.clear_state(m.chat.id, m.from_user.id, True)
        raw = (m.text or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            kb = K().add(B("◀️ К пакам", callback_data=f"{CBT_PACKS}:{offset}"))
            self.bot.send_message(
                m.chat.id, "❌ Нужно положительное целое число.", reply_markup=kb
            )
            return
        robux = int(raw)
        pack = db.packs.get(robux)
        if pack is None:
            pack = Pack(robux)
            db.packs.save(pack)
        self.bot.send_message(
            m.chat.id, self._pack_text(pack), reply_markup=self._pack_kb(pack, offset)
        )

    @staticmethod
    def _order_emoji(record: OrderRecord) -> str:
        if record.pending:
            return "⏳"
        if record.status == OrderStatus.COMPLETED.value:
            return "✅"
        if record.status == OrderStatus.PARTIALLY_DELIVERED.value:
            return "⚠️"
        if record.status == MANUAL_COMPLETION_STATUS:
            return "🛠"
        return "❌"

    @staticmethod
    def _status_label(record: OrderRecord) -> str:
        if record.pending:
            return (
                "⏳ В работе "
                f"({STAGE_LABELS.get(record.stage, record.stage.value)})"
            )
        labels = {
            OrderStatus.COMPLETED.value: "✅ Выполнен",
            OrderStatus.PARTIALLY_DELIVERED.value: "⚠️ Частично выдан",
            OrderStatus.FAILED.value: "❌ Ошибка",
            OrderStatus.CANCELLED.value: "❌ Отменён",
            OrderStatus.EXPIRED.value: "⌛ Просрочен",
            MANUAL_COMPLETION_STATUS: "🛠 Нужно завершить вручную",
            "refunded": "💸 Возврат",
        }
        return labels.get(record.status, record.status or "—")

    def _order_short(self, record: OrderRecord) -> str:
        date = datetime.fromtimestamp(record.created_at).strftime("%d.%m %H:%M")
        return f"{self._order_emoji(record)} {record.robux_amount} R$ • {record.buyer_username[:16]} • {date}"

    def _history_text(self, flt: str, query: str | None = None) -> str:
        if flt == "search":
            total = db.orders.search_count(query) if query else 0
            return (
                "📜 <b>История заказов</b>\n\n"
                f"<blockquote>"
                f"Поиск: <code>{utils.escape(query or '')}</code>\n"
                f"Найдено: <code>{total}</code>\n"
                f"</blockquote>"
            )
        total = db.orders.history_count(flt)
        return (
            "📜 <b>История заказов</b>\n\n"
            f"<blockquote>"
            f"Фильтр: <b>{HISTORY_FILTER_LABELS[flt]}</b>\n"
            f"Найдено: <code>{total}</code>"
            f"</blockquote>"
        )

    def _history_kb(self, flt: str, offset: int, query: str | None = None) -> K:
        kb = K()
        buttons = []
        for f in HISTORY_FILTERS:
            label = (
                ("• " if f == flt else "")
                + HISTORY_FILTER_LABELS[f]
                + (" •" if f == flt else "")
            )
            buttons.append(B(label, callback_data=f"{CBT_HISTORY}:0:{f}"))
        for i in range(0, len(buttons), 2):
            kb.row(*buttons[i : i + 2])
        kb.add(B("🔍 Поиск", callback_data=f"{CBT_SEARCH}:{offset}"))
        if flt == "search":
            orders = (
                db.orders.search(query, limit=HISTORY_PER_PAGE, offset=offset)
                if query
                else []
            )
            total = db.orders.search_count(query) if query else 0
        else:
            orders = db.orders.history(flt, limit=HISTORY_PER_PAGE, offset=offset)
            total = db.orders.history_count(flt)
        for record in orders:
            kb.add(
                B(
                    self._order_short(record),
                    callback_data=f"{CBT_ORDER}:{offset}:{flt}:{record.funpay_order_id}",
                )
            )
        utils.add_navigation_buttons(
            kb, offset, HISTORY_PER_PAGE, len(orders), total, CBT_HISTORY, extra=[flt]
        )
        kb.add(B("◀️ Назад", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}:0"))
        return kb

    def _order_detail_text(self, record: OrderRecord) -> str:
        created = datetime.fromtimestamp(record.created_at).strftime("%d.%m.%Y %H:%M")
        updated = datetime.fromtimestamp(record.updated_at).strftime("%d.%m.%Y %H:%M")
        info = [
            f"• Покупатель: <code>{utils.escape(record.buyer_username)}</code> (ID: <code>{record.buyer_id}</code>)",
            f"• Roblox: <code>{utils.escape(record.roblox_username or '—')}</code>",
            f"• Пак: <code>{record.robux_amount} R$</code>",
            f"• Статус: {self._status_label(record)}",
        ]
        if record.data.get("robux_credited") is not None:
            info.append(f"• Начислено: <code>{record.data['robux_credited']} R$</code>")
        if record.data.get("reason"):
            info.append(f"• Причина: {utils.escape(str(record.data['reason']))}")
        if record.swizzyer_order_id:
            info.append(
                f"• Swizzyer: <code>{utils.escape(record.swizzyer_order_id)}</code>"
            )
        return (
            f"<b> -|- Заказ {utils.escape(record.funpay_order_id)}</b>\n\n"
            f"<blockquote>" + "\n".join(info) + "</blockquote>\n\n"
            f"<b>-| Время</b>\n"
            f"<blockquote>"
            f"• Создан: <code>{created}</code>\n"
            f"• Обновлён: <code>{updated}</code>"
            f"</blockquote>"
        )

    def open_history(self, c: CallbackQuery) -> None:
        parts = c.data.split(":")
        offset = int(parts[1])
        flt = parts[2] if len(parts) > 2 else "all"
        if flt != "search" and flt not in HISTORY_FILTER_SQL:
            flt = "all"
        query = self._searches.get(c.message.chat.id) if flt == "search" else None
        self.bot.edit_message_text(
            self._history_text(flt, query),
            c.message.chat.id,
            c.message.id,
            reply_markup=self._history_kb(flt, offset, query),
        )
        self.bot.answer_callback_query(c.id)

    @staticmethod
    def _stats_bounds(period: str) -> tuple[int, int]:
        far = 9_999_999_999
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "yesterday":
            yday = today - timedelta(days=1)
            return int(yday.timestamp()), int(today.timestamp())
        if period == "day":
            return int(today.timestamp()), far
        if period == "week":
            monday = today - timedelta(days=today.weekday())
            return int(monday.timestamp()), far
        if period == "month":
            return int(today.replace(day=1).timestamp()), far
        return 0, far  # all

    def _stats_text(self, period: str, start: int, end: int, label: str) -> str:
        s = db.orders.stats(start, end)
        total = s["total"]
        done = s["completed"]
        conv = f"{done / total * 100:.0f}%" if total else "—"
        fmt = "%d.%m.%Y %H:%M:%S"
        now = datetime.now()
        start_ts = (s["first"] or int(now.timestamp())) if period == "all" else start
        start_str = datetime.fromtimestamp(start_ts).strftime(fmt)
        if period == "yesterday":
            end_str = datetime.fromtimestamp(end - 1).strftime(fmt)
        elif period == "custom":
            end_str = datetime.fromtimestamp(end).strftime(fmt)
        else:
            end_str = now.strftime(fmt)
        return (
            f"<b> -|- Статистика · {label}</b>\n"
            f"<blockquote>{start_str} → {end_str}</blockquote>\n\n"
            f"<b>-| Заказы</b>\n"
            f"<blockquote>"
            f"• Всего: <code>{total}</code>\n"
            f"• Выполнено: <code>{done}</code>\n"
            f"• Частично: <code>{s['partial']}</code>\n"
            f"• Провалено: <code>{s['failed']}</code>\n"
            f"• Возвраты: <code>{s['refunded']}</code>\n"
            f"• В работе: <code>{s['pending']}</code>"
            f"</blockquote>\n\n"
            f"<b>-| Итоги</b>\n"
            f"<blockquote>"
            f"• Выдано робуксов: <code>{s['robux']} R$</code>\n"
            f"• Конверсия: <code>{conv}</code>"
            f"</blockquote>"
        )

    def _stats_kb(self, period: str) -> K:
        kb = K()
        buttons = [
            B(
                ("• " if p == period else "") + STATS_PERIOD_LABELS[p],
                callback_data=f"{CBT_STATS}:{p}",
            )
            for p in STATS_PERIODS
        ]
        for i in range(0, len(buttons), 2):
            kb.row(*buttons[i : i + 2])
        kb.add(
            B(
                ("• " if period == "custom" else "") + "Свой период",
                callback_data=f"{CBT_STATS_CUSTOM}:",
            )
        )
        kb.add(B("◀️ Назад", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}:0"))
        return kb

    def open_stats(self, c: CallbackQuery) -> None:
        period = c.data.split(":")[-1]
        if period not in STATS_PERIOD_LABELS:
            period = "day"
        start, end = self._stats_bounds(period)
        try:
            self.bot.edit_message_text(
                self._stats_text(period, start, end, STATS_PERIOD_LABELS[period]),
                c.message.chat.id,
                c.message.id,
                reply_markup=self._stats_kb(period),
            )
        except Exception as e:
            if "message not modified" not in str(e):
                raise
        self.bot.answer_callback_query(c.id)

    def act_stats_custom(self, c: CallbackQuery) -> None:
        result = self.bot.send_message(
            c.message.chat.id,
            "<b> -|- Свой период</b>\n\n"
            "Отправь диапазон дат:\n"
            "<blockquote>ДД.ММ.ГГГГ - ДД.ММ.ГГГГ</blockquote>\n"
            "Можно со временем: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>. "
            "Без времени берётся <code>00:00:00</code>.\n\n"
            "Пример:\n"
            "<blockquote>01.06.2026 - 24.06.2026</blockquote>",
            reply_markup=skb.CLEAR_STATE_BTN(),
        )
        self.tg.set_state(
            result.chat.id, result.id, c.from_user.id, STATE_STATS_RANGE, {}
        )
        self.bot.answer_callback_query(c.id)

    @staticmethod
    def _parse_dt(s: str) -> datetime | None:
        s = s.strip()
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    @classmethod
    def _parse_stats_range(cls, text: str) -> tuple[int, int] | None:
        parts = re.split(r"\s*[-—–]\s*", text.strip(), maxsplit=1)
        if len(parts) != 2:
            return None
        start, end = cls._parse_dt(parts[0]), cls._parse_dt(parts[1])
        if not start or not end:
            return None
        if start > end:
            start, end = end, start
        return int(start.timestamp()), int(end.timestamp())

    def do_stats_range(self, m: Message) -> None:
        self.tg.clear_state(m.chat.id, m.from_user.id, True)
        rng = self._parse_stats_range(m.text or "")
        if rng is None:
            kb = K().add(B("◀️ К статистике", callback_data=f"{CBT_STATS}:day"))
            self.bot.send_message(
                m.chat.id,
                "❌ Не понял диапазон.\nФормат: <code>ДД.ММ.ГГГГ - ДД.ММ.ГГГГ</code>",
                reply_markup=kb,
            )
            return
        start, end = rng
        self.bot.send_message(
            m.chat.id,
            self._stats_text("custom", start, end, "Свой период"),
            reply_markup=self._stats_kb("custom"),
        )

    def act_search(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        result = self.bot.send_message(
            c.message.chat.id,
            "<b> -|- Поиск заказа</b>\n\n"
            "Отправь одно из значений:\n"
            "<blockquote>"
            "• ID заказа на Starvell\n"
            "• ID заказа в swizzyer / rbcode\n"
            "• Никнейм покупателя на Starvell\n"
            "• Логин в Roblox"
            "</blockquote>",
            reply_markup=skb.CLEAR_STATE_BTN(),
        )
        self.tg.set_state(
            result.chat.id, result.id, c.from_user.id, STATE_SEARCH, {"offset": offset}
        )
        self.bot.answer_callback_query(c.id)

    def do_search(self, m: Message) -> None:
        self.tg.clear_state(m.chat.id, m.from_user.id, True)
        query = (m.text or "").strip()
        if not query:
            back_kb = K().add(B("◀️ К истории", callback_data=f"{CBT_HISTORY}:0:all"))
            self.bot.send_message(m.chat.id, "❌ Пустой запрос.", reply_markup=back_kb)
            return
        self._searches[m.chat.id] = query
        self.bot.send_message(
            m.chat.id,
            self._history_text("search", query),
            reply_markup=self._history_kb("search", 0, query),
        )

    def _render_messages(self, chat_id: int, message_id: int, offset: int) -> None:
        kb = K()
        cat_buttons = []
        for cat, label in MESSAGE_CATEGORY_LABELS.items():
            n = messages.custom_count(cat)
            suffix = f" ({n} изм.)" if n else ""
            cat_buttons.append(
                B(f"{label}{suffix}", callback_data=f"{CBT_MSG_CAT}:0:{cat}")
            )
        kb.row(*cat_buttons)
        total_custom = messages.custom_count()
        if total_custom:
            kb.add(
                B(
                    f"♻️ Сбросить все ({total_custom})",
                    callback_data=f"{CBT_MSG_RESET_ALL}:ask",
                )
            )
        kb.add(B("◀️ Назад", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}:{offset}"))
        self.bot.edit_message_text(
            "💬 <b>Сообщения покупателю</b>\n\n"
            "<blockquote>Здесь можно изменить любой текст, который бот отправляет "
            "покупателю на Starvell. В текстах можно использовать переменные.</blockquote>",
            chat_id,
            message_id,
            reply_markup=kb,
        )

    def open_messages(self, c: CallbackQuery) -> None:
        offset = int(c.data.split(":")[-1])
        self._render_messages(c.message.chat.id, c.message.id, offset)
        self.bot.answer_callback_query(c.id)

    def _render_msg_category(
        self, chat_id: int, message_id: int, category: str, offset: int
    ) -> None:
        specs = [s for s in MESSAGE_SPECS if s.category == category]
        page_specs = specs[offset : offset + MESSAGES_PER_PAGE]
        kb = K()
        for spec in page_specs:
            mark = "✏️ " if messages.is_custom(spec.key) else ""
            kb.add(
                B(
                    f"{mark}{spec.label}",
                    callback_data=f"{CBT_MSG}:{spec.key}:{category}:{offset}",
                )
            )
        utils.add_navigation_buttons(
            kb,
            offset,
            MESSAGES_PER_PAGE,
            len(page_specs),
            len(specs),
            CBT_MSG_CAT,
            extra=[category],
        )
        n = messages.custom_count(category)
        if n:
            kb.add(
                B(
                    f"♻️ Сбросить категорию ({n})",
                    callback_data=f"{CBT_MSG_RESET_CAT}:{category}:ask",
                )
            )
        kb.add(B("◀️ Назад", callback_data=f"{CBT_MESSAGES}:0"))
        self.bot.edit_message_text(
            f"<b> -|- {MESSAGE_CATEGORY_LABELS[category]} сообщения</b>\n\n"
            "<blockquote>✏️ — отмечены изменённые сообщения.</blockquote>",
            chat_id,
            message_id,
            reply_markup=kb,
        )

    def open_msg_category(self, c: CallbackQuery) -> None:
        _, page, category = c.data.split(":")
        self._render_msg_category(c.message.chat.id, c.message.id, category, int(page))
        self.bot.answer_callback_query(c.id)

    @staticmethod
    def _msg_detail_text(spec: MessageSpec) -> str:
        current = messages.text(spec.key)
        if spec.variables:
            vars_block = "\n".join(
                f"• <code>{v}</code> — {VARIABLE_DOCS.get(v, '')}".rstrip(" —")
                for v in spec.variables
            )
        else:
            vars_block = "—"
        status = "изменено" if messages.is_custom(spec.key) else "по умолчанию"
        return (
            f"<b> -|- {utils.escape(spec.label)}</b>\n\n"
            f"• Статус: <b>{status}</b>\n\n"
            f"<b>-| Переменные</b>\n<blockquote>{vars_block}</blockquote>\n\n"
            f"<b>-| Текущий текст</b>\n<blockquote><code>{utils.escape(current)}</code></blockquote>"
        )

    def _msg_detail_kb(self, key: str, category: str, offset: str) -> K:
        kb = K()
        row = [B("Изменить", callback_data=f"{CBT_MSG_EDIT}:{key}:{category}:{offset}")]
        if messages.is_custom(key):
            row.append(
                B(
                    "Сбросить",
                    callback_data=f"{CBT_MSG_RESET}:{key}:{category}:{offset}",
                )
            )
        kb.row(*row)
        kb.add(B("◀️ Назад", callback_data=f"{CBT_MSG_CAT}:{offset}:{category}"))
        return kb

    def open_msg(self, c: CallbackQuery) -> None:
        _, key, category, offset = c.data.split(":")
        spec = MESSAGE_BY_KEY.get(key)
        if not spec:
            self.bot.answer_callback_query(
                c.id, "Сообщение не найдено", show_alert=True
            )
            return
        self.bot.edit_message_text(
            self._msg_detail_text(spec),
            c.message.chat.id,
            c.message.id,
            reply_markup=self._msg_detail_kb(key, category, offset),
        )
        self.bot.answer_callback_query(c.id)

    def act_edit_msg(self, c: CallbackQuery) -> None:
        _, key, category, offset = c.data.split(":")
        spec = MESSAGE_BY_KEY.get(key)
        if not spec:
            self.bot.answer_callback_query(
                c.id, "Сообщение не найдено", show_alert=True
            )
            return
        if spec.variables:
            vars_block = (
                "<b>-| Доступные переменные</b>\n<blockquote>"
                + "\n".join(
                    f"• <code>{v}</code> — {VARIABLE_DOCS.get(v, '')}".rstrip(" —")
                    for v in spec.variables
                )
                + "</blockquote>"
            )
        else:
            vars_block = "<i>Переменные недоступны для этого сообщения.</i>"
        result = self.bot.send_message(
            c.message.chat.id,
            f"<b> -|- {utils.escape(spec.label)}</b>\n\n"
            f"Отправь новый текст сообщения.\n\n{vars_block}",
            reply_markup=skb.CLEAR_STATE_BTN(),
        )
        self.tg.set_state(
            result.chat.id,
            result.id,
            c.from_user.id,
            STATE_EDIT_MSG,
            {"key": key, "category": category, "offset": offset},
        )
        self.bot.answer_callback_query(c.id)

    def save_msg(self, m: Message) -> None:
        data = self.tg.get_state(m.chat.id, m.from_user.id)["data"]
        self.tg.clear_state(m.chat.id, m.from_user.id, True)
        key, category, offset = data["key"], data["category"], data["offset"]
        spec = MESSAGE_BY_KEY.get(key)
        back_kb = K().add(
            B("◀️ К сообщению", callback_data=f"{CBT_MSG}:{key}:{category}:{offset}")
        )
        if not spec:
            self.bot.send_message(m.chat.id, "❌ Сообщение не найдено.")
            return
        text = m.text or ""
        if not text.strip():
            self.bot.send_message(m.chat.id, "❌ Пустой текст.", reply_markup=back_kb)
            return
        messages.set(key, text)
        self.bot.send_message(
            m.chat.id, f"✅ Сообщение «{spec.label}» сохранено.", reply_markup=back_kb
        )

    def reset_msg(self, c: CallbackQuery) -> None:
        _, key, category, offset = c.data.split(":")
        spec = MESSAGE_BY_KEY.get(key)
        if not spec:
            self.bot.answer_callback_query(
                c.id, "Сообщение не найдено", show_alert=True
            )
            return
        messages.reset(key)
        self.bot.edit_message_text(
            self._msg_detail_text(spec),
            c.message.chat.id,
            c.message.id,
            reply_markup=self._msg_detail_kb(key, category, offset),
        )
        self.bot.answer_callback_query(c.id, "Сброшено к значению по умолчанию")

    def reset_msg_category(self, c: CallbackQuery) -> None:
        parts = c.data.split(":")
        category = parts[1]
        confirm = len(parts) > 2 and parts[2] == "yes"
        label = MESSAGE_CATEGORY_LABELS.get(category, category)
        if not confirm:
            kb = K()
            kb.row(
                B("✅ Да", callback_data=f"{CBT_MSG_RESET_CAT}:{category}:yes"),
                B("❌ Нет", callback_data=f"{CBT_MSG_CAT}:0:{category}"),
            )
            self.bot.edit_message_text(
                f"<b> -|- Сброс категории</b>\n\n"
                f"Сбросить все «{label}» сообщения к значениям по умолчанию?\n"
                f"<blockquote>Будет сброшено: <code>{messages.custom_count(category)}</code></blockquote>",
                c.message.chat.id,
                c.message.id,
                reply_markup=kb,
            )
            self.bot.answer_callback_query(c.id)
            return
        count = messages.reset_category(category)
        self._render_msg_category(c.message.chat.id, c.message.id, category, 0)
        self.bot.answer_callback_query(c.id, f"Сброшено: {count}", show_alert=True)

    def reset_msg_all(self, c: CallbackQuery) -> None:
        parts = c.data.split(":")
        confirm = len(parts) > 1 and parts[1] == "yes"
        if not confirm:
            kb = K()
            kb.row(
                B("✅ Да", callback_data=f"{CBT_MSG_RESET_ALL}:yes"),
                B("❌ Нет", callback_data=f"{CBT_MESSAGES}:0"),
            )
            self.bot.edit_message_text(
                "<b> -|- Сброс сообщений</b>\n\n"
                "Сбросить <b>все</b> сообщения к значениям по умолчанию?\n"
                f"<blockquote>Будет сброшено: <code>{messages.custom_count()}</code></blockquote>",
                c.message.chat.id,
                c.message.id,
                reply_markup=kb,
            )
            self.bot.answer_callback_query(c.id)
            return
        count = messages.reset_all()
        self._render_messages(c.message.chat.id, c.message.id, 0)
        self.bot.answer_callback_query(c.id, f"Сброшено: {count}", show_alert=True)

    def open_order(self, c: CallbackQuery) -> None:
        _, offset, flt, order_id = c.data.split(":", 3)
        record = db.orders.get(order_id)
        if not record:
            self.bot.answer_callback_query(c.id, "Заказ не найден", show_alert=True)
            return
        kb = K().add(B("◀️ Назад", callback_data=f"{CBT_HISTORY}:{offset}:{flt}"))
        self.bot.edit_message_text(
            self._order_detail_text(record),
            c.message.chat.id,
            c.message.id,
            reply_markup=kb,
        )
        self.bot.answer_callback_query(c.id)


class RobuxDelivery:
    def __init__(self, cardinal: Cardinal) -> None:
        self.cardinal = cardinal
        self._api: SwizzyerAPI | None = None
        self._api_key: str | None = None
        self._dispatch_lock = Lock()
        self._queues: dict[str, queue.Queue] = {}
        self._workers: set[str] = set()
        self._pre_startup_logged: set[str] = set()

    def start(self) -> None:
        resumed = 0
        for record in db.orders.verifying():
            record.await_version = None
            record.data.pop("last_seq", None)
            db.orders.save(record)
            resumed += 1
        requeued = 0
        for buyer_id in db.orders.queued_buyer_ids():
            if db.orders.active_by_buyer(buyer_id) is None:
                self._start_next(buyer_id)
                requeued += 1
        Thread(target=self._poll_loop, name="arp-poll", daemon=True).start()
        logger.info(
            f"Авто-выдача робуксов запущена$RESET. "
            f"Возобновлено проверок: $MAGENTA{resumed}$RESET, "
            f"очередей: $MAGENTA{requeued}."
        )

    def _submit(self, buyer_id: str, task) -> None:
        buyer_id = str(buyer_id)
        with self._dispatch_lock:
            q = self._queues.setdefault(buyer_id, queue.Queue())
            q.put(task)
            if buyer_id not in self._workers:
                self._workers.add(buyer_id)
                Thread(
                    target=self._worker,
                    args=(buyer_id, q),
                    name=f"arp-{buyer_id}",
                    daemon=True,
                ).start()

    def _submit_if_idle(self, buyer_id: str, task) -> None:
        buyer_id = str(buyer_id)
        with self._dispatch_lock:
            if buyer_id in self._workers:
                return
        self._submit(buyer_id, task)

    def _worker(self, buyer_id: str, q: queue.Queue) -> None:
        while True:
            try:
                task = q.get_nowait()
            except queue.Empty:
                with self._dispatch_lock:
                    if q.empty():
                        self._workers.discard(buyer_id)
                        self._queues.pop(buyer_id, None)
                        return
                    continue
            try:
                task()
            except Exception:
                logger.error("Ошибка обработки заказа")
                logger.debug("TRACEBACK", exc_info=True)
            finally:
                q.task_done()

    def _proxies(self) -> dict[str, str] | None:
        raw = getattr(getattr(self.cardinal, "account", None), "proxy", None)
        if not raw:
            return None
        if isinstance(raw, dict):
            return raw
        url = str(raw)
        return {"http": url, "https": url}

    def _my_id(self) -> str:
        user = getattr(getattr(self.cardinal, "account", None), "user", None)
        return str(getattr(user, "id", "") or "")

    def _find_buyer_chat(self, buyer_id: str, buyer_username: str) -> str:
        account = getattr(self.cardinal, "account", None)
        if account is None:
            return ""
        my_id = self._my_id()

        def _match(chat) -> bool:
            other = chat.other_user(my_id)
            if not other:
                return False
            if buyer_id and other.id == buyer_id:
                return True
            return bool(buyer_username and other.username == buyer_username)

        chats = list(getattr(getattr(self.cardinal, "runner", None), "last_chats", None) or [])
        for chat in chats:
            if _match(chat):
                return chat.id
        try:
            for chat in account.get_chats():
                if _match(chat):
                    return chat.id
        except Exception:
            logger.debug("не удалось получить чаты Starvell", exc_info=True)
        return ""

    def api(self) -> SwizzyerAPI:
        if self._api is None or self._api_key != settings.api_key:
            self._api = SwizzyerAPI(
                settings.api_key, proxies=self._proxies()
            )
            self._api_key = settings.api_key
        return self._api

    def _enabled(self) -> bool:
        return bool(settings.on and settings.api_key)

    def _send(
        self, chat_id: int | str, text: str, chat_name: str | None = None
    ) -> None:
        if not chat_id or not text:
            return
        try:
            self.cardinal.send_message(str(chat_id), text)
        except Exception:
            logger.error("Не удалось отправить сообщение покупателю")
            logger.debug("TRACEBACK", exc_info=True)

    @staticmethod
    def _fmt(key: str, record: OrderRecord | None = None, **extra) -> str:
        template = messages.text(key)
        values: dict[str, object] = {}
        if record is not None:
            values["$order_id"] = record.funpay_order_id
            values["$buyer"] = record.buyer_username or str(record.buyer_id)
            values["$account"] = record.roblox_username or ""
            values["$robux"] = record.robux_amount
            values["$quantity"] = record.quantity
            values["$total"] = record.robux_amount * record.quantity
        for name, value in extra.items():
            values["$" + name] = value
        for name, value in values.items():
            template = template.replace(name, str(value))
        return template

    def _send_tpl(self, record: OrderRecord, key: str, **extra) -> None:
        self._send(
            record.chat_id, self._fmt(key, record, **extra), record.buyer_username
        )

    def _notify_admin(
        self, chat_ids: list[int], text: str, keyboard: K | None = None
    ) -> None:
        tg = getattr(self.cardinal, "telegram", None)
        if tg is None or not chat_ids:
            return
        for chat_id in chat_ids:
            try:
                tg.bot.send_message(
                    chat_id, text, parse_mode="HTML", reply_markup=keyboard
                )
            except Exception:
                logger.debug(
                    f"не удалось отправить уведомление в чат {chat_id}", exc_info=True
                )

    @staticmethod
    def _notify_kb(record: OrderRecord) -> K:
        return K().row(
            B(
                "🛒 Заказ",
                url=f"https://starvell.com/order/{record.funpay_order_id}",
            ),
            B(
                f"👤 {record.buyer_username}",
                url=f"https://starvell.com/profile/{record.buyer_username}",
            ),
        )

    def handle_new_order(self, order: SvOrder | None) -> None:
        if not self._enabled() or order is None:
            return
        buyer = order.buyer
        if buyer is None:
            return
        my_id = self._my_id()
        if my_id and buyer.id == my_id:
            return
        haystack = " ".join(
            part
            for part in (
                order.offer_name,
                str((order.raw or {}).get("description") or ""),
                str((order.raw or {}).get("productName") or ""),
            )
            if part
        )
        robux = self._robux_for_order(order, haystack)
        if not robux:
            return
        quantity = order.quantity or 1
        chat_id = self._find_buyer_chat(buyer.id, buyer.username)
        if not chat_id:
            logger.error(
                f"Чат покупателя не найден для заказа $YELLOW#{order.id}$RESET "
                f"($CYAN{buyer.username}$RESET) — выдача не стартовала"
            )
            return
        wrapped = SimpleNamespace(
            id=order.id,
            chat_id=chat_id,
            buyer_id=str(buyer.id),
            buyer_username=buyer.username,
            amount=quantity,
            description=haystack,
        )
        self._submit(
            wrapped.buyer_id, lambda: self._process_new_order(wrapped, robux, quantity)
        )

    @staticmethod
    def _robux_for_order(order: SvOrder, haystack: str) -> int | None:
        offer_id = (order.offer_id or "").strip()
        offer_name = (order.offer_name or "").strip()
        if offer_id and offer_id in settings.lot_bindings:
            return int(settings.lot_bindings[offer_id])
        if offer_name and offer_name in settings.lot_bindings:
            return int(settings.lot_bindings[offer_name])
        for key, robux in settings.lot_bindings.items():
            title = str(settings.lot_titles.get(key) or "")
            if offer_name and title and title == offer_name:
                return int(robux)
        match = ROBUX_RE.search(haystack)
        if match:
            return int(match.group(1))
        return None

    def _process_new_order(
        self, order: Any, robux: int, quantity: int
    ) -> None:
        if db.orders.get(order.id):
            return
        logger.info(
            f"Новый заказ $YELLOW#{order.id}$RESET: $MAGENTA{robux * quantity}$RESET робуксов "
            f"(пак $MAGENTA{robux}$RESET × $MAGENTA{quantity}$RESET), "
            f"покупатель $CYAN{order.buyer_username}$RESET"
        )
        record = OrderRecord(
            funpay_order_id=order.id,
            chat_id=str(order.chat_id),
            buyer_id=str(order.buyer_id),
            buyer_username=order.buyer_username,
            robux_amount=robux,
            quantity=quantity,
        )
        if db.orders.active_by_buyer(order.buyer_id):
            record.stage = Stage.QUEUED
            db.orders.save(record)
            logger.info(
                f"Заказ $YELLOW#{order.id}$RESET поставлен в очередь "
                f"(у покупателя $CYAN{order.buyer_username}$RESET уже есть активный заказ)"
            )
            self._send_tpl(record, "order_queued")
        else:
            db.orders.save(record)
            self._start_order(record)

    def handle_new_message(self, message: SvMessage | None) -> None:
        if not self._enabled() or message is None:
            return
        my_id = self._my_id()
        if my_id and message.author_id == my_id:
            return
        text = (message.text or "").strip()
        if not text:
            return
        author_id = str(message.author_id or "")
        if not author_id:
            return
        if not db.orders.active_by_buyer(author_id):
            return
        self._submit(author_id, lambda: self._process_new_message(message, text))

    def _process_new_message(self, message: Any, text: str) -> None:
        record = db.orders.active_by_buyer(str(message.author_id))
        if not record:
            return
        record.data["last_buyer_at"] = int(time.time())
        db.orders.save(record)
        if self._handle_refund_request(record, text):
            return
        if record.stage is Stage.AWAITING_LOGIN:
            self._on_login(record, text, cred=False)
        elif record.stage is Stage.AWAITING_PASSWORD:
            self._on_password(record, text, cred=False)
        elif record.stage is Stage.AWAITING_CRED_LOGIN:
            self._on_login(record, text, cred=True)
        elif record.stage is Stage.AWAITING_CRED_PASSWORD:
            self._on_password(record, text, cred=True)
        elif record.stage is Stage.VERIFYING:
            self._route_verifying(record, text)

    def handle_status_change(self, order: Any) -> None:
        status = str(getattr(order, "status", "") or "").upper()
        if status not in {"REFUNDED", "CANCELLED", "CANCELED", "REFUND"}:
            return
        buyer_id = ""
        buyer = getattr(order, "buyer", None)
        if buyer is not None:
            buyer_id = str(getattr(buyer, "id", "") or "")
        if not buyer_id:
            buyer_id = str(getattr(order, "buyer_id", "") or "")
        order_id = str(getattr(order, "id", "") or "")
        if not order_id:
            return
        self._submit(buyer_id or order_id, lambda: self._cancel_refunded(order_id))

    def _cancel_refunded(self, order_id: str) -> None:
        record = db.orders.get(order_id)
        if not record or not record.pending:
            return
        if record.swizzyer_order_id:
            try:
                self.api().cancel_order(record.swizzyer_order_id)
            except (SwizzyerError, requests.RequestException) as e:
                logger.warning(
                    f"Не удалось отменить заказ {record.swizzyer_order_id} в swizzyer: {e}"
                )
        record.pending = False
        record.stage = Stage.DONE
        record.status = "refunded"
        db.orders.save(record)
        logger.info(
            f"$YELLOW#{order_id}$RESET возвращён — отменён в swizzyer и в плагине."
        )
        self._start_next(record.buyer_id)

    def _passkey_refunded(self, record: OrderRecord) -> bool:
        if not settings.refund_on_passkey or not self._refund_allowed(record):
            return False
        return self._refund(record, "refund_passkey")

    @staticmethod
    def _charge_locked(order: Order) -> bool:
        if not order.charge_free:
            return True
        if order.status is OrderStatus.PARTIALLY_DELIVERED:
            return True
        return order.situation_state in CHARGE_LOCK_SITUATIONS

    def _created_before_startup(self, record: OrderRecord) -> bool:
        if not record.created_at or record.created_at >= PLUGIN_STARTED_AT:
            return False
        if record.funpay_order_id not in self._pre_startup_logged:
            self._pre_startup_logged.add(record.funpay_order_id)
            logger.info(
                f"$YELLOW#{record.funpay_order_id}$RESET: заказ создан до запуска "
                f"плагина — авто-возврат по нему не оформляется"
            )
        return True

    def _refund_allowed(self, record: OrderRecord) -> bool:
        if not record.pending:
            return False
        if self._created_before_startup(record):
            return False
        if record.data.get("charge_locked") or record.data.get("refund_blocked"):
            return False
        return not any(
            record.data.get(key)
            for key in ("relink_pending", "relink_active", "relink_done")
        )

    @staticmethod
    def _error_text(error: Exception) -> str:
        short = getattr(error, "short_str", None)
        return short() if callable(short) else str(error)[:200]

    def _refund(
        self, record: OrderRecord, message_key: str | None = None, **extra
    ) -> bool:
        try:
            self.cardinal.refund_order(str(record.funpay_order_id))
        except Exception as e:
            record.data["refund_blocked"] = True
            db.orders.save(record)
            logger.error(
                f"$YELLOW#{record.funpay_order_id}$RESET: возврат не оформлен "
                f"({self._error_text(e)})"
            )
            logger.debug("TRACEBACK", exc_info=True)
            return False
        if record.swizzyer_order_id:
            try:
                self.api().cancel_order(record.swizzyer_order_id)
            except (SwizzyerError, requests.RequestException) as e:
                logger.warning(
                    f"Не удалось отменить заказ {record.swizzyer_order_id} в swizzyer: {e}"
                )
        reason = REFUND_REASON_LABELS.get(message_key or "", "")
        record.pending = False
        record.stage = Stage.DONE
        record.status = "refunded"
        if reason:
            record.data["reason"] = reason
        db.orders.save(record)
        logger.info(
            f"$YELLOW#{record.funpay_order_id}$RESET: оформлен возврат средств"
            + (f" — {reason}" if reason else "")
        )
        if message_key:
            self._send_tpl(record, message_key, **extra)
        self._notify_refund(record, reason)
        self._start_next(record.buyer_id)
        return True

    def _notify_refund(self, record: OrderRecord, reason: str) -> None:
        lines = [
            f"• Заказ: <code>#{utils.escape(record.funpay_order_id)}</code>",
            f"• Покупатель: <code>{utils.escape(record.buyer_username)}</code>",
            f"• Аккаунт: <code>{utils.escape(record.roblox_username or '—')}</code>",
            f"• Номинал: <code>{record.robux_amount} R$</code> × "
            f"<code>{record.quantity}</code>",
        ]
        if reason:
            lines.append(f"• Причина: <code>{utils.escape(reason)}</code>")
        self._notify_admin(
            settings.notify_failure,
            "💸 <b><u>Оформлен возврат средств</u></b>\n\n<blockquote>"
            + "\n".join(lines)
            + "</blockquote>",
            self._notify_kb(record),
        )

    def _refund_for_code(
        self, record: OrderRecord, code: str, *, reason: str = "", fallback: bool = False
    ) -> bool:
        if not self._refund_allowed(record):
            return False
        for flag, codes, message_key in REFUND_CATEGORIES:
            if not getattr(settings, flag, False):
                continue
            if code in codes or (fallback and message_key == "refund_failed"):
                logger.warning(
                    f"$YELLOW#{record.funpay_order_id}$RESET: "
                    f"{REFUND_REASON_LABELS[message_key]} "
                    f"({code or 'без кода'}) — оформляю возврат"
                )
                return self._refund(record, message_key, reason=reason)
        return False

    def _count_attempt(self, record: OrderRecord) -> bool:
        attempts = int(record.data.get("fail_attempts") or 0) + 1
        record.data["fail_attempts"] = attempts
        db.orders.save(record)
        if not settings.refund_on_attempts:
            return False
        limit = max(1, settings.refund_max_attempts)
        if attempts < limit:
            return False
        if not self._refund_allowed(record):
            return False
        logger.warning(
            f"$YELLOW#{record.funpay_order_id}$RESET: неудачных попыток ввода "
            f"{attempts}/{limit} — оформляю возврат"
        )
        return self._refund(record, "refund_attempts", attempts=attempts, max=limit)

    @staticmethod
    def _looks_like_refund_request(text: str) -> bool:
        low = re.sub(r"\s+", " ", text.lower().replace("ё", "е"))
        for keyword in settings.refund_request_keywords:
            normalized = re.sub(r"\s+", " ", keyword.strip().lower().replace("ё", "е"))
            if normalized and normalized in low:
                return True
        return False

    @staticmethod
    def _delivery_started(record: OrderRecord) -> bool:
        if record.swizzyer_order_id:
            return True
        return record.stage not in (
            Stage.QUEUED,
            Stage.AWAITING_LOGIN,
            Stage.AWAITING_PASSWORD,
        )

    def _handle_refund_request(self, record: OrderRecord, text: str) -> bool:
        if not settings.refund_on_request:
            return False
        if not self._looks_like_refund_request(text):
            return False
        if self._delivery_started(record) or not self._refund_allowed(record):
            if not record.data.get("refund_request_notified"):
                record.data["refund_request_notified"] = True
                db.orders.save(record)
                logger.info(
                    f"$YELLOW#{record.funpay_order_id}$RESET: покупатель просит возврат, "
                    f"но выдача уже запущена — возврат не оформляю"
                )
                self._notify_admin(
                    settings.notify_failure,
                    f"💬 <b><u>Покупатель просит возврат</u></b>\n\n"
                    f"<blockquote>"
                    f"• Заказ: <code>#{utils.escape(record.funpay_order_id)}</code>\n"
                    f"• Покупатель: <code>{utils.escape(record.buyer_username)}</code>\n"
                    f"• Сообщение: <code>{utils.escape(text[:200])}</code>\n"
                    f"• Выдача уже запущена — автовозврат не сделан"
                    f"</blockquote>",
                    self._notify_kb(record),
                )
            return False
        logger.info(
            f"$YELLOW#{record.funpay_order_id}$RESET: покупатель попросил возврат — "
            f"оформляю"
        )
        return self._refund(record, "refund_requested")

    def _start_order(self, record: OrderRecord) -> None:
        record.stage = Stage.AWAITING_LOGIN
        record.data["last_buyer_at"] = int(time.time())
        db.orders.save(record)
        self._send_tpl(record, "greeting")

    def _start_next(self, buyer_id: int) -> None:
        nxt = db.orders.next_queued_by_buyer(buyer_id)
        if nxt:
            self._start_order(nxt)

    @staticmethod
    def _valid_login(value: str) -> bool:
        if not LOGIN_MIN_LENGTH <= len(value) <= LOGIN_MAX_LENGTH:
            return False
        if not CRED_LOGIN_VALUE_RE.fullmatch(value):
            return False
        return not LOGIN_LABEL_ONLY_RE.match(value) and not PASS_LABEL_ONLY_RE.match(
            value
        )

    @staticmethod
    def _take_login(text: str, start: int) -> tuple[str, int]:
        line_end = text.find("\n", start)
        if line_end == -1:
            line_end = len(text)
        segment = text[start:line_end]
        offset = len(segment) - len(segment.lstrip(CRED_QUOTES))
        match = CRED_LOGIN_VALUE_RE.match(segment, offset)
        if not match:
            return "", start
        return match.group(0), start + match.end()

    @staticmethod
    def _trim_chatter(value: str) -> str:
        tokens = value.split()
        if len(tokens) < 2:
            return value
        if any(
            CYRILLIC_WORD_RE.match(t.strip(CRED_CHATTER_TRIM)) for t in tokens[1:]
        ):
            return tokens[0]
        return value

    @classmethod
    def _take_password(cls, text: str, start: int, end: int) -> str:
        value = text[start:end].split("\n")[0].strip(CRED_QUOTES)
        if PASS_LABEL_ONLY_RE.match(value) or LOGIN_LABEL_ONLY_RE.match(value):
            return ""
        return cls._trim_chatter(value)

    @classmethod
    def _split_credentials(cls, text: str) -> tuple[str, str] | None:
        text = text.strip()
        if not text:
            return None
        login_anchor = CRED_LOGIN_ANCHOR_RE.search(text)
        pass_anchor = CRED_PASS_ANCHOR_RE.search(text)
        if login_anchor and pass_anchor:
            login, _ = cls._take_login(text, login_anchor.end())
            if pass_anchor.start() > login_anchor.start():
                password = cls._take_password(text, pass_anchor.end(), len(text))
            else:
                password = cls._take_password(
                    text, pass_anchor.end(), login_anchor.start()
                )
            if login and password and cls._valid_login(login):
                return login, password
            return None
        if login_anchor:
            login, login_end = cls._take_login(text, login_anchor.end())
            if not login or not cls._valid_login(login):
                return None
            password = cls._take_password(text, login_end, len(text))
            return (login, password) if password else None
        if pass_anchor:
            candidates = CRED_LOGIN_VALUE_RE.findall(text[: pass_anchor.start()])
            login = candidates[-1] if candidates else ""
            password = cls._take_password(text, pass_anchor.end(), len(text))
            if login and password and cls._valid_login(login):
                return login, password
            return None
        return cls._split_positional(text)

    @classmethod
    def _split_positional(cls, text: str) -> tuple[str, str] | None:
        return cls._split_plain(text) or cls._split_plain(cls._strip_decor(text))

    @staticmethod
    def _strip_decor(text: str) -> str:
        tokens: list[str] = []
        for token in text.split():
            if CRED_LIST_MARKER_RE.match(token) or CRED_WORDLESS_RE.match(token):
                continue
            tokens.append(token.lstrip("@") if not tokens else token)
        return " ".join(tokens)

    @classmethod
    def _split_plain(cls, text: str) -> tuple[str, str] | None:
        pair = CRED_PAIR_RE.match(text)
        if pair and cls._valid_login(pair.group(1)):
            password = cls._trim_chatter(pair.group(2).strip(CRED_QUOTES))
            if password:
                return pair.group(1), password
        parts = text.split(None, 1)
        if len(parts) != 2:
            return None
        login = parts[0].strip(CRED_QUOTES)
        password = cls._trim_chatter(parts[1].strip(CRED_QUOTES))
        if not password or not cls._valid_login(login):
            return None
        if LOGIN_LABEL_ONLY_RE.match(password) or PASS_LABEL_ONLY_RE.match(password):
            return None
        return login, password

    def _reject_invalid_login(self, record: OrderRecord) -> bool:
        if settings.warn_invalid_login:
            self._send_tpl(record, "invalid_login")
        return self._count_attempt(record)

    def _on_login(self, record: OrderRecord, text: str, *, cred: bool) -> None:
        combined = self._split_credentials(text)
        if combined:
            login, password = combined
            if RUSSIAN_RE.search(login) or " " in login:
                self._reject_invalid_login(record)
                return
            record.roblox_username = login
            db.orders.save(record)
            logger.info(
                f"$YELLOW#{record.funpay_order_id}$RESET: получены логин и пароль Roblox "
                f"($MAGENTA{login}$RESET)"
            )
            self._submit_credentials(
                record,
                creds=Credentials(username=login, password=password),
                retry=cred,
            )
            return
        text = LOGIN_LABEL_RE.sub("", text, count=1)
        text = LOGIN_LABEL_SUFFIX_RE.sub("", text, count=1).strip()
        if RUSSIAN_RE.search(text) or " " in text or not text:
            self._reject_invalid_login(record)
            return
        record.roblox_username = text
        record.stage = Stage.AWAITING_CRED_PASSWORD if cred else Stage.AWAITING_PASSWORD
        db.orders.save(record)
        logger.info(
            f"$YELLOW#{record.funpay_order_id}$RESET: получен логин Roblox "
            f"$MAGENTA{text}$RESET, запрашиваю пароль"
        )
        self._send_tpl(record, "ask_password")

    def _on_password(self, record: OrderRecord, text: str, *, cred: bool) -> None:
        labeled = CRED_LOGIN_ANCHOR_RE.search(text) or CRED_PASS_ANCHOR_RE.search(text)
        combined = self._split_credentials(text) if labeled else None
        if combined:
            login, password = combined
            record.roblox_username = login
            db.orders.save(record)
            logger.info(
                f"$YELLOW#{record.funpay_order_id}$RESET: получены логин и пароль Roblox "
                f"($MAGENTA{login}$RESET)"
            )
            self._submit_credentials(
                record,
                creds=Credentials(username=login, password=password),
                retry=cred,
            )
            return
        stripped = PASS_LABEL_RE.sub("", text, count=1).strip()
        without_suffix = PASS_LABEL_SUFFIX_RE.sub("", stripped, count=1).strip()
        if without_suffix and len(without_suffix.split()) == 1:
            stripped = without_suffix
        text = stripped.strip(CRED_QUOTES) or text
        if settings.ignore_russian_password and RUSSIAN_RE.search(text):
            logger.info(
                f"$YELLOW#{record.funpay_order_id}$RESET: пароль содержит русские символы, "
                f"игнорирую"
            )
            return
        creds = Credentials(username=record.roblox_username or "", password=text)
        self._submit_credentials(record, creds=creds, retry=cred)

    def _submit_credentials(
        self, record: OrderRecord, *, creds: Credentials, retry: bool
    ) -> None:
        if retry:
            self._respond_credentials(record, creds)
        elif record.data.get("relink_pending"):
            self._relink_order(record, creds)
        else:
            self._create_order(record, creds)

    def _order_items(self, record: OrderRecord) -> list[OrderItem] | None:
        pack = db.packs.get(record.robux_amount)
        if not pack:
            logger.error(
                f"Нет пака для {record.robux_amount} робуксов (заказ {record.funpay_order_id})"
            )
            self._fail(record, self._fmt("no_pack", record))
            return None
        items = pack.to_items(record.quantity)
        if not items:
            logger.error(
                f"Пак на {record.robux_amount} робуксов пуст (заказ {record.funpay_order_id})"
            )
            self._fail(record, self._fmt("no_pack", record))
            return None
        units = pack.total_units * record.quantity
        premium_units = pack.premium_units * record.quantity
        if units > MAX_UNITS_PER_ORDER or premium_units > MAX_PREMIUM_UNITS_PER_ORDER:
            logger.error(
                f"$YELLOW#{record.funpay_order_id}$RESET: пак превышает лимит одной покупки "
                f"({units}/{MAX_UNITS_PER_ORDER} шт., премиум {premium_units}/"
                f"{MAX_PREMIUM_UNITS_PER_ORDER})"
            )
            self._notify_admin(
                settings.notify_failure,
                f"⚠️ <b><u>Пак не помещается в один заказ</u></b>\n\n"
                f"<blockquote>"
                f"• Заказ: <code>#{utils.escape(record.funpay_order_id)}</code>\n"
                f"• Номинал: <code>{record.robux_amount} R$</code> × "
                f"<code>{record.quantity}</code>\n"
                f"• Позиций: <code>{units}</code> (лимит {MAX_UNITS_PER_ORDER})\n"
                f"• Премиум: <code>{premium_units}</code> "
                f"(лимит {MAX_PREMIUM_UNITS_PER_ORDER})"
                f"</blockquote>",
                self._notify_kb(record),
            )
            self._fail(record, self._fmt("pack_too_large", record))
            return None
        return items

    def _pool_affordable(self, record: OrderRecord, items: list[OrderItem]) -> bool:
        if not settings.use_managed_pool:
            return True
        try:
            quote = self.api().quote_wallet(items)
        except SwizzyerError as e:
            logger.warning(f"wallet/quote: {e}")
            return True
        except requests.RequestException:
            return True
        if not quote.pool_enabled:
            logger.error("Управляемый пул выключен на стороне сервиса")
            self._notify_admin(
                settings.notify_failure,
                "🛠 <b><u>Управляемый пул выключен</u></b>\n\n"
                "<blockquote>Заказы с пула Swizzyer сейчас недоступны — "
                "отключите тумблер или дождитесь окончания работ.</blockquote>",
            )
            self._restart_credentials(
                record,
                "restart_service_disabled",
                code=ErrorCode.SERVICE_TEMPORARILY_DISABLED.value,
            )
            return False
        if not quote.affordable:
            logger.error(
                f"Недостаточно средств на кошельке: нужно {quote.price}$, "
                f"доступно {quote.available}$"
            )
            self._notify_admin(
                settings.notify_failure,
                f"💸 <b><u>Не хватает баланса кошелька</u></b>\n\n"
                f"<blockquote>"
                f"• Заказ: <code>#{utils.escape(record.funpay_order_id)}</code>\n"
                f"• Нужно: <code>{quote.price:.2f} $</code>\n"
                f"• Доступно: <code>{quote.available:.2f} $</code>\n"
                f"• Пополнить минимум на: <code>{quote.shortfall:.2f} $</code>"
                f"</blockquote>",
                self._notify_kb(record),
            )
            self._restart_credentials(
                record,
                "restart_insufficient_funds",
                code=ErrorCode.INSUFFICIENT_FUNDS.value,
            )
            return False
        return True

    def _create_order(self, record: OrderRecord, creds: Credentials) -> None:
        items = self._order_items(record)
        if items is None:
            return
        if not self._pool_affordable(record, items):
            return
        self._send_tpl(record, "creating_order")
        try:
            order = self.api().create_order(
                creds,
                items,
                language=Language.RU,
                metadata={
                    "external_order_id": record.funpay_order_id,
                    "funpay_order_id": record.funpay_order_id,
                },
                use_managed_pool=settings.use_managed_pool,
            )
        except SwizzyerError as e:
            self._handle_create_error(record, e)
            return
        except requests.RequestException:
            self._send_tpl(record, "service_unavailable_password")
            return
        record.swizzyer_order_id = order.id
        record.stage = Stage.VERIFYING
        record.await_version = None
        record.data["processing_notified"] = True
        record.data.pop("last_seq", None)
        record.data.pop("push_sent_at", None)
        record.data.pop("extended_at", None)
        db.orders.save(record)
        logger.info(
            f"$YELLOW#{record.funpay_order_id}$RESET: создан swizzyer-заказ "
            f"$CYAN{order.id}$RESET, начинаю верификацию"
        )
        self._apply(record, order)

    def _relink_order(self, record: OrderRecord, creds: Credentials) -> None:
        if not record.swizzyer_order_id:
            record.data.pop("relink_pending", None)
            db.orders.save(record)
            return
        self._send_tpl(record, "creating_order")
        try:
            response = self.api().relink_order(
                record.swizzyer_order_id,
                creds,
                language=Language.RU,
                metadata={"external_order_id": record.funpay_order_id},
            )
        except SwizzyerError as e:
            self._handle_relink_error(record, e)
            return
        except requests.RequestException:
            self._send_tpl(record, "service_unavailable_password")
            return
        record.data.pop("relink_pending", None)
        record.data["relink_active"] = True
        record.data["relink_started_at"] = int(time.time())
        record.data["processing_notified"] = True
        record.data.pop("last_seq", None)
        record.data.pop("push_sent_at", None)
        record.data.pop("extended_at", None)
        record.stage = Stage.VERIFYING
        record.await_version = None
        record.pending = True
        db.orders.save(record)
        logger.info(
            f"$YELLOW#{record.funpay_order_id}$RESET: запущен докид остатка по заказу "
            f"$CYAN{record.swizzyer_order_id}$RESET"
        )
        action = response.next_action
        if action is not None and action.type is not NextActionType.WAIT:
            self._present(record, action)
            db.orders.save(record)
            return
        self._sync(record)

    def _respond_credentials(self, record: OrderRecord, creds: Credentials) -> None:
        if not record.swizzyer_order_id:
            return
        try:
            order = self.api().get_order(record.swizzyer_order_id)
        except requests.RequestException:
            self._send_tpl(record, "service_unavailable_password")
            return
        except SwizzyerError as e:
            logger.error(f"get_order: {e}")
            self._send_tpl(record, "generic_error_later")
            return
        action = order.next_action
        record.stage = Stage.VERIFYING
        if not action or action.type is not NextActionType.CREDENTIALS_RETRY:
            record.await_version = None
            db.orders.save(record)
            self._apply(record, order)
            return
        self._respond_and_sync(
            record,
            lambda api, k: api.respond_credentials(
                order.id, action.version, creds, idempotency_key=k
            ),
        )

    def _route_verifying(self, record: OrderRecord, text: str) -> None:
        if not record.swizzyer_order_id:
            return
        try:
            order = self.api().get_order(record.swizzyer_order_id)
        except requests.RequestException:
            self._send_tpl(record, "service_unavailable_retry")
            return
        except SwizzyerError as e:
            logger.error(f"get_order: {e}")
            return
        action = order.next_action
        t = action.type if action else None
        if t not in ACTIONABLE_STEPS:
            if not order.is_terminal and order.situation_state not in HANDLED_SITUATIONS:
                self._send_tpl(record, "processing_short")
                record.data["processing_notified"] = True
            self._apply(record, order)
            return
        if order.is_terminal and not record.data.get("relink_active"):
            self._finish(record, order)
            return
        if t is NextActionType.CHOOSE_ONE:
            idx = self._parse_single_index(text, len(action.options))
            if idx is None:
                self._send_tpl(record, "choose_one_invalid", n=len(action.options))
                self._count_attempt(record)
                return
            choice_id = action.options[idx - 1].id
            self._respond_and_sync(
                record,
                lambda api, k: api.respond_choice_one(
                    order.id, action.version, choice_id, idempotency_key=k
                ),
            )
        elif t is NextActionType.CHOOSE_MANY:
            idxs = self._parse_indices(text, len(action.options), action.select_exactly)
            if idxs is None:
                example = ", ".join(str(i) for i in range(1, action.select_exactly + 1))
                self._send_tpl(
                    record,
                    "choose_many_invalid",
                    count=action.select_exactly,
                    n=len(action.options),
                    example=example,
                )
                self._count_attempt(record)
                return
            ids = [action.options[i - 1].id for i in idxs]
            self._respond_and_sync(
                record,
                lambda api, k: api.respond_choice_many(
                    order.id, action.version, ids, idempotency_key=k
                ),
            )
        elif t is NextActionType.PROVIDE_INPUT:
            value = self._clean_input(text, action.input)
            if value is None:
                self._send_tpl(
                    record, "input_invalid_format", prompt=self._input_text(action)
                )
                self._count_attempt(record)
                return
            self._respond_and_sync(
                record,
                lambda api, k: api.respond_input(
                    order.id, action.version, value, idempotency_key=k
                ),
            )
        elif t is NextActionType.CREDENTIALS_RETRY:
            record.stage = Stage.AWAITING_CRED_LOGIN
            record.await_version = action.version
            db.orders.save(record)
            self._send_tpl(record, "credentials_retry_short")
        else:
            self._send_tpl(record, "processing_short")

    def _respond_and_sync(self, record: OrderRecord, fn) -> None:
        key = str(uuid.uuid4())
        for attempt in range(RESPOND_ATTEMPTS):
            last = attempt == RESPOND_ATTEMPTS - 1
            try:
                order = fn(self.api(), key)
            except requests.RequestException:
                if not last:
                    time.sleep(RESPOND_RETRY_DELAY)
                    continue
                self._send_tpl(record, "service_unavailable_retry")
                return
            except SwizzyerError as e:
                if e.code in TRANSIENT_CODES and not last:
                    logger.warning(
                        f"временная ошибка при отправке ответа: {e}, "
                        f"повтор {attempt + 1}/{RESPOND_ATTEMPTS}"
                    )
                    time.sleep(RESPOND_RETRY_DELAY)
                    continue
                self._handle_respond_error(record, e)
                return
            self._apply(record, order)
            return

    def _sync(self, record: OrderRecord) -> None:
        order = self._fetch(record)
        if order is None:
            return
        order = self._maybe_extend(record, order)
        self._apply(record, order)

    def _fetch(self, record: OrderRecord) -> Order | None:
        if not record.swizzyer_order_id:
            return None
        try:
            return self.api().get_order(record.swizzyer_order_id)
        except requests.RequestException:
            return None
        except SwizzyerError as e:
            if e.code is not ErrorCode.ORDER_NOT_FOUND:
                logger.error(f"sync: {e}")
                return None
            reason = str((e.details or {}).get("reason") or "")
            if reason == "id_expired_or_invalid":
                return self._recover_by_external_id(record)
            logger.error(
                f"$YELLOW#{record.funpay_order_id}$RESET: заказ "
                f"{record.swizzyer_order_id} не найден ({reason or 'без причины'})"
            )
            self._fail(record, self._fmt("order_not_found", record))
            return None

    def _recover_by_external_id(self, record: OrderRecord) -> Order | None:
        try:
            found = self.api().list_orders(
                external_id=record.funpay_order_id, limit=5
            )
        except (SwizzyerError, requests.RequestException) as e:
            logger.warning(
                f"Не удалось восстановить заказ {record.funpay_order_id} по external_id: {e}"
            )
            return None
        order = next(iter(found.data), None)
        if order is None:
            logger.error(
                f"$YELLOW#{record.funpay_order_id}$RESET: ID заказа вышел из окна "
                f"получения и не восстановился по external_id"
            )
            self._fail(record, self._fmt("order_not_found", record))
            return None
        if order.id != record.swizzyer_order_id:
            logger.info(
                f"$YELLOW#{record.funpay_order_id}$RESET: заказ восстановлен по "
                f"external_id — $CYAN{order.id}"
            )
            record.swizzyer_order_id = order.id
            db.orders.save(record)
        return order

    def _maybe_extend(self, record: OrderRecord, order: Order) -> Order:
        if order.is_terminal:
            return order
        expires_at = order.session_expires_at
        if not expires_at or expires_at - time.time() > EXTEND_THRESHOLD:
            return order
        last = float(record.data.get("extended_at") or 0)
        if last and time.time() - last < EXTEND_COOLDOWN:
            return order
        try:
            extended = self.api().extend_order(record.swizzyer_order_id)
            record.data["extended_at"] = int(time.time())
            db.orders.save(record)
            logger.info(f"Продлил TTL проверки заказа {record.funpay_order_id}")
            return extended
        except (SwizzyerError, requests.RequestException) as e:
            logger.warning(
                f"Не удалось продлить TTL заказа {record.funpay_order_id}: {e}"
            )
            return order

    def _apply(self, record: OrderRecord, order: Order) -> None:
        record.status = (
            order.status.value if isinstance(order.status, Enum) else str(order.status)
        )
        if order.last_event_sequence is not None:
            record.data["last_seq"] = order.last_event_sequence
        if self._charge_locked(order):
            record.data["charge_locked"] = True
        if record.data.get("relink_active"):
            self._apply_relink(record, order)
            return
        if self._handle_situation(record, order):
            return
        if order.is_terminal:
            self._finish(record, order)
            return
        action = order.next_action
        if action is None or action.type is NextActionType.WAIT:
            if not record.data.get("processing_notified"):
                self._send_tpl(record, "processing")
                record.data["processing_notified"] = True
            db.orders.save(record)
            return
        record.data["processing_notified"] = False
        if record.await_version == action.version:
            db.orders.save(record)
            return
        self._present(record, action)
        db.orders.save(record)

    def _apply_relink(self, record: OrderRecord, order: Order) -> None:
        started = record.data.get("relink_started_at") or 0
        expired = started and time.time() - started > RELINK_MAX_LIFETIME
        action = order.next_action
        if order.is_completed:
            record.data.pop("relink_active", None)
            self._finish(record, order)
            return
        if action is not None and action.type is not NextActionType.WAIT and not expired:
            record.data["processing_notified"] = False
            if record.await_version != action.version:
                self._present(record, action)
            db.orders.save(record)
            return
        if expired or (order.is_terminal and not order.relink_available):
            record.data.pop("relink_active", None)
            self._finish(record, order)
            return
        if not record.data.get("processing_notified"):
            self._send_tpl(record, "processing")
            record.data["processing_notified"] = True
        db.orders.save(record)

    def _handle_situation(self, record: OrderRecord, order: Order) -> bool:
        state = order.situation_state
        if state is SituationState.REAUTH_AVAILABLE:
            self._handle_reauth(record, order)
            return True
        if state is SituationState.MANUAL_COMPLETION_REQUIRED:
            self._handle_manual_completion(record, order)
            return True
        if state is SituationState.CHARGE_VERDICT_PENDING:
            if not record.data.get("charge_pending_notified"):
                logger.warning(
                    f"$YELLOW#{record.funpay_order_id}$RESET: исход списания неизвестен, "
                    f"жду вердикт — заказ пересоздавать нельзя"
                )
                self._send_tpl(record, "charge_pending")
                record.data["charge_pending_notified"] = True
            db.orders.save(record)
            return True
        if state is SituationState.RECOVERY_IN_PROGRESS:
            if not record.data.get("recovery_notified"):
                logger.info(
                    f"$YELLOW#{record.funpay_order_id}$RESET: деньги списаны, "
                    f"сервис довыполняет заказ автоматически"
                )
                self._send_tpl(record, "recovery_in_progress")
                record.data["recovery_notified"] = True
            db.orders.save(record)
            return True
        return False

    def _handle_reauth(self, record: OrderRecord, order: Order) -> None:
        failure = order.last_failure
        code = failure.code if failure else ""
        logger.warning(
            f"$YELLOW#{record.funpay_order_id}$RESET: попытка сорвалась без списания "
            f"({code or 'без причины'}), завершаю ожидание и перезапрашиваю данные"
        )
        if record.swizzyer_order_id:
            try:
                self.api().cancel_order(
                    record.swizzyer_order_id, idempotency_key=str(uuid.uuid4())
                )
            except SwizzyerError as e:
                if e.code is ErrorCode.ORDER_CANNOT_BE_CANCELLED:
                    logger.info(
                        f"$YELLOW#{record.funpay_order_id}$RESET: выдача уже началась, "
                        f"жду её результат"
                    )
                    return
                logger.warning(f"cancel (reauth): {e}")
            except requests.RequestException:
                return
        if failure and failure.category is FailureCategory.SELLER_ACTION_REQUIRED:
            self._notify_admin(
                settings.notify_failure,
                f"🛠 <b><u>Выдача сорвалась — нужно ваше действие</u></b>\n\n"
                f"<blockquote>"
                f"• Заказ: <code>#{utils.escape(record.funpay_order_id)}</code>\n"
                f"• Покупатель: <code>{utils.escape(record.buyer_username)}</code>\n"
                f"• Код: <code>{utils.escape(code)}</code>\n"
                f"• {utils.escape(self._ru(failure.user_message))}\n"
                f"• Списания не было, у покупателя запрошены данные заново"
                f"</blockquote>",
                self._notify_kb(record),
            )
        if code == ErrorCode.VERIFICATION_METHOD_NOT_SUPPORTED.value:
            if self._passkey_refunded(record):
                return
            self._restart_credentials(record, "restart_method_not_supported")
            return
        if code in TIMEOUT_FAILURE_CODES:
            self._restart_credentials(record, "restart_timeout", code=code)
            return
        self._restart_credentials(
            record, RESTART_FAILURE_MESSAGE_KEYS.get(code, "restart_default"), code=code
        )

    def _handle_manual_completion(self, record: OrderRecord, order: Order) -> None:
        if record.data.get("manual_notified"):
            return
        failure = order.last_failure
        reason = self._ru(failure.user_message) if failure else ""
        logger.error(
            f"$YELLOW#{record.funpay_order_id}$RESET: деньги списаны, автоматика "
            f"остановлена — нужен продавец ({failure.code if failure else 'без причины'})"
        )
        record.data["manual_notified"] = True
        record.data["reason"] = reason
        record.pending = False
        record.stage = Stage.DONE
        record.status = MANUAL_COMPLETION_STATUS
        db.orders.save(record)
        self._send_tpl(record, "manual_completion")
        lines = [
            f"• Заказ: <code>#{utils.escape(record.funpay_order_id)}</code>",
            f"• Покупатель: <code>{utils.escape(record.buyer_username)}</code>",
            f"• Аккаунт: <code>{utils.escape(order.roblox_username or record.roblox_username or '—')}</code>",
        ]
        if reason:
            lines.append(f"• Причина: <code>{utils.escape(reason)}</code>")
        self._notify_admin(
            settings.notify_failure,
            "🛠 <b><u>Требуется ручное завершение</u></b>\n\n<blockquote>"
            + "\n".join(lines)
            + "</blockquote>",
            self._notify_kb(record),
        )
        self._start_next(record.buyer_id)

    @staticmethod
    def _push_approval_due(record: OrderRecord) -> bool:
        sent = record.data.get("push_sent_at")
        if not sent:
            return True
        repeat = max(0, settings.push_approval_repeat_minutes)
        if repeat <= 0:
            return False
        return time.time() - float(sent) >= repeat * 60

    def _present(self, record: OrderRecord, action: NextAction) -> None:
        t = action.type
        if t is NextActionType.CREDENTIALS_RETRY and self._count_attempt(record):
            return
        if t is not NextActionType.PUSH_APPROVAL:
            record.data.pop("push_sent_at", None)
        record.stage = Stage.VERIFYING
        logger.info(
            f"$YELLOW#{record.funpay_order_id}$RESET → запрашиваю у покупателя: "
            f"$MAGENTA{STEP_LABELS.get(t, t)}$RESET"
        )
        if t is NextActionType.CHOOSE_ONE:
            self._send(
                record.chat_id, self._choose_one_text(action), record.buyer_username
            )
            record.await_version = action.version
        elif t is NextActionType.CHOOSE_MANY:
            self._send(
                record.chat_id, self._choose_many_text(action), record.buyer_username
            )
            record.await_version = action.version
        elif t is NextActionType.PROVIDE_INPUT:
            self._send(record.chat_id, self._input_text(action), record.buyer_username)
            record.await_version = action.version
        elif t is NextActionType.CREDENTIALS_RETRY:
            record.stage = Stage.AWAITING_CRED_LOGIN
            record.await_version = action.version
            if self._credentials_incorrect(action):
                self._send_tpl(record, "credentials_retry_incorrect")
            else:
                self._send_tpl(record, "credentials_retry")
        elif t is NextActionType.PUSH_APPROVAL:
            if self._push_approval_due(record):
                self._send_tpl(record, "push_approval")
                record.data["push_sent_at"] = int(time.time())
            else:
                logger.debug(
                    f"#{record.funpay_order_id}: просьба про Approve уже отправлена, "
                    f"повтор подавлен"
                )
            record.await_version = action.version

    @staticmethod
    def _delivered_robux(record: OrderRecord, order: Order) -> int:
        credited = order.credited_robux
        if credited is not None:
            return credited
        if order.is_completed:
            if order.result and order.result.robux_credited:
                return order.result.robux_credited
            return record.robux_amount * record.quantity
        return 0

    def _finish(self, record: OrderRecord, order: Order) -> None:
        failure = order.failure_reason
        if failure:
            logger.error(f"Заказ #{record.funpay_order_id} провален — {failure.code}")
            logger.debug(f"Ответ: {order.model_dump_json(indent=2, exclude_none=True)}")
        code = failure.code if failure else ""
        if order.status is OrderStatus.PARTIALLY_DELIVERED and self._start_relink(
            record, order
        ):
            return
        if code in NEVER_RECREATE_CODES or not order.charge_free:
            self._finish_terminal(record, order)
            return
        if order.status is OrderStatus.FAILED and code:
            if (
                code == ErrorCode.VERIFICATION_METHOD_NOT_SUPPORTED.value
                and self._passkey_refunded(record)
            ):
                return
            if code in RESTART_FAILURE_CODES:
                self._restart_credentials(
                    record,
                    RESTART_FAILURE_MESSAGE_KEYS.get(code, "restart_default"),
                    code=code,
                )
                return
            if code in TIMEOUT_FAILURE_CODES:
                self._restart_credentials(record, "restart_timeout", code=code)
                return
        if order.status is OrderStatus.EXPIRED:
            self._restart_credentials(
                record,
                "restart_timeout",
                code=code or ErrorCode.VERIFICATION_SESSION_EXPIRED.value,
            )
            return
        if order.status is OrderStatus.CANCELLED and settings.auto_recover_cancelled:
            logger.info(
                f"Заказ $YELLOW#{record.funpay_order_id}$RESET отменён — "
                f"авто-восстановление, перезапрашиваю логин"
            )
            self._restart_credentials(record, "restart_cancelled")
            return
        if order.status in (
            OrderStatus.FAILED,
            OrderStatus.EXPIRED,
            OrderStatus.CANCELLED,
        ) and self._refund_for_code(
            record,
            code,
            reason=self._ru(failure.user_message) if failure else "",
            fallback=True,
        ):
            return
        self._finish_terminal(record, order)

    def _start_relink(self, record: OrderRecord, order: Order) -> bool:
        if record.data.get("relink_done") or record.data.get("relink_pending"):
            return False
        if not order.relink_available:
            return False
        if order.charge_state is not ChargeState.CHARGED:
            return False
        delivered = self._delivered_robux(record, order)
        total = order.target_robux or record.robux_amount * record.quantity
        logger.warning(
            f"$YELLOW#{record.funpay_order_id}$RESET: выдано {delivered} из {total} "
            f"робуксов — запускаю докид остатка по тому же заказу"
        )
        record.data["relink_pending"] = True
        record.data["relink_done"] = True
        record.data["robux_credited"] = delivered
        record.data["processing_notified"] = False
        record.await_version = None
        record.roblox_username = None
        record.pending = True
        record.stage = Stage.AWAITING_LOGIN
        record.status = (
            order.status.value if isinstance(order.status, Enum) else str(order.status)
        )
        db.orders.save(record)
        self._send_tpl(record, "relink_request", robux=delivered, total=total)
        self._notify_admin(
            settings.notify_failure,
            f"⚠️ <b><u>Частичная выдача — запущен докид</u></b>\n\n"
            f"<blockquote>"
            f"• Заказ: <code>#{utils.escape(record.funpay_order_id)}</code>\n"
            f"• Покупатель: <code>{utils.escape(record.buyer_username)}</code>\n"
            f"• Выдано: <code>{delivered}</code> из <code>{total}</code> R$\n"
            f"• Повторная оплата не требуется"
            f"</blockquote>",
            self._notify_kb(record),
        )
        return True

    def _finish_terminal(self, record: OrderRecord, order: Order) -> None:
        record.pending = False
        record.stage = Stage.DONE
        record.status = (
            order.status.value if isinstance(order.status, Enum) else str(order.status)
        )
        record.data.pop("relink_active", None)
        record.data.pop("relink_pending", None)
        record.data["robux_credited"] = self._delivered_robux(record, order)
        if order.failure_reason:
            record.data["reason"] = self._ru(order.failure_reason.user_message)
        db.orders.save(record)
        status = order.status
        oid = record.funpay_order_id
        name = utils.escape(order.roblox_username or record.roblox_username or "")
        robux = self._delivered_robux(record, order)
        if status is OrderStatus.COMPLETED:
            logger.info(
                f"Заказ $YELLOW#{oid} выполнен$RESET: начислено "
                f"$MAGENTA{robux}$RESET робуксов на "
                f"$CYAN{order.roblox_username or record.roblox_username}"
            )
            self._send_tpl(
                record,
                "success",
                robux=robux,
                account=order.roblox_username or record.roblox_username or "",
            )
        elif status is OrderStatus.PARTIALLY_DELIVERED:
            total = order.target_robux or record.robux_amount * record.quantity
            logger.warning(f"Заказ #{oid} выдан частично: {robux} из {total} робуксов")
            key = "relink_unavailable" if record.data.get("relink_done") else "partial"
            self._send_tpl(record, key, robux=robux, total=total)
        elif status is OrderStatus.FAILED:
            reason = (
                self._ru(order.failure_reason.user_message)
                if order.failure_reason
                else ""
            )
            logger.warning(f"Заказ #{oid} не выполнен: {reason or 'без причины'}")
            self._send(
                record.chat_id,
                self._fmt("failed", record, reason=reason).rstrip(),
                record.buyer_username,
            )
        elif status is OrderStatus.CANCELLED:
            logger.info(f"Заказ $YELLOW#{oid}$RESET отменён")
            self._send_tpl(record, "cancelled")
        elif status is OrderStatus.EXPIRED:
            logger.info(f"Заказ $YELLOW#{oid}$RESET просрочен")
            self._send_tpl(record, "expired")
        else:
            self._send_tpl(record, "done_generic")
        self._notify_finish(record, order, status, name)
        self._start_next(record.buyer_id)

    def _notify_finish(
        self, record: OrderRecord, order: Order, status, name: str
    ) -> None:
        buyer = utils.escape(record.buyer_username or str(record.buyer_id))
        oid = utils.escape(record.funpay_order_id)
        account = name or "—"
        if status is OrderStatus.COMPLETED:
            if not settings.notify_success:
                return
            robux = self._delivered_robux(record, order)
            self._notify_admin(
                settings.notify_success,
                f"🎉 <b><u>Заказ выполнен</u></b>\n\n"
                f"<blockquote>"
                f"• Заказ: <code>#{oid}</code>\n"
                f"• Покупатель: <code>{buyer}</code>\n"
                f"• Аккаунт: <code>{account}</code>\n"
                f"• Начислено: <code>{robux} R$</code>"
                f"</blockquote>",
                self._notify_kb(record),
            )
            return
        if not settings.notify_failure:
            return
        labels = {
            OrderStatus.PARTIALLY_DELIVERED: "⚠️ <b><u>Заказ выдан частично</u></b>",
            OrderStatus.FAILED: "❌ <b><u>Заказ не выполнен</u></b>",
            OrderStatus.CANCELLED: "❌ <b><u>Заказ отменён</u></b>",
            OrderStatus.EXPIRED: "⌛ <b><u>Заказ просрочен</u></b>",
        }
        title = labels.get(status, "⚠️ <b><u>Проблема с заказом</u></b>")
        lines = [
            f"• Заказ: <code>#{oid}</code>",
            f"• Покупатель: <code>{buyer}</code>",
            f"• Аккаунт: <code>{account}</code>",
        ]
        if status is OrderStatus.PARTIALLY_DELIVERED:
            total = order.target_robux or record.robux_amount * record.quantity
            lines.append(
                f"• Выдано: <code>{self._delivered_robux(record, order)}</code> "
                f"из <code>{total} R$</code>"
            )
        if not order.charge_free:
            lines.append(
                f"• Оплата: <code>{_code_str(order.charge_state)}</code> — "
                f"пересоздавать заказ нельзя"
            )
        reason = (
            self._ru(order.failure_reason.user_message) if order.failure_reason else ""
        )
        if reason:
            lines.append(f"• Причина: <code>{utils.escape(reason)}</code>")
        if order.failure_reason:
            lines.append(
                f"• Код: <code>{utils.escape(order.failure_reason.code)}</code>"
            )
        text = f"{title}\n\n<blockquote>" + "\n".join(lines) + "</blockquote>"
        self._notify_admin(settings.notify_failure, text, self._notify_kb(record))

    def _restart_credentials(
        self,
        record: OrderRecord,
        key: str = "restart_default",
        *,
        code: str = "",
        **extra,
    ) -> bool:
        if code and self._refund_for_code(
            record, code, reason=str(extra.get("reason", ""))
        ):
            return True
        record.data["processing_notified"] = False
        record.data.pop("charge_pending_notified", None)
        record.data.pop("recovery_notified", None)
        record.data.pop("last_seq", None)
        record.data.pop("push_sent_at", None)
        record.data["last_buyer_at"] = int(time.time())
        record.swizzyer_order_id = None
        record.await_version = None
        record.roblox_username = None
        record.status = None
        record.pending = True
        record.stage = Stage.AWAITING_LOGIN
        db.orders.save(record)
        logger.info(
            f"$YELLOW#{record.funpay_order_id}$RESET: перезапрашиваю логин/пароль у покупателя"
        )
        self._send_tpl(record, key, **extra)
        return True

    def _fail(self, record: OrderRecord, text: str, *, code: str = "") -> None:
        if self._refund_for_code(record, code, fallback=True):
            return
        record.pending = False
        record.stage = Stage.DONE
        record.status = record.status or "failed"
        db.orders.save(record)
        self._send(record.chat_id, text, record.buyer_username)
        self._start_next(record.buyer_id)

    def _notify_seller_error(self, record: OrderRecord, e: SwizzyerError) -> None:
        message = self._ru(e.message)
        lines = [
            f"• Заказ: <code>#{utils.escape(record.funpay_order_id)}</code>",
            f"• Код: <code>{utils.escape(_code_str(e.code))}</code>",
        ]
        if message:
            lines.append(f"• {utils.escape(message)}")
        details = e.details or {}
        if "required" in details and "available" in details:
            lines.append(
                f"• Нужно: <code>{details['required']}</code>, "
                f"доступно: <code>{details['available']}</code>"
            )
        self._notify_admin(
            settings.notify_failure,
            "⚠️ <b><u>Заказ не создан</u></b>\n\n<blockquote>"
            + "\n".join(lines)
            + "</blockquote>",
            self._notify_kb(record),
        )

    def _handle_create_error(self, record: OrderRecord, e: SwizzyerError) -> None:
        code = e.code
        logger.error(f"create_order: {e}")
        if _code_str(code) in NEVER_RECREATE_CODES:
            record.pending = False
            record.stage = Stage.DONE
            record.status = MANUAL_COMPLETION_STATUS
            record.data["charge_locked"] = True
            record.data["reason"] = self._ru(e.message)
            db.orders.save(record)
            self._send_tpl(record, "manual_completion")
            self._notify_seller_error(record, e)
            self._start_next(record.buyer_id)
            return
        if code in PERMANENT_ITEM_CODES:
            self._notify_seller_error(record, e)
            self._fail(
                record, self._fmt("items_unavailable", record), code=_code_str(code)
            )
            return
        if code is ErrorCode.PREMIUM_SUB_QUANTITY_LIMIT:
            self._notify_seller_error(record, e)
            self._fail(
                record, self._fmt("pack_too_large", record), code=_code_str(code)
            )
            return
        if code in (
            ErrorCode.DUPLICATE_RECENT_ORDER,
            ErrorCode.SESSION_ALREADY_HAS_ACTIVE_ORDER,
        ):
            self._fail(record, self._fmt("duplicate_order", record), code=_code_str(code))
            return
        if code in (
            ErrorCode.INSUFFICIENT_FUNDS,
            ErrorCode.NO_FUNDED_ACCOUNT,
            ErrorCode.NO_BILLING_ADDRESS,
            ErrorCode.PAYMENT_DECLINED,
            ErrorCode.PAYMENT_METHOD_EXPIRED,
            ErrorCode.SUBSCRIPTION_REQUIRED,
            ErrorCode.SUBSCRIPTION_EXPIRED,
            ErrorCode.TRANSACTIONS_QUOTA_EXCEEDED,
        ) or code in AUTH_ERROR_CODES:
            self._notify_seller_error(record, e)
        key = RESTART_FAILURE_MESSAGE_KEYS.get(_code_str(code))
        if key:
            self._restart_credentials(record, key, code=_code_str(code))
            return
        if code in AUTH_ERROR_CODES:
            self._restart_credentials(record, "restart_auth")
            return
        if code in (ErrorCode.PAYMENT_DECLINED, ErrorCode.PAYMENT_METHOD_EXPIRED):
            self._restart_credentials(
                record, "restart_insufficient_funds", code=_code_str(code)
            )
            return
        reason = self._ru(e.message) or "Не удалось создать заказ."
        self._restart_credentials(
            record, "restart_create_generic", code=_code_str(code), reason=reason
        )

    def _handle_relink_error(self, record: OrderRecord, e: SwizzyerError) -> None:
        logger.error(f"relink: {e}")
        record.data.pop("relink_pending", None)
        if e.code in TRANSIENT_CODES:
            record.data["relink_pending"] = True
            db.orders.save(record)
            self._send_tpl(record, "service_unavailable_password")
            return
        delivered = record.data.get("robux_credited") or 0
        total = record.robux_amount * record.quantity
        record.pending = False
        record.stage = Stage.DONE
        record.status = OrderStatus.PARTIALLY_DELIVERED.value
        record.data["reason"] = self._ru(e.message)
        db.orders.save(record)
        self._send_tpl(record, "relink_unavailable", robux=delivered, total=total)
        self._notify_admin(
            settings.notify_failure,
            f"⚠️ <b><u>Докид остатка недоступен</u></b>\n\n"
            f"<blockquote>"
            f"• Заказ: <code>#{utils.escape(record.funpay_order_id)}</code>\n"
            f"• Покупатель: <code>{utils.escape(record.buyer_username)}</code>\n"
            f"• Выдано: <code>{delivered}</code> из <code>{total} R$</code>\n"
            f"• Код: <code>{utils.escape(_code_str(e.code))}</code>"
            f"</blockquote>",
            self._notify_kb(record),
        )
        self._start_next(record.buyer_id)

    def _handle_respond_error(self, record: OrderRecord, e: SwizzyerError) -> None:
        code = e.code
        if code in (
            ErrorCode.VERIFICATION_STATE_CHANGED,
            ErrorCode.VERIFICATION_ALREADY_RESPONDED,
            ErrorCode.VERIFICATION_STEP_EXPIRED,
            ErrorCode.VERIFICATION_NOT_READY,
        ):
            record.await_version = None
            db.orders.save(record)
            self._sync(record)
            return
        if code is ErrorCode.VERIFICATION_INPUT_INVALID:
            self._send_tpl(record, "respond_input_invalid")
            if self._count_attempt(record):
                return
            record.await_version = None
            db.orders.save(record)
            self._sync(record)
            return
        if code is ErrorCode.VERIFICATION_METHOD_NOT_SUPPORTED:
            if self._passkey_refunded(record):
                return
            self._restart_credentials(record, "restart_method_not_supported")
            return
        if code in (
            ErrorCode.VERIFICATION_SESSION_EXPIRED,
            ErrorCode.VERIFICATION_TIMEOUT,
            ErrorCode.PROMPT_TIMEOUT,
        ):
            self._restart_credentials(record, "restart_timeout", code=_code_str(code))
            return
        if code is ErrorCode.CHARGE_OUTCOME_UNKNOWN:
            logger.error(
                f"$YELLOW#{record.funpay_order_id}$RESET: исход списания неизвестен — "
                f"заказ пересоздавать нельзя"
            )
            self._send_tpl(record, "charge_pending")
            record.data["charge_pending_notified"] = True
            record.data["charge_locked"] = True
            db.orders.save(record)
            self._notify_seller_error(record, e)
            return
        if code is ErrorCode.ORDER_NOT_FOUND:
            record.await_version = None
            db.orders.save(record)
            self._sync(record)
            return
        key = RESTART_FAILURE_MESSAGE_KEYS.get(_code_str(code))
        if key and code not in TRANSIENT_CODES:
            logger.warning(f"respond: {e}")
            self._restart_credentials(record, key, code=_code_str(code))
            return
        logger.warning(f"respond: {e}")
        record.await_version = None
        db.orders.save(record)
        self._sync(record)

    def _poll_loop(self) -> None:
        while True:
            try:
                if self._enabled():
                    self._poll_batch()
                    self._check_timeouts()
                    self._check_deadlines()
            except Exception:
                logger.error("Ошибка в цикле опроса заказов")
                logger.debug("TRACEBACK", exc_info=True)
            time.sleep(POLL_INTERVAL)

    def _check_timeouts(self) -> None:
        if not settings.refund_on_timeout:
            return
        limit = max(1, settings.refund_timeout_minutes) * 60
        now = time.time()
        for record in db.orders.active_waiting():
            last = float(record.data.get("last_buyer_at") or record.created_at)
            if now - last < limit or not self._refund_allowed(record):
                continue
            self._submit_if_idle(
                record.buyer_id,
                lambda oid=record.funpay_order_id: self._refund_timeout(oid),
            )

    def _refund_timeout(self, funpay_order_id: str) -> None:
        record = db.orders.get(funpay_order_id)
        if not record or record.stage in (Stage.QUEUED, Stage.DONE):
            return
        if not settings.refund_on_timeout or not self._refund_allowed(record):
            return
        minutes = max(1, settings.refund_timeout_minutes)
        last = float(record.data.get("last_buyer_at") or record.created_at)
        if time.time() - last < minutes * 60:
            return
        logger.warning(
            f"$YELLOW#{record.funpay_order_id}$RESET: покупатель не отвечал "
            f"{minutes} мин — оформляю возврат"
        )
        self._refund(record, "refund_timeout", minutes=minutes)

    @staticmethod
    def _elapsed_minutes(record: OrderRecord) -> float:
        return (time.time() - record.created_at) / 60.0

    @staticmethod
    def _deadline_left(record: OrderRecord) -> int:
        elapsed = (time.time() - record.created_at) / 60.0
        return max(0, int(settings.deadline_minutes - elapsed))

    def _deadline_actions(self, record: OrderRecord, now: float) -> list[str]:
        if not settings.deadline_on or not record.pending:
            return []
        if record.stage is Stage.DONE:
            return []
        elapsed = (now - record.created_at) / 60.0
        actions: list[str] = []
        if (
            settings.deadline_warn_on
            and not record.data.get("deadline_warned")
            and elapsed >= settings.deadline_warn_minutes
        ):
            actions.append(DEADLINE_WARN)
        if not settings.deadline_refund_on:
            return actions
        refund_at = settings.deadline_refund_minutes
        if (
            settings.deadline_buyer_warn_on
            and not record.data.get("deadline_buyer_warned")
            and refund_at - settings.deadline_buyer_warn_minutes <= elapsed < refund_at
        ):
            actions.append(DEADLINE_BUYER_WARN)
        if elapsed < refund_at:
            return actions
        if self._refund_allowed(record):
            actions.append(DEADLINE_REFUND)
        elif elapsed >= settings.deadline_minutes:
            if not record.data.get("deadline_final"):
                actions.append(DEADLINE_FINAL)
        else:
            last = record.data.get("deadline_urgent_at")
            period = max(1, settings.deadline_repeat_minutes) * 60
            if not last or now - float(last) >= period:
                actions.append(DEADLINE_URGENT)
        return actions

    def _check_deadlines(self) -> None:
        if not settings.deadline_on:
            return
        now = time.time()
        for record in db.orders.pending_all():
            if not self._deadline_actions(record, now):
                continue
            self._submit_if_idle(
                record.buyer_id,
                lambda oid=record.funpay_order_id: self._apply_deadline(oid),
            )

    def _apply_deadline(self, funpay_order_id: str) -> None:
        record = db.orders.get(funpay_order_id)
        if not record:
            return
        for action in self._deadline_actions(record, time.time()):
            if action == DEADLINE_WARN:
                self._notify_deadline_warning(record)
            elif action == DEADLINE_BUYER_WARN:
                self._warn_buyer_deadline(record)
            elif action == DEADLINE_REFUND:
                logger.warning(
                    f"$YELLOW#{record.funpay_order_id}$RESET: заказ не выполнен за "
                    f"{settings.deadline_refund_minutes} мин — оформляю возврат, "
                    f"чтобы уложиться в регламент"
                )
                self._refund(record, "refund_deadline")
                return
            elif action == DEADLINE_URGENT:
                self._notify_deadline_urgent(record)
            elif action == DEADLINE_FINAL:
                self._notify_deadline_final(record)

    def _deadline_lines(self, record: OrderRecord) -> list[str]:
        return [
            f"• Заказ: <code>#{utils.escape(record.funpay_order_id)}</code>",
            f"• Покупатель: <code>{utils.escape(record.buyer_username)}</code>",
            f"• Аккаунт: <code>{utils.escape(record.roblox_username or '—')}</code>",
            f"• Номинал: <code>{record.robux_amount} R$</code> × "
            f"<code>{record.quantity}</code>",
            f"• Шаг: <code>"
            f"{STAGE_LABELS.get(record.stage, record.stage.value)}</code>",
            f"• В работе: <code>{int(self._elapsed_minutes(record))} мин</code>",
            f"• До дедлайна: <code>{self._deadline_left(record)} мин</code>",
        ]

    def _notify_deadline_warning(self, record: OrderRecord) -> None:
        record.data["deadline_warned"] = True
        db.orders.save(record)
        logger.warning(
            f"$YELLOW#{record.funpay_order_id}$RESET: в работе "
            f"{int(self._elapsed_minutes(record))} мин, до дедлайна "
            f"{self._deadline_left(record)} мин"
        )
        self._notify_admin(
            settings.notify_failure,
            "⏳ <b><u>Заказ долго не выполняется</u></b>\n\n<blockquote>"
            + "\n".join(self._deadline_lines(record))
            + "</blockquote>",
            self._notify_kb(record),
        )

    def _warn_buyer_deadline(self, record: OrderRecord) -> None:
        record.data["deadline_buyer_warned"] = True
        db.orders.save(record)
        left = max(
            1,
            int(settings.deadline_refund_minutes - self._elapsed_minutes(record)),
        )
        logger.info(
            f"$YELLOW#{record.funpay_order_id}$RESET: предупреждаю покупателя — "
            f"до авто-возврата {left} мин"
        )
        self._send_tpl(record, "deadline_buyer_warning", minutes=left)

    def _notify_deadline_urgent(self, record: OrderRecord) -> None:
        record.data["deadline_urgent_at"] = int(time.time())
        db.orders.save(record)
        left = self._deadline_left(record)
        logger.error(
            f"$YELLOW#{record.funpay_order_id}$RESET: срок истекает, возврат "
            f"невозможен — нужно завершить вручную, осталось {left} мин"
        )
        self._notify_admin(
            settings.notify_failure,
            f"🚨 <b><u>Не успеваем — завершите вручную</u></b>\n\n<blockquote>"
            + "\n".join(self._deadline_lines(record))
            + "\n• Авто-возврат невозможен: деньги уже списаны или робуксы "
            "выданы частично"
            + "</blockquote>",
            self._notify_kb(record),
        )

    def _notify_deadline_final(self, record: OrderRecord) -> None:
        record.data["deadline_final"] = True
        db.orders.save(record)
        logger.error(
            f"$YELLOW#{record.funpay_order_id}$RESET: срок выполнения вышел, "
            f"заказ так и не закрыт"
        )
        self._notify_admin(
            settings.notify_failure,
            "⛔️ <b><u>Срок выполнения вышел</u></b>\n\n<blockquote>"
            + "\n".join(self._deadline_lines(record))
            + "\n• Напоминать больше не буду — закройте заказ вручную"
            + "</blockquote>",
            self._notify_kb(record),
        )

    def _poll_batch(self) -> None:
        records = db.orders.verifying()
        by_swizzyer = {r.swizzyer_order_id: r for r in records if r.swizzyer_order_id}
        ids = list(by_swizzyer)
        if not ids:
            return
        for start in range(0, len(ids), LOOKUP_BATCH):
            chunk = ids[start : start + LOOKUP_BATCH]
            try:
                result = self.api().lookup_orders(chunk)
            except (SwizzyerError, requests.RequestException) as e:
                logger.warning(f"lookup_orders: {e}")
                continue
            if result.not_found:
                logger.warning(
                    f"lookup_orders: не найдены заказы swizzyer: {result.not_found}"
                )
            for order in result.data:
                record = by_swizzyer.get(order.id)
                if not record:
                    continue
                self._submit_if_idle(
                    record.buyer_id,
                    lambda oid=record.funpay_order_id, o=order: self._poll_apply(
                        oid, o
                    ),
                )

    def _poll_apply(self, funpay_order_id: str, order: Order) -> None:
        record = db.orders.get(funpay_order_id)
        if not record or not record.pending or record.stage is not Stage.VERIFYING:
            return
        if record.swizzyer_order_id != order.id:
            return
        order = self._maybe_extend(record, order)
        seq = order.last_event_sequence
        if seq is not None and record.data.get("last_seq") == seq:
            return
        self._apply(record, order)

    @staticmethod
    def _ru(msg: I18nMessage | None, default: str = "") -> str:
        return msg.get("ru") if isinstance(msg, I18nMessage) else default

    @staticmethod
    def _credentials_incorrect(action: NextActionCredentialsRetry) -> bool:
        prompt = getattr(action, "prompt", None)
        if not isinstance(prompt, I18nMessage):
            return False
        text = f"{prompt.en} {prompt.ru}".lower()
        return any(
            w in text
            for w in (
                "incorrect",
                "invalid",
                "wrong",
                "неверн",
                "неправильн",
                "некорректн",
            )
        )

    @staticmethod
    def _translate_option(label: I18nMessage | str) -> str:
        if isinstance(label, I18nMessage):
            source = f"{label.en} {label.ru}"
            fallback = label.get("ru") or label.en
        else:
            source = str(label)
            fallback = str(label)
        low = source.lower()
        for keywords, translation in OPTION_TRANSLATIONS:
            if any(kw in low for kw in keywords):
                return translation
        return fallback

    def _choose_one_text(self, action: NextActionChooseOne) -> str:
        prompt = self._ru(action.prompt) or "Roblox просит сделать выбор:"
        options = "\n".join(
            f"{i}. {self._translate_option(opt.label)}"
            for i, opt in enumerate(action.options, 1)
        )
        return self._fmt("choose_one", prompt=prompt, options=options)

    def _choose_many_text(self, action: NextActionChooseMany) -> str:
        n = len(action.options)
        exactly = action.select_exactly
        lines = []
        for i, opt in enumerate(action.options, 1):
            label = (
                opt.label.get("ru")
                if isinstance(opt.label, I18nMessage)
                else str(opt.label)
            )
            lines.append(f"{i}. {swap_homoglyphs(label)}")
        return self._fmt(
            "choose_many", count=exactly, options_count=n, options="\n".join(lines)
        )

    def _input_text(self, action: NextActionProvideInput) -> str:
        spec = action.input
        extra = ""
        if action.attempt and action.max_attempts and action.attempt > 1:
            extra = self._fmt(
                "input_retry_warning",
                attempt=action.attempt,
                max=action.max_attempts,
            )
        if spec.format is InputFormat.RECOVERY_CODE:
            return self._fmt("input_recovery", extra=extra)
        if action.email_hint is None:
            return self._fmt("input_authenticator", extra=extra)
        hint = action.email_hint.strip()
        if "@" in hint:
            return self._fmt("input_email", hint=hint, extra=extra)
        return self._fmt("input_email_nohint", extra=extra)

    @staticmethod
    def _parse_single_index(text: str, n: int) -> int | None:
        nums = re.findall(r"\d+", text)
        if len(nums) != 1:
            return None
        value = int(nums[0])
        return value if 1 <= value <= n else None

    @staticmethod
    def _parse_indices(text: str, n: int, exactly: int) -> list[int] | None:
        nums = re.findall(r"\d+", text)
        if len(nums) == 1 and len(nums[0]) == exactly:
            nums = list(nums[0])
        result: list[int] = []
        for token in nums:
            value = int(token)
            if not 1 <= value <= n or value in result:
                return None
            result.append(value)
        return result if len(result) == exactly else None

    @staticmethod
    def _clean_input(text: str, spec: InputSpec) -> str | None:
        value = text.strip()
        if spec.format is InputFormat.DIGITS:
            value = re.sub(r"\D", "", value)
        if not spec.min_length <= len(value) <= spec.max_length:
            return None
        return value


processor: RobuxDelivery | None = None


def init(cardinal: Cardinal, *args) -> None:
    global processor
    processor = RobuxDelivery(cardinal)
    if getattr(cardinal, "telegram", None):
        AutoRobuxMenu(cardinal).register()


def start(cardinal: Cardinal, *args) -> None:
    if processor:
        processor.start()


def on_new_order(cardinal: Cardinal, event: NewOrderEvent, *args) -> None:
    if processor and event.order:
        processor.handle_new_order(event.order)


def on_new_message(cardinal: Cardinal, event: NewMessageEvent, *args) -> None:
    if processor and event.message:
        processor.handle_new_message(event.message)


BIND_TO_PRE_INIT = [init]
BIND_TO_POST_INIT = [start]
BIND_TO_NEW_ORDER = [on_new_order]
BIND_TO_NEW_MESSAGE = [on_new_message]
BIND_TO_DELETE = None
