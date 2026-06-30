"""
Клавиатуры — основная и inline.
"""
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

# === Текстовые кнопки (reply keyboard внизу экрана) ===
BTN_FIND = "🔍 Найти АЗС"
BTN_REPORT = "📝 Сообщить о наличии"
BTN_SUBSCRIBE = "🔔 Подписки"
BTN_MAP = "🗺 Открыть карту"
BTN_PROFILE = "👤 Профиль"
BTN_OWNER = "🏪 Владелец АЗС"
BTN_MY_STATIONS = "📊 Мои АЗС"
BTN_HELP = "ℹ️ Помощь"
BTN_STATS = "📊 Статистика"
BTN_PREMIUM = "💎 Premium"
BTN_HOME = "🏠 В начало"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню (кнопки внизу экрана)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_FIND), KeyboardButton(text=BTN_MAP)],
            [KeyboardButton(text=BTN_REPORT), KeyboardButton(text=BTN_PROFILE)],
            [KeyboardButton(text=BTN_SUBSCRIBE), KeyboardButton(text=BTN_PREMIUM)],
            [KeyboardButton(text=BTN_OWNER), KeyboardButton(text=BTN_MY_STATIONS)],
            [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_HELP)],
            [KeyboardButton(text=BTN_HOME)],
        ],
        resize_keyboard=True,
    )


def main_inline_keyboard() -> InlineKeyboardMarkup:
    """Главное inline-меню (отображается в сообщении)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 Найти АЗС", callback_data="menu:find"),
                InlineKeyboardButton(text="🌍 Выбрать город", callback_data="menu:city"),
            ],
            [
                InlineKeyboardButton(text="🚨 Экстренный", callback_data="menu:emergency"),
                InlineKeyboardButton(text="🗺 Карта", callback_data="menu:map"),
            ],
            [
                InlineKeyboardButton(text="📝 Сообщить", callback_data="menu:report"),
                InlineKeyboardButton(text="👤 Профиль", callback_data="menu:profile"),
            ],
            [
                InlineKeyboardButton(text="🔔 Подписки", callback_data="menu:subscribe"),
                InlineKeyboardButton(text="💎 Premium", callback_data="menu:premium"),
            ],
            [
                InlineKeyboardButton(text="🏪 Владелец", callback_data="menu:owner"),
                InlineKeyboardButton(text="📊 Мои АЗС", callback_data="menu:my_stations"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="menu:stats"),
                InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu:help"),
            ],
        ],
    )


# === Топ городов для быстрого выбора (Иваново + соседи + крупные) ===
TOP_CITIES = [
    ("Иваново", "Иваново"),
    ("Москва", "Москва"),
    ("Санкт-Петербург", "Санкт-Петербург"),
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
    # По 2 кнопки в ряд
    for i in range(0, len(TOP_CITIES), 2):
        row = []
        for j in range(i, min(i + 2, len(TOP_CITIES))):
            name, _ = TOP_CITIES[j]
            row.append(InlineKeyboardButton(
                text=f"📍 {name}",
                callback_data=f"city:{name}",
            ))
        rows.append(row)
    # Кнопка "другой город" — ввод текстом
    rows.append([InlineKeyboardButton(
        text="✏️ Другой город (напишите в сообщении)",
        callback_data="city:other",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def price_filter_keyboard() -> InlineKeyboardMarkup:
    """Фильтр по цене (любая / до 50/60/70/80/100)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Любая", callback_data="price:any"),
                InlineKeyboardButton(text="до 50₽", callback_data="price:50"),
                InlineKeyboardButton(text="до 60₽", callback_data="price:60"),
            ],
            [
                InlineKeyboardButton(text="до 70₽", callback_data="price:70"),
                InlineKeyboardButton(text="до 80₽", callback_data="price:80"),
                InlineKeyboardButton(text="до 100₽", callback_data="price:100"),
            ],
        ],
    )


def network_filter_keyboard() -> InlineKeyboardMarkup:
    """Фильтр по сети АЗС."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⛽ Любая сеть", callback_data="net:any"),
                InlineKeyboardButton(text="Лукойл", callback_data="net:Лукойл"),
                InlineKeyboardButton(text="Газпром", callback_data="net:Газпромнефть"),
            ],
            [
                InlineKeyboardButton(text="Роснефть", callback_data="net:Роснефть"),
                InlineKeyboardButton(text="Татнефть", callback_data="net:Татнефть"),
                InlineKeyboardButton(text="Shell", callback_data="net:Shell"),
            ],
            [
                InlineKeyboardButton(text="Teboil", callback_data="net:Teboil"),
                InlineKeyboardButton(text="Башнефть", callback_data="net:Башнефть"),
            ],
        ],
    )


def flow_keyboard(extra_buttons: list[KeyboardButton] | None = None) -> ReplyKeyboardMarkup:
    """Клавиатура для flow — с кнопкой «В начало» (для отмены и возврата).

    extra_buttons: опциональный ряд кнопок над «В начало» (например, "📍 Отправить геолокацию").
    """
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


def station_actions_keyboard(station_id: int, has_statuses: bool = True) -> InlineKeyboardMarkup:
    """Действия с конкретной АЗС — только кнопки."""
    return with_home_inline(InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Сообщить о наличии",
                    callback_data=f"report:{station_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Подписаться на эту АЗС",
                    callback_data=f"sub_station:{station_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗺 На карте",
                    web_app=WebAppInfo(
                        url=f"https://benzin-mini.vercel.app/?station={station_id}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад к списку",
                    callback_data="back_to_list",
                ),
            ],
        ],
    ))


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
