from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from utils.config import ROOT

logger = logging.getLogger("SVC.restart")
FLAG_PATH = ROOT / "storage" / "cache" / "need_restart"
EXIT_RESTART = 75


def clear_restart_flag() -> None:
    try:
        FLAG_PATH.unlink(missing_ok=True)
    except TypeError:
        try:
            FLAG_PATH.unlink()
        except FileNotFoundError:
            pass
    except Exception:
        pass


def mark_restart() -> None:
    FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    FLAG_PATH.write_text("1", encoding="utf-8")


def restart_program() -> None:
    mark_restart()
    python = sys.executable or "python"
    script = sys.argv[0] if sys.argv else str(ROOT / "main.py")
    path = Path(script)
    if not path.is_absolute():
        candidate = ROOT / path.name
        script = str(candidate if candidate.exists() else ROOT / "main.py")
    os.chdir(str(ROOT))
    argv = [python, script, *sys.argv[1:]]
    logger.info("Перезапуск: %s", argv)
    try:
        os.execv(python, argv)
    except Exception:
        logger.exception("os.execv не сработал, выхожу с кодом %s", EXIT_RESTART)
        raise SystemExit(EXIT_RESTART)
