from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
MAIN_CFG_PATH = CONFIGS / "_main.cfg"


DEFAULTS = {
    "Starvell": {
        "session_cookie": "",
        "proxy": "",
    },
    "Bot": {
        "chats_interval": "4",
        "orders_interval": "8",
        "handler_timeout": "0",
        "language": "ru",
    },
    "Telegram": {
        "enabled": "0",
        "token": "",
        "password": "",
        "proxy": "",
    },
    "Proxy": {
        "enabled": "0",
        "url": "",
    },
    "Greetings": {
        "enabled": "0",
        "text": "Здравствуйте! Заказ принят, скоро ответим.",
    },
    "AutoDelivery": {
        "enabled": "1",
    },
    "AutoResponse": {
        "enabled": "1",
    },
}


def ensure_dirs() -> None:
    for path in (
        CONFIGS,
        ROOT / "logs",
        ROOT / "plugins",
        ROOT / "storage",
        ROOT / "storage" / "cache",
        ROOT / "storage" / "plugins",
        ROOT / "storage" / "products",
    ):
        path.mkdir(parents=True, exist_ok=True)
    for name in ("auto_delivery.cfg", "auto_response.cfg"):
        file = CONFIGS / name
        if not file.exists():
            file.write_text("", encoding="utf-8")


def load_main_config(path: Path | str = MAIN_CFG_PATH) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    for section, values in DEFAULTS.items():
        cfg[section] = dict(values)
    cfg.read(path, encoding="utf-8")
    return cfg


def save_main_config(cfg: configparser.ConfigParser, path: Path | str = MAIN_CFG_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        cfg.write(handle)


def cfg_get(cfg: configparser.ConfigParser, section: str, key: str, default: Any = "") -> str:
    try:
        return cfg.get(section, key, fallback=str(default)).strip()
    except Exception:
        return str(default)


def proxy_url(cfg: configparser.ConfigParser) -> str | None:
    url = cfg_get(cfg, "Proxy", "url") or cfg_get(cfg, "Starvell", "proxy")
    if not url:
        return None
    if cfg_get(cfg, "Proxy", "enabled", "0") in {"1", "true", "yes", "on"}:
        return url
    return None
