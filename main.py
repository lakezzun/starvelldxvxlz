from __future__ import annotations

import logging
import os
import sys
import time

import colorama
from colorama import Fore, Style

from core import App
from first_setup import first_setup, setup_telegram
from utils.brand import APP_NAME, DEVELOPER, VERSION
from utils.config import MAIN_CFG_PATH, cfg_get, ensure_dirs, load_main_config
from utils.logger import configure_logging, set_console_title
from utils.restart import clear_restart_flag
from utils.updater import check_update, run_update

LOGO = r"""
   _____ __                      ____
  / ___// /_____ ________   _____/ /
  \__ \/ __/ __ `/ ___/ / | / / _  /
 ___/ / /_/ /_/ / /  / /| |/ /  __/
/____/\__/\__,_/_/  /_/ |___/\___/
"""


def main() -> None:
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))
    else:
        os.chdir(os.path.dirname(os.path.abspath(__file__)))

    if len(sys.argv) > 1 and sys.argv[1].lower() in {"update", "--update"}:
        print(run_update())
        return

    ensure_dirs()
    clear_restart_flag()
    set_console_title(f"{APP_NAME} v{VERSION}")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    colorama.init()
    configure_logging()
    logger = logging.getLogger("main")

    try:
        print(f"{Fore.CYAN}{LOGO}{Style.RESET_ALL}")
        print(f"{Fore.RED}{Style.BRIGHT}v{VERSION}{Style.RESET_ALL}")
    except Exception:
        try:
            print(f"v{VERSION}")
        except Exception:
            pass
    logger.info("Разработчик %s", DEVELOPER)
    try:
        info = check_update()
        if info.ok and info.has_update:
            logger.info(
                "Доступно обновление %s (сейчас %s). update.bat или /update",
                info.remote_version,
                info.local_version,
            )
    except Exception:
        pass

    try:
        if not MAIN_CFG_PATH.exists():
            first_setup()

        cfg = load_main_config()
        if not cfg_get(cfg, "Telegram", "token"):
            print("Telegram-панель ещё не настроена (нет токена @BotFather).")
            answer = input("Настроить бота сейчас? [Y/n]: ").strip().lower()
            if answer not in {"n", "no", "н", "нет"}:
                setup_telegram(cfg)
                cfg = load_main_config()
        App(cfg, VERSION).init().run()
    except KeyboardInterrupt:
        logger.info("Выход.")
    except Exception:
        logger.critical("Необработанная ошибка", exc_info=True)
        logger.error("Завершаю программу...")
        time.sleep(4)
        sys.exit(1)


if __name__ == "__main__":
    main()
