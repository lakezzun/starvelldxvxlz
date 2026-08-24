from __future__ import annotations

from typing import Any

from starvell.exceptions import StarvellAuthError, StarvellResponseError
from starvell.http import BASE_URL, HttpClient, build_cookies, collection, ensure_success, items_list, page_props
from starvell.types import Chat, Message, Order, User


class Account:
    def __init__(self, session_cookie: str, proxy: str | None = None, timeout: float = 20.0) -> None:
        self.session_cookie = session_cookie
        self.proxy = proxy or None
        self.user: User | None = None
        self.sid: str | None = None
        self.http = HttpClient(build_cookies(session_cookie), proxy=self.proxy, timeout=timeout)

    def close(self) -> None:
        self.http.close()

    def get(self) -> Account:
        data = self.http.next_data("index.json", referer=BASE_URL + "/")
        props = page_props(data, "homepage")
        user = User.from_dict(props.get("user"))
        if not user:
            raise StarvellAuthError("cookie не авторизован. Вставьте свежий session с starvell.com")
        self.user = user
        sid = props.get("sid")
        if sid:
            self.sid = str(sid)
            self.http.update_cookies({"sid": str(sid)})
        return self

    def get_chats(self) -> list[Chat]:
        data = self.http.next_data("chat.json", referer=f"{BASE_URL}/chat")
        props = page_props(data, "chats")
        chats: list[Chat] = []
        for item in collection(props, "chats"):
            chat = Chat.from_dict(item)
            if chat:
                chats.append(chat)
        return chats

    def get_orders(self, page: int = 1) -> list[Order]:
        path = "account/sells.json" if page <= 1 else f"account/sells.json?page={page}"
        data = self.http.next_data(path, referer=f"{BASE_URL}/account/sells")
        props = page_props(data, "orders")
        orders: list[Order] = []
        for item in collection(props, "orders"):
            order = Order.from_dict(item)
            if order:
                orders.append(order)
        return orders

    def get_chat_messages(self, chat_id: str, limit: int = 50) -> list[Message]:
        payload = {"chatId": chat_id, "limit": min(100, max(1, int(limit)))}
        data = self.http.json(
            "POST",
            f"{BASE_URL}/api/messages/list",
            headers={
                "accept": "*/*",
                "content-type": "application/json",
                "origin": BASE_URL,
                "referer": f"{BASE_URL}/chat",
            },
            json_body=payload,
        )
        messages: list[Message] = []
        for item in items_list(data, "messages"):
            message = Message.from_dict(item, chat_id=chat_id)
            if message:
                messages.append(message)
        return messages

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        data = self.http.json(
            "POST",
            f"{BASE_URL}/api/messages/send",
            headers={
                "accept": "*/*",
                "content-type": "application/json",
                "origin": BASE_URL,
                "referer": f"{BASE_URL}/chat/{chat_id}",
            },
            json_body={"chatId": chat_id, "content": text},
        )
        return ensure_success(data, "send message")

    def refund_order(self, order_id: str) -> dict[str, Any]:
        data = self.http.json(
            "POST",
            f"{BASE_URL}/api/orders/refund",
            headers={
                "accept": "*/*",
                "content-type": "application/json",
                "origin": BASE_URL,
                "referer": f"{BASE_URL}/order/{order_id}",
            },
            json_body={"orderId": order_id},
        )
        return ensure_success(data, "refund")

    def bump(self, game_id: int, category_ids: list[int]) -> dict[str, Any]:
        data = self.http.json(
            "POST",
            f"{BASE_URL}/api/offers/bump",
            headers={
                "accept": "*/*",
                "content-type": "application/json",
                "origin": BASE_URL,
                "referer": BASE_URL + "/",
            },
            json_body={"gameId": int(game_id), "categoryIds": list(category_ids)},
        )
        if not isinstance(data, dict):
            raise StarvellResponseError("bump: некорректный ответ")
        return data
