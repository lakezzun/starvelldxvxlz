from __future__ import annotations

import json
import threading
import time
from html.parser import HTMLParser
from typing import Any

import httpx

from starvell.exceptions import StarvellAuthError, StarvellResponseError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
BASE_URL = "https://starvell.com"


class _NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._inside = False
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self._inside = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._inside:
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._inside:
            self.chunks.append(data)


def extract_build_id(html: str) -> str:
    parser = _NextDataParser()
    parser.feed(html or "")
    payload = "".join(parser.chunks).strip()
    if not payload:
        raise StarvellResponseError("на странице нет __NEXT_DATA__")
    data = json.loads(payload)
    build_id = data.get("buildId") if isinstance(data, dict) else None
    if not build_id:
        raise StarvellResponseError("buildId не найден")
    return str(build_id)


def parse_cookie_string(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in (raw or "").split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def build_cookies(raw_session: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    cookies = {
        "starvell.theme": "dark",
        "starvell.time_zone": "Europe/Moscow",
    }
    raw = (raw_session or "").strip()
    if "=" in raw:
        cookies.update(parse_cookie_string(raw))
    elif raw:
        cookies["session"] = raw
    if extra:
        cookies.update({k: v for k, v in extra.items() if v})
    return {k: v for k, v in cookies.items() if v}


def page_props(data: Any, operation: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StarvellResponseError(f"{operation}: ожидался JSON-объект")
    props = data.get("pageProps")
    if not isinstance(props, dict):
        nested = data.get("props")
        if isinstance(nested, dict):
            props = nested.get("pageProps")
    if not isinstance(props, dict):
        raise StarvellResponseError(f"{operation}: нет pageProps")
    redirect = props.get("__N_REDIRECT")
    if redirect:
        raise StarvellAuthError(f"{operation}: нужна авторизация ({redirect})")
    if props.get("error") or props.get("ok") is False or props.get("success") is False:
        raise StarvellResponseError(f"{operation}: сервер вернул ошибку")
    return dict(props)


def collection(props: dict[str, Any], key: str) -> list[dict[str, Any]]:
    candidates = [props.get(key)]
    for container_key in ("bff", "data", "result"):
        container = props.get(container_key)
        if isinstance(container, dict):
            candidates.append(container.get(key))
    for value in candidates:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def ensure_success(data: Any, operation: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StarvellResponseError(f"{operation}: ожидался JSON-объект")
    if data.get("error") or data.get("ok") is False or data.get("success") is False:
        detail = data.get("message") or data.get("error") or "ошибка API"
        raise StarvellResponseError(f"{operation}: {detail}")
    return dict(data)


def items_list(data: Any, operation: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    payload = ensure_success(data, operation)
    candidates = [payload.get("items")]
    for key in ("messagesListResult", "result", "data"):
        container = payload.get(key)
        if isinstance(container, dict):
            candidates.append(container.get("items"))
    for value in candidates:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


class HttpClient:
    def __init__(self, cookies: dict[str, str], proxy: str | None = None, timeout: float = 20.0) -> None:
        self._cookies = dict(cookies)
        self._proxy = proxy or None
        self._timeout = timeout
        self._build_id: str | None = None
        self._build_at = 0.0
        self._lock = threading.RLock()
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": True,
            "headers": {"user-agent": USER_AGENT, "accept-language": "ru,en;q=0.9"},
        }
        if self._proxy:
            try:
                self._client = httpx.Client(proxy=self._proxy, **kwargs)
            except TypeError:
                self._client = httpx.Client(proxies=self._proxy, **kwargs)
        else:
            self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def update_cookies(self, extra: dict[str, str]) -> None:
        with self._lock:
            self._cookies.update({k: v for k, v in extra.items() if v})

    def reset_build_id(self) -> None:
        with self._lock:
            self._build_id = None
            self._build_at = 0.0

    def _merge_cookies(self) -> dict[str, str]:
        with self._lock:
            return dict(self._cookies)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        retry_404_build: bool = False,
    ) -> httpx.Response:
        req_headers = {"user-agent": USER_AGENT, "accept-language": "ru,en;q=0.9"}
        if headers:
            req_headers.update(headers)
        response = self._client.request(
            method,
            url,
            headers=req_headers,
            cookies=self._merge_cookies(),
            json=json_body,
            params=params,
        )
        for name, value in response.cookies.items():
            if value:
                self.update_cookies({name: value})
        if retry_404_build and response.status_code == 404:
            self.reset_build_id()
        return response

    def json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        retry_404_build: bool = False,
    ) -> Any:
        response = self.request(
            method,
            url,
            headers=headers,
            json_body=json_body,
            params=params,
            retry_404_build=retry_404_build,
        )
        if response.status_code in {401, 403}:
            raise StarvellAuthError(f"HTTP {response.status_code}: {url}")
        if response.status_code >= 400:
            raise StarvellResponseError(f"HTTP {response.status_code}: {url}")
        ctype = response.headers.get("content-type", "")
        if "json" not in ctype.lower():
            text = (response.text or "")[:200]
            raise StarvellResponseError(f"не JSON ({ctype}): {text}")
        return response.json()

    def build_id(self) -> str:
        with self._lock:
            if self._build_id and (time.monotonic() - self._build_at) < 1800:
                return self._build_id
        response = self.request(
            "GET",
            BASE_URL + "/",
            headers={"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        if response.status_code in {401, 403}:
            raise StarvellAuthError("сессия Starvell не принята")
        if response.status_code >= 400:
            raise StarvellResponseError(f"не удалось открыть starvell.com (HTTP {response.status_code})")
        build_id = extract_build_id(response.text)
        with self._lock:
            self._build_id = build_id
            self._build_at = time.monotonic()
        return build_id

    def next_data(self, path: str, *, referer: str = BASE_URL + "/") -> dict[str, Any]:
        clean = path.lstrip("/")
        last_error: Exception | None = None
        for attempt in range(2):
            build_id = self.build_id()
            url = f"{BASE_URL}/_next/data/{build_id}/{clean}"
            try:
                data = self.json(
                    "GET",
                    url,
                    headers={"accept": "*/*", "referer": referer, "x-nextjs-data": "1"},
                    retry_404_build=True,
                )
                if isinstance(data, dict):
                    return data
                raise StarvellResponseError(f"некорректный Next.js ответ: {path}")
            except StarvellResponseError as exc:
                last_error = exc
                if attempt == 0 and "HTTP 404" in str(exc):
                    self.reset_build_id()
                    continue
                raise
        raise last_error or StarvellResponseError(f"не удалось загрузить {path}")
