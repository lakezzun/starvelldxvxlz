from __future__ import annotations

import html
import json
import logging
import os
import random
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from utils.config import ROOT

NAME = "Автозвёзды"
VERSION = "1.0.0"
DESCRIPTION = "Продажа Telegram Stars через Fragment: лоты Starvell, @username, очередь и возврат."
CREDITS = "@tinechelovec / port @dxvxlz"
UUID = "c8e4a1b2-7d3f-4a91-9e5c-2b8f6d0a1c47"
SETTINGS_PAGE = True
COMMANDS = {
    "fnp": "панель автозвёзд",
    "fnpjwt": "сохранить Fragment JWT",
    "fnphelp": "справка автозвёзд",
}

logger = logging.getLogger("SVC.autostars")
CB = "fts"
PS = f"47:{UUID}"
MIN_STARS = int(os.getenv("FTS_MIN_STARS", "50"))
FRAGMENT_BASE = os.getenv("FRAGMENT_BASE", "https://api.fragment-api.com/v1").rstrip("/")
FRAGMENT_ORDER_STARS = os.getenv("FRAGMENT_ORDER_STARS", f"{FRAGMENT_BASE}/order/stars/")
FRAGMENT_WALLET_URLS = [
    f"{FRAGMENT_BASE}/misc/wallet/",
    f"{FRAGMENT_BASE}/misc/wallet",
    f"{FRAGMENT_BASE}/wallet/balance/",
    f"{FRAGMENT_BASE}/wallet/balance",
]
FRAGMENT_USER_URLS = [
    f"{FRAGMENT_BASE}/misc/user/user/",
    f"{FRAGMENT_BASE}/misc/user/",
]
FTS_BRIDGE_URL = os.getenv("FTS_BRIDGE_URL", "https://fts-transfer-token.vercel.app").strip().rstrip("/")
FTS_BRIDGE_REDEEM_URL = os.getenv("FTS_BRIDGE_REDEEM_URL", f"{FTS_BRIDGE_URL}/api/redeem").strip()
FTS_BRIDGE_PLUGIN_SECRET = os.getenv(
    "FTS_TRANSFER_TOKEN_PLUGIN_SECRET",
    os.getenv(
        "FTS_BRIDGE_PLUGIN_SECRET",
        os.getenv(
            "PLUGIN_API_SECRET",
            "fa6db024bd75b3ff33ef46bfae67185d0c343ec47c09f18c2ac594bc276cde1ab887dff104545cd9d5ce955d9db4f7d3",
        ),
    ),
).strip()
CONNECT_TIMEOUT = float(os.getenv("FTS_FRAGMENT_CONNECT_TIMEOUT_SEC", "15"))
READ_TIMEOUT = float(os.getenv("FTS_FRAGMENT_READ_TIMEOUT_SEC", "180"))
CURRENCY_TON = "ton"
CURRENCY_USDT = "usdt_ton"

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
_PLUS_RE = re.compile(r"^\s*(?:\+{1,2}|ok|да)\s*$", re.I)
_INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_JWT_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_JWT_JSON_KEYS = {"token", "jwt", "access", "access_token", "authorization", "auth", "fragment_jwt"}
_FTS_BRIDGE_CODE_RE = re.compile(r"FTS-[A-HJ-NP-Z2-9]{4}(?:-[A-HJ-NP-Z2-9]{4}){3}", re.I)
_IO_LOCK = threading.RLock()
_SEND_LOCKS: dict[str, threading.Lock] = {}
_APP: Any = None
_ACTIVE_SENDS: set[str] = set()
_CHAT_BY_BUYER: dict[str, str] = {}
_SEEN_ORDERS: set[str] = set()
_SEEN_MESSAGES: set[str] = set()

ST_JWT = "fts_jwt"
ST_LOT_QTY = "fts_lot_qty"
ST_LOT_ID = "fts_lot_id"
ST_TPL = "fts_tpl"
ST_MBAL = "fts_mbal"
ST_PHOST = "fts_phost"
ST_PPORT = "fts_pport"


def _plugin_dir() -> Path:
    return ROOT / "storage" / "plugins" / "FTS-Plugin"


def _settings_path() -> Path:
    return _plugin_dir() / "settings.json"


def _orders_path() -> Path:
    return _plugin_dir() / "orders.json"


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _default_templates() -> dict[str, str]:
    return {
        "purchase_created": (
            "Спасибо за покупку {qty}⭐!\n"
            "Напишите ваш Telegram-тег одной строкой в формате @username.\n"
            "Пример: @username"
        ),
        "username_invalid": (
            "Некорректный или несуществующий тег.\n"
            "Отправьте верный Telegram-тег в формате @username (5–32, латиница/цифры/подчёркивание)."
        ),
        "username_valid": "✅ Тег принят: @{username}.",
        "sending": "Отправляю {qty}⭐ на @{username}…",
        "sent": "✅ Готово: отправлено {qty}⭐ на @{username}.",
        "failed": "❌ Не удалось отправить звёзды: {reason}",
        "queued": "Заказ принят. Сейчас вы в очереди: позиция {pos}. Я напишу, когда дойдёт ваша очередь.",
        "your_turn": "До вас дошла очередь на {qty}⭐.\nПришлите ваш Telegram-тег одной строкой: @username",
        "confirm": (
            "Проверьте данные:\n- Количество: {qty}⭐\n- Ник: @{username}\n\n"
            "Если всё верно — ответьте «+».\nЧтобы изменить — пришлите другой @username."
        ),
        "refund": "🔁 Оформляю возврат по заказу #{oid}…",
        "refund_ok": "✅ Возврат по заказу #{oid} выполнен.",
        "refund_fail": "❌ Не удалось оформить возврат по заказу #{oid}: {reason}",
        "too_small": "⚠️ Заказ на {qty}⭐ меньше минимума ({min}⭐). Напишите продавцу.",
    }


def _default_cfg() -> dict[str, Any]:
    return {
        "plugin_enabled": True,
        "fragment_jwt": None,
        "auto_send_without_plus": False,
        "skip_username_check": False,
        "auto_refund": False,
        "anonymous_stars_send": True,
        "retry_liteserver": True,
        "stars_currency": CURRENCY_TON,
        "usdt_fallback_to_ton": False,
        "min_balance_ton": 5.0,
        "balance_ton": None,
        "balance_usdt": None,
        "wallet_version": None,
        "star_lots": [],
        "templates": _default_templates(),
        "fragment_proxy_type": None,
        "fragment_proxy_host": None,
        "fragment_proxy_port": 0,
        "fragment_proxy_username": None,
        "fragment_proxy_password": None,
        "admin_chat_id": None,
        "stats": {"sent_orders": 0, "sent_stars": 0, "failed_orders": 0},
    }


def _load_json(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if data is not None else default
    except Exception:
        return default


def _sanitize_cfg(raw: Any) -> dict[str, Any]:
    cfg = _default_cfg()
    if not isinstance(raw, dict):
        return cfg
    cfg.update({k: v for k, v in raw.items() if k in cfg or k in {"last_wallet_raw", "last_error"}})
    for alias in ("jwt", "token", "fragment_token", "fragmentApiToken"):
        if not cfg.get("fragment_jwt") and raw.get(alias):
            cfg["fragment_jwt"] = str(raw.get(alias)).strip()
    jwt = _jwt_from_text(cfg.get("fragment_jwt"))
    cfg["fragment_jwt"] = jwt
    cfg["plugin_enabled"] = bool(cfg.get("plugin_enabled", True))
    cfg["auto_send_without_plus"] = bool(cfg.get("auto_send_without_plus", False))
    cfg["skip_username_check"] = bool(cfg.get("skip_username_check", False))
    cfg["auto_refund"] = bool(cfg.get("auto_refund", False))
    cfg["anonymous_stars_send"] = bool(cfg.get("anonymous_stars_send", True))
    cfg["retry_liteserver"] = bool(cfg.get("retry_liteserver", True))
    cur = str(cfg.get("stars_currency") or CURRENCY_TON).strip().lower()
    cfg["stars_currency"] = CURRENCY_USDT if cur in {"usdt", "usdt_ton", "usdt-ton"} else CURRENCY_TON
    cfg["usdt_fallback_to_ton"] = bool(cfg.get("usdt_fallback_to_ton", False))
    try:
        cfg["min_balance_ton"] = max(0.0, float(cfg.get("min_balance_ton") or 5.0))
    except Exception:
        cfg["min_balance_ton"] = 5.0
    lots = []
    for item in cfg.get("star_lots") or []:
        if not isinstance(item, dict):
            continue
        lot_id = str(item.get("lot_id") or item.get("id") or "").strip()
        try:
            qty = int(item.get("qty") or 0)
        except Exception:
            qty = 0
        if lot_id and qty >= MIN_STARS:
            lots.append({"lot_id": lot_id, "qty": qty, "active": bool(item.get("active", True))})
    cfg["star_lots"] = lots
    tpls = dict(_default_templates())
    incoming = cfg.get("templates") if isinstance(cfg.get("templates"), dict) else {}
    for key, value in incoming.items():
        if key in tpls and isinstance(value, str) and value.strip():
            tpls[key] = value
    cfg["templates"] = tpls
    stats = cfg.get("stats") if isinstance(cfg.get("stats"), dict) else {}
    cfg["stats"] = {
        "sent_orders": int(stats.get("sent_orders") or 0),
        "sent_stars": int(stats.get("sent_stars") or 0),
        "failed_orders": int(stats.get("failed_orders") or 0),
    }
    ptype = str(cfg.get("fragment_proxy_type") or "").strip().lower()
    cfg["fragment_proxy_type"] = ptype if ptype in {"http", "socks4", "socks5"} else None
    try:
        cfg["fragment_proxy_port"] = int(cfg.get("fragment_proxy_port") or 0)
    except Exception:
        cfg["fragment_proxy_port"] = 0
    return cfg


def load_cfg() -> dict[str, Any]:
    with _IO_LOCK:
        return _sanitize_cfg(_load_json(_settings_path(), {}))


def save_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    cleaned = _sanitize_cfg(cfg)
    with _IO_LOCK:
        _atomic_write_json(_settings_path(), cleaned)
    return cleaned


def update_cfg(**updates: Any) -> dict[str, Any]:
    with _IO_LOCK:
        cfg = _sanitize_cfg(_load_json(_settings_path(), {}))
        cfg.update(updates)
        cleaned = _sanitize_cfg(cfg)
        _atomic_write_json(_settings_path(), cleaned)
        return cleaned


def _default_orders() -> dict[str, Any]:
    return {"queues": {}, "done": [], "blocked": []}


def load_orders() -> dict[str, Any]:
    with _IO_LOCK:
        data = _load_json(_orders_path(), _default_orders())
    if not isinstance(data, dict):
        return _default_orders()
    data.setdefault("queues", {})
    data.setdefault("done", [])
    data.setdefault("blocked", [])
    if not isinstance(data["queues"], dict):
        data["queues"] = {}
    return data


def save_orders(data: dict[str, Any]) -> None:
    with _IO_LOCK:
        _atomic_write_json(_orders_path(), data)


def _h(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _strip_invisible(text: str) -> str:
    return _INVISIBLE_RE.sub("", text or "")


def _tpl(key: str, **kwargs: Any) -> str:
    tpls = load_cfg().get("templates") or _default_templates()
    raw = str(tpls.get(key) or _default_templates().get(key) or "")
    try:
        return raw.format(**kwargs)
    except Exception:
        return raw


def _validate_username(value: str | None) -> bool:
    if not value:
        return False
    return bool(_USERNAME_RE.fullmatch(_strip_invisible(value).strip().lstrip("@")))


def _extract_username(text: str | None) -> str | None:
    if not text:
        return None
    source = _strip_invisible(str(text))
    patterns = [
        r"(?i)(?:по|by)\s*username\s*[,:\-]?\s*@?([A-Za-z0-9_]{5,32})",
        r"(?i)\b(?:ник|username)\s*[:=]\s*@?([A-Za-z0-9_]{5,32})",
        r"(?i)(?:https?://)?t\.me/(?:@)?([A-Za-z0-9_]{5,32})",
        r"@([A-Za-z0-9_]{5,32})",
        r"(?i)\b(?:tg|тг|telegram|телеграм)\b\s*[,:\-=]?\s*@?([A-Za-z0-9_]{5,32})",
        r"(?i)\b(?:для|to)\b\s*@?([A-Za-z0-9_]{5,32})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            cand = match.group(1)
            if _validate_username(cand) and re.search(r"[A-Za-z]", cand):
                return cand
    match = re.fullmatch(r"\s*@?([A-Za-z0-9_]{5,32})\s*[.!?,;:]*\s*", source)
    if match:
        cand = match.group(1)
        if re.search(r"[A-Za-z]", cand):
            return cand
    return None


def _extract_qty_from_title(title: str | None) -> int | None:
    if not title:
        return None
    source = str(title)
    match = re.search(r"(\d{1,7})\s*(?:зв[её]зд\w*|stars?)\b", source, re.I)
    if not match:
        match = re.search(r"(\d{1,7})\s*(?:⭐️|⭐)", source)
    if not match:
        match = re.search(r"(?:⭐️|⭐)\s*(\d{1,7})", source)
    if match:
        qty = int(match.group(1))
        return qty if qty >= MIN_STARS else None
    if re.search(r"(?:зв[её]зд|stars?|⭐️|⭐)", source, re.I):
        nums = [int(x) for x in re.findall(r"\d{1,7}", source)]
        qty = max(nums) if nums else None
        return qty if qty and qty >= MIN_STARS else None
    return None


def _looks_like_stars(text: str | None) -> bool:
    if not text:
        return False
    return bool(re.search(r"(?:зв[её]зд|stars?|⭐️|⭐|telegram\s*stars)", str(text), re.I))


def _gift_like(text: str | None) -> bool:
    t = (text or "").lower()
    return any(word in t for word in ("подарок", "подарком", "подарки", "gift", "в подарок"))


def _order_text_blob(order: Any) -> str:
    raw = getattr(order, "raw", None)
    if not isinstance(raw, dict):
        raw = order if isinstance(order, dict) else {}
    offer = raw.get("offerDetails") or raw.get("offer") or {}
    if not isinstance(offer, dict):
        offer = {}
    offer_obj = offer.get("offer") or {}
    rus = (offer.get("descriptions") or {}).get("rus") or {}
    parts = [
        getattr(order, "offer_name", None),
        rus.get("briefDescription"),
        rus.get("description"),
        offer_obj.get("name") if isinstance(offer_obj, dict) else None,
        offer.get("name"),
        offer.get("title"),
        raw.get("comment"),
        raw.get("buyerComment"),
        raw.get("message"),
    ]
    return " ".join(str(x) for x in parts if x)


def _order_offer_id(order: Any) -> str | None:
    oid = str(getattr(order, "offer_id", "") or "").strip()
    if oid:
        return oid
    raw = getattr(order, "raw", None)
    if not isinstance(raw, dict):
        raw = order if isinstance(order, dict) else {}
    offer = raw.get("offerDetails") or raw.get("offer") or {}
    if not isinstance(offer, dict):
        offer = {}
    offer_obj = offer.get("offer") if isinstance(offer.get("offer"), dict) else {}
    for source in (offer_obj, offer, raw):
        if not isinstance(source, dict):
            continue
        for key in ("publicId", "public_id", "id", "offerId", "lot_id"):
            value = source.get(key)
            if value:
                return str(value)
    return None


def _order_qty(order: Any, cfg: dict[str, Any]) -> int | None:
    offer_id = _order_offer_id(order)
    quantity = max(1, int(getattr(order, "quantity", 1) or 1))
    for item in cfg.get("star_lots") or []:
        if str(item.get("lot_id")) == str(offer_id) and item.get("active", True):
            return int(item["qty"]) * quantity
    from_title = _extract_qty_from_title(_order_text_blob(order))
    if from_title:
        return from_title * quantity if quantity > 1 else from_title
    active = [int(x["qty"]) for x in (cfg.get("star_lots") or []) if x.get("active", True)]
    if len(active) == 1:
        return active[0] * quantity
    return None


def _order_is_stars(order: Any, cfg: dict[str, Any]) -> bool:
    offer_id = _order_offer_id(order)
    mapped = [str(x.get("lot_id")) for x in (cfg.get("star_lots") or []) if x.get("active", True)]
    if mapped:
        return str(offer_id) in mapped
    blob = _order_text_blob(order)
    return _looks_like_stars(blob) and not _gift_like(blob)


def _queue_for(chat_id: str) -> list[dict[str, Any]]:
    data = load_orders()
    items = data["queues"].get(str(chat_id)) or []
    return [x for x in items if isinstance(x, dict)]


def _set_queue(chat_id: str, items: list[dict[str, Any]]) -> None:
    data = load_orders()
    key = str(chat_id)
    if items:
        data["queues"][key] = items
    else:
        data["queues"].pop(key, None)
    save_orders(data)


def _find_item(oid: str | None = None, chat_id: str | None = None) -> dict[str, Any] | None:
    data = load_orders()
    for cid, items in data.get("queues", {}).items():
        for item in items or []:
            if oid and str(item.get("order_id")) == str(oid):
                return item
            if chat_id and not oid and str(cid) == str(chat_id) and not item.get("finalized"):
                return item
    return None


def _upsert_item(item: dict[str, Any]) -> dict[str, Any]:
    chat_id = str(item.get("chat_id") or "")
    items = _queue_for(chat_id)
    found = False
    for idx, current in enumerate(items):
        if str(current.get("order_id")) == str(item.get("order_id")):
            current.update(item)
            items[idx] = current
            item = current
            found = True
            break
    if not found:
        items.append(item)
    _set_queue(chat_id, items)
    return item


def _pop_item(chat_id: str, oid: str | None = None) -> dict[str, Any] | None:
    items = _queue_for(chat_id)
    if not items:
        return None
    removed = None
    if oid:
        keep = []
        for item in items:
            if removed is None and str(item.get("order_id")) == str(oid):
                removed = item
            else:
                keep.append(item)
        items = keep
    else:
        removed = items.pop(0)
    _set_queue(chat_id, items)
    return removed


def _current(chat_id: str) -> dict[str, Any] | None:
    items = [x for x in _queue_for(chat_id) if not x.get("finalized")]
    return items[0] if items else None


def _mark_done(oid: str, ok: bool) -> None:
    data = load_orders()
    bucket = "done" if ok else "blocked"
    values = [str(x) for x in data.get(bucket) or []]
    if str(oid) not in values:
        values.append(str(oid))
        data[bucket] = values[-400:]
    save_orders(data)


def _is_done(oid: str) -> bool:
    data = load_orders()
    oid = str(oid)
    return oid in {str(x) for x in data.get("done") or []} or oid in {str(x) for x in data.get("blocked") or []}


def _lock_for(chat_id: str) -> threading.Lock:
    key = str(chat_id)
    lock = _SEND_LOCKS.get(key)
    if lock is None:
        lock = threading.Lock()
        _SEND_LOCKS[key] = lock
    return lock


def _send_buyer(chat_id: str | None, text: str) -> bool:
    if not chat_id or not text or chat_id == "__unbound__" or _APP is None:
        return False
    try:
        _APP.send_message(str(chat_id), text)
        return True
    except Exception as exc:
        logger.warning("send_message failed chat_id=%s error=%s", chat_id, exc)
        return False


def _notify_admin(text: str) -> None:
    cfg = load_cfg()
    chat_id = cfg.get("admin_chat_id")
    if _APP is None or not getattr(_APP, "telegram", None):
        return
    try:
        if chat_id:
            _APP.telegram.bot.send_message(int(chat_id), text, parse_mode="HTML")
            return
    except Exception:
        pass
    try:
        _APP.telegram.send_notification(text)
    except Exception as exc:
        logger.debug("admin notify failed: %s", exc)


def _buyer_id(order: Any) -> str | None:
    buyer = getattr(order, "buyer", None)
    if buyer and getattr(buyer, "id", None):
        return str(buyer.id)
    raw = getattr(order, "raw", None) if not isinstance(order, dict) else order
    if not isinstance(raw, dict):
        return None
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    for value in (raw.get("buyerId"), raw.get("buyer_id"), user.get("id")):
        if value:
            return str(value).strip()
    return None


def _buyer_name(order: Any) -> str | None:
    buyer = getattr(order, "buyer", None)
    if buyer and getattr(buyer, "username", None):
        return str(buyer.username).strip().lstrip("@").lower()
    raw = getattr(order, "raw", None) if not isinstance(order, dict) else order
    if not isinstance(raw, dict):
        return None
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    text = str(user.get("username") or "").strip().lstrip("@").lower()
    return text or None


def _remember_chat(buyer_id: str | None, buyer_name: str | None, chat_id: str) -> None:
    if buyer_id:
        _CHAT_BY_BUYER[buyer_id] = chat_id
    if buyer_name:
        _CHAT_BY_BUYER[f"user:{buyer_name}"] = chat_id


def _match_chats(chats: list, buyer_id: str | None, buyer_name: str | None, my_id: str) -> str | None:
    for chat in chats or []:
        other = chat.other_user(my_id) if hasattr(chat, "other_user") else None
        if not other:
            continue
        if buyer_id and str(other.id) == str(buyer_id):
            return str(chat.id)
        if buyer_name and str(other.username or "").strip().lstrip("@").lower() == buyer_name:
            return str(chat.id)
    return None


def _chat_id_for_order(order: Any) -> str | None:
    buyer_id = _buyer_id(order)
    buyer_name = _buyer_name(order)
    if buyer_id and buyer_id in _CHAT_BY_BUYER:
        return _CHAT_BY_BUYER[buyer_id]
    if buyer_name and f"user:{buyer_name}" in _CHAT_BY_BUYER:
        return _CHAT_BY_BUYER[f"user:{buyer_name}"]
    if _APP is None or not _APP.account:
        return None
    my_id = _APP.account.user.id if _APP.account.user else ""
    chats = list(getattr(getattr(_APP, "runner", None), "last_chats", None) or [])
    cid = _match_chats(chats, buyer_id, buyer_name, my_id)
    if cid:
        _remember_chat(buyer_id, buyer_name, cid)
        return cid
    try:
        fetched = _APP.account.get_chats()
    except Exception as exc:
        logger.warning("chat lookup failed: %s", exc)
        return None
    cid = _match_chats(fetched, buyer_id, buyer_name, my_id)
    if cid:
        _remember_chat(buyer_id, buyer_name, cid)
    return cid


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    proxy_url: str | None = None,
    timeout: tuple[float, float] = (15, 30),
) -> tuple[int, Any, str]:
    req_headers = dict(headers or {})
    req_headers.setdefault("Accept", "application/json")
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(timeout[0] + timeout[1], connect=timeout[0], read=timeout[1]),
        "follow_redirects": True,
        "headers": req_headers,
    }
    client = None
    try:
        if proxy_url:
            try:
                client = httpx.Client(proxy=proxy_url, **kwargs)
            except TypeError:
                client = httpx.Client(proxies=proxy_url, **kwargs)
        else:
            client = httpx.Client(**kwargs)
        response = client.request(method.upper(), url, json=payload)
        raw = response.text or ""
        try:
            parsed = response.json()
        except Exception:
            parsed = None
        return int(response.status_code), parsed, raw
    finally:
        if client is not None:
            client.close()


def _proxy_url(cfg: dict[str, Any] | None = None) -> str | None:
    cfg = cfg or load_cfg()
    ptype = cfg.get("fragment_proxy_type")
    host = str(cfg.get("fragment_proxy_host") or "").strip()
    port = int(cfg.get("fragment_proxy_port") or 0)
    if not ptype or not host or not port:
        return None
    scheme = {"http": "http", "socks4": "socks4", "socks5": "socks5"}.get(str(ptype), "http")
    user = cfg.get("fragment_proxy_username")
    password = cfg.get("fragment_proxy_password")
    auth = ""
    if user:
        auth = quote(str(user), safe="")
        if password is not None:
            auth += ":" + quote(str(password), safe="")
        auth += "@"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{auth}{host}:{port}"


def _clean_jwt_text(value: Any) -> str:
    text = _INVISIBLE_RE.sub("", str(value or ""))
    text = text.strip().strip('"').strip("'").strip("`")
    text = re.sub(r"^(?:JWT|Bearer)\s+", "", text, flags=re.I)
    return re.sub(r"\s+", "", text)


def _jwt_from_text(value: Any) -> str | None:
    match = _JWT_RE.search(_clean_jwt_text(value))
    return match.group(0) if match else None


def _is_jwt_like(value: Any) -> bool:
    return bool(_JWT_RE.fullmatch(_clean_jwt_text(value)))


def _find_jwt_in_json(obj: Any) -> str | None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in _JWT_JSON_KEYS and isinstance(value, str):
                token = _jwt_from_text(value)
                if token:
                    return token
        for value in obj.values():
            token = _find_jwt_in_json(value)
            if token:
                return token
    elif isinstance(obj, list):
        for value in obj:
            token = _find_jwt_in_json(value)
            if token:
                return token
    return None


def _normalize_bridge_code(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().upper()


def _bridge_code_from_text(value: Any) -> str | None:
    match = _FTS_BRIDGE_CODE_RE.search(_normalize_bridge_code(value))
    return match.group(0).upper() if match else None


def _fragment_headers(jwt: str | None, scheme: str = "JWT") -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": f"{NAME}/{VERSION}",
        "Connection": "close",
    }
    token = _jwt_from_text(jwt) or _clean_jwt_text(jwt)
    if token:
        headers["Authorization"] = f"{scheme} {token}"
    return headers


def _as_balance_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value.strip().replace(",", "."))
            return float(match.group(0)) if match else None
    except Exception:
        return None
    return None


def _balance_from_node(node: Any) -> float | None:
    if isinstance(node, dict):
        for key in ("balance", "amount", "value", "available", "free"):
            val = _as_balance_float(node.get(key))
            if val is not None:
                return val
    return _as_balance_float(node)


def _extract_wallet_info(data: Any) -> tuple[str | None, float | None, float | None]:
    if not isinstance(data, dict):
        return None, None, None
    ver = None
    for key in ("wallet_version", "walletVersion", "version"):
        if data.get(key) is not None:
            ver = str(data.get(key))
            break
    ton = None
    usdt = None
    for key in ("balance_ton", "balanceTon", "ton_balance", "tonBalance", "balance"):
        ton = _balance_from_node(data.get(key))
        if ton is not None:
            break
    for key in ("balance_usdt", "balanceUsdt", "usdt_balance", "usdtBalance", "usdt_ton_balance"):
        usdt = _balance_from_node(data.get(key))
        if usdt is not None:
            break
    return ver, ton, usdt


def _fragment_error_detail(data: Any, raw: str = "") -> str:
    if isinstance(data, dict):
        for key in ("detail", "message", "error", "msg"):
            value = data.get(key)
            if isinstance(value, list) and value:
                value = value[0]
            if value:
                return str(value)[:300]
    text = str(raw or "").strip()
    return text[:300] if text else ""


def _auth_missing(status: int, data: Any, raw: str) -> bool:
    blob = f"{raw or ''} {json.dumps(data, ensure_ascii=False) if data is not None else ''}".lower()
    return status in {401, 403} or "credentials were not provided" in blob or "authentication credentials" in blob


def _wallet_info(jwt: str | None) -> tuple[str | None, float | None, float | None, dict[str, Any]]:
    last: dict[str, Any] = {}
    proxy = _proxy_url()
    token = _jwt_from_text(jwt) or _clean_jwt_text(jwt)
    if not token:
        return None, None, None, {"status": 401, "auth_error": True, "raw": "Fragment JWT не задан"}
    saw_auth = False
    for url in FRAGMENT_WALLET_URLS:
        for scheme in ("JWT", "Bearer"):
            try:
                status, data, raw = _http_json(
                    "GET",
                    url,
                    headers=_fragment_headers(token, scheme),
                    proxy_url=proxy,
                    timeout=(10, 20),
                )
                last = {"status": status, "data": data, "raw": (raw or "")[:400], "url": url, "scheme": scheme}
                if _auth_missing(status, data, raw):
                    last["auth_error"] = True
                    saw_auth = True
                    continue
                if status >= 400 or not isinstance(data, dict):
                    continue
                ver, ton, usdt = _extract_wallet_info(data)
                last["auth_error"] = False
                return ver, ton, usdt, last
            except Exception as exc:
                last = {"error": str(exc), "url": url, "scheme": scheme}
    if saw_auth:
        last["auth_error"] = True
    return None, None, None, last


def _username_exists(username: str, jwt: str | None) -> tuple[str, str]:
    uname = username.lstrip("@")
    urls = [f"{base.rstrip('/')}/{uname}/" for base in FRAGMENT_USER_URLS]
    urls.append(f"{FRAGMENT_BASE}/misc/user/{uname}/")
    proxy = _proxy_url()
    seen: list[tuple[str, str]] = []
    for url in urls:
        try:
            status, data, raw = _http_json("GET", url, headers=_fragment_headers(jwt), proxy_url=proxy, timeout=(8, 8))
            blob = (raw or "") + " " + (json.dumps(data, ensure_ascii=False) if data is not None else "")
            low = blob.lower()
            if status == 200:
                return "exists", f"HTTP {status}"
            if status in (401, 403) or "invalid jwt" in low or "token expired" in low:
                return "auth_error", f"HTTP {status}"
            if status == 404 or "user not found" in low or "username not found" in low:
                seen.append(("not_found", f"HTTP {status}"))
                continue
            if status in (408, 429, 500, 502, 503, 504):
                seen.append(("unavailable", f"HTTP {status}"))
                continue
            seen.append(("unknown", f"HTTP {status}"))
        except Exception as exc:
            seen.append(("unavailable", str(exc)))
    for wanted in ("auth_error", "unavailable", "not_found", "unknown"):
        for state, detail in seen:
            if state == wanted:
                return ("unavailable" if state == "unknown" else state), detail
    return "unavailable", "no usable response"


def _fragment_order_status(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("status", "state", "order_status"):
            if data.get(key):
                return str(data.get(key)).upper()
    return ""


def _fragment_order_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("id", "order_id", "orderId", "transaction_id", "request_id"):
        if data.get(key):
            return str(data.get(key))
    return None


def _order_stars(jwt: str, username: str, quantity: int, *, show_sender: bool, currency: str) -> dict[str, Any]:
    token = _jwt_from_text(jwt)
    if not token:
        return {"ok": False, "status": 401, "text": "Fragment JWT не задан", "json": None, "currency": currency}
    u = username.lstrip("@").strip()
    payload: dict[str, Any] = {"username": u, "quantity": int(quantity), "show_sender": bool(show_sender)}
    if currency == CURRENCY_USDT:
        payload["currency"] = CURRENCY_USDT
    logger.info("SEND start: %s⭐ → @%s currency=%s", quantity, u, currency)
    last: dict[str, Any] | None = None
    try:
        for scheme in ("JWT", "Bearer"):
            status, data, raw = _http_json(
                "POST",
                FRAGMENT_ORDER_STARS,
                headers=_fragment_headers(token, scheme),
                payload=payload,
                proxy_url=_proxy_url(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            flags = set()
            st = _fragment_order_status(data).lower()
            if isinstance(data, dict):
                for key in ("ok", "success", "sent", "purchased", "done"):
                    if data.get(key) is True:
                        flags.add(key)
                if st in {"ok", "success", "completed", "complete", "done", "pending", "blockchain_sent"}:
                    flags.add("status")
                if _fragment_order_id(data):
                    flags.add("tx")
            ok = bool(flags) and data is not None
            last = {
                "ok": ok,
                "status": status,
                "text": raw,
                "json": data,
                "currency": currency,
                "order_status": st.upper() if st else None,
                "fragment_order_id": _fragment_order_id(data),
            }
            if ok or not _auth_missing(status, data, raw):
                return last
        return last or {"ok": False, "status": 401, "text": "Authentication credentials were not provided.", "json": None, "currency": currency}
    except Exception as exc:
        logger.exception("SEND exception")
        return {"ok": False, "status": 0, "text": str(exc), "json": None, "currency": currency, "uncertain": True}


def _is_liteserver(resp: dict[str, Any]) -> bool:
    blob = f"{resp.get('text') or ''} {json.dumps(resp.get('json'), ensure_ascii=False) if resp.get('json') is not None else ''}".lower()
    if "liteserver" not in blob and "lite server" not in blob:
        return False
    if any(word in blob for word in ("not enough", "insufficient", "balance", "user not found", "invalid", "429")):
        return False
    return True


def _is_balance_fail(resp: dict[str, Any]) -> bool:
    blob = f"{resp.get('text') or ''} {json.dumps(resp.get('json'), ensure_ascii=False) if resp.get('json') is not None else ''}".lower()
    return any(word in blob for word in ("not enough", "insufficient", "balance", "не хватает", "недостат"))


def _send_stars(jwt: str, username: str, quantity: int, cfg: dict[str, Any]) -> dict[str, Any]:
    currency = cfg.get("stars_currency") or CURRENCY_TON
    show_sender = not bool(cfg.get("anonymous_stars_send", True))
    resp = _order_stars(jwt, username, quantity, show_sender=show_sender, currency=currency)
    if not resp.get("ok") and cfg.get("retry_liteserver") and (resp.get("safe_to_retry") or _is_liteserver(resp)):
        time.sleep(random.uniform(0.8, 1.8))
        resp = _order_stars(jwt, username, quantity, show_sender=show_sender, currency=currency)
        resp["_retried"] = True
    if currency == CURRENCY_USDT and not resp.get("ok") and cfg.get("usdt_fallback_to_ton") and _is_balance_fail(resp):
        update_cfg(stars_currency=CURRENCY_TON)
        resp = _order_stars(jwt, username, quantity, show_sender=show_sender, currency=CURRENCY_TON)
        resp["_currency_fallback"] = "usdt_ton->ton"
    return resp


def _refund(order_id: str) -> tuple[bool, str]:
    if _APP is None:
        return False, "нет приложения"
    try:
        _APP.refund_order(str(order_id))
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _process_new_order(order: Any) -> None:
    cfg = load_cfg()
    if not cfg.get("plugin_enabled", True):
        return
    oid = str(getattr(order, "id", "") or "")
    if not oid or oid in _SEEN_ORDERS or _is_done(oid):
        return
    _SEEN_ORDERS.add(oid)
    if not _order_is_stars(order, cfg):
        logger.info("ignore non-stars order #%s", oid)
        return
    blob = _order_text_blob(order)
    if _gift_like(blob):
        logger.info("ignore gift-like order #%s", oid)
        return
    qty = _order_qty(order, cfg)
    chat_id = _chat_id_for_order(order)
    if not chat_id:
        for _ in range(3):
            time.sleep(2)
            chat_id = _chat_id_for_order(order)
            if chat_id:
                break
    if not chat_id:
        logger.warning("no chat yet for order #%s", oid)
        item = {
            "order_id": oid,
            "chat_id": "__unbound__",
            "qty": int(qty or 0) or None,
            "qty_pending": qty is None,
            "candidate": _extract_username(blob),
            "stage": "await_username",
            "finalized": False,
            "confirmed": False,
            "created_ts": int(time.time()),
            "offer_id": _order_offer_id(order),
            "buyer": _buyer_name(order),
            "buyer_id": _buyer_id(order),
        }
        if item["candidate"]:
            item["candidate"] = str(item["candidate"]).lstrip("@")
        _upsert_item(item)
        _notify_admin(
            f"⚠️ Автозвёзды: заказ <code>{_h(oid)}</code> принят, но чат Starvell ещё не найден. "
            "Как только покупатель напишет — диалог привяжется сам."
        )
        return
    logger.info("new stars order #%s qty=%s chat=%s", oid, qty if qty is not None else "pending", chat_id)
    if qty is not None and qty < MIN_STARS:
        _send_buyer(chat_id, _tpl("too_small", qty=qty, min=MIN_STARS))
        if cfg.get("auto_refund"):
            _send_buyer(chat_id, _tpl("refund", oid=oid))
            ok, reason = _refund(oid)
            _send_buyer(chat_id, _tpl("refund_ok" if ok else "refund_fail", oid=oid, reason=reason))
        _mark_done(oid, False)
        return
    hint = _extract_username(blob)
    item = {
        "order_id": oid,
        "chat_id": str(chat_id),
        "qty": int(qty or 0) or None,
        "qty_pending": qty is None,
        "candidate": hint.lstrip("@") if hint else None,
        "stage": "await_confirm" if hint else "await_username",
        "finalized": False,
        "confirmed": False,
        "created_ts": int(time.time()),
        "offer_id": _order_offer_id(order),
        "buyer": _buyer_name(order),
        "buyer_id": _buyer_id(order),
    }
    queue = _queue_for(str(chat_id))
    if any(str(x.get("order_id")) == oid for x in queue):
        return
    _upsert_item(item)
    pos = len(_queue_for(str(chat_id)))
    if pos > 1:
        _send_buyer(chat_id, _tpl("queued", pos=pos))
        return
    _prompt_current(str(chat_id))


def _prompt_current(chat_id: str) -> None:
    item = _current(chat_id)
    if not item:
        return
    qty = int(item.get("qty") or 0) or MIN_STARS
    uname = str(item.get("candidate") or "").lstrip("@")
    if uname and _validate_username(uname):
        item["stage"] = "await_confirm"
        _upsert_item(item)
        _send_buyer(chat_id, _tpl("username_valid", qty=qty, username=uname))
        if load_cfg().get("auto_send_without_plus"):
            _confirm_send(chat_id, str(item.get("order_id")))
            return
        _send_buyer(chat_id, _tpl("confirm", qty=qty, username=uname))
        return
    item["stage"] = "await_username"
    _upsert_item(item)
    _send_buyer(chat_id, _tpl("purchase_created", qty=qty))


def _bind_unbound_to_chat(chat_id: str) -> dict[str, Any] | None:
    unbound = [x for x in _queue_for("__unbound__") if not x.get("finalized")]
    if not unbound:
        return None
    if len(unbound) > 1:
        logger.warning("several unbound FTS orders (%s)", len(unbound))
        return None
    item = dict(unbound[0])
    _pop_item("__unbound__", str(item.get("order_id")))
    item["chat_id"] = str(chat_id)
    _upsert_item(item)
    _remember_chat(item.get("buyer_id"), item.get("buyer"), str(chat_id))
    logger.info("bound order #%s to chat %s", item.get("order_id"), chat_id)
    return item


def _process_chat_message(text: str, chat_id: str) -> None:
    cfg = load_cfg()
    if not cfg.get("plugin_enabled", True):
        return
    chat_id = str(chat_id)
    clean = _strip_invisible(text or "").strip()
    key = f"{chat_id}:{clean[:80]}"
    if key in _SEEN_MESSAGES:
        return
    _SEEN_MESSAGES.add(key)
    if len(_SEEN_MESSAGES) > 4000:
        _SEEN_MESSAGES.clear()
    item = _current(chat_id)
    if not item:
        item = _bind_unbound_to_chat(chat_id)
        if item:
            if not _PLUS_RE.fullmatch(clean) and not _extract_username(clean):
                _prompt_current(chat_id)
                return
        else:
            return
    if str(chat_id) in _ACTIVE_SENDS:
        return
    if _PLUS_RE.fullmatch(clean):
        candidate = str(item.get("candidate") or "").lstrip("@")
        if item.get("stage") != "await_confirm" or not _validate_username(candidate):
            _send_buyer(chat_id, "Сначала пришлите Telegram-тег в формате @username.")
            return
        _confirm_send(chat_id, str(item.get("order_id")))
        return
    username = _extract_username(clean)
    if not username or not _validate_username(username):
        _send_buyer(chat_id, _tpl("username_invalid"))
        return
    jwt = cfg.get("fragment_jwt")
    if jwt and not cfg.get("skip_username_check"):
        state, _detail = _username_exists(username, jwt)
        if state == "not_found":
            _send_buyer(chat_id, _tpl("username_invalid"))
            return
        if state == "auth_error":
            _send_buyer(chat_id, "Fragment JWT отклонён. Продавец скоро проверит настройки.")
            _notify_admin("⚠️ Автозвёзды: Fragment JWT недействителен.")
            return
    qty = int(item.get("qty") or 0) or MIN_STARS
    item.update(candidate=username, stage="await_confirm", confirmed=False, stage_ts=time.time())
    _upsert_item(item)
    _send_buyer(chat_id, _tpl("username_valid", qty=qty, username=username))
    if cfg.get("auto_send_without_plus"):
        _confirm_send(chat_id, str(item.get("order_id")))
        return
    _send_buyer(chat_id, _tpl("confirm", qty=qty, username=username))


def _confirm_send(chat_id: str, oid: str) -> None:
    with _lock_for(chat_id):
        if str(chat_id) in _ACTIVE_SENDS:
            return
        _ACTIVE_SENDS.add(str(chat_id))
        try:
            _do_send(chat_id, oid)
        finally:
            _ACTIVE_SENDS.discard(str(chat_id))


def _do_send(chat_id: str, oid: str) -> None:
    cfg = load_cfg()
    item = _find_item(oid=oid, chat_id=chat_id) or _current(chat_id)
    if not item:
        _send_buyer(chat_id, "Нет активного заказа.")
        return
    jwt = cfg.get("fragment_jwt")
    username = str(item.get("candidate") or "").lstrip("@")
    qty = int(item.get("qty") or 0)
    oid = str(item.get("order_id") or oid)
    if not jwt:
        _send_buyer(chat_id, _tpl("failed", reason="не задан Fragment JWT"))
        _notify_admin("⚠️ Автозвёзды: нет JWT, заказ не выполнен.")
        return
    if not _validate_username(username) or qty < MIN_STARS:
        _send_buyer(chat_id, _tpl("username_invalid"))
        return
    _send_buyer(chat_id, _tpl("sending", qty=qty, username=username))
    resp = _send_stars(jwt, username, qty, cfg)
    if resp.get("ok"):
        extra = ""
        st = str(resp.get("order_status") or "")
        fid = resp.get("fragment_order_id")
        if st in {"PENDING", "BLOCKCHAIN_SENT"}:
            extra = f" Статус Fragment: {st}." + (f" ID: {fid}." if fid else "")
        _send_buyer(chat_id, _tpl("sent", qty=qty, username=username) + extra)
        _pop_item(chat_id, oid)
        _mark_done(oid, True)
        stats = cfg.get("stats") or {}
        stats["sent_orders"] = int(stats.get("sent_orders") or 0) + 1
        stats["sent_stars"] = int(stats.get("sent_stars") or 0) + qty
        update_cfg(stats=stats)
        logger.info("order #%s sent %s⭐ to @%s", oid, qty, username)
        nxt = _current(chat_id)
        if nxt:
            _send_buyer(chat_id, _tpl("your_turn", qty=int(nxt.get("qty") or MIN_STARS)))
            _prompt_current(chat_id)
        return
    reason = str(resp.get("text") or "unknown")[:180]
    _send_buyer(chat_id, _tpl("failed", reason=reason))
    stats = cfg.get("stats") or {}
    stats["failed_orders"] = int(stats.get("failed_orders") or 0) + 1
    update_cfg(stats=stats, last_error=reason)
    _notify_admin(f"❌ Автозвёзды заказ #{_h(oid)}: {_h(reason)}")
    if cfg.get("auto_refund") and not resp.get("uncertain"):
        _send_buyer(chat_id, _tpl("refund", oid=oid))
        ok, refund_reason = _refund(oid)
        _send_buyer(chat_id, _tpl("refund_ok" if ok else "refund_fail", oid=oid, reason=refund_reason))
        if ok:
            _pop_item(chat_id, oid)
            _mark_done(oid, False)
            nxt = _current(chat_id)
            if nxt:
                _prompt_current(chat_id)


def _on(flag: bool) -> str:
    return "🟢 Вкл" if flag else "🔴 Выкл"


def _kb(rows: list[list[tuple[str, str]]]) -> K:
    markup = K()
    for row in rows:
        markup.row(*[B(text, callback_data=data) for text, data in row])
    return markup


def _home_text(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_cfg()
    jwt = "задан" if cfg.get("fragment_jwt") else "не задан"
    lots = cfg.get("star_lots") or []
    active = sum(1 for x in lots if x.get("active", True))
    stats = cfg.get("stats") or {}
    return (
        f"<b>{_h(NAME)}</b> v{_h(VERSION)}\n"
        "Продажа Telegram Stars на Starvell через Fragment.\n\n"
        f"Плагин: <b>{_on(bool(cfg.get('plugin_enabled', True)))}</b>\n"
        f"JWT: <b>{_h(jwt)}</b>\n"
        f"Баланс: <code>{_h(cfg.get('balance_ton') if cfg.get('balance_ton') is not None else '—')} TON</code> / "
        f"<code>{_h(cfg.get('balance_usdt') if cfg.get('balance_usdt') is not None else '—')} USDT</code>\n"
        f"Лоты: <b>{active}</b>/{len(lots)}\n"
        f"Валюта: <b>{'USDT' if cfg.get('stars_currency') == CURRENCY_USDT else 'TON'}</b>\n"
        f"Автоотправка без +: <b>{_on(bool(cfg.get('auto_send_without_plus')))}</b>\n"
        f"Выполнено: <b>{int(stats.get('sent_orders') or 0)}</b> заказов / "
        f"<b>{int(stats.get('sent_stars') or 0)}</b>⭐"
    )


def _home_kb(cfg: dict[str, Any] | None = None) -> K:
    return _kb(
        [
            [("⚙️ Настройки", f"{CB}:set"), ("🔑 JWT", f"{CB}:tok")],
            [("⭐ Лоты", f"{CB}:lots"), ("✉️ Сообщения", f"{CB}:msg")],
            [("📊 Статистика", f"{CB}:st"), ("🌐 Прокси", f"{CB}:px")],
            [("🔄 Обновить", f"{CB}:home")],
        ]
    )


def _settings_text(cfg: dict[str, Any]) -> str:
    return (
        "<b>Настройки автозвёзд</b>\n\n"
        f"Плагин: {_on(bool(cfg.get('plugin_enabled', True)))}\n"
        f"Автоотправка без +: {_on(bool(cfg.get('auto_send_without_plus')))}\n"
        f"Пропуск проверки ника: {_on(bool(cfg.get('skip_username_check')))}\n"
        f"Авто-возврат: {_on(bool(cfg.get('auto_refund')))}\n"
        f"Анонимная отправка: {_on(bool(cfg.get('anonymous_stars_send', True)))}\n"
        f"Retry liteserver: {_on(bool(cfg.get('retry_liteserver', True)))}\n"
        f"USDT → TON fallback: {_on(bool(cfg.get('usdt_fallback_to_ton')))}\n"
        f"Валюта: {'USDT (TON)' if cfg.get('stars_currency') == CURRENCY_USDT else 'TON'}\n"
        f"Мин. баланс TON: <code>{_h(cfg.get('min_balance_ton'))}</code>\n\n"
        "Для лотов со звёздами выключи встроенную автовыдачу кодов."
    )


def _settings_kb(cfg: dict[str, Any]) -> K:
    return _kb(
        [
            [(f"Плагин: {_on(bool(cfg.get('plugin_enabled', True)))}", f"{CB}:tg")],
            [(f"Автоотправка: {_on(bool(cfg.get('auto_send_without_plus')))}", f"{CB}:tas")],
            [(f"Проверка ника: {_on(not bool(cfg.get('skip_username_check')))}", f"{CB}:tsk")],
            [(f"Авто-возврат: {_on(bool(cfg.get('auto_refund')))}", f"{CB}:trf")],
            [(f"Анонимно: {_on(bool(cfg.get('anonymous_stars_send', True)))}", f"{CB}:tan")],
            [(f"Валюта: {'USDT' if cfg.get('stars_currency') == CURRENCY_USDT else 'TON'}", f"{CB}:tcur")],
            [("Мин. баланс TON", f"{CB}:mbal")],
            [("⬅️ Назад", f"{CB}:home")],
        ]
    )


def _token_text(cfg: dict[str, Any]) -> str:
    jwt = cfg.get("fragment_jwt") or ""
    masked = (jwt[:6] + "…" + jwt[-4:]) if len(jwt) > 12 else ("задан" if jwt else "не задан")
    bridge = _h(FTS_BRIDGE_URL + "/")
    return (
        "<b>Fragment JWT</b>\n\n"
        f"Токен: <code>{_h(masked)}</code>\n"
        f"Длина: <code>{len(jwt) if jwt else 0}</code>\n"
        f"Версия кошелька: <code>{_h(cfg.get('wallet_version') or '—')}</code>\n"
        f"TON: <code>{_h(cfg.get('balance_ton') if cfg.get('balance_ton') is not None else '—')}</code>\n"
        f"USDT: <code>{_h(cfg.get('balance_usdt') if cfg.get('balance_usdt') is not None else '—')}</code>\n\n"
        "Длинный JWT в чат лучше не слать — Telegram его обрезает.\n"
        f"Используйте короткий код с <a href=\"{bridge}\">FTS Transfer Token</a> или файл .txt/.json."
    )


def _token_kb() -> K:
    return _kb(
        [
            [("✏️ Вставить JWT", f"{CB}:sjwt"), ("🗑 Удалить", f"{CB}:djwt")],
            [("🔄 Баланс", f"{CB}:wref")],
            [("⬅️ Назад", f"{CB}:home")],
        ]
    )


def _lots_text(cfg: dict[str, Any]) -> str:
    lots = cfg.get("star_lots") or []
    if not lots:
        return (
            "<b>Лоты Stars</b>\n\n"
            "Пока нет привязок.\n"
            "Добавьте publicId объявления Starvell и количество ⭐.\n"
            "publicId есть в ссылке: <code>starvell.com/offers/&lt;id&gt;</code>"
        )
    lines = ["<b>Лоты Stars</b>", ""]
    for idx, item in enumerate(lots, 1):
        mark = "✅" if item.get("active", True) else "⏸️"
        lines.append(f"{idx}. {mark} <code>{_h(item.get('lot_id'))}</code> — {int(item.get('qty') or 0)}⭐")
    return "\n".join(lines)


def _lots_kb(cfg: dict[str, Any]) -> K:
    rows: list[list[tuple[str, str]]] = [[("➕ Добавить лот", f"{CB}:ladd")]]
    for idx, item in enumerate(cfg.get("star_lots") or []):
        mark = "✅" if item.get("active", True) else "⏸️"
        rows.append([(f"{mark} {int(item.get('qty') or 0)}⭐", f"{CB}:lt:{idx}"), ("🗑", f"{CB}:ld:{idx}")])
    rows.append([("⬅️ Назад", f"{CB}:home")])
    return _kb(rows)


def _msg_text(cfg: dict[str, Any]) -> str:
    lines = ["<b>Шаблоны сообщений покупателю</b>", ""]
    for key, value in (cfg.get("templates") or _default_templates()).items():
        short = value.replace("\n", " ")
        if len(short) > 70:
            short = short[:69] + "…"
        lines.append(f"<b>{_h(key)}</b>: {_h(short)}")
    return "\n".join(lines)


def _msg_kb(cfg: dict[str, Any]) -> K:
    rows = [[(key, f"{CB}:me:{key}")] for key in (cfg.get("templates") or _default_templates()).keys()]
    rows.append([("⬅️ Назад", f"{CB}:home")])
    return _kb(rows)


def _info_text() -> str:
    return (
        f"<b>{_h(NAME)}</b> v{_h(VERSION)}\n\n"
        "1. Привяжите лоты Starvell к количеству ⭐.\n"
        "2. Укажите Fragment JWT.\n"
        "3. Покупатель пишет @username, затем «+» (если автоотправка выключена).\n"
        "4. Плагин отправляет звёзды через Fragment.\n\n"
        "Для лотов со звёздами отключите встроенную автовыдачу кодов."
    )


def _token_prompt_text() -> str:
    bridge = _h(FTS_BRIDGE_URL + "/")
    return (
        "<b>Подключение токена Fragment</b>\n\n"
        "Telegram часто обрезает длинный JWT.\n\n"
        f"1. Откройте <a href=\"{bridge}\">FTS Transfer Token</a>.\n"
        "2. Вставьте туда JWT из кабинета fragment-api.com.\n"
        "3. Скопируйте короткий код вида <code>FTS-XXXX-XXXX-XXXX-XXXX</code>.\n"
        "4. Пришлите этот код сюда одним сообщением.\n\n"
        "Либо сохраните полный JWT в файл <code>.txt</code> или <code>.json</code> и пришлите файл.\n"
        "Отмена: /cancel"
    )


def _edit(call: CallbackQuery, text: str, markup: K | None = None) -> None:
    bot = _APP.telegram.bot if _APP and _APP.telegram else None
    if not bot:
        return
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.id, reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        try:
            bot.send_message(call.message.chat.id, text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def _redeem_bridge_code(code: str) -> tuple[bool, str, dict[str, Any] | None]:
    normalized = _bridge_code_from_text(code)
    if not normalized:
        return False, "format", None
    if not FTS_BRIDGE_PLUGIN_SECRET:
        return False, "not_configured", None
    try:
        status, data, raw = _http_json(
            "POST",
            FTS_BRIDGE_REDEEM_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {FTS_BRIDGE_PLUGIN_SECRET}",
                "User-Agent": f"{NAME}/{VERSION}",
            },
            payload={"code": normalized},
            timeout=(10, 25),
        )
    except Exception as exc:
        return False, "unavailable", {"error": str(exc)}
    payload = data if isinstance(data, dict) else {}
    if status in (401, 403):
        return False, "unauthorized", payload
    if status in (400, 404):
        return False, "invalid", payload
    if status >= 400:
        return False, "unavailable", payload or {"raw": (raw or "")[:200]}
    token = _jwt_from_text((payload or {}).get("token"))
    if not token:
        return False, "bad_response", payload
    result = dict(payload)
    result["token"] = token
    return True, "ok", result


def _extract_jwt_candidate(text: str) -> tuple[str | None, str | None]:
    bridge = _bridge_code_from_text(text)
    if bridge:
        return None, bridge
    stripped = str(text or "").strip()
    if stripped[:1] in "{[":
        try:
            found = _find_jwt_in_json(json.loads(stripped))
            if found:
                return found, None
        except Exception:
            pass
    token = _jwt_from_text(text)
    return (token, None) if token else (None, None)


def _save_jwt_from_text(message: Message, token: str, *, acc: str = "", from_file: bool = False) -> str:
    bot = _APP.telegram.bot if _APP and _APP.telegram else None
    if not bot:
        return "fail"
    jwt_val, bridge_code = _extract_jwt_candidate(str(token or ""))
    if not jwt_val and not bridge_code:
        cleaned = _clean_jwt_text((acc or "") + str(token or ""))
        if not from_file and cleaned.lower().startswith("eyj") and cleaned.count(".") < 2:
            return f"acc:{cleaned}"
        jwt_val = _jwt_from_text(cleaned)
        if not jwt_val:
            bot.send_message(message.chat.id, "Не удалось распознать JWT или короткий код.\n\n" + _token_prompt_text(), disable_web_page_preview=True)
            return "fail"
    if bridge_code:
        bot.send_message(message.chat.id, "Обмениваю короткий код на JWT…")
        ok, reason, payload = _redeem_bridge_code(bridge_code)
        if not ok:
            if reason == "invalid":
                msg = f"❌ Код не найден. Создайте новый на <a href=\"{_h(FTS_BRIDGE_URL + '/')}\">FTS Transfer Token</a>."
            elif reason == "unauthorized" or reason == "not_configured":
                msg = "⚠️ Короткий код недоступен. Пришлите JWT файлом .txt/.json."
            else:
                msg = "⚠️ Не удалось получить JWT по коду. Пришлите JWT файлом .txt/.json."
            bot.send_message(message.chat.id, msg, disable_web_page_preview=True)
            return "fail"
        jwt_val = str((payload or {}).get("token") or "")
    if not _is_jwt_like(jwt_val):
        bot.send_message(message.chat.id, "Похоже, это не JWT. Пришлите короткий код или файл с токеном.")
        return "fail"
    ver, ton, usdt, raw = _wallet_info(jwt_val)
    auth_fail = bool((raw or {}).get("auth_error")) or _auth_missing(
        int((raw or {}).get("status") or 0),
        (raw or {}).get("data"),
        str((raw or {}).get("raw") or ""),
    )
    if auth_fail:
        detail = _fragment_error_detail((raw or {}).get("data"), str((raw or {}).get("raw") or ""))
        bot.send_message(
            message.chat.id,
            "❌ Fragment не принял JWT.\n"
            f"API: <code>{_h(detail or 'Authentication credentials were not provided.')}</code>",
            reply_markup=_home_kb(),
        )
        return "fail"
    saved = update_cfg(
        fragment_jwt=jwt_val,
        admin_chat_id=message.chat.id,
        wallet_version=ver,
        balance_ton=ton,
        balance_usdt=usdt,
        last_wallet_raw=raw,
    )
    if not _jwt_from_text(saved.get("fragment_jwt")):
        bot.send_message(message.chat.id, "❌ Не удалось записать JWT в settings.json.")
        return "fail"
    extra = " Кошелёк прочитан." if ton is not None or ver else " Токен сохранён."
    prefix = "✅ Короткий код принят." if bridge_code else "✅ JWT проверен и сохранён."
    bot.send_message(message.chat.id, prefix + extra, reply_markup=_home_kb())
    return "ok"


def _ask_state(call: CallbackQuery, state: str, text: str, data: dict | None = None) -> None:
    tg = _APP.telegram
    msg = tg.bot.send_message(call.message.chat.id, text, disable_web_page_preview=True)
    tg.set_state(call.message.chat.id, msg.id, call.from_user.id, state, data or {})
    try:
        tg.bot.answer_callback_query(call.id)
    except Exception:
        pass


def _handle_callback(call: CallbackQuery) -> None:
    data = str(call.data or "")
    if data.startswith(PS):
        data = f"{CB}:home"
    if not data.startswith(f"{CB}:"):
        return
    update_cfg(admin_chat_id=call.message.chat.id)
    cfg = load_cfg()
    action = data.split(":", 1)[1]
    if action == "home":
        _edit(call, _home_text(cfg), _home_kb(cfg))
        return
    if action == "set":
        _edit(call, _settings_text(cfg), _settings_kb(cfg))
        return
    if action == "tok":
        _edit(call, _token_text(cfg), _token_kb())
        return
    if action == "lots":
        _edit(call, _lots_text(cfg), _lots_kb(cfg))
        return
    if action == "msg":
        _edit(call, _msg_text(cfg), _msg_kb(cfg))
        return
    if action == "st":
        stats = cfg.get("stats") or {}
        pending = sum(len(v or []) for v in load_orders().get("queues", {}).values())
        text = (
            "<b>Статистика автозвёзд</b>\n\n"
            f"Выполнено заказов: <b>{int(stats.get('sent_orders') or 0)}</b>\n"
            f"Отправлено ⭐: <b>{int(stats.get('sent_stars') or 0)}</b>\n"
            f"Ошибок: <b>{int(stats.get('failed_orders') or 0)}</b>\n"
            f"В очереди сейчас: <b>{pending}</b>"
        )
        _edit(call, text, _kb([[("⬅️ Назад", f"{CB}:home")]]))
        return
    if action == "px":
        proxy = _proxy_url(cfg) or "не задан"
        text = (
            "<b>Прокси Fragment</b>\n\n"
            f"Тип: <code>{_h(cfg.get('fragment_proxy_type') or '—')}</code>\n"
            f"Адрес: <code>{_h(cfg.get('fragment_proxy_host') or '—')}:{_h(cfg.get('fragment_proxy_port') or 0)}</code>\n"
            f"URL: <code>{_h(proxy)}</code>"
        )
        _edit(
            call,
            text,
            _kb(
                [
                    [("✏️ Host", f"{CB}:phost"), ("HTTP", f"{CB}:ptype:http")],
                    [("SOCKS5", f"{CB}:ptype:socks5"), ("🗑 Удалить", f"{CB}:pdel")],
                    [("⬅️ Назад", f"{CB}:home")],
                ]
            ),
        )
        return
    if action == "tg":
        cfg = update_cfg(plugin_enabled=not bool(cfg.get("plugin_enabled", True)))
        _edit(call, _settings_text(cfg), _settings_kb(cfg))
        return
    if action == "tas":
        cfg = update_cfg(auto_send_without_plus=not bool(cfg.get("auto_send_without_plus")))
        _edit(call, _settings_text(cfg), _settings_kb(cfg))
        return
    if action == "tsk":
        cfg = update_cfg(skip_username_check=not bool(cfg.get("skip_username_check")))
        _edit(call, _settings_text(cfg), _settings_kb(cfg))
        return
    if action == "trf":
        cfg = update_cfg(auto_refund=not bool(cfg.get("auto_refund")))
        _edit(call, _settings_text(cfg), _settings_kb(cfg))
        return
    if action == "tan":
        cfg = update_cfg(anonymous_stars_send=not bool(cfg.get("anonymous_stars_send", True)))
        _edit(call, _settings_text(cfg), _settings_kb(cfg))
        return
    if action == "tcur":
        nxt = CURRENCY_USDT if cfg.get("stars_currency") != CURRENCY_USDT else CURRENCY_TON
        cfg = update_cfg(stars_currency=nxt)
        _edit(call, _settings_text(cfg), _settings_kb(cfg))
        return
    if action == "sjwt":
        _ask_state(call, ST_JWT, _token_prompt_text(), {"acc": ""})
        return
    if action == "djwt":
        cfg = update_cfg(fragment_jwt=None)
        _edit(call, _token_text(cfg), _kb([[("⬅️ Назад", f"{CB}:tok")]]))
        return
    if action == "wref":
        try:
            _APP.telegram.bot.answer_callback_query(call.id, "Проверяю кошелёк…")
        except Exception:
            pass
        ver, ton, usdt, raw = _wallet_info(cfg.get("fragment_jwt"))
        cfg = update_cfg(wallet_version=ver, balance_ton=ton, balance_usdt=usdt, last_wallet_raw=raw)
        _edit(call, _token_text(cfg), _token_kb())
        return
    if action == "ladd":
        _ask_state(call, ST_LOT_QTY, "Сколько ⭐ в этом лоте? Например: 50")
        return
    if action.startswith("lt:"):
        idx = int(action.split(":")[1])
        lots = list(cfg.get("star_lots") or [])
        if 0 <= idx < len(lots):
            lots[idx]["active"] = not bool(lots[idx].get("active", True))
            cfg = update_cfg(star_lots=lots)
        _edit(call, _lots_text(cfg), _lots_kb(cfg))
        return
    if action.startswith("ld:"):
        idx = int(action.split(":")[1])
        lots = list(cfg.get("star_lots") or [])
        if 0 <= idx < len(lots):
            lots.pop(idx)
            cfg = update_cfg(star_lots=lots)
        _edit(call, _lots_text(cfg), _lots_kb(cfg))
        return
    if action.startswith("me:"):
        key = action.split(":", 1)[1]
        _ask_state(
            call,
            ST_TPL,
            f"Новый текст для <code>{_h(key)}</code>.\nПеременные: {{qty}} {{username}} {{oid}} {{reason}} {{pos}} {{min}}",
            {"key": key},
        )
        return
    if action == "mbal":
        _ask_state(call, ST_MBAL, "Введите минимальный баланс TON, например 5")
        return
    if action.startswith("ptype:"):
        ptype = action.split(":")[1]
        update_cfg(fragment_proxy_type=ptype)
        _edit(call, f"Тип прокси: <b>{_h(ptype)}</b>", _kb([[("⬅️ Назад", f"{CB}:px")]]))
        return
    if action == "phost":
        _ask_state(call, ST_PHOST, "Введите host прокси (или «-» чтобы очистить).")
        return
    if action == "pdel":
        update_cfg(
            fragment_proxy_type=None,
            fragment_proxy_host=None,
            fragment_proxy_port=0,
            fragment_proxy_username=None,
            fragment_proxy_password=None,
        )
        _edit(call, "Прокси удалён.", _kb([[("⬅️ Назад", f"{CB}:home")]]))
        return


def _admin_state(message: Message) -> dict[str, Any] | None:
    tg = _APP.telegram if _APP else None
    if not tg:
        return None
    return tg.get_state(message.chat.id, message.from_user.id)


def _clear_admin(message: Message) -> None:
    tg = _APP.telegram
    tg.clear_state(message.chat.id, message.from_user.id, True)


def _handle_admin_text(message: Message) -> None:
    tg = _APP.telegram
    state = _admin_state(message)
    if not state:
        return
    kind = str(state.get("state") or "")
    text = (message.text or message.caption or "").strip()
    if text.lower() in {"/cancel", "cancel", "отмена"}:
        _clear_admin(message)
        tg.bot.send_message(message.chat.id, "Отменено.")
        return
    if kind == ST_JWT:
        data = state.get("data") or {}
        result = _save_jwt_from_text(message, text, acc=str(data.get("acc") or ""))
        if str(result).startswith("acc:"):
            tg.set_state(message.chat.id, state.get("mid") or message.id, message.from_user.id, ST_JWT, {"acc": result.split(":", 1)[1]})
            tg.bot.send_message(message.chat.id, "Принял часть токена. Пришлите оставшуюся часть, короткий код или /cancel.")
            return
        _clear_admin(message)
        return
    if kind == ST_LOT_QTY:
        try:
            qty = int(text)
        except Exception:
            tg.bot.send_message(message.chat.id, "Нужно целое число, например 50.")
            return
        if qty < MIN_STARS:
            tg.bot.send_message(message.chat.id, f"Минимум {MIN_STARS}⭐.")
            return
        tg.set_state(message.chat.id, state.get("mid") or message.id, message.from_user.id, ST_LOT_ID, {"qty": qty})
        tg.bot.send_message(message.chat.id, "Теперь publicId объявления Starvell (из ссылки /offers/...).")
        return
    if kind == ST_LOT_ID:
        qty = int((state.get("data") or {}).get("qty") or 0)
        lot_id = text.strip()
        if lot_id.startswith("http"):
            lot_id = lot_id.rstrip("/").split("/")[-1]
        if not lot_id:
            tg.bot.send_message(message.chat.id, "id пустой. Пришлите ещё раз.")
            return
        cfg = load_cfg()
        lots = [x for x in (cfg.get("star_lots") or []) if str(x.get("lot_id")) != lot_id]
        lots.append({"lot_id": lot_id, "qty": qty, "active": True})
        update_cfg(star_lots=lots)
        _clear_admin(message)
        tg.bot.send_message(message.chat.id, f"✅ Лот <code>{_h(lot_id)}</code> = {qty}⭐", reply_markup=_lots_kb(load_cfg()))
        return
    if kind == ST_TPL:
        key = (state.get("data") or {}).get("key")
        cfg = load_cfg()
        tpls = dict(cfg.get("templates") or _default_templates())
        if key in tpls:
            tpls[key] = text
            update_cfg(templates=tpls)
        _clear_admin(message)
        tg.bot.send_message(message.chat.id, "✅ Шаблон обновлён.", reply_markup=_msg_kb(load_cfg()))
        return
    if kind == ST_MBAL:
        try:
            value = float(text.replace(",", "."))
        except Exception:
            tg.bot.send_message(message.chat.id, "Нужно число, например 5")
            return
        update_cfg(min_balance_ton=max(0.0, value))
        _clear_admin(message)
        tg.bot.send_message(message.chat.id, f"✅ Мин. баланс: {value} TON", reply_markup=_settings_kb(load_cfg()))
        return
    if kind == ST_PHOST:
        if text == "-":
            update_cfg(fragment_proxy_host=None, fragment_proxy_port=0, fragment_proxy_type=None)
            _clear_admin(message)
            tg.bot.send_message(message.chat.id, "Прокси очищен.")
            return
        tg.set_state(message.chat.id, state.get("mid") or message.id, message.from_user.id, ST_PPORT, {"host": text})
        tg.bot.send_message(message.chat.id, "Порт прокси?")
        return
    if kind == ST_PPORT:
        try:
            port = int(text)
        except Exception:
            tg.bot.send_message(message.chat.id, "Нужно число порта.")
            return
        host = (state.get("data") or {}).get("host")
        update_cfg(fragment_proxy_host=host, fragment_proxy_port=port)
        _clear_admin(message)
        tg.bot.send_message(message.chat.id, "✅ Прокси сохранён.", reply_markup=_home_kb())
        return


def _handle_jwt_file(message: Message) -> None:
    tg = _APP.telegram
    doc = message.document
    if not doc:
        tg.bot.send_message(message.chat.id, "Нужен файл .txt или .json")
        return
    if int(doc.file_size or 0) > 2_000_000:
        tg.bot.send_message(message.chat.id, "Файл слишком большой (>2MB).")
        return
    info = tg.bot.get_file(doc.file_id)
    raw = tg.bot.download_file(info.file_path)
    text = raw.decode("utf-8-sig", errors="ignore") if isinstance(raw, bytes) else str(raw or "")
    state = _admin_state(message) or {}
    acc = str((state.get("data") or {}).get("acc") or "")
    result = _save_jwt_from_text(message, text, acc=acc, from_file=True)
    if not str(result).startswith("acc:"):
        _clear_admin(message)


def on_init(app) -> None:
    global _APP
    _APP = app
    _plugin_dir().mkdir(parents=True, exist_ok=True)
    save_cfg(load_cfg())
    save_orders(load_orders())
    tg = app.telegram
    if not tg:
        logger.info("Автозвёзды v%s: Telegram-панель выключена, продажи в чатах Starvell всё равно работают.", VERSION)
        return
    app.add_telegram_commands(UUID, [("fnp", "панель автозвёзд", True), ("fnpjwt", "сохранить Fragment JWT", False), ("fnphelp", "справка автозвёзд", False)])

    def cmd_fnp(message: Message) -> None:
        update_cfg(admin_chat_id=message.chat.id)
        tg.bot.send_message(message.chat.id, _home_text(), reply_markup=_home_kb(), disable_web_page_preview=True)

    def cmd_fnpjwt(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        payload = parts[1] if len(parts) > 1 else ""
        if payload:
            _save_jwt_from_text(message, payload)
            return
        msg = tg.bot.send_message(message.chat.id, _token_prompt_text(), disable_web_page_preview=True)
        tg.set_state(message.chat.id, msg.id, message.from_user.id, ST_JWT, {"acc": ""})

    def cmd_fnphelp(message: Message) -> None:
        tg.bot.send_message(message.chat.id, _info_text())

    tg.msg_handler(cmd_fnp, commands=["fnp"])
    tg.msg_handler(cmd_fnpjwt, commands=["fnpjwt"])
    tg.msg_handler(cmd_fnphelp, commands=["fnphelp"])
    tg.cbq_handler(_handle_callback, lambda c: str(c.data or "").startswith(f"{CB}:") or str(c.data or "").startswith(PS))
    tg.msg_handler(
        _handle_admin_text,
        func=lambda m: bool(_admin_state(m)) and str((_admin_state(m) or {}).get("state") or "").startswith("fts_"),
    )
    tg.file_handler(ST_JWT, _handle_jwt_file)
    logger.info("Автозвёзды v%s готовы.", VERSION)


def on_order(app, event) -> None:
    global _APP
    _APP = app
    order = getattr(event, "order", None)
    if order is None:
        return
    _process_new_order(order)


def on_message(app, event) -> None:
    global _APP
    _APP = app
    if not event or not event.chat or not event.message:
        return
    _process_chat_message(event.message.text or "", event.chat.id)


BIND_TO_POST_INIT = [on_init]
BIND_TO_NEW_ORDER = [on_order]
BIND_TO_NEW_MESSAGE = [on_message]
NEW_ORDER_CXH = [on_order]
NEW_MESSAGE_CXH = [on_message]
