"""
Клавиатуры — основная и inline.
Новая архитектура: город → фильтры → АЗС
"""
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# === Текстовые кнопки (reply keyboard внизу экрана) ===
BTN_FIND = "🔍 Найти АЗС"
BTN_REPORT = "📝 Сообщить о наличии"
BTN_SUBSCRIBE = "🔔 Уведомления"
BTN_OWNER = "👤 Я владелец АЗС"
BTN_APP = "📱 Приложение"
BTN_PROFILE = "👤 Профиль"
BTN_MY_STATIONS = "🏪 Мои АЗС"
BTN_HELP = "❓ Помощь"
BTN_PREMIUM = "💎 Premium"
BTN_DONATE = "❤️ Поддержать"
BTN_BUG = "🐛 Ошибка"
BTN_IDEA = "💡 Предложение"
BTN_HOME = "🏠 В начало"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню (кнопки внизу экрана)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_FIND), KeyboardButton(text=BTN_REPORT)],
            [KeyboardButton(text=BTN_SUBSCRIBE), KeyboardButton(text=BTN_OWNER)],
            [KeyboardButton(text=BTN_APP), KeyboardButton(text=BTN_PROFILE)],
            [KeyboardButton(text=BTN_MY_STATIONS), KeyboardButton(text=BTN_HELP)],
            [KeyboardButton(text=BTN_PREMIUM), KeyboardButton(text=BTN_DONATE)],
            [KeyboardButton(text=BTN_BUG), KeyboardButton(text=BTN_IDEA)],
        ],
        resize_keyboard=True,
    )


def main_inline_keyboard() -> InlineKeyboardMarkup:
    """Главное inline-меню (отображается в сообщении)."""
    from config import settings
    rows = [
        [
            InlineKeyboardButton(text="🔍 Найти АЗС", callback_data="menu:find"),
            InlineKeyboardButton(text="📝 Сообщить", callback_data="menu:report"),
        ],
        [
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="menu:subscribe"),
            InlineKeyboardButton(text="👤 Я владелец", callback_data="menu:owner"),
        ],
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu:profile"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help"),
        ],
        [
            InlineKeyboardButton(text="💎 Premium", callback_data="menu:premium"),
            InlineKeyboardButton(text="❤️ Поддержать", callback_data="menu:donate"),
        ],
    ]
    # Рекламный баннер (если задан)
    if settings.AD_BANNER_TEXT and settings.AD_BANNER_URL:
        rows.append([InlineKeyboardButton(text=f"📢 {settings.AD_BANNER_TEXT}", url=settings.AD_BANNER_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# === Топ городов для быстрого выбора (Иваново + соседи + крупные) ===
TOP_CITIES = [
    ("Иваново", "Иваново"),
    ("Кинешма", "Кинешма"),
    ("Шуя", "Шуя"),
    ("Кохма", "Кохма"),
    ("Вичуга", "Вичуга"),
    ("Фурманов", "Фурманов"),
    ("Москва", "Москва"),
    ("Ярославль", "Ярославль"),
    ("Кострома", "Кострома"),
    ("Владимир", "Владимир"),
    ("Нижний Новгород", "Нижний Новгород"),
    ("Тула", "Тула"),
    ("Калуга", "Калуга"),
    ("Краснодар", "Краснодар"),
    ("Ростов-на-Дону", "Ростов-на-Дону"),
    ("Казань", "Казань"),
    ("Екатеринбург", "Екатеринбург"),
    ("Новосибирск", "Новосибирск"),
    ("Челябинск", "Челябинск"),
    ("Самара", "Самара"),
]


def city_keyboard() -> InlineKeyboardMarkup:
    """Кнопки для выбора города (inline)."""
    rows = []
    for i in range(0, len(TOP_CITIES), 2):
        row = []
        for j in range(i, min(i + 2, len(TOP_CITIES))):
            name, _ = TOP_CITIES[j]
            row.append(InlineKeyboardButton(
                text=f"📍 {name}",
                callback_data=f"city:{name}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text="✏️ Другой город (напишите в сообщении)",
        callback_data="city:other",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def filters_keyboard(city: str) -> InlineKeyboardMarkup:
    """Меню фильтров после выбора города."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⛽ АИ-92", callback_data=f"fuel:{city}:92"),
                InlineKeyboardButton(text="⛽ АИ-95", callback_data=f"fuel:{city}:95"),
            ],
            [
                InlineKeyboardButton(text="⛽ АИ-98", callback_data=f"fuel:{city}:98"),
                InlineKeyboardButton(text="🛢 Дизель", callback_data=f"fuel:{city}:diesel"),
            ],
            [
                InlineKeyboardButton(text="💰 Фильтр по цене", callback_data=f"price_menu:{city}"),
                InlineKeyboardButton(text="⛽ Фильтр по сети", callback_data=f"net_menu:{city}"),
            ],
            [
                InlineKeyboardButton(text="🚨 Экстренный (любая цена/сеть)", callback_data=f"emergency:{city}"),
            ],
        ],
    )


def price_filter_keyboard(city: str, fuel: str | None = None) -> InlineKeyboardMarkup:
    """Фильтр по цене."""
    if fuel:
        def _cb(price: str) -> str:
            return f"price:{city}:{fuel}:{price}"
    else:
        def _cb(price: str) -> str:
            return f"price:{city}:{price}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Любая", callback_data=_cb("any")),
                InlineKeyboardButton(text="до 50₽", callback_data=_cb("50")),
                InlineKeyboardButton(text="до 60₽", callback_data=_cb("60")),
            ],
            [
                InlineKeyboardButton(text="до 70₽", callback_data=_cb("70")),
                InlineKeyboardButton(text="до 80₽", callback_data=_cb("80")),
                InlineKeyboardButton(text="до 100₽", callback_data=_cb("100")),
            ],
        ],
    )


def network_filter_keyboard(city: str, fuel: str | None = None) -> InlineKeyboardMarkup:
    """Фильтр по сети АЗС."""
    if fuel:
        def _cb(net: str) -> str:
            return f"net:{city}:{fuel}:{net}"
    else:
        def _cb(net: str) -> str:
            return f"net:{city}:{net}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⛽ Любая сеть", callback_data=_cb("any")),
                InlineKeyboardButton(text="Лукойл", callback_data=_cb("Лукойл")),
                InlineKeyboardButton(text="Газпром", callback_data=_cb("Газпромнефть")),
            ],
            [
                InlineKeyboardButton(text="Роснефть", callback_data=_cb("Роснефть")),
                InlineKeyboardButton(text="Татнефть", callback_data=_cb("Татнефть")),
                InlineKeyboardButton(text="Газоил", callback_data=_cb("Газоил")),
            ],
            [
                InlineKeyboardButton(text="Опти", callback_data=_cb("Опти")),
                InlineKeyboardButton(text="Shell", callback_data=_cb("Shell")),
            ],
        ],
    )


def flow_keyboard(extra_buttons: list[KeyboardButton] | None = None) -> ReplyKeyboardMarkup:
    """Клавиатура для flow — с кнопкой «В начало»."""
    keyboard = []
    if extra_buttons:
        keyboard.append(extra_buttons)
    keyboard.append([KeyboardButton(text="🏠 В начало")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def home_inline_button() -> InlineKeyboardButton:
    """Inline-кнопка «В начало» для callback-сценариев."""
    return InlineKeyboardButton(text="🏠 В начало", callback_data="go_home")


def with_home_inline(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Добавляет inline-кнопку «🏠 В начало» в конец существующей клавиатуры."""
    new_kb = list(markup.inline_keyboard)
    new_kb.append([home_inline_button()])
    return InlineKeyboardMarkup(inline_keyboard=new_kb)


def station_actions_keyboard(station_id: int, has_statuses: bool = True, lat: float = None, lon: float = None) -> InlineKeyboardMarkup:
    """Действия с конкретной АЗС."""
    buttons = []
    # Кнопка маршрута (если есть координаты)
    if lat and lon:
        buttons.append([InlineKeyboardButton(
            text="📍 Построить маршрут",
            callback_data=f"route:{station_id}:{lat}:{lon}",
        )])
    buttons.append([InlineKeyboardButton(
        text="📝 Сообщить о наличии",
        callback_data=f"report:{station_id}",
    )])
    buttons.append([InlineKeyboardButton(
        text="⭐ Оценить качество бензина",
        callback_data=f"review_start:{station_id}",
    )])
    buttons.append([InlineKeyboardButton(
        text="🔔 Подписаться на эту АЗС",
        callback_data=f"sub_station:{station_id}",
    )])
    buttons.append([InlineKeyboardButton(
        text="◀️ Назад к списку",
        callback_data="back_to_list",
    )])
    return with_home_inline(InlineKeyboardMarkup(inline_keyboard=buttons))


def fuel_type_keyboard(station_id: int) -> InlineKeyboardMarkup:
    """Выбор типа топлива для отчёта."""
    return with_home_inline(InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⛽ АИ-92",
                    callback_data=f"report_fuel:{station_id}:92",
                ),
                InlineKeyboardButton(
                    text="⛽ АИ-95",
                    callback_data=f"report_fuel:{station_id}:95",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⛽ АИ-98",
                    callback_data=f"report_fuel:{station_id}:98",
                ),
                InlineKeyboardButton(
                    text="🛢 Дизель",
                    callback_data=f"report_fuel:{station_id}:diesel",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Отмена",
                    callback_data="cancel",
                ),
            ],
        ],
    ))


def report_status_keyboard(station_id: int, fuel: str) -> InlineKeyboardMarkup:
    """Статус наличия топлива."""
    return with_home_inline(InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Есть",
                    callback_data=f"report_status:{station_id}:{fuel}:yes",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🕐 Большая очередь",
                    callback_data=f"report_status:{station_id}:{fuel}:queue",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Кончается",
                    callback_data=f"report_status:{station_id}:{fuel}:low",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data=f"report_status:{station_id}:{fuel}:no",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=f"report:{station_id}",
                ),
            ],
        ],
    ))


def bug_report_keyboard() -> InlineKeyboardMarkup:
    """Кнопки для отправки баг-репорта."""
    return with_home_inline(InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📸 Скриншот (опционально)", callback_data="bug_screenshot")],
            [InlineKeyboardButton(text="✅ Отправить", callback_data="bug_submit")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
        ],
    ))


def idea_keyboard() -> InlineKeyboardMarkup:
    """Кнопки для отправки предложения."""
    return with_home_inline(InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="idea_submit")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
        ],
    ))


def premium_keyboard() -> InlineKeyboardMarkup:
    """Кнопки Premium."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Попробовать 7 дней бесплатно", callback_data="premium_trial")],
            [InlineKeyboardButton(text="💎 Купить Premium", callback_data="buy_premium")],
            [InlineKeyboardButton(text="🏠 В начало", callback_data="go_home")],
        ],
    )


def web_app_keyboard(web_app_url: str) -> InlineKeyboardMarkup:
    """Кнопка для открытия Telegram Web App."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="📱 Открыть приложение",
                web_app={"url": web_app_url},
            )],
            [InlineKeyboardButton(text="🏠 В начало", callback_data="go_home")],
        ],
    )


def report_city_keyboard() -> InlineKeyboardMarkup:
    """Выбор города для отчёта о наличии."""
    rows = []
    for i in range(0, len(TOP_CITIES), 2):
        row = []
        for j in range(i, min(i + 2, len(TOP_CITIES))):
            name, _ = TOP_CITIES[j]
            row.append(InlineKeyboardButton(
                text=f"📍 {name}",
                callback_data=f"report_city:{name}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text="✏️ Другой город (напишите в сообщении)",
        callback_data="report_city:other",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_station_keyboard(stations: list[dict], city: str) -> InlineKeyboardMarkup:
    """Список АЗС для выбора при отчёте — сеть + адрес."""
    buttons = []
    for s in stations[:15]:
        operator = (s.get("operator") or "")[:15]
        address = (s.get("address") or "")[:25]
        if operator and address:
            label = f"⛽ {operator} — {address}"
        elif operator:
            label = f"⛽ {operator}"
        elif address:
            label = f"⛽ {address}"
        else:
            label = f"⛽ {s.get('name', 'АЗС')}"
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"report_pick:{s['id']}",
        )])
    buttons.append([InlineKeyboardButton(
        text="🔍 Найти другую АЗС по адресу",
        callback_data="report_address:start",
    )])
    buttons.append([InlineKeyboardButton(
        text="◀️ Назад к городу",
        callback_data="menu:report",
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def report_address_results_keyboard(stations: list[dict]) -> InlineKeyboardMarkup:
    """Список АЗС, найденных по адресу — сеть + адрес."""
    buttons = []
    for s in stations[:10]:
        operator = (s.get("operator") or "")[:15]
        address = (s.get("address") or "")[:25]
        if operator and address:
            label = f"⛽ {operator} — {address}"
        elif operator:
            label = f"⛽ {operator}"
        elif address:
            label = f"⛽ {address}"
        else:
            label = f"⛽ {s.get('name', 'АЗС')}"
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"report_pick:{s['id']}",
        )])
    buttons.append([InlineKeyboardButton(
        text="🔍 Найти другую АЗС",
        callback_data="report_address:start",
    )])
    buttons.append([InlineKeyboardButton(
        text="◀️ Назад",
        callback_data="menu:report",
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def review_rating_keyboard(station_id: int, fuel_type: str) -> InlineKeyboardMarkup:
    """Клавиатура для выбора рейтинга качества бензина (0-5 звёзд)."""
    return with_home_inline(InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data=f"review:{station_id}:{fuel_type}:5"),
                InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data=f"review:{station_id}:{fuel_type}:4"),
            ],
            [
                InlineKeyboardButton(text="⭐⭐⭐", callback_data=f"review:{station_id}:{fuel_type}:3"),
                InlineKeyboardButton(text="⭐⭐", callback_data=f"review:{station_id}:{fuel_type}:2"),
            ],
            [
                InlineKeyboardButton(text="⭐", callback_data=f"review:{station_id}:{fuel_type}:1"),
                InlineKeyboardButton(text="Без звёзд", callback_data=f"review:{station_id}:{fuel_type}:0"),
            ],
            [
                InlineKeyboardButton(text="◀️ Назад", callback_data=f"report:{station_id}"),
            ],
        ],
    ))


def review_fuel_keyboard(station_id: int) -> InlineKeyboardMarkup:
    """Выбор типа топлива для отзыва."""
    return with_home_inline(InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⛽ АИ-92", callback_data=f"review_fuel:{station_id}:92"),
                InlineKeyboardButton(text="⛽ АИ-95", callback_data=f"review_fuel:{station_id}:95"),
            ],
            [
                InlineKeyboardButton(text="⛽ АИ-98", callback_data=f"review_fuel:{station_id}:98"),
                InlineKeyboardButton(text="🛢 Дизель", callback_data=f"review_fuel:{station_id}:diesel"),
            ],
            [
                InlineKeyboardButton(text="◀️ Отмена", callback_data="cancel"),
            ],
        ],
    ))
