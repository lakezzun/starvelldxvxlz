from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import quote

from starvell.exceptions import StarvellAuthError, StarvellResponseError
from starvell.http import BASE_URL, HttpClient, build_cookies, collection, ensure_success, items_list, page_props
from starvell.types import Chat, Lot, Message, Order, User

logger = logging.getLogger("SVC.starvell")
_WAIT_MIN_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:минут|минуты|мин|minutes?|mins?)\b", re.I)
_WAIT_SEC_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:секунд|секунды|сек|seconds?|secs?)\b", re.I)
_WAIT_HOUR_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:час(?:а|ов)?|hours?|hrs?)\b", re.I)


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

    def get_lots(self) -> list[Lot]:
        if not self.user:
            return []
        attempts: list[tuple[str, str]] = []
        if self.user.username:
            name = quote(self.user.username, safe="")
            attempts.append((f"profile/{name}.json", f"{BASE_URL}/profile/{self.user.username}"))
        if self.user.id:
            attempts.append((f"users/{self.user.id}.json?user_id={self.user.id}", f"{BASE_URL}/users/{self.user.id}"))
        last_error: Exception | None = None
        for path, referer in attempts:
            try:
                data = self.http.next_data(path, referer=referer)
                lots = _lots_from_props(page_props(data, "profile"))
                game_ids = sorted({lot.game_id for lot in lots if lot.game_id})
                if game_ids:
                    self.http.update_cookies({"starvell.my_games": ",".join(str(gid) for gid in game_ids)})
                return lots
            except Exception as exc:
                last_error = exc
                logger.debug("Лоты %s: %s", path, exc)
        if last_error:
            logger.warning("Не удалось получить лоты профиля: %s", last_error)
        return []

    def bump(self, game_id: int, category_ids: list[int], referer: str | None = None) -> dict[str, Any]:
        cats = [int(x) for x in category_ids if str(x).strip()]
        response = self.http.request(
            "POST",
            f"{BASE_URL}/api/offers/bump",
            headers={
                "accept": "*/*",
                "content-type": "application/json",
                "origin": BASE_URL,
                "referer": referer or (BASE_URL + "/"),
            },
            json_body={"gameId": int(game_id), "categoryIds": cats},
        )
        payload: Any = {}
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        wait = _bump_wait(payload, response.headers.get("Retry-After") or "")
        http_ok = 200 <= response.status_code < 300
        api_fail = payload.get("success") is False or payload.get("ok") is False or bool(payload.get("error"))
        result = dict(payload)
        result["success"] = bool(http_ok and not api_fail)
        result["status"] = response.status_code
        result["wait"] = wait
        result["gameId"] = int(game_id)
        result["categoryIds"] = cats
        if response.status_code in {401, 403}:
            raise StarvellAuthError(f"bump HTTP {response.status_code}")
        return result


def _maybe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _lots_from_props(props: dict[str, Any]) -> list[Lot]:
    categories = props.get("userProfileOffers")
    if not categories:
        bff = props.get("bff")
        if isinstance(bff, dict):
            categories = bff.get("userProfileOffers")
    if not categories:
        categories = props.get("categoriesWithOffers")
    if not isinstance(categories, list):
        categories = collection(props, "userProfileOffers") or collection(props, "categoriesWithOffers")
    lots: list[Lot] = []
    for category in categories:
        if not isinstance(category, dict):
            continue
        category_id = _maybe_int(category.get("id") or category.get("categoryId"))
        game = category.get("game") if isinstance(category.get("game"), dict) else {}
        game_id = _maybe_int(category.get("gameId") or game.get("id"))
        game_slug = str(game.get("slug") or category.get("gameSlug") or "").strip()
        category_slug = str(category.get("slug") or "").strip()
        category_url = f"{BASE_URL}/{game_slug}/{category_slug}/trade" if game_slug and category_slug else ""
        offers = category.get("offers") or []
        if not isinstance(offers, list):
            continue
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            offer_id = str(offer.get("publicId") or offer.get("id") or "").strip()
            if not offer_id:
                continue
            rus = (offer.get("descriptions") or {}).get("rus") if isinstance(offer.get("descriptions"), dict) else {}
            if not isinstance(rus, dict):
                rus = {}
            title = str(
                rus.get("briefDescription")
                or rus.get("description")
                or offer.get("name")
                or offer.get("title")
                or offer_id
            ).strip()
            offer_game = offer.get("game") if isinstance(offer.get("game"), dict) else {}
            offer_cat = offer.get("category") if isinstance(offer.get("category"), dict) else {}
            lots.append(
                Lot(
                    id=offer_id,
                    title=title,
                    game_id=_maybe_int(offer.get("gameId") or offer_game.get("id")) or game_id,
                    category_id=_maybe_int(offer.get("categoryId") or offer_cat.get("id")) or category_id,
                    price=offer.get("price"),
                    url=f"{BASE_URL}/offers/{offer_id}",
                    category_url=category_url,
                    raw=offer,
                )
            )
    return lots


def _bump_wait(payload: dict[str, Any], retry_after: str) -> int:
    if retry_after.strip():
        try:
            return max(0, int(float(retry_after.strip())))
        except ValueError:
            pass
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    sources = [payload, nested]
    for source in sources:
        for key in ("retryAfter", "retry_after", "wait", "waitSeconds", "nextBumpIn", "cooldown", "delay"):
            value = source.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        next_at = source.get("nextBumpAt") or source.get("next_bump_at")
        if isinstance(next_at, (int, float)) and next_at > 1_000_000_000:
            return max(0, int(next_at - time.time()))
    text = " ".join(str(payload.get(key) or "") for key in ("message", "error", "detail"))
    match = _WAIT_HOUR_RE.search(text)
    if match:
        return int(float(match.group(1).replace(",", ".")) * 3600)
    match = _WAIT_MIN_RE.search(text)
    if match:
        return int(float(match.group(1).replace(",", ".")) * 60)
    match = _WAIT_SEC_RE.search(text)
    if match:
        return int(float(match.group(1).replace(",", ".")))
    return 0
