from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from colorama import Fore, Style

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
_SECRET_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+", re.I)


def _has_console() -> bool:
    try:
        stream = sys.stdout
        return bool(stream) and stream.isatty()
    except Exception:
        return False


def _enable_windows_vt() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _safe_reconfigure() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass


HAS_CONSOLE = _has_console()
_safe_reconfigure()
HAS_COLOR = bool(HAS_CONSOLE and _enable_windows_vt())


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


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: Fore.LIGHTBLACK_EX,
        logging.INFO: Fore.CYAN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not HAS_COLOR:
            return message
        color = self.COLORS.get(record.levelno, "")
        return f"{color}{message}{Style.RESET_ALL}"


class SafeStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._write(self.format(record) + self.terminator)
        except Exception:
            pass

    def flush(self) -> None:
        try:
            super().flush()
        except Exception:
            pass

    def _write(self, msg: str) -> None:
        stream = self.stream or sys.stdout
        try:
            stream.write(msg)
        except Exception:
            raw = msg.encode("utf-8", errors="replace")
            buf = getattr(stream, "buffer", None)
            if buf is None:
                buf = getattr(sys.__stdout__, "buffer", None)
            if buf is not None:
                buf.write(raw)
            else:
                sys.__stdout__.write(msg.encode("ascii", errors="replace").decode("ascii"))
        try:
            stream.flush()
        except Exception:
            try:
                sys.__stdout__.flush()
            except Exception:
                pass


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    secret = SecretFilter()

    console = SafeStreamHandler(sys.__stdout__ if hasattr(sys, "__stdout__") else sys.stdout)
    console.setFormatter(ColorFormatter(fmt, datefmt))
    console.addFilter(secret)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "starvell-dxvxlz.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(fmt, datefmt))
    file_handler.addFilter(secret)
    root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("TeleBot").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("requests").setLevel(logging.WARNING)


def set_console_title(title: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
    elif HAS_CONSOLE:
        try:
            sys.stdout.write(f"\33]0;{title}\a")
            sys.stdout.flush()
        except Exception:
            pass
