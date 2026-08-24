from __future__ import annotations

import configparser

from colorama import Fore, Style

from tg_bot.utils import hash_password
from utils.brand import APP_NAME
from utils.config import CONFIGS, DEFAULTS, save_main_config


def first_setup() -> None:
    print(f"Первичная настройка {APP_NAME}\n")
    print("1. Откройте https://starvell.com в браузере и войдите в аккаунт продавца.")
    print("2. F12 → Application / Хранилище → Cookies → starvell.com → скопируйте session")
    print("   (можно вставить всю строку Cookie целиком).\n")
    cookie = input("Session cookie: ").strip()
    while not cookie:
        cookie = input("Cookie пустой. Вставьте session ещё раз: ").strip()

    proxy = input("Прокси (http://user:pass@host:port, Enter — без прокси): ").strip()
    chats = input("Интервал чатов в секундах [4]: ").strip() or "4"
    orders = input("Интервал заказов в секундах [8]: ").strip() or "8"

    cfg = configparser.ConfigParser(interpolation=None)
    for section, values in DEFAULTS.items():
        cfg[section] = dict(values)
    cfg["Starvell"]["session_cookie"] = cookie
    cfg["Starvell"]["proxy"] = proxy
    cfg["Proxy"]["enabled"] = "1" if proxy else "0"
    cfg["Proxy"]["url"] = proxy
    cfg["Bot"]["chats_interval"] = chats
    cfg["Bot"]["orders_interval"] = orders

    _ask_telegram(cfg)

    CONFIGS.mkdir(parents=True, exist_ok=True)
    save_main_config(cfg)
    print("\nКонфиг сохранён: configs/_main.cfg")
    print("Запускаю ядро. Напишите боту пароль, затем /menu.\n")


def setup_telegram(cfg: configparser.ConfigParser) -> None:
    _ask_telegram(cfg)
    save_main_config(cfg)
    print("Telegram-панель сохранена. Сейчас запущу мониторинг Starvell.\n")


def _ask_telegram(cfg: configparser.ConfigParser) -> None:
    print(f"\n{Fore.CYAN}Telegram-панель{Style.RESET_ALL}")
    print("Токен берётся у @BotFather. Enter — пропустить, консоль будет работать без бота.\n")
    token = input("API-токен бота: ").strip()
    if not token:
        cfg["Telegram"]["enabled"] = "0"
        cfg["Telegram"]["token"] = ""
        cfg["Telegram"]["password"] = ""
        return
    while ":" not in token or not token.split(":", 1)[0].isdigit():
        token = input("Похоже, это не токен BotFather. Вставьте ещё раз (или Enter — пропустить): ").strip()
        if not token:
            cfg["Telegram"]["enabled"] = "0"
            cfg["Telegram"]["token"] = ""
            cfg["Telegram"]["password"] = ""
            return
    username = _check_token(token)
    if username:
        print(f"Бот найден: @{username}")
    else:
        print("Токен сохранён, но сейчас не удалось спросить Telegram (сеть/прокси). Проверим при запуске.")

    password = _ask_password()
    tg_proxy = input("Прокси для Telegram (если кнопки панели падают по SSL, Enter — без): ").strip()
    if "Telegram" not in cfg:
        cfg.add_section("Telegram")
    cfg["Telegram"]["enabled"] = "1"
    cfg["Telegram"]["token"] = token
    cfg["Telegram"]["password"] = hash_password(password)
    cfg["Telegram"]["proxy"] = tg_proxy


def _ask_password() -> str:
    print("\nПароль панели (его спросит бот). Минимум 8 символов, заглавные, строчные и цифра.")
    while True:
        password = input("Пароль: ").strip()
        if (
            len(password) < 8
            or password.lower() == password
            or password.upper() == password
            or not any(ch.isdigit() for ch in password)
        ):
            print("Слабый пароль. Попробуйте ещё раз.")
            continue
        return password


def _check_token(token: str) -> str | None:
    try:
        import telebot

        return telebot.TeleBot(token).get_me().username
    except Exception:
        return None
