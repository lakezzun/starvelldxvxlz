from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_str(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class User:
    id: str = ""
    username: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> User | None:
        if not isinstance(data, dict):
            return None
        user_id = _as_str(data.get("id"))
        username = _as_str(data.get("username") or data.get("login") or data.get("name"))
        if not user_id and not username:
            return None
        return cls(id=user_id, username=username, raw=data)


@dataclass
class Message:
    id: str = ""
    chat_id: str = ""
    text: str = ""
    author_id: str = ""
    author_username: str = ""
    created_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any, *, chat_id: str = "") -> Message | None:
        if not isinstance(data, dict):
            return None
        msg_id = _as_str(data.get("id"))
        if not msg_id:
            return None
        author = data.get("author") or data.get("user") or data.get("sender") or {}
        if not isinstance(author, dict):
            author = {}
        text = data.get("content") or data.get("text") or data.get("message") or ""
        return cls(
            id=msg_id,
            chat_id=chat_id or _as_str(data.get("chatId") or data.get("chat_id")),
            text=str(text or ""),
            author_id=_as_str(author.get("id") or data.get("authorId") or data.get("senderId")),
            author_username=_as_str(author.get("username") or author.get("login")),
            created_at=_as_str(data.get("createdAt") or data.get("created_at") or data.get("date")),
            raw=data,
        )


@dataclass
class Chat:
    id: str = ""
    unread: int = 0
    last_message: Message | None = None
    participants: list[User] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> Chat | None:
        if not isinstance(data, dict):
            return None
        chat_id = _as_str(data.get("id"))
        if not chat_id:
            return None
        people: list[User] = []
        for item in data.get("participants") or []:
            user = User.from_dict(item)
            if user:
                people.append(user)
        return cls(
            id=chat_id,
            unread=_as_int(data.get("unreadMessageCount") or data.get("unread")),
            last_message=Message.from_dict(data.get("lastMessage") or data.get("last_message"), chat_id=chat_id),
            participants=people,
            raw=data,
        )

    def other_user(self, my_id: str = "") -> User | None:
        for user in self.participants:
            if my_id and user.id == my_id:
                continue
            return user
        return self.participants[0] if self.participants else None


@dataclass
class Lot:
    id: str = ""
    title: str = ""
    game_id: int = 0
    category_id: int = 0
    price: Any = None
    url: str = ""
    category_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Order:
    id: str = ""
    status: str = ""
    quantity: int = 1
    price: Any = None
    offer_id: str = ""
    offer_name: str = ""
    buyer: User | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> Order | None:
        if not isinstance(data, dict):
            return None
        order_id = _as_str(data.get("id"))
        if not order_id:
            return None
        offer = data.get("offerDetails") or data.get("offer") or {}
        if not isinstance(offer, dict):
            offer = {}
        return cls(
            id=order_id,
            status=_as_str(data.get("status")).upper(),
            quantity=max(1, _as_int(data.get("quantity"), 1)),
            price=data.get("basePrice") or data.get("totalPrice") or data.get("price"),
            offer_id=_as_str(offer.get("id") or offer.get("publicId") or data.get("offerId")),
            offer_name=_as_str(offer.get("name") or offer.get("title") or data.get("productName")),
            buyer=User.from_dict(data.get("user") or data.get("buyer")),
            raw=data,
        )
