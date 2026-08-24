from __future__ import annotations

from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message

from tg_bot import cbt, keyboards as kb
from tg_bot.utils import h, mask_cookie, mask_proxy
from utils.brand import APP_NAME, CREDITS_HTML, DESC_AD, DESC_AR, DESC_AU, DESC_GR, DESC_GS, DESC_LANG, DESC_MAIN, DESC_NS, DESC_PROXY, DESC_UPD
from utils.config import ROOT, cfg_get, save_main_config
from utils.storage import load_stats, save_authorized_users
from utils.updater import check_update, run_update

if TYPE_CHECKING:
    from core import App


def init_panel(cardinal: App) -> None:
    tg = cardinal.telegram
    if not tg:
        return
    bot = tg.bot

    def _edit(call: CallbackQuery, text: str, markup) -> None:
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.id, reply_markup=markup)
        except Exception:
            try:
                bot.send_message(call.message.chat.id, text, reply_markup=markup)
            except Exception:
                pass
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

    def main_text() -> str:
        return DESC_MAIN

    def send_menu(message: Message) -> None:
        tg._ensure_chat_settings(message.chat.id)
        bot.send_message(message.chat.id, main_text(), reply_markup=kb.main())

    def open_main(call: CallbackQuery) -> None:
        _edit(call, main_text(), kb.main())

    def open_main2(call: CallbackQuery) -> None:
        _edit(call, main_text(), kb.main2())

    def profile_text() -> str:
        user = cardinal.account.user if cardinal.account else None
        proxy = cfg_get(cardinal.cfg, "Proxy", "url") or cfg_get(cardinal.cfg, "Starvell", "proxy")
        return (
            f"<b>Профиль Starvell</b>\n\n"
            f"• Ник: <code>{h(user.username if user else '—')}</code>\n"
            f"• ID: <code>{h(user.id if user else '—')}</code>\n"
            f"• Cookie: <code>{h(mask_cookie(cfg_get(cardinal.cfg, 'Starvell', 'session_cookie')))}</code>\n"
            f"• Прокси: <code>{h(mask_proxy(proxy))}</code>\n"
            f"• Чаты каждые: <code>{h(cfg_get(cardinal.cfg, 'Bot', 'chats_interval', '4'))}</code> сек\n"
            f"• Заказы каждые: <code>{h(cfg_get(cardinal.cfg, 'Bot', 'orders_interval', '8'))}</code> сек"
        )

    def open_profile(call: CallbackQuery) -> None:
        _edit(call, profile_text(), kb._rows([[("🔄 Обновить", cbt.PROFILE), ("◀️ Назад", cbt.STARVELL)]]))

    def send_profile(message: Message) -> None:
        bot.send_message(message.chat.id, profile_text(), reply_markup=kb._rows([[("🔄 Обновить", cbt.PROFILE)]]))

    def open_notif(call: CallbackQuery) -> None:
        _edit(call, DESC_NS.format(call.message.chat.id), kb.notifications(tg, call.message.chat.id))

    def switch_notif(call: CallbackQuery) -> None:
        kind = call.data.split(":", 1)[1]
        on = tg.toggle_notification(call.message.chat.id, kind)
        bot.answer_callback_query(call.id, "Включено" if on else "Выключено")
        open_notif(call)

    def open_starvell(call: CallbackQuery) -> None:
        _edit(
            call,
            "<b>Настройки Starvell</b>\n\n"
            "Cookie — сессия с сайта. Если вылетели — вставь новую.\n"
            "Интервалы — как часто бот смотрит чаты и продажи.",
            kb.starvell(cardinal),
        )

    def ask_cookie(call: CallbackQuery) -> None:
        msg = bot.send_message(call.message.chat.id, "Пришли новый <code>session</code> cookie одним сообщением.", reply_markup=kb.cancel())
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.SET_COOKIE)
        bot.answer_callback_query(call.id)

    def save_cookie(message: Message) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        cookie = (message.text or "").strip()
        try:
            bot.delete_message(message.chat.id, message.id)
        except Exception:
            pass
        if not cookie or len(cookie) < 8:
            bot.send_message(message.chat.id, "Похоже, это не cookie.")
            return
        cardinal.cfg.set("Starvell", "session_cookie", cookie)
        save_main_config(cardinal.cfg)
        try:
            if cardinal.account:
                cardinal.account.close()
            from starvell.account import Account
            from utils.config import proxy_url

            cardinal.account = Account(cookie, proxy=proxy_url(cardinal.cfg)).get()
            if cardinal.runner:
                cardinal.runner.account = cardinal.account
            name = cardinal.account.user.username if cardinal.account.user else "?"
            bot.send_message(message.chat.id, f"✅ Cookie принят. Аккаунт: <b>{h(name)}</b>")
        except Exception as exc:
            bot.send_message(message.chat.id, f"❌ Cookie не принят Starvell: {h(exc)}")

    def ask_interval(call: CallbackQuery) -> None:
        msg = bot.send_message(
            call.message.chat.id,
            "Пришли два числа через пробел: интервал чатов и интервал заказов в секундах.\nПример: <code>4 8</code>",
            reply_markup=kb.cancel(),
        )
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.SET_INTERVAL)
        bot.answer_callback_query(call.id)

    def save_interval(message: Message) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        parts = (message.text or "").replace(",", " ").split()
        try:
            chats, orders = float(parts[0]), float(parts[1])
            if chats < 2 or orders < 2:
                raise ValueError("too small")
        except Exception:
            bot.send_message(message.chat.id, "Нужно два числа, например <code>4 8</code>. Минимум 2 секунды.")
            return
        cardinal.cfg.set("Bot", "chats_interval", str(chats))
        cardinal.cfg.set("Bot", "orders_interval", str(orders))
        save_main_config(cardinal.cfg)
        if cardinal.runner:
            cardinal.runner.chats_interval = max(2.0, chats)
            cardinal.runner.orders_interval = max(3.0, orders)
        bot.send_message(message.chat.id, f"✅ Интервалы: чаты {chats}с, заказы {orders}с.")

    def open_proxy(call: CallbackQuery) -> None:
        enabled = cfg_get(cardinal.cfg, "Proxy", "enabled", "0") in {"1", "true", "yes"}
        url = cfg_get(cardinal.cfg, "Proxy", "url") or cfg_get(cardinal.cfg, "Starvell", "proxy")
        tg_proxy = cfg_get(cardinal.cfg, "Telegram", "proxy")
        text = (
            f"<b>Прокси</b>\n\n"
            f"{DESC_PROXY}\n\n"
            f"Starvell: <b>{'вкл' if enabled and url else 'выкл'}</b>\n"
            f"URL: <code>{h(mask_proxy(url))}</code>\n"
            f"Telegram: <code>{h(mask_proxy(tg_proxy))}</code>\n\n"
            "Формат: <code>http://user:pass@host:port</code> или socks5."
        )
        markup = kb._rows(
            [
                [(("🔴 Выключить Starvell" if enabled else "🟢 Включить Starvell"), cbt.TOGGLE_PROXY)],
                [("✏️ Прокси Starvell", cbt.SET_PROXY)],
                [("✏️ Прокси Telegram", cbt.SET_TG_PROXY)],
                [("◀️ Назад", cbt.MAIN2)],
            ]
        )
        _edit(call, text, markup)

    def toggle_proxy(call: CallbackQuery) -> None:
        enabled = cfg_get(cardinal.cfg, "Proxy", "enabled", "0") in {"1", "true", "yes"}
        cardinal.cfg.set("Proxy", "enabled", "0" if enabled else "1")
        save_main_config(cardinal.cfg)
        bot.answer_callback_query(call.id, "Сохранено. Прокси Starvell применится после перезапуска.")
        open_proxy(call)

    def ask_proxy(call: CallbackQuery) -> None:
        msg = bot.send_message(call.message.chat.id, "Пришли URL прокси для Starvell. «-» — очистить.", reply_markup=kb.cancel())
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.SET_PROXY)
        bot.answer_callback_query(call.id)

    def save_proxy(message: Message) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        raw = (message.text or "").strip()
        if raw == "-":
            raw = ""
        cardinal.cfg.set("Proxy", "url", raw)
        cardinal.cfg.set("Starvell", "proxy", raw)
        cardinal.cfg.set("Proxy", "enabled", "1" if raw else "0")
        save_main_config(cardinal.cfg)
        bot.send_message(message.chat.id, "✅ Прокси Starvell сохранён. Перезапусти бота, чтобы применить.")

    def ask_tg_proxy(call: CallbackQuery) -> None:
        msg = bot.send_message(
            call.message.chat.id,
            "Пришли URL прокси для Telegram API. «-» — очистить.\nНужен, если кнопки панели падают по SSL.",
            reply_markup=kb.cancel(),
        )
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.SET_TG_PROXY)
        bot.answer_callback_query(call.id)

    def save_tg_proxy(message: Message) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        raw = (message.text or "").strip()
        if raw == "-":
            raw = ""
        if "Telegram" not in cardinal.cfg:
            cardinal.cfg.add_section("Telegram")
        cardinal.cfg.set("Telegram", "proxy", raw)
        save_main_config(cardinal.cfg)
        bot.send_message(message.chat.id, "✅ Прокси Telegram сохранён. Перезапусти бота, чтобы применить.")

    def open_stats(call: CallbackQuery) -> None:
        stats = load_stats()
        text = (
            f"<b>Статистика</b>\n\n"
            f"• Заказы: <b>{int(stats.get('orders') or 0)}</b>\n"
            f"• Сообщения: <b>{int(stats.get('messages') or 0)}</b>\n"
            f"• Ошибки: <b>{int(stats.get('errors') or 0)}</b>\n"
            f"• Бампы: <b>{int(stats.get('bumps') or 0)}</b>"
        )
        _edit(call, text, kb._rows([[("📜 Логи", cbt.LOGS)], [("◀️ Назад", cbt.MAIN2)]]))

    def send_logs(call: CallbackQuery) -> None:
        log_file = ROOT / "logs" / "starvell-dxvxlz.log"
        if not log_file.exists():
            log_file = ROOT / "logs" / "starvell-cardinal.log"
        bot.answer_callback_query(call.id)
        if not log_file.exists():
            bot.send_message(call.message.chat.id, "Лог пока пуст.")
            return
        with open(log_file, "rb") as handle:
            bot.send_document(call.message.chat.id, handle, caption=f"📜 Логи {APP_NAME}")

    def cmd_logs(message: Message) -> None:
        log_file = ROOT / "logs" / "starvell-dxvxlz.log"
        if not log_file.exists():
            log_file = ROOT / "logs" / "starvell-cardinal.log"
        if not log_file.exists():
            bot.send_message(message.chat.id, "Лог пока пуст.")
            return
        with open(log_file, "rb") as handle:
            bot.send_document(message.chat.id, handle, caption="📜 Логи")

    def open_users(call: CallbackQuery) -> None:
        offset = int(call.data.split(":")[1]) if ":" in call.data else 0
        text = DESC_AU
        _edit(call, text, kb.users_list(cardinal, offset))

    def kick_user(call: CallbackQuery) -> None:
        _, uid, offset = call.data.split(":")
        user_id = int(uid)
        if user_id == call.from_user.id:
            bot.answer_callback_query(call.id, "Нельзя удалить самого себя.", show_alert=True)
            return
        tg.authorized_users.pop(user_id, None)
        save_authorized_users({str(k): v for k, v in tg.authorized_users.items()})
        bot.answer_callback_query(call.id, "Доступ отозван.")
        call.data = f"{cbt.USERS}:{offset}"
        open_users(call)

    def open_greetings(call: CallbackQuery) -> None:
        enabled = cfg_get(cardinal.cfg, "Greetings", "enabled", "0") in {"1", "true", "yes"}
        text_value = cfg_get(cardinal.cfg, "Greetings", "text", "Здравствуйте! Заказ принят, скоро ответим.")
        _edit(
            call,
            DESC_GR.format(h(text_value)),
            kb._rows(
                [
                    [(f"{'🟢' if enabled else '🔴'} Приветствовать пользователей", f"{cbt.GREETINGS}:tg")],
                    [("✏️ Изменить текст приветственного сообщения", f"{cbt.GREETINGS}:tx")],
                    [("◀️ Назад", cbt.MAIN2)],
                ]
            ),
        )

    def greetings_action(call: CallbackQuery) -> None:
        action = call.data.split(":")[1] if ":" in call.data else ""
        if action == "tg":
            enabled = cfg_get(cardinal.cfg, "Greetings", "enabled", "0") in {"1", "true", "yes"}
            if "Greetings" not in cardinal.cfg:
                cardinal.cfg.add_section("Greetings")
            cardinal.cfg.set("Greetings", "enabled", "0" if enabled else "1")
            save_main_config(cardinal.cfg)
            open_greetings(call)
            return
        if action == "tx":
            msg = bot.send_message(call.message.chat.id, "Введи текст приветственного сообщения.", reply_markup=kb.cancel())
            tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.GREETINGS)
            bot.answer_callback_query(call.id)
            return
        open_greetings(call)

    def save_greetings(message: Message) -> None:
        tg.clear_state(message.chat.id, message.from_user.id, True)
        if "Greetings" not in cardinal.cfg:
            cardinal.cfg.add_section("Greetings")
        cardinal.cfg.set("Greetings", "text", message.text or "")
        save_main_config(cardinal.cfg)
        bot.send_message(message.chat.id, "✅ Текст приветствия изменён.")

    def open_ad(call: CallbackQuery) -> None:
        enabled = cfg_get(cardinal.cfg, "AutoDelivery", "enabled", "1") not in {"0", "false", "no"}
        lots = len(cardinal.AD_CFG.sections())
        _edit(
            call,
            f"{DESC_AD}\n\n"
            f"Состояние: <b>{'вкл' if enabled else 'выкл'}</b>\n"
            f"Лотов: <b>{lots}</b>",
            kb._rows(
                [
                    [(("🔴 Выключить" if enabled else "🟢 Включить"), f"{cbt.AUTO_DELIVERY}:tg")],
                    [("🗳️ Редактировать авто-выдачу лотов", f"{cbt.AD_LOTS}:0")],
                    [("➕ Привязать авто-выдачу лоту", cbt.ADD_AD_LOT)],
                    [("📋 Редактировать товарные файлы", f"{cbt.PRODUCTS_LIST}:0")],
                    [("◀️ Назад", cbt.MAIN)],
                ]
            ),
        )

    def ad_toggle(call: CallbackQuery) -> None:
        enabled = cfg_get(cardinal.cfg, "AutoDelivery", "enabled", "1") not in {"0", "false"}
        if "AutoDelivery" not in cardinal.cfg:
            cardinal.cfg.add_section("AutoDelivery")
        cardinal.cfg.set("AutoDelivery", "enabled", "0" if enabled else "1")
        save_main_config(cardinal.cfg)
        open_ad(call)

    def open_ar(call: CallbackQuery) -> None:
        enabled = cfg_get(cardinal.cfg, "AutoResponse", "enabled", "1") not in {"0", "false", "no"}
        cmds = len(cardinal.AR_CFG.sections())
        _edit(
            call,
            f"{DESC_AR}\n\n"
            f"Состояние: <b>{'вкл' if enabled else 'выкл'}</b>\n"
            f"Команд: <b>{cmds}</b>",
            kb._rows(
                [
                    [(("🔴 Выключить" if enabled else "🟢 Включить"), f"{cbt.AUTO_RESPONSE}:tg")],
                    [("✏️ Редактировать существующие команды", f"{cbt.CMD_LIST}:0")],
                    [("➕ Добавить команду / сет команд", cbt.ADD_CMD)],
                    [("◀️ Назад", cbt.MAIN)],
                ]
            ),
        )

    def ar_toggle(call: CallbackQuery) -> None:
        enabled = cfg_get(cardinal.cfg, "AutoResponse", "enabled", "1") not in {"0", "false"}
        if "AutoResponse" not in cardinal.cfg:
            cardinal.cfg.add_section("AutoResponse")
        cardinal.cfg.set("AutoResponse", "enabled", "0" if enabled else "1")
        save_main_config(cardinal.cfg)
        open_ar(call)

    def ask_reply(call: CallbackQuery) -> None:
        chat_id = call.data.split(":", 1)[1]
        msg = bot.send_message(call.message.chat.id, f"Сообщение для чата <code>{h(chat_id)}</code>:", reply_markup=kb.cancel())
        tg.set_state(call.message.chat.id, msg.id, call.from_user.id, cbt.SEND_SV, {"chat_id": chat_id})
        bot.answer_callback_query(call.id)

    def send_reply(message: Message) -> None:
        state = tg.get_state(message.chat.id, message.from_user.id) or {}
        chat_id = (state.get("data") or {}).get("chat_id")
        tg.clear_state(message.chat.id, message.from_user.id, True)
        if not chat_id:
            bot.send_message(message.chat.id, "Чат не найден.")
            return
        try:
            cardinal.send_message(chat_id, message.text or "")
            bot.send_message(message.chat.id, "✅ Отправлено в Starvell.")
        except Exception as exc:
            bot.send_message(message.chat.id, f"❌ Не отправилось: {h(exc)}")

    def ask_refund(call: CallbackQuery) -> None:
        order_id = call.data.split(":", 1)[1]
        _edit(call, f"Вернуть заказ <code>{h(order_id)}</code>?", kb.confirm_refund(order_id))

    def do_refund(call: CallbackQuery) -> None:
        order_id = call.data.split(":", 1)[1]
        try:
            cardinal.refund_order(order_id)
            _edit(call, f"✅ Средства по заказу <code>{h(order_id)}</code> возвращены.", kb._rows([[("◀️ Меню", cbt.MAIN)]]))
        except Exception as exc:
            bot.answer_callback_query(call.id, "Ошибка возврата", show_alert=True)
            bot.send_message(call.message.chat.id, f"❌ {h(exc)}")

    def cancel_refund(call: CallbackQuery) -> None:
        _edit(call, "Возврат отменён.", kb._rows([[("◀️ Меню", cbt.MAIN)]]))

    def cancel_state(call: CallbackQuery) -> None:
        tg.clear_state(call.message.chat.id, call.from_user.id, True)
        bot.answer_callback_query(call.id, "Отменено")
        try:
            bot.edit_message_text("Отменено.", call.message.chat.id, call.message.id)
        except Exception:
            pass

    def open_language(call: CallbackQuery) -> None:
        _edit(call, DESC_LANG, kb.language())

    def open_globals(call: CallbackQuery) -> None:
        _edit(call, DESC_GS, kb.global_switches(cardinal))

    def toggle_global(call: CallbackQuery) -> None:
        kind = call.data.split(":")[1]
        mapping = {
            "ar": ("AutoResponse", "1"),
            "ad": ("AutoDelivery", "1"),
            "gr": ("Greetings", "0"),
        }
        section, default = mapping[kind]
        enabled = cfg_get(cardinal.cfg, section, "enabled", default) not in {"0", "false", "no"}
        if section not in cardinal.cfg:
            cardinal.cfg.add_section(section)
        cardinal.cfg.set(section, "enabled", "0" if enabled else "1")
        save_main_config(cardinal.cfg)
        open_globals(call)

    def ignore_empty(call: CallbackQuery) -> None:
        bot.answer_callback_query(call.id)

    def about(message: Message) -> None:
        bot.send_message(
            message.chat.id,
            f"{CREDITS_HTML}\n<i>Версия:</i> <code>{h(cardinal.version)}</code>",
        )

    def _update_text() -> str:
        info = check_update()
        if not info.ok:
            return f"{DESC_UPD}\n\n❌ GitHub недоступен: {h(info.error)}"
        status = "есть новая версия" if info.has_update else "уже последняя"
        return (
            f"{DESC_UPD}\n\n"
            f"Сейчас: <code>{h(info.local_version)}</code>\n"
            f"GitHub: <code>{h(info.remote_version)}</code>\n"
            f"Статус: <b>{status}</b>"
        )

    def _update_kb():
        return kb._rows(
            [
                [("🔄 Проверить", f"{cbt.UPDATE}:chk")],
                [("⬇️ Скачать обновление", f"{cbt.UPDATE}:go")],
                [("◀️ Назад", cbt.MAIN2)],
            ]
        )

    def open_update(call: CallbackQuery) -> None:
        _edit(call, _update_text(), _update_kb())

    def cmd_update(message: Message) -> None:
        bot.send_message(message.chat.id, _update_text(), reply_markup=_update_kb())

    def update_action(call: CallbackQuery) -> None:
        action = call.data.split(":")[1] if ":" in call.data else "chk"
        if action == "go":
            bot.answer_callback_query(call.id, "Качаю с GitHub...")
            try:
                result = run_update()
            except Exception as exc:
                result = f"Ошибка обновления: {exc}"
            bot.send_message(call.message.chat.id, h(result))
            open_update(call)
            return
        bot.answer_callback_query(call.id)
        open_update(call)

    tg.msg_handler(send_menu, commands=["menu", "start"])
    tg.msg_handler(send_profile, commands=["profile"])
    tg.msg_handler(cmd_logs, commands=["logs"])
    tg.msg_handler(about, commands=["about"])
    tg.msg_handler(cmd_update, commands=["update"])
    tg.cbq_handler(update_action, lambda c: c.data == cbt.UPDATE or c.data.startswith(f"{cbt.UPDATE}:"))
    tg.cbq_handler(open_main, lambda c: c.data == cbt.MAIN)
    tg.cbq_handler(open_main2, lambda c: c.data == cbt.MAIN2)
    tg.cbq_handler(open_language, lambda c: c.data == cbt.LANGUAGE)
    tg.cbq_handler(open_globals, lambda c: c.data == cbt.GLOBALS)
    tg.cbq_handler(toggle_global, lambda c: c.data.startswith(f"{cbt.TOGGLE_GS}:"))
    tg.cbq_handler(ignore_empty, lambda c: c.data == cbt.EMPTY)
    tg.cbq_handler(open_profile, lambda c: c.data == cbt.PROFILE)
    tg.cbq_handler(open_notif, lambda c: c.data == cbt.NOTIF)
    tg.cbq_handler(switch_notif, lambda c: c.data.startswith(f"{cbt.SWITCH_N}:"))
    tg.cbq_handler(open_starvell, lambda c: c.data == cbt.STARVELL)
    tg.cbq_handler(ask_cookie, lambda c: c.data == cbt.SET_COOKIE)
    tg.msg_handler(save_cookie, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.SET_COOKIE))
    tg.cbq_handler(ask_interval, lambda c: c.data == cbt.SET_INTERVAL)
    tg.msg_handler(save_interval, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.SET_INTERVAL))
    tg.cbq_handler(open_proxy, lambda c: c.data == cbt.PROXY)
    tg.cbq_handler(toggle_proxy, lambda c: c.data == cbt.TOGGLE_PROXY)
    tg.cbq_handler(ask_proxy, lambda c: c.data == cbt.SET_PROXY)
    tg.msg_handler(save_proxy, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.SET_PROXY))
    tg.cbq_handler(ask_tg_proxy, lambda c: c.data == cbt.SET_TG_PROXY)
    tg.msg_handler(save_tg_proxy, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.SET_TG_PROXY))
    tg.cbq_handler(open_stats, lambda c: c.data == cbt.STATS)
    tg.cbq_handler(send_logs, lambda c: c.data == cbt.LOGS)
    tg.cbq_handler(open_users, lambda c: c.data.startswith(f"{cbt.USERS}:") or c.data == cbt.USERS)
    tg.cbq_handler(kick_user, lambda c: c.data.startswith(f"{cbt.KICK_USER}:"))
    tg.cbq_handler(greetings_action, lambda c: c.data == cbt.GREETINGS or c.data.startswith(f"{cbt.GREETINGS}:"))
    tg.msg_handler(save_greetings, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.GREETINGS))
    tg.cbq_handler(open_ad, lambda c: c.data == cbt.AUTO_DELIVERY)
    tg.cbq_handler(ad_toggle, lambda c: c.data == f"{cbt.AUTO_DELIVERY}:tg")
    tg.cbq_handler(open_ar, lambda c: c.data == cbt.AUTO_RESPONSE)
    tg.cbq_handler(ar_toggle, lambda c: c.data == f"{cbt.AUTO_RESPONSE}:tg")
    tg.cbq_handler(ask_reply, lambda c: c.data.startswith(f"{cbt.SEND_SV}:"))
    tg.msg_handler(send_reply, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, cbt.SEND_SV))
    tg.cbq_handler(ask_refund, lambda c: c.data.startswith(f"{cbt.REFUND}:") and not c.data.startswith(cbt.REFUND_OK) and not c.data.startswith(cbt.REFUND_NO))
    tg.cbq_handler(do_refund, lambda c: c.data.startswith(f"{cbt.REFUND_OK}:"))
    tg.cbq_handler(cancel_refund, lambda c: c.data.startswith(f"{cbt.REFUND_NO}:"))
    tg.cbq_handler(cancel_state, lambda c: c.data == cbt.CLEAR_STATE)
