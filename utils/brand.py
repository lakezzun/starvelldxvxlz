from __future__ import annotations

APP_NAME = "StarvellDxvxlz"
VERSION = "0.2.3"
DEVELOPER = "@dxvxlz"
DEVELOPER_URL = "https://t.me/dxvxlz"
GITHUB_REPO = "lakezzun/starvelldxvxlz"
GITHUB_URL = "https://github.com/lakezzun/starvelldxvxlz"
GITHUB_BRANCH = "main"

CREDITS_HTML = (
    f"<b>{APP_NAME}</b>\n\n"
    "Панель продавца <a href=\"https://starvell.com\">Starvell</a>: заказы, чаты, автовыдача, автоответ и плагины.\n\n"
    f"👨‍💻 Разработчик: <a href=\"{DEVELOPER_URL}\">{DEVELOPER}</a>\n\n"
    "<b>Как устроен доступ к сайту</b>\n"
    "Свой HTTP-клиент на <code>httpx</code> к starvell.com (cookie <code>session</code>). "
    "Telegram-панель — <code>pyTelegramBotAPI</code>.\n\n"
    "<b>Вдохновители</b>\n"
    "• API и автоматизация Starvell — <a href=\"https://t.me/exfador\">@exfador</a>\n"
    "  GitHub: github.com/exfador/starvell_api\n"
    "  Канал: <a href=\"https://t.me/starvellapi\">@starvellapi</a>\n"
    "• Панель и UX — FunPay Cardinal, <a href=\"https://t.me/sidor0912\">@sidor0912</a>\n"
    "  Канал: <a href=\"https://t.me/fpc_updates\">@fpc_updates</a>\n"
)

DESC_MAIN = "Выбери категорию настроек."
DESC_GS = "Здесь можно включать и выключать основные функции бота."
DESC_NS = (
    "Здесь можно настроить уведомления.\n\n"
    "<b><u>Настройки раздельные для каждого Telegram-чата.</u></b>\n\n"
    "ID текущего чата: <code>{}</code>"
)
DESC_AR = "Здесь можно добавить команды или редактировать существующие."
DESC_AD = "Здесь можно изменить авто-выдачу, загрузить товарные файлы и привязать лоты."
DESC_GR = "Здесь можно настроить приветствие новых покупателей.\n\n<b>Текст приветствия:</b>\n<code>{}</code>"
DESC_PL = (
    "Плагины — один <code>.py</code> в папке <code>plugins/</code>.\n\n"
    "После добавления или удаления плагина лучше перезапустить бота."
)
DESC_CFG = "Здесь можно скачать и залить конфиги. Основной файл уходит без cookie, токена и пароля."
DESC_AU = "Кто ввёл пароль панели. Нажми на пользователя, чтобы отозвать доступ."
DESC_PROXY = (
    "Прокси для сайта Starvell и отдельно для Telegram.\n"
    "Если кнопки в боте иногда отваливаются по SSL — задай прокси именно для Telegram."
)
DESC_LANG = "Сейчас интерфейс на русском."
DESC_UPD = (
    "Обновление качается с GitHub: <code>lakezzun/starvelldxvxlz</code>.\n\n"
    "Cookie, конфиги, товары и твои плагины не затираются.\n"
    "После загрузки перезапусти <code>start.bat</code>."
)
ACCESS_DENIED = (
    "👋 Привет, <b><i>{}</i></b>!\n\n"
    "❌ Ты неавторизованный пользователь.\n\n"
    "🔑 Отправь <u><b>секретный пароль</b></u>, который вводил при настройке, чтобы открыть панель."
)
ACCESS_GRANTED = (
    "🔓 Доступ к панели открыт.\n\n"
    "🔔 Уведомления для этого чата включаются в «Настройках уведомлений».\n\n"
    "⚙️ Меню — команда /menu."
)
BOT_STARTED = (
    f"✅ <b>{APP_NAME}</b> v{{}} запущен.\n\n"
    f"👨‍💻 Разработчик: {DEVELOPER}\n"
    "Чтобы узнать больше — /about"
)
