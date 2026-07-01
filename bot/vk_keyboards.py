"""
VK-клавиатуры — аналог keyboards.py для Telegram.
VK использует JSON-формат для клавиатур.
"""
import json
from typing import Any


def _button(label: str, color: str = "secondary", payload: dict | None = None) -> dict:
    """Создаёт одну кнопку VK."""
    btn: dict[str, Any] = {
        "action": {
            "type": "text",
            "label": label,
        },
        "color": color,
    }
    if payload:
        btn["action"]["payload"] = json.dumps(payload)
    return btn


def _callback_button(label: str, color: str = "secondary", payload: dict | None = None) -> dict:
    """Inline-кнопка VK (callback)."""
    btn: dict[str, Any] = {
        "action": {
            "type": "callback",
            "label": label,
        },
        "color": color,
    }
    if payload:
        btn["action"]["payload"] = json.dumps(payload)
    return btn


def _link_button(label: str, link: str) -> dict:
    """Кнопка-ссылка VK."""
    return {
        "action": {
            "type": "open_link",
            "label": label,
            "link": link,
        },
    }


def _location_button() -> dict:
    """Кнопка отправки геолокации."""
    return {
        "action": {
            "type": "location",
        },
    }


def vk_keyboard(rows: list[list[dict]], one_time: bool = False, inline: bool = False) -> str:
    """Сериализует клавиатуру VK в JSON."""
    kb = {
        "one_time": one_time,
        "buttons": rows,
        "inline": inline,
    }
    return json.dumps(kb, ensure_ascii=False)


# === Текстовые кнопки (аналог BTN_*) ===
VK_BTN_FIND = "🔍 Найти АЗС"
VK_BTN_REPORT = "📝 Сообщить"
VK_BTN_SUBSCRIBE = "🔔 Уведомления"
VK_BTN_OWNER = "👤 Я владелец"
VK_BTN_PROFILE = "👤 Профиль"
VK_BTN_HELP = "❓ Помощь"
VK_BTN_PREMIUM = "💎 Premium"
VK_BTN_DONATE = "❤️ Поддержать"
VK_BTN_HOME = "🏠 В начало"


def vk_main_menu() -> str:
    """Главное меню VK."""
    return vk_keyboard([
        [_button(VK_BTN_FIND, "primary"), _button(VK_BTN_REPORT, "positive")],
        [_button(VK_BTN_SUBSCRIBE), _button(VK_BTN_OWNER)],
        [_button(VK_BTN_PROFILE), _button(VK_BTN_HELP)],
        [_button(VK_BTN_PREMIUM), _button(VK_BTN_DONATE)],
    ])


def vk_city_keyboard() -> str:
    """Выбор города (inline, max 6 rows)."""
    from keyboards import TOP_CITIES
    rows = []
    for i in range(0, min(len(TOP_CITIES), 8), 2):
        row = []
        for j in range(i, min(i + 2, len(TOP_CITIES))):
            name, _ = TOP_CITIES[j]
            row.append(_callback_button(f"📍 {name}", "primary", {"cmd": "city", "city": name}))
        rows.append(row)
    rows.append([_callback_button("✏️ Другой город", "secondary", {"cmd": "city", "city": "other"})])
    rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
    return vk_keyboard(rows, inline=True)


def vk_filters_keyboard(city: str) -> str:
    """Меню фильтров."""
    return vk_keyboard([
        [
            _callback_button("⛽ АИ-92", "primary", {"cmd": "fuel", "city": city, "fuel": "92"}),
            _callback_button("⛽ АИ-95", "primary", {"cmd": "fuel", "city": city, "fuel": "95"}),
        ],
        [
            _callback_button("⛽ АИ-98", "secondary", {"cmd": "fuel", "city": city, "fuel": "98"}),
            _callback_button("🛢 Дизель", "secondary", {"cmd": "fuel", "city": city, "fuel": "diesel"}),
        ],
        [
            _callback_button("🚨 Экстренный", "negative", {"cmd": "emergency", "city": city}),
        ],
        [_callback_button("🏠 В начало", "secondary", {"cmd": "home"})],
    ], inline=True)


def vk_station_list_keyboard(stations: list[dict], city: str) -> str:
    """Список АЗС для выбора."""
    rows = []
    for s in stations[:10]:
        name = (s.get("name") or "АЗС")[:25]
        rows.append([_callback_button(f"⛽ {name}", "primary", {"cmd": "st", "id": s["id"]})])
    rows.append([_callback_button("🔄 Фильтры", "secondary", {"cmd": "filters", "city": city})])
    rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
    return vk_keyboard(rows, inline=True)


def vk_station_actions(station_id: int) -> str:
    """Действия с АЗС."""
    return vk_keyboard([
        [_callback_button("📝 Сообщить о наличии", "positive", {"cmd": "report_start", "id": station_id})],
        [_callback_button("🔔 Подписаться", "primary", {"cmd": "sub_station", "id": station_id})],
        [_callback_button("◀️ Назад к списку", "secondary", {"cmd": "back_to_list"})],
        [_callback_button("🏠 В начало", "secondary", {"cmd": "home"})],
    ], inline=True)


def vk_fuel_type_keyboard(station_id: int) -> str:
    """Выбор типа топлива для отчёта."""
    return vk_keyboard([
        [
            _callback_button("⛽ АИ-92", "primary", {"cmd": "report_fuel", "id": station_id, "fuel": "92"}),
            _callback_button("⛽ АИ-95", "primary", {"cmd": "report_fuel", "id": station_id, "fuel": "95"}),
        ],
        [
            _callback_button("⛽ АИ-98", "secondary", {"cmd": "report_fuel", "id": station_id, "fuel": "98"}),
            _callback_button("🛢 Дизель", "secondary", {"cmd": "report_fuel", "id": station_id, "fuel": "diesel"}),
        ],
        [_callback_button("◀️ Отмена", "secondary", {"cmd": "home"})],
    ], inline=True)


def vk_report_status_keyboard(station_id: int, fuel: str) -> str:
    """Статус наличия топлива."""
    return vk_keyboard([
        [_callback_button("✅ Есть", "positive", {"cmd": "report_submit", "id": station_id, "fuel": fuel, "status": "yes"})],
        [_callback_button("⚠️ Кончается", "secondary", {"cmd": "report_submit", "id": station_id, "fuel": fuel, "status": "low"})],
        [_callback_button("❌ Нет", "negative", {"cmd": "report_submit", "id": station_id, "fuel": fuel, "status": "no"})],
        [_callback_button("◀️ Назад", "secondary", {"cmd": "report_start", "id": station_id})],
    ], inline=True)


def vk_subscribe_geo_keyboard() -> str:
    """Кнопка отправки геолокации для подписки."""
    return vk_keyboard([
        [_location_button()],
        [_button(VK_BTN_HOME)],
    ])


def vk_subscribe_radius_keyboard() -> str:
    """Выбор радиуса подписки."""
    return vk_keyboard([
        [
            _callback_button("3 км", "primary", {"cmd": "sub_radius", "radius": 3}),
            _callback_button("5 км", "primary", {"cmd": "sub_radius", "radius": 5}),
            _callback_button("10 км", "primary", {"cmd": "sub_radius", "radius": 10}),
        ],
        [_callback_button("🏠 В начало", "secondary", {"cmd": "home"})],
    ], inline=True)


def vk_premium_keyboard() -> str:
    """Кнопки Premium."""
    return vk_keyboard([
        [_callback_button("🎁 7 дней бесплатно", "positive", {"cmd": "premium_trial"})],
        [_callback_button("💎 Купить Premium", "primary", {"cmd": "buy_premium"})],
        [_callback_button("🏠 В начало", "secondary", {"cmd": "home"})],
    ], inline=True)


def vk_report_city_keyboard() -> str:
    """Выбор города для отчёта (inline, max 6 rows)."""
    from keyboards import TOP_CITIES
    rows = []
    for i in range(0, min(len(TOP_CITIES), 8), 2):
        row = []
        for j in range(i, min(i + 2, len(TOP_CITIES))):
            name, _ = TOP_CITIES[j]
            row.append(_callback_button(f"📍 {name}", "primary", {"cmd": "report_city", "city": name}))
        rows.append(row)
    rows.append([_callback_button("✏️ Другой город", "secondary", {"cmd": "report_city", "city": "other"})])
    rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
    return vk_keyboard(rows, inline=True)


def vk_report_station_keyboard(stations: list[dict]) -> str:
    """Список АЗС для выбора при отчёте (max 6 rows)."""
    rows = []
    for s in stations[:4]:
        name = (s.get("name") or "АЗС")[:25]
        rows.append([_callback_button(f"⛽ {name}", "primary", {"cmd": "report_pick", "id": s["id"]})])
    rows.append([_callback_button("◀️ Назад", "secondary", {"cmd": "report_city_menu"})])
    rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
    return vk_keyboard(rows, inline=True)
