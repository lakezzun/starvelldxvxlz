from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "storage" / "cache"
STATS_PATH = CACHE / "stats.json"
SEEN_PATH = CACHE / "seen.json"
USERS_PATH = CACHE / "authorized_users.json"
NOTIF_PATH = CACHE / "notification_settings.json"
DISABLED_PLUGINS_PATH = CACHE / "disabled_plugins.json"


def _load(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if data is not None else default
    except Exception:
        return default


def _save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_stats() -> dict[str, Any]:
    data = _load(STATS_PATH, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("orders", 0)
    data.setdefault("messages", 0)
    data.setdefault("errors", 0)
    data.setdefault("bumps", 0)
    return data


def save_stats(data: dict[str, Any]) -> None:
    _save(STATS_PATH, data)


def bump_stat(key: str, amount: int = 1) -> dict[str, Any]:
    stats = load_stats()
    stats[key] = int(stats.get(key) or 0) + amount
    save_stats(stats)
    return stats


def load_authorized_users() -> dict[str, Any]:
    data = _load(USERS_PATH, {})
    return data if isinstance(data, dict) else {}


def save_authorized_users(data: dict[str, Any]) -> None:
    _save(USERS_PATH, data)


def load_notification_settings() -> dict[str, Any]:
    data = _load(NOTIF_PATH, {})
    return data if isinstance(data, dict) else {}


def save_notification_settings(data: dict[str, Any]) -> None:
    _save(NOTIF_PATH, data)


def load_disabled_plugins() -> set[str]:
    data = _load(DISABLED_PLUGINS_PATH, [])
    if isinstance(data, list):
        return {str(x) for x in data}
    return set()


def save_disabled_plugins(uuids: set[str]) -> None:
    _save(DISABLED_PLUGINS_PATH, sorted(uuids))
