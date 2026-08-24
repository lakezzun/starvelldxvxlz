from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from starvell.types import Chat, Message, Order, User


@dataclass
class BaseEvent:
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NewMessageEvent(BaseEvent):
    chat: Chat | None = None
    message: Message | None = None


@dataclass
class NewOrderEvent(BaseEvent):
    order: Order | None = None


@dataclass
class SessionLostEvent(BaseEvent):
    reason: str = ""


@dataclass
class InitializedEvent(BaseEvent):
    user: User | None = None
