from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import App
    from starvell.events import NewMessageEvent, NewOrderEvent

NAME = "Заготовка"
VERSION = "1.0.0"
DESCRIPTION = "Пустой плагин-заготовка. Можно выключить или удалить в меню."
CREDITS = "@dxvxlz"
UUID = "7f3c1e90-2b4a-4d6f-9c11-8a2e5b7d4f01"


def on_init(cardinal: App) -> None:
    pass


def on_message(cardinal: App, event: NewMessageEvent) -> None:
    pass


def on_order(cardinal: App, event: NewOrderEvent) -> None:
    pass


BIND_TO_POST_INIT = [on_init]
BIND_TO_NEW_MESSAGE = [on_message]
BIND_TO_NEW_ORDER = [on_order]
