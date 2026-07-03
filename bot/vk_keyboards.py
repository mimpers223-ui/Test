"""
VK-клавиатуры — аналог keyboards.py для Telegram.
VK использует JSON-формат для клавиатур.

ВАЖНО: Используем ТОЛЬКО type:"text" кнопки (НЕ callback!).
Callback-кнопки требуют message_event acknowledgment, который не работает
через Bot Long Poll API + vkbottle polling. Текстовые кнопки отправляют
обычные сообщения через message_new — надёжно и без спиннера.
"""
import json
from typing import Any


def _button(label: str, color: str = "secondary", payload: dict | None = None) -> dict:
    """Создаёт одну текстовую кнопку VK."""
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
    ])


def vk_city_keyboard() -> str:
    """Выбор города."""
    from keyboards import TOP_CITIES
    rows = []
    for i in range(0, min(len(TOP_CITIES), 8), 2):
        row = []
        for j in range(i, min(i + 2, len(TOP_CITIES))):
            name, _ = TOP_CITIES[j]
            row.append(_button(f"📍 {name}", "primary"))
        rows.append(row)
    rows.append([_button("✏️ Другой город", "secondary")])
    rows.append([_button(VK_BTN_HOME)])
    return vk_keyboard(rows)


def vk_filters_keyboard(city: str) -> str:
    """Меню фильтров."""
    return vk_keyboard([
        [
            _button("⛽ АИ-92", "primary"),
            _button("⛽ АИ-95", "primary"),
        ],
        [
            _button("⛽ АИ-98", "secondary"),
            _button("🛢 Дизель", "secondary"),
        ],
        [
            _button("🚨 Экстренный", "negative"),
        ],
        [_button(VK_BTN_HOME)],
    ])


def vk_station_list_keyboard(stations: list[dict], city: str) -> str:
    """Список АЗС для выбора — кнопки с ID в label."""
    rows = []
    for i, s in enumerate(stations[:5]):
        name = (s.get("name") or "АЗС")[:20]
        rows.append([_button(f"#{s['id']} {name}", "primary")])
    rows.append([_button("🔄 Фильтры", "secondary")])
    rows.append([_button(VK_BTN_HOME)])
    return vk_keyboard(rows)


def vk_station_actions(station_id: int, lat: float | None = None, lon: float | None = None) -> str:
    """Действия с АЗС."""
    rows = []
    if lat and lon:
        yandex_url = f"https://yandex.ru/maps/?rtext={lat},{lon}&rtt=auto"
        rows.append([_link_button("🗺 Маршрут", yandex_url)])
    rows.append([_button(f"📝 Отчёт #{station_id}", "positive")])
    rows.append([_button(f"⭐ Оценить качество #{station_id}", "primary")])
    rows.append([_button(f"🔔 Подписка #{station_id}", "primary")])
    rows.append([_button("◀️ Назад к списку", "secondary")])
    rows.append([_button(VK_BTN_HOME)])
    return vk_keyboard(rows)


def vk_fuel_type_keyboard(station_id: int) -> str:
    """Выбор типа топлива для отчёта."""
    return vk_keyboard([
        [
            _button(f"⛽ 92 #{station_id}", "primary"),
            _button(f"⛽ 95 #{station_id}", "primary"),
        ],
        [
            _button(f"⛽ 98 #{station_id}", "secondary"),
            _button(f"🛢 ДТ #{station_id}", "secondary"),
        ],
        [_button("◀️ Отмена", "secondary")],
    ])


def vk_report_status_keyboard(station_id: int, fuel: str) -> str:
    """Статус наличия топлива."""
    return vk_keyboard([
        [_button(f"✅ Есть #{station_id}:{fuel}", "positive")],
        [_button(f"⚠️ Кончается #{station_id}:{fuel}", "secondary")],
        [_button(f"❌ Нет #{station_id}:{fuel}", "negative")],
        [_button("◀️ Назад", "secondary")],
    ])


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
            _button("3 км", "primary"),
            _button("5 км", "primary"),
            _button("10 км", "primary"),
        ],
        [_button(VK_BTN_HOME)],
    ])


VK_GROUP_ID = 239975253
VK_DONATE_URL = "https://vk.com/donut/benzyn_ryadom"


def vk_premium_keyboard() -> str:
    """Кнопки Premium — ссылка на VK Донат."""
    return vk_keyboard([
        [_link_button("💎 Поддержать 99₽", VK_DONATE_URL)],
        [_button(VK_BTN_HOME)],
    ])


def vk_donate_keyboard() -> str:
    """Кнопки доната — ссылка на VK Донат."""
    return vk_keyboard([
        [_link_button("☕ 50₽", VK_DONATE_URL)],
        [_link_button("⛽ 100₽", VK_DONATE_URL)],
        [_link_button("🔧 250₽", VK_DONATE_URL)],
        [_link_button("💎 500₽", VK_DONATE_URL)],
        [_link_button("👑 Шейх 10 000₽", VK_DONATE_URL)],
        [_button(VK_BTN_HOME)],
    ])


def vk_report_city_keyboard() -> str:
    """Выбор города для отчёта."""
    from keyboards import TOP_CITIES
    rows = []
    for i in range(0, min(len(TOP_CITIES), 8), 2):
        row = []
        for j in range(i, min(i + 2, len(TOP_CITIES))):
            name, _ = TOP_CITIES[j]
            row.append(_button(f"📍 {name}", "primary"))
        rows.append(row)
    rows.append([_button("✏️ Другой город", "secondary")])
    rows.append([_button(VK_BTN_HOME)])
    return vk_keyboard(rows)


def vk_report_station_keyboard(stations: list[dict]) -> str:
    """Список АЗС для выбора при отчёте."""
    rows = []
    for s in stations[:4]:
        name = (s.get("name") or "АЗС")[:20]
        rows.append([_button(f"#{s['id']} {name}", "primary")])
    rows.append([_button("🔍 Найти по адресу", "positive")])
    rows.append([_button("◀️ Назад", "secondary")])
    rows.append([_button(VK_BTN_HOME)])
    return vk_keyboard(rows)


def vk_report_address_results_keyboard(stations: list[dict]) -> str:
    """Список АЗС, найденных по адресу."""
    rows = []
    for s in stations[:5]:
        name = (s.get("name") or "АЗС")[:18]
        addr = (s.get("address") or "")[:15]
        label = f"#{s['id']} {name}"
        if addr:
            label += f" {addr}"
        rows.append([_button(label, "primary")])
    rows.append([_button("🔍 Найти другую", "positive")])
    rows.append([_button("◀️ Назад", "secondary")])
    rows.append([_button(VK_BTN_HOME)])
    return vk_keyboard(rows)


def vk_review_fuel_keyboard(station_id: int) -> str:
    """Выбор типа топлива для отзыва."""
    return vk_keyboard([
        [
            _button(f"⛽ 92 #{station_id}", "primary"),
            _button(f"⛽ 95 #{station_id}", "primary"),
        ],
        [
            _button(f"⛽ 98 #{station_id}", "secondary"),
            _button(f"🛢 ДТ #{station_id}", "secondary"),
        ],
        [_button("◀️ Отмена", "secondary")],
    ])


def vk_review_rating_keyboard(station_id: int, fuel: str) -> str:
    """Клавиатура для выбора рейтинга качества бензина (0-5 звёзд)."""
    return vk_keyboard([
        [
            _button(f"⭐⭐⭐⭐⭐ #{station_id}:{fuel}", "positive"),
            _button(f"⭐⭐⭐⭐ #{station_id}:{fuel}", "positive"),
        ],
        [
            _button(f"⭐⭐⭐ #{station_id}:{fuel}", "secondary"),
            _button(f"⭐⭐ #{station_id}:{fuel}", "secondary"),
        ],
        [
            _button(f"⭐ #{station_id}:{fuel}", "negative"),
            _button(f"Без звёзд #{station_id}:{fuel}", "secondary"),
        ],
        [_button("◀️ Назад", "secondary")],
    ])
