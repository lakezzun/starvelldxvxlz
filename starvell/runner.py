from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from starvell.account import Account
from starvell.events import NewMessageEvent, NewOrderEvent, SessionLostEvent
from starvell.exceptions import StarvellAuthError, StarvellRateLimitError
from starvell.types import Chat, Message

logger = logging.getLogger("SVC.runner")


class Runner:
    def __init__(self, account: Account, *, chats_interval: float = 4.0, orders_interval: float = 8.0) -> None:
        self.account = account
        self.chats_interval = max(2.0, float(chats_interval))
        self.orders_interval = max(3.0, float(orders_interval))
        self._seen_messages: dict[str, str] = {}
        self._seen_orders: set[str] = set()
        self._orders_ready = False
        self._running = False
        self.last_chats: list[Chat] = []

    def listen(
        self,
        on_message: Callable[[NewMessageEvent], None],
        on_order: Callable[[NewOrderEvent], None],
        on_session_lost: Callable[[SessionLostEvent], None] | None = None,
    ) -> None:
        self._running = True
        next_chats = 0.0
        next_orders = 0.0
        while self._running:
            now = time.monotonic()
            try:
                if now >= next_chats:
                    self._poll_chats(on_message)
                    next_chats = time.monotonic() + self.chats_interval
                if now >= next_orders:
                    self._poll_orders(on_order)
                    next_orders = time.monotonic() + self.orders_interval
            except StarvellAuthError as exc:
                logger.error("Сессия Starvell потеряна: %s", exc)
                if on_session_lost:
                    on_session_lost(SessionLostEvent(reason=str(exc)))
                time.sleep(15)
            except StarvellRateLimitError as exc:
                wait = max(15, exc.wait)
                logger.warning("Starvell: слишком частые запросы, пауза %s сек.", wait)
                time.sleep(wait)
                next_chats = time.monotonic() + wait
                next_orders = time.monotonic() + wait
            except Exception:
                logger.exception("Ошибка цикла мониторинга")
                time.sleep(5)
            time.sleep(0.2)

    def stop(self) -> None:
        self._running = False

    def _poll_chats(self, on_message: Callable[[NewMessageEvent], None]) -> None:
        chats = self.account.get_chats()
        self.last_chats = chats
        my_id = self.account.user.id if self.account.user else ""
        for chat in chats:
            last = chat.last_message
            if not last:
                continue
            previous = self._seen_messages.get(chat.id)
            if previous is None:
                self._seen_messages[chat.id] = last.id
                if chat.unread <= 0:
                    continue
            if previous == last.id:
                continue
            self._seen_messages[chat.id] = last.id
            if my_id and last.author_id == my_id:
                continue
            on_message(NewMessageEvent(chat=chat, message=last, raw=chat.raw))

    def _poll_orders(self, on_order: Callable[[NewOrderEvent], None]) -> None:
        orders = self.account.get_orders(page=1)
        if not self._orders_ready:
            self._seen_orders = {order.id for order in orders}
            self._orders_ready = True
            return
        for order in reversed(orders):
            if order.id in self._seen_orders:
                continue
            self._seen_orders.add(order.id)
            if order.status and order.status not in {"CREATED", "PAID", "NEW"}:
                continue
            on_order(NewOrderEvent(order=order, raw=order.raw))


def iter_new_messages(chats: Iterable[Chat], seen: dict[str, str]) -> Iterable[tuple[Chat, Message]]:
    for chat in chats:
        last = chat.last_message
        if not last:
            continue
        if seen.get(chat.id) == last.id:
            continue
        seen[chat.id] = last.id
        yield chat, last
