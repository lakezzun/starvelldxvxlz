from __future__ import annotations

import hashlib
import hmac
import html
from typing import Any


class NotificationTypes:
    new_message = "new_message"
    new_order = "new_order"
    bot_start = "bot_start"
    critical = "critical"
    lots_raise = "lots_raise"
    other = "other"


def h(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def get_offset(index: int, amount: int = 8) -> int:
    if amount <= 0:
        return 0
    return index - (index % amount)


def hash_password(password: str) -> str:
    return hashlib.sha256(f"svc:{password}".encode("utf-8")).hexdigest()


def check_password(plain: str, stored: str) -> bool:
    stored = (stored or "").strip()
    if not stored:
        return False
    if len(stored) == 64 and all(ch in "0123456789abcdef" for ch in stored.lower()):
        return hmac.compare_digest(hash_password(plain), stored.lower())
    return hmac.compare_digest(plain, stored)


def mask_cookie(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return "не задан"
    if "=" in token:
        return "cookie-строка задана"
    if len(token) <= 12:
        return token[:4] + "…"
    return token[:8] + "…" + token[-4:]


def mask_proxy(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "нет"
    if "@" in url:
        return url.split("@", 1)[-1]
    return url
