from __future__ import annotations

import configparser
import logging
from pathlib import Path

from starvell.events import NewMessageEvent, NewOrderEvent
from tg_bot import keyboards as kb
from tg_bot.utils import NotificationTypes, h
from utils.config import ROOT, cfg_get

logger = logging.getLogger("SVC.features")

_GREETED: set[str] = set()


def _load_ini(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    if path.exists():
        cfg.read(path, encoding="utf-8")
    return cfg


def notify_message(cardinal, event: NewMessageEvent) -> None:
    if not cardinal.telegram or not event.message or not event.chat:
        return
    other = event.chat.other_user(cardinal.account.user.id if cardinal.account and cardinal.account.user else "")
    name = other.username if other else event.chat.id
    text = (
        f"💬 <b>Новое сообщение</b> от <code>{h(name)}</code>\n\n"
        f"{h((event.message.text or '')[:1500]) or '<i>(пусто / вложение)</i>'}"
    )
    cardinal.telegram.send_notification(text, kb.new_message(event.chat.id), NotificationTypes.new_message)


def notify_order(cardinal, event: NewOrderEvent) -> None:
    if not cardinal.telegram or not event.order:
        return
    order = event.order
    buyer = order.buyer.username if order.buyer else "?"
    chat_id = ""
    chats = getattr(getattr(cardinal, "runner", None), "last_chats", None) or getattr(cardinal, "_last_chats", []) or []
    if cardinal.account and order.buyer:
        for chat in chats:
            other = chat.other_user(cardinal.account.user.id if cardinal.account.user else "")
            if other and (other.id == order.buyer.id or other.username == order.buyer.username):
                chat_id = chat.id
                break
    text = (
        f"🛒 <b>Новый заказ</b> <code>{h(order.id)}</code>\n"
        f"• Покупатель: <code>{h(buyer)}</code>\n"
        f"• Лот: {h(order.offer_name or order.offer_id or '—')}\n"
        f"• Кол-во: {order.quantity}\n"
        f"• Сумма: {h(order.price)} ₽"
    )
    cardinal.telegram.send_notification(text, kb.new_order(order.id, chat_id), NotificationTypes.new_order)


def maybe_greet(cardinal, event: NewMessageEvent) -> None:
    if not event.chat or cfg_get(cardinal.cfg, "Greetings", "enabled", "0") not in {"1", "true", "yes"}:
        return
    if event.chat.id in _GREETED:
        return
    _GREETED.add(event.chat.id)
    text = cfg_get(cardinal.cfg, "Greetings", "text", "Здравствуйте! Заказ принят, скоро ответим.")
    if text:
        try:
            cardinal.send_message(event.chat.id, text)
        except Exception:
            logger.exception("Приветствие не отправилось")


def maybe_autoresponse(cardinal, event: NewMessageEvent) -> None:
    if cfg_get(cardinal.cfg, "AutoResponse", "enabled", "1") in {"0", "false", "no"}:
        return
    if not event.message or not event.chat:
        return
    incoming = (event.message.text or "").strip().lower()
    if not incoming:
        return
    cfg = getattr(cardinal, "AR_CFG", None) or _load_ini(ROOT / "configs" / "auto_response.cfg")
    for section in cfg.sections():
        if cfg.get(section, "enabled", fallback="1") in {"0", "false", "no"}:
            continue
        aliases = [part.strip().lower() for part in section.split("|") if part.strip()]
        if incoming in aliases or any(incoming.startswith(alias + " ") for alias in aliases):
            reply = cfg.get(section, "response", fallback="").replace("\\n", "\n")
            if reply:
                cardinal.send_message(event.chat.id, reply)
            return


def maybe_autodelivery(cardinal, event: NewOrderEvent) -> None:
    if cfg_get(cardinal.cfg, "AutoDelivery", "enabled", "1") in {"0", "false", "no"}:
        return
    if not event.order:
        return
    cfg = getattr(cardinal, "AD_CFG", None) or _load_ini(ROOT / "configs" / "auto_delivery.cfg")
    offer_id = event.order.offer_id
    section = None
    if offer_id and cfg.has_section(offer_id):
        section = offer_id
    elif event.order.offer_name and cfg.has_section(event.order.offer_name):
        section = event.order.offer_name
    if not section:
        return
    chat_id = ""
    chats = getattr(getattr(cardinal, "runner", None), "last_chats", None) or []
    if cardinal.account and event.order.buyer:
        try:
            candidates = list(chats) if chats else []
            if not candidates:
                candidates = cardinal.account.get_chats()
            for chat in candidates:
                other = chat.other_user(cardinal.account.user.id if cardinal.account.user else "")
                if other and (other.id == event.order.buyer.id or other.username == event.order.buyer.username):
                    chat_id = chat.id
                    break
            if not chat_id:
                for chat in cardinal.account.get_chats():
                    other = chat.other_user(cardinal.account.user.id if cardinal.account.user else "")
                    if other and (other.id == event.order.buyer.id or other.username == event.order.buyer.username):
                        chat_id = chat.id
                        break
        except Exception:
            logger.exception("Не удалось найти чат для автовыдачи")
    if not chat_id:
        logger.error("Автовыдача: чат покупателя не найден, заказ %s", event.order.id)
        return
    reply = cfg.get(section, "response", fallback="").replace("\\n", "\n")
    filename = cfg.get(section, "productsFileName", fallback="").strip()
    products: list[str] = []
    rest: list[str] = []
    path = None
    if filename:
        path = ROOT / "storage" / "products" / filename
        if path.exists():
            lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            take = max(1, event.order.quantity)
            products = lines[:take]
            rest = lines[take:]
            path.write_text("\n".join(rest) + ("\n" if rest else ""), encoding="utf-8")
        reply = reply.replace("$product", "\n".join(products) or "—")
    if not reply:
        if path is not None and products:
            leftover = products + rest
            path.write_text("\n".join(leftover) + ("\n" if leftover else ""), encoding="utf-8")
        return
    try:
        cardinal.send_message(chat_id, reply)
        logger.info("Автовыдача по заказу %s", event.order.id)
    except Exception:
        if path is not None and products:
            leftover = products + rest
            path.write_text("\n".join(leftover) + ("\n" if leftover else ""), encoding="utf-8")
        logger.exception("Автовыдача не отправилась")
