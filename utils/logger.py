from __future__ import annotations

import atexit
import logging
import logging.handlers
import os
import queue
import re
import sys

from colorama import Back, Fore, Style

from utils.config import ROOT

LOG_DIR = ROOT / "logs"
_SECRET_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+", re.I)

LOG_COLORS = {
    logging.DEBUG: Fore.BLACK + Style.BRIGHT,
    logging.INFO: Fore.GREEN,
    logging.WARN: Fore.YELLOW,
    logging.ERROR: Fore.RED,
    logging.CRITICAL: Back.RED,
}

CLI_LOG_FORMAT = (
    f"{Fore.BLACK + Style.BRIGHT}[%(asctime)s]{Style.RESET_ALL}"
    f"{Fore.CYAN}>{Style.RESET_ALL} $RESET%(levelname).1s: %(message)s{Style.RESET_ALL}"
)
CLI_TIME_FORMAT = "%d-%m-%Y %H:%M:%S"
FILE_LOG_FORMAT = "[%(asctime)s][%(filename)s][%(lineno)d]> %(levelname).1s: %(message)s"
FILE_TIME_FORMAT = "%d.%m.%y %H:%M:%S"
CLEAR_RE = re.compile(r"(\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]))|(\n)|(\r)")
LOGGER_NAMES = ["main", "SVC", "TGBot"]


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _SECRET_RE.sub("bot***", str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _SECRET_RE.sub("bot***", str(v)) for k, v in record.args.items()}
                else:
                    record.args = tuple(_SECRET_RE.sub("bot***", str(a)) for a in record.args)
        except Exception:
            pass
        return True


def add_colors(text: str) -> str:
    colors = {
        "$YELLOW": Fore.YELLOW,
        "$CYAN": Fore.CYAN,
        "$MAGENTA": Fore.MAGENTA,
        "$BLUE": Fore.BLUE,
        "$GREEN": Fore.GREEN,
        "$BLACK": Fore.BLACK,
        "$WHITE": Fore.WHITE,
        "$B_YELLOW": Back.YELLOW,
        "$B_CYAN": Back.CYAN,
        "$B_MAGENTA": Back.MAGENTA,
        "$B_BLUE": Back.BLUE,
        "$B_GREEN": Back.GREEN,
        "$B_BLACK": Back.BLACK,
        "$B_WHITE": Back.WHITE,
    }
    for key, value in colors.items():
        if key in text:
            text = text.replace(key, value)
    return text


class CLILoggerFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        msg = add_colors(msg)
        msg = msg.replace("$RESET", LOG_COLORS[record.levelno])
        record.msg = msg
        record.args = None
        log_format = CLI_LOG_FORMAT.replace("$RESET", Style.RESET_ALL + LOG_COLORS[record.levelno])
        return logging.Formatter(log_format, CLI_TIME_FORMAT).format(record)


class FileLoggerFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = CLEAR_RE.sub("", record.getMessage())
        record.msg = msg
        record.args = None
        return logging.Formatter(FILE_LOG_FORMAT, FILE_TIME_FORMAT).format(record)


class _QueueHandler(logging.handlers.QueueHandler):
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return record


def configure_logging() -> logging.handlers.QueueListener:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    secret = SecretFilter()

    cli_handler = logging.StreamHandler()
    cli_handler.setLevel(logging.INFO)
    cli_handler.setFormatter(CLILoggerFormatter())
    cli_handler.addFilter(secret)
    cli_handler.addFilter(lambda record: record.name != "TeleBot")

    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(LOG_DIR / "starvell-dxvxlz.log"),
        maxBytes=20 * 1024 * 1024,
        backupCount=25,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(FileLoggerFormatter())
    file_handler.addFilter(secret)

    log_queue = queue.SimpleQueue()
    queue_handler = _QueueHandler(log_queue)

    for name in LOGGER_NAMES:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(logging.DEBUG)
        logger.addHandler(queue_handler)
        logger.propagate = False

    telebot_logger = logging.getLogger("TeleBot")
    telebot_logger.handlers.clear()
    telebot_logger.setLevel(logging.ERROR)
    telebot_logger.propagate = False
    telebot_logger.addHandler(queue_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("requests").setLevel(logging.WARNING)

    listener = logging.handlers.QueueListener(log_queue, cli_handler, file_handler, respect_handler_level=True)
    listener.start()
    atexit.register(listener.stop)
    return listener


def set_console_title(title: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
        return
    try:
        sys.stdout.write(f"\33]0;{title}\a")
        sys.stdout.flush()
    except Exception:
        pass
