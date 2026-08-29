from __future__ import annotations

import configparser
import importlib.util
import inspect
import logging
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from starvell.account import Account
from starvell.events import NewMessageEvent, NewOrderEvent, SessionLostEvent
from starvell.exceptions import StarvellAuthError, StarvellRateLimitError
from starvell.runner import Runner
from utils.config import ROOT, cfg_get, proxy_url
from utils.storage import bump_stat, load_disabled_plugins, load_stats, save_disabled_plugins

logger = logging.getLogger("SVC.core")

BIND_MAP = {
    "BIND_TO_PRE_INIT": "pre_init",
    "BIND_TO_POST_INIT": "post_init",
    "BIND_TO_NEW_MESSAGE": "new_message",
    "BIND_TO_NEW_ORDER": "new_order",
    "BIND_TO_SESSION_LOST": "session_lost",
    "NEW_MESSAGE_CXH": "new_message",
    "NEW_ORDER_CXH": "new_order",
}

REQUIRED_FIELDS = ("NAME", "VERSION", "DESCRIPTION", "CREDITS", "UUID")


@dataclass
class PluginData:
    name: str
    version: str
    description: str
    credits: str
    uuid: str
    path: Path
    module: ModuleType
    enabled: bool = True
    handlers: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)
    commands: dict[str, str] = field(default_factory=dict)
    settings_page: bool = False
    delete_handler: Callable[..., Any] | None = None


class App:
    def __init__(self, cfg, version: str) -> None:
        self.cfg = cfg
        self.version = version
        self.account: Account | None = None
        self.runner: Runner | None = None
        self.telegram = None
        self.plugins: dict[str, PluginData] = {}
        self.AR_CFG = configparser.ConfigParser(interpolation=None)
        self.AD_CFG = configparser.ConfigParser(interpolation=None)
        self._handlers: dict[str, list[tuple[str, Callable[..., Any]]]] = {
            "pre_init": [],
            "post_init": [],
            "new_message": [],
            "new_order": [],
            "session_lost": [],
        }
        timeout = float(cfg_get(cfg, "Bot", "handler_timeout", "0") or 0)
        self.handler_timeout = timeout if timeout > 0 else None
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="SVC-PLUG")
        self._stopping = False
        self._restart = False
        self._bump_lock = threading.Lock()
        self.last_bump: dict[str, Any] = {"ok": 0, "fail": 0, "lots": 0, "at": 0, "wait": 0, "error": ""}

    def init(self) -> App:
        cookie = cfg_get(self.cfg, "Starvell", "session_cookie")
        if not cookie:
            raise RuntimeError("В configs/_main.cfg нет session_cookie")
        proxy = proxy_url(self.cfg)
        logger.info("Авторизуюсь на Starvell...")
        if proxy:
            logger.info("Прокси: %s", _mask_proxy(proxy))
        self.account = Account(cookie, proxy=proxy).get()
        user = self.account.user
        logger.info("Аккаунт: %s (id=%s)", user.username if user else "?", user.id if user else "?")
        self._load_feature_configs()
        self._init_telegram()
        self._load_plugins()
        if self.telegram:
            from tg_bot.auto_delivery_cp import init_auto_delivery_cp
            from tg_bot.auto_response_cp import init_auto_response_cp
            from tg_bot.config_loader_cp import init_config_loader_cp
            from tg_bot.panel import init_panel
            from tg_bot.plugins_cp import init_plugins_cp

            init_panel(self)
            init_plugins_cp(self)
            init_auto_response_cp(self)
            init_auto_delivery_cp(self)
            init_config_loader_cp(self)
        self.run_handlers("pre_init", (self,), wait=True)
        chats_i = float(cfg_get(self.cfg, "Bot", "chats_interval", "4") or 4)
        orders_i = float(cfg_get(self.cfg, "Bot", "orders_interval", "8") or 8)
        self.runner = Runner(self.account, chats_interval=chats_i, orders_interval=orders_i)
        threading.Thread(target=self._bump_loop, daemon=True, name="SVC-BUMP").start()
        self.run_handlers("post_init", (self,), wait=True)
        if self.telegram:
            threading.Thread(target=self.telegram.run, daemon=True, name="SVC-TG").start()
        stats = load_stats()
        logger.info(
            "Плагинов: %s | статистика: заказы=%s, сообщения=%s",
            sum(1 for p in self.plugins.values() if p.enabled),
            stats.get("orders", 0),
            stats.get("messages", 0),
        )
        return self

    def run(self) -> None:
        if not self.runner:
            raise RuntimeError("Сначала вызови init()")
        logger.info("Мониторинг чатов и заказов запущен. Ctrl+C — выход.")
        try:
            self.runner.listen(
                on_message=self._on_message,
                on_order=self._on_order,
                on_session_lost=self._on_session_lost,
            )
        except KeyboardInterrupt:
            logger.info("Остановка...")
        finally:
            self._stopping = True
            if self.account:
                try:
                    self.account.close()
                except Exception:
                    pass
            self._pool.shutdown(wait=False)
        if self._restart:
            from utils.restart import restart_program

            restart_program()

    def request_restart(self) -> None:
        self._restart = True
        self._stopping = True
        logger.info("Запрошен перезапуск.")
        if self.telegram:
            try:
                self.telegram.bot.stop_polling()
            except Exception:
                pass
        if self.runner:
            self.runner.stop()

    def autoraise_enabled(self) -> bool:
        return cfg_get(self.cfg, "AutoRaise", "enabled", "1") not in {"0", "false", "no", "off"}

    def bump_interval(self) -> int:
        try:
            value = float(cfg_get(self.cfg, "AutoRaise", "interval", "1800") or 1800)
        except ValueError:
            value = 1800
        return max(300, int(value))

    def raise_lots(self) -> int:
        with self._bump_lock:
            return self._raise_lots()

    def _raise_lots(self) -> int:
        if not self.account:
            return self.bump_interval()
        try:
            lots = self.account.get_lots()
        except StarvellRateLimitError as exc:
            wait = max(60, exc.wait)
            logger.warning("Автоподнятие: слишком частые запросы, пауза %s сек.", wait)
            self.last_bump = {"ok": 0, "fail": 1, "lots": 0, "at": time.time(), "wait": wait, "error": "Starvell просит подождать"}
            return wait
        if not lots:
            self.last_bump = {"ok": 0, "fail": 0, "lots": 0, "at": time.time(), "wait": self.bump_interval(), "error": "нет лотов"}
            logger.info("Автоподнятие: лоты не найдены. Если на сайте они есть — Starvell не отдал список профиля.")
            return self.bump_interval()
        groups: dict[int, set[int]] = {}
        referer = ""
        for lot in lots:
            if lot.game_id and lot.category_id:
                groups.setdefault(lot.game_id, set()).add(lot.category_id)
            if lot.category_url and not referer:
                referer = lot.category_url
        if not groups:
            self.last_bump = {"ok": 0, "fail": 0, "lots": len(lots), "at": time.time(), "wait": self.bump_interval(), "error": "нет gameId/categoryId"}
            logger.warning("Автоподнятие: лоты есть, но нет gameId/categoryId.")
            return self.bump_interval()
        ok = 0
        fail = 0
        wait_for = 0
        last_error = ""
        for game_id, categories in groups.items():
            if self._stopping:
                break
            try:
                result = self.account.bump(game_id, sorted(categories), referer=referer or None)
                wait_for = max(wait_for, int(result.get("wait") or 0))
                if result.get("success"):
                    ok += 1
                    bump_stat("bumps")
                    logger.info("Поднял игру %s, категории %s", game_id, sorted(categories))
                else:
                    fail += 1
                    last_error = str(result.get("message") or result.get("error") or f"HTTP {result.get('status')}")
                    logger.warning("Бамп игры %s не принят: %s", game_id, last_error)
            except StarvellAuthError as exc:
                last_error = str(exc)
                logger.error("Автоподнятие: сессия Starvell не принята.")
                self.last_bump = {"ok": ok, "fail": fail + 1, "lots": len(lots), "at": time.time(), "wait": 60, "error": last_error}
                return 60
            except StarvellRateLimitError as exc:
                wait = max(60, exc.wait)
                last_error = "Starvell просит подождать"
                logger.warning("Автоподнятие: слишком частые запросы, пауза %s сек.", wait)
                self.last_bump = {"ok": ok, "fail": fail + 1, "lots": len(lots), "at": time.time(), "wait": wait, "error": last_error}
                return wait
            except Exception as exc:
                fail += 1
                last_error = str(exc)
                logger.exception("Ошибка бампа игры %s", game_id)
        wait = wait_for if wait_for > 0 else self.bump_interval()
        self.last_bump = {"ok": ok, "fail": fail, "lots": len(lots), "at": time.time(), "wait": wait, "error": last_error}
        if self.telegram and (ok or fail):
            from tg_bot.utils import NotificationTypes

            if ok:
                text = f"📈 Автоподнятие: поднято игр <b>{ok}</b>, лотов <b>{len(lots)}</b>."
            else:
                err = (last_error or "нет успеха").replace("<", "").replace(">", "")[:180]
                text = f"⚠️ Автоподнятие не вышло ({err})."
            try:
                self.telegram.send_notification(text, notification_type=NotificationTypes.lots_raise)
            except Exception:
                logger.exception("Уведомление о бампе")
        return max(300, wait)

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end and not self._stopping:
            time.sleep(min(1.0, end - time.monotonic()))

    def _bump_loop(self) -> None:
        logger.info("Цикл автоподнятия запущен.")
        self._sleep(8)
        while not self._stopping:
            try:
                if not self.autoraise_enabled():
                    self._sleep(5)
                    continue
                wait = self.raise_lots()
                self._sleep(wait)
            except Exception:
                logger.exception("Цикл автоподнятия")
                self._sleep(30)

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        if not self.account:
            raise RuntimeError("нет аккаунта")
        return self.account.send_message(str(chat_id), text)

    def refund_order(self, order_id: str) -> dict[str, Any]:
        if not self.account:
            raise RuntimeError("нет аккаунта")
        return self.account.refund_order(str(order_id))

    def toggle_plugin(self, plugin_uuid: str) -> None:
        plugin = self.plugins[plugin_uuid]
        plugin.enabled = not plugin.enabled
        disabled = {key for key, item in self.plugins.items() if not item.enabled}
        save_disabled_plugins(disabled)
        logger.info("Плагин %s: %s", plugin.name, "вкл" if plugin.enabled else "выкл")

    def add_telegram_commands(self, plugin_uuid: str, commands: list[tuple[str, str, bool]]) -> None:
        plugin = self.plugins.get(plugin_uuid)
        if not plugin:
            return
        for name, desc, in_menu in commands:
            plugin.commands[name] = desc
            if in_menu and self.telegram:
                self.telegram.commands[name] = desc

    def save_config(self, cfg: configparser.ConfigParser, rel_path: str) -> None:
        path = ROOT / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            cfg.write(handle)

    def load_plugin_file(self, path: Path) -> PluginData:
        module = _import_plugin(path)
        plugin_uuid = str(getattr(module, "UUID", "") or "")
        if plugin_uuid in self.plugins:
            self._unload_plugin(plugin_uuid)
        plugin = self._plugin_from_module(path, module)
        self.plugins[plugin.uuid] = plugin
        if plugin.commands and self.telegram:
            self.telegram.commands.update(plugin.commands)
        for kind, funcs in plugin.handlers.items():
            for func in funcs:
                self._handlers.setdefault(kind, []).append((plugin.uuid, func))
        logger.info("Плагин %s v%s загружен с диска.", plugin.name, plugin.version)
        return plugin

    def _unload_plugin(self, plugin_uuid: str) -> None:
        self.plugins.pop(plugin_uuid, None)
        for kind, items in list(self._handlers.items()):
            self._handlers[kind] = [pair for pair in items if pair[0] != plugin_uuid]

    def _load_feature_configs(self) -> None:
        self.AR_CFG = configparser.ConfigParser(interpolation=None)
        self.AD_CFG = configparser.ConfigParser(interpolation=None)
        ar = ROOT / "configs" / "auto_response.cfg"
        ad = ROOT / "configs" / "auto_delivery.cfg"
        if ar.exists():
            self.AR_CFG.read(ar, encoding="utf-8")
        if ad.exists():
            self.AD_CFG.read(ad, encoding="utf-8")

    def _init_telegram(self) -> None:
        enabled = cfg_get(self.cfg, "Telegram", "enabled", "0") in {"1", "true", "yes", "on"}
        token = cfg_get(self.cfg, "Telegram", "token")
        if not enabled or not token:
            logger.info("Telegram-панель выключена. Включите [Telegram] enabled=1 и token в configs/_main.cfg")
            return
        from tg_bot.bot import TGBot

        try:
            self.telegram = TGBot(self)
            me = self.telegram.bot.get_me()
            self.telegram.setup()
            logger.info("Telegram-панель инициализирована (@%s).", me.username)
        except Exception:
            self.telegram = None
            logger.exception("Telegram-панель не поднялась. Консоль продолжит работу.")

    def _on_message(self, event: NewMessageEvent) -> None:
        msg = event.message
        chat = event.chat
        other = chat.other_user(self.account.user.id if self.account and self.account.user else "") if chat else None
        preview = (msg.text or "").replace("\n", " ")[:80] if msg else ""
        logger.info("💬 %s | %s", other.username if other else chat.id if chat else "?", preview or "(пусто)")
        bump_stat("messages")
        try:
            from tg_bot.features import maybe_autoresponse, maybe_greet, notify_message

            notify_message(self, event)
            maybe_greet(self, event)
            maybe_autoresponse(self, event)
        except Exception:
            logger.exception("Обработка сообщения")
        self.run_handlers("new_message", (self, event))

    def _on_order(self, event: NewOrderEvent) -> None:
        order = event.order
        buyer = order.buyer.username if order and order.buyer else "?"
        logger.info(
            "🛒 Заказ %s | %s | %s | %s ₽ x%s",
            order.id if order else "?",
            buyer,
            order.offer_name if order else "?",
            order.price if order else "?",
            order.quantity if order else 1,
        )
        bump_stat("orders")
        try:
            from tg_bot.features import maybe_autodelivery, notify_order

            notify_order(self, event)
            maybe_autodelivery(self, event)
        except Exception:
            logger.exception("Обработка заказа")
        self.run_handlers("new_order", (self, event))

    def _on_session_lost(self, event: SessionLostEvent) -> None:
        bump_stat("errors")
        if self.telegram:
            from tg_bot.utils import NotificationTypes

            self.telegram.send_notification(
                f"⚠️ Сессия Starvell потеряна: {event.reason or 'cookie не принят'}",
                notification_type=NotificationTypes.critical,
            )
        self.run_handlers("session_lost", (self, event))

    def run_handlers(self, kind: str, args: tuple[Any, ...], wait: bool = False) -> None:
        for plugin_uuid, func in self._handlers.get(kind, []):
            plugin = self.plugins.get(plugin_uuid)
            if not plugin or not plugin.enabled:
                continue
            self._call_handler(plugin_uuid, func, args, wait=wait)

    def _call_handler(self, plugin_uuid: str, func: Callable[..., Any], args: tuple[Any, ...], wait: bool = False) -> None:
        name = getattr(func, "__name__", "handler")
        plugin = self.plugins.get(plugin_uuid)
        label = plugin.name if plugin else plugin_uuid

        def job() -> None:
            try:
                result = func(*args)
                if inspect.iscoroutine(result):
                    import asyncio

                    asyncio.run(result)
            except Exception:
                logger.error("Плагин %s упал в %s:\n%s", label, name, traceback.format_exc())
                bump_stat("errors")

        try:
            future = self._pool.submit(job)
            if wait or self.handler_timeout:
                future.result(timeout=self.handler_timeout)
            else:
                future.add_done_callback(lambda fut: fut.exception())
        except FuturesTimeout:
            logger.error("Плагин %s: %s превысил таймаут %ss", label, name, self.handler_timeout)
            bump_stat("errors")
        except Exception:
            logger.exception("Не удалось запустить хэндлер %s/%s", label, name)

    def _load_plugins(self) -> None:
        plugins_dir = ROOT / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        if str(plugins_dir) not in sys.path:
            sys.path.insert(0, str(plugins_dir))
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        disabled = load_disabled_plugins()
        for path in sorted(plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                first = path.read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
                if first and "noplug" in first[0].lower():
                    continue
                module = _import_plugin(path)
                plugin = self._plugin_from_module(path, module)
                if plugin.uuid in disabled:
                    plugin.enabled = False
                self.plugins[plugin.uuid] = plugin
                if plugin.commands and self.telegram:
                    self.telegram.commands.update(plugin.commands)
                for kind, funcs in plugin.handlers.items():
                    for func in funcs:
                        self._handlers.setdefault(kind, []).append((plugin.uuid, func))
                state = "вкл" if plugin.enabled else "выкл"
                logger.info("Плагин %s v%s [%s] (%s)", plugin.name, plugin.version, state, path.name)
            except Exception:
                logger.error("Не загрузился %s:\n%s", path.name, traceback.format_exc())


    def _plugin_from_module(self, path: Path, module: ModuleType) -> PluginData:
        missing = [name for name in REQUIRED_FIELDS if not getattr(module, name, None)]
        if missing:
            raise RuntimeError(f"{path.name}: нет полей {', '.join(missing)}")
        plugin_uuid = str(getattr(module, "UUID"))
        uuid.UUID(plugin_uuid)
        if plugin_uuid in self.plugins:
            raise RuntimeError(f"UUID {plugin_uuid} уже занят")
        handlers: dict[str, list[Callable[..., Any]]] = {}
        for attr, kind in BIND_MAP.items():
            value = getattr(module, attr, None)
            if not value:
                continue
            funcs = value if isinstance(value, (list, tuple)) else [value]
            handlers.setdefault(kind, []).extend([fn for fn in funcs if callable(fn)])
        delete_handler = getattr(module, "BIND_TO_DELETE", None)
        if isinstance(delete_handler, (list, tuple)):
            delete_handler = delete_handler[0] if delete_handler else None
        raw_cmds = getattr(module, "COMMANDS", None) or {}
        commands = {str(key): str(value) for key, value in raw_cmds.items()} if isinstance(raw_cmds, dict) else {}
        return PluginData(
            name=str(module.NAME),
            version=str(module.VERSION),
            description=str(module.DESCRIPTION),
            credits=str(module.CREDITS),
            uuid=plugin_uuid,
            path=path,
            module=module,
            handlers=handlers,
            commands=commands,
            settings_page=bool(getattr(module, "SETTINGS_PAGE", False)),
            delete_handler=delete_handler if callable(delete_handler) else None,
        )


def _import_plugin(path: Path) -> ModuleType:
    name = f"plugins.{path.stem}"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("spec error")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _mask_proxy(url: str) -> str:
    if "@" in url:
        return url.split("@", 1)[-1]
    return url
