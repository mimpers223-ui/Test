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


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню (кнопки внизу экрана)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Найти АЗС")],
            [KeyboardButton(text="📝 Сообщить")],
            [KeyboardButton(text="🔔 Подписки")],
            [
                KeyboardButton(text="🗺 Карта"),
                KeyboardButton(text="👤 Профиль"),
            ],
            [
                KeyboardButton(text="👤 Владелец/Работник АЗС"),
                KeyboardButton(text="📊 Мои АЗС"),
            ],
            [KeyboardButton(text="🏠 В начало")],
        ],
        resize_keyboard=True,
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
