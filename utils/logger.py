from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

from utils.config import ROOT

LOG_DIR = ROOT / "logs"
_SECRET_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+", re.I)
ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

CLI_LOG_FORMAT = "[%(asctime)s] > %(levelname).1s: %(message)s"
CLI_TIME_FORMAT = "%d-%m-%Y %H:%M:%S"
FILE_LOG_FORMAT = "[%(asctime)s][%(filename)s][%(lineno)d]> %(levelname).1s: %(message)s"
FILE_TIME_FORMAT = "%d.%m.%y %H:%M:%S"
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


class CLILoggerFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        record.msg = msg
        record.args = None
        return logging.Formatter(CLI_LOG_FORMAT, CLI_TIME_FORMAT).format(record)


class FileLoggerFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = ANSI_RE.sub("", record.getMessage())
        record.msg = msg
        record.args = None
        return logging.Formatter(FILE_LOG_FORMAT, FILE_TIME_FORMAT).format(record)


def _write_line(text: str) -> None:
    line = ANSI_RE.sub("", text).rstrip("\r\n") + "\n"
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            if handle and kernel32.GetFileType(handle) == 2:
                data = line.replace("\n", "\r\n")
                written = ctypes.c_ulong(0)
                buf = ctypes.create_unicode_buffer(data)
                if kernel32.WriteConsoleW(handle, buf, len(data), ctypes.byref(written), None):
                    return
        except Exception:
            pass
    raw = line.encode("utf-8", errors="replace")
    for fd in (1, 2):
        try:
            os.write(fd, raw)
            return
        except Exception:
            continue
    try:
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        return
    except Exception:
        pass
    print(line, end="", flush=True)


class ConsoleHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _write_line(self.format(record))
        except Exception:
            try:
                print(self.format(record), flush=True)
            except Exception:
                pass


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    secret = SecretFilter()

    cli_handler = ConsoleHandler()
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

    for name in LOGGER_NAMES:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(logging.DEBUG)
        logger.addHandler(cli_handler)
        logger.addHandler(file_handler)
        logger.propagate = False

    telebot_logger = logging.getLogger("TeleBot")
    telebot_logger.handlers.clear()
    telebot_logger.setLevel(logging.CRITICAL)
    telebot_logger.propagate = False
    telebot_logger.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("requests").setLevel(logging.WARNING)


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
