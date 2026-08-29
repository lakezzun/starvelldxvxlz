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
_STD_OUTPUT = 0xFFFFFFF5  # STD_OUTPUT_HANDLE
_STD_ERROR = 0xFFFFFFF4  # STD_ERROR_HANDLE
_kernel32 = None


def _bad_handle(handle) -> bool:
    if handle is None:
        return True
    value = handle if isinstance(handle, int) else getattr(handle, "value", handle)
    return value in {None, 0, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF}

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


def _win_api():
    global _kernel32
    if _kernel32 is not None:
        return _kernel32
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetStdHandle.argtypes = [wintypes.DWORD]
    k32.GetStdHandle.restype = wintypes.HANDLE
    k32.WriteConsoleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    k32.WriteConsoleW.restype = wintypes.BOOL
    k32.SetConsoleOutputCP.argtypes = [wintypes.UINT]
    k32.SetConsoleOutputCP.restype = wintypes.BOOL
    k32.SetConsoleCP.argtypes = [wintypes.UINT]
    k32.SetConsoleCP.restype = wintypes.BOOL
    k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetConsoleMode.restype = wintypes.BOOL
    k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.SetConsoleMode.restype = wintypes.BOOL
    k32.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
    k32.SetConsoleTitleW.restype = wintypes.BOOL
    _kernel32 = k32
    return k32


def enable_windows_console() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = _win_api()
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleCP(65001)
        vt = 0x0004
        for std_id in (_STD_OUTPUT, _STD_ERROR):
            handle = k32.GetStdHandle(std_id)
            if _bad_handle(handle):
                continue
            mode = wintypes.DWORD()
            if k32.GetConsoleMode(handle, ctypes.byref(mode)):
                k32.SetConsoleMode(handle, mode.value | vt)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _write_console_w(text: str) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = _win_api()
        handle = k32.GetStdHandle(_STD_OUTPUT)
        if _bad_handle(handle):
            return False
        payload = text.replace("\n", "\r\n")
        written = wintypes.DWORD(0)
        nchars = len(payload.encode("utf-16-le")) // 2
        return bool(k32.WriteConsoleW(handle, payload, nchars, ctypes.byref(written), None))
    except Exception:
        return False


def _write_line(text: str) -> None:
    line = ANSI_RE.sub("", text).rstrip("\r\n") + "\n"
    if _write_console_w(line):
        return
    raw = line.encode("utf-8", errors="replace")
    try:
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        return
    except Exception:
        pass
    try:
        sys.stdout.write(line)
        sys.stdout.flush()
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
    enable_windows_console()
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
            _win_api().SetConsoleTitleW(title)
        except Exception:
            pass
        return
    try:
        sys.stdout.write(f"\33]0;{title}\a")
        sys.stdout.flush()
    except Exception:
        pass
