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


def _vkapp_button(label: str, app_id: int, hash: str = "", owner_id: int = 0) -> dict:
    """Кнопка VK Mini App — fallback на ссылку если не настоящий Mini App.

    Standalone-приложения VK не поддерживают type=open_app (ошибка
    "Приложение не инициализировано"). Используем open_link с прямым URL
    — приложение откроется во встроенном браузере VK, работает одинаково.
    """
    import os
    # Прямой URL приложения (работает в любом случае)
    direct_url = os.getenv("VK_MINI_APP_DIRECT_URL", "https://benzin-ryadom.onrender.com/v2")
    # Если задан VK_USE_OPEN_APP=1, используем нативный open_app (требует настоящий Mini App)
    if os.getenv("VK_USE_OPEN_APP", "").lower() in ("1", "true", "yes"):
        link = f"https://vk.com/app{app_id}"
        if hash:
            link += f"#{hash}"
        return {
            "action": {
                "type": "open_app",
                "label": label,
                "app_id": app_id,
                "owner_id": owner_id,
                "hash": hash,
            },
        }
    # По умолчанию — обычная ссылка (надёжный вариант)
    return _link_button(label, direct_url)


def _callback_button(label: str, payload: dict | str, color: str = "secondary") -> dict:
    """Callback-кнопка (отправляет message_event с payload).

    Требует Callback API. Нажатие НЕ создаёт новое сообщение,
    а показывает spinner; ответ через messages.sendMessageEventAnswer.

    payload: dict (сериализуется в JSON) или готовая JSON-строка.
    """
    if isinstance(payload, dict):
        payload_str = json.dumps(payload, ensure_ascii=False)
    else:
        payload_str = payload
    return {
        "action": {
            "type": "callback",
            "label": label,
            "payload": payload_str,
        },
        "color": color,
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
    """Главное меню VK — использует callback-кнопки для inline-навигации."""
    import os
    rows = [
        [
            _callback_button(VK_BTN_FIND, {"a": "find"}, "primary"),
            _callback_button(VK_BTN_REPORT, {"a": "report_start"}, "positive"),
        ],
    ]
    # Добавляем кнопку Mini App / ссылку на приложение
    app_id = os.getenv("VK_MINI_APP_ID", "")
    if app_id and app_id.isdigit():
        rows.append([_vkapp_button("📱 Открыть приложение", int(app_id))])
    rows.append([
        _callback_button(VK_BTN_SUBSCRIBE, {"a": "subscribe"}),
        _callback_button(VK_BTN_OWNER, {"a": "owner"}),
    ])
    rows.append([
        _callback_button(VK_BTN_PROFILE, {"a": "profile"}),
        _callback_button(VK_BTN_HELP, {"a": "help"}),
    ])
    return vk_keyboard(rows)


def vk_city_keyboard() -> str:
    """Выбор города — callback-кнопки с payload {a: "city", c: <name>}."""
    from keyboards import TOP_CITIES
    rows = []
    for i in range(0, min(len(TOP_CITIES), 8), 2):
        row = []
        for j in range(i, min(i + 2, len(TOP_CITIES))):
            name, _ = TOP_CITIES[j]
            row.append(_callback_button(
                f"📍 {name}",
                {"a": "city", "c": name},
                "primary",
            ))
        rows.append(row)
    rows.append([
        _callback_button("✏️ Другой город", {"a": "city_input"}, "secondary"),
        _callback_button("🏠 В начало", {"a": "home"}),
    ])
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
    """Действия с АЗС — callback-кнопки."""
    rows = []
    if lat and lon:
        yandex_url = f"https://yandex.ru/maps/?rtext={lat},{lon}&rtt=auto"
        rows.append([_link_button("🗺 Маршрут", yandex_url)])
    rows.append([
        _callback_button("📝 Отчёт", {"a": "report", "s": station_id}, "positive"),
        _callback_button("⭐ Отзыв", {"a": "review", "s": station_id}, "primary"),
    ])
    rows.append([
        _callback_button("🔔 Подписка", {"a": "sub_station", "s": station_id}, "primary"),
        _callback_button("◀️ Назад", {"a": "find"}, "secondary"),
    ])
    rows.append([
        _callback_button("🏠 В начало", {"a": "home"}),
    ])
    return vk_keyboard(rows)


def vk_fuel_type_keyboard(station_id: int) -> str:
    """Выбор типа топлива для отчёта."""
    return vk_keyboard([
        [
            _callback_button("⛽ АИ-92", {"a": "report_fuel", "s": station_id, "f": "92"}, "primary"),
            _callback_button("⛽ АИ-95", {"a": "report_fuel", "s": station_id, "f": "95"}, "primary"),
        ],
        [
            _callback_button("⛽ АИ-98", {"a": "report_fuel", "s": station_id, "f": "98"}, "secondary"),
            _callback_button("🛢 Дизель", {"a": "report_fuel", "s": station_id, "f": "diesel"}, "secondary"),
        ],
        [
            _callback_button("⛽ АИ-100", {"a": "report_fuel", "s": station_id, "f": "100"}, "secondary"),
            _callback_button("🔥 Газ", {"a": "report_fuel", "s": station_id, "f": "lpg"}, "secondary"),
        ],
        [
            _callback_button("◀️ Отмена", {"a": "station", "s": station_id}, "secondary"),
        ],
    ])


def vk_report_status_keyboard(station_id: int, fuel: str) -> str:
    """Статус наличия топлива."""
    return vk_keyboard([
        [
            _callback_button("✅ Есть", {"a": "report_status", "s": station_id, "f": fuel, "v": "yes"}, "positive"),
        ],
        [
            _callback_button("⚠️ Кончается", {"a": "report_status", "s": station_id, "f": fuel, "v": "low"}, "secondary"),
        ],
        [
            _callback_button("❌ Нет", {"a": "report_status", "s": station_id, "f": fuel, "v": "no"}, "negative"),
        ],
        [
            _callback_button("◀️ Назад", {"a": "report", "s": station_id}, "secondary"),
        ],
    ])


def vk_subscribe_geo_keyboard() -> str:
    """Кнопка отправки геолокации для подписки."""
    return vk_keyboard([
        [_location_button()],
        [_callback_button("◀️ Назад", {"a": "home"}, "secondary"),
         _callback_button("🏠 В начало", {"a": "home"})],
    ])


def vk_subscribe_radius_keyboard() -> str:
    """Выбор радиуса подписки."""
    return vk_keyboard([
        [
            _callback_button("3 км", {"a": "sub_radius", "r": 3}, "primary"),
            _callback_button("5 км", {"a": "sub_radius", "r": 5}, "primary"),
            _callback_button("10 км", {"a": "sub_radius", "r": 10}, "primary"),
        ],
        [_callback_button("🏠 В начало", {"a": "home"})],
    ])


VK_GROUP_ID = 239975253
VK_DONATE_URL = "https://vk.com/donut/benzyn_ryadom"


def vk_premium_keyboard() -> str:
    """Кнопки Premium — ссылка на VK Донат."""
    return vk_keyboard([
        [_link_button("💎 Поддержать 99₽", VK_DONATE_URL)],
        [_callback_button("◀️ Назад", {"a": "home"}, "secondary")],
    ])


def vk_donate_keyboard() -> str:
    """Кнопки доната — ссылка на VK Донат."""
    return vk_keyboard([
        [_link_button("☕ 50₽", VK_DONATE_URL)],
        [_link_button("⛽ 100₽", VK_DONATE_URL)],
        [_link_button("🔧 250₽", VK_DONATE_URL)],
        [_link_button("💎 500₽", VK_DONATE_URL)],
        [_link_button("👑 Шейх 10 000₽", VK_DONATE_URL)],
        [_callback_button("◀️ Назад", {"a": "home"}, "secondary")],
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
    """Список АЗС для выбора при отчёте — сеть + адрес."""
    rows = []
    for s in stations[:4]:
        operator = (s.get("operator") or "")[:12]
        address = (s.get("address") or "")[:15]
        if operator and address:
            label = f"#{s['id']} {operator} {address}"
        elif operator:
            label = f"#{s['id']} {operator}"
        elif address:
            label = f"#{s['id']} {address}"
        else:
            label = f"#{s['id']} {s.get('name', 'АЗС')}"
        rows.append([_button(label, "primary")])
    rows.append([_button("🔍 Найти по адресу", "positive")])
    rows.append([_button("◀️ Назад", "secondary")])
    rows.append([_button(VK_BTN_HOME)])
    return vk_keyboard(rows)


def vk_report_address_results_keyboard(stations: list[dict]) -> str:
    """Список АЗС, найденных по адресу — сеть + адрес."""
    rows = []
    for s in stations[:5]:
        operator = (s.get("operator") or "")[:12]
        address = (s.get("address") or "")[:15]
        if operator and address:
            label = f"#{s['id']} {operator} {address}"
        elif operator:
            label = f"#{s['id']} {operator}"
        elif address:
            label = f"#{s['id']} {address}"
        else:
            label = f"#{s['id']} {s.get('name', 'АЗС')}"
        rows.append([_button(label, "primary")])
    rows.append([_button("🔍 Найти другую", "positive")])
    rows.append([_button("◀️ Назад", "secondary")])
    rows.append([_button(VK_BTN_HOME)])
    return vk_keyboard(rows)


def vk_review_fuel_keyboard(station_id: int) -> str:
    """Выбор типа топлива для отзыва."""
    return vk_keyboard([
        [
            _callback_button("⛽ АИ-92", {"a": "review_fuel", "s": station_id, "f": "92"}, "primary"),
            _callback_button("⛽ АИ-95", {"a": "review_fuel", "s": station_id, "f": "95"}, "primary"),
        ],
        [
            _callback_button("⛽ АИ-98", {"a": "review_fuel", "s": station_id, "f": "98"}, "secondary"),
            _callback_button("🛢 Дизель", {"a": "review_fuel", "s": station_id, "f": "diesel"}, "secondary"),
        ],
        [
            _callback_button("⛽ АИ-100", {"a": "review_fuel", "s": station_id, "f": "100"}, "secondary"),
            _callback_button("🔥 Газ", {"a": "review_fuel", "s": station_id, "f": "lpg"}, "secondary"),
        ],
        [
            _callback_button("◀️ Отмена", {"a": "station", "s": station_id}, "secondary"),
        ],
    ])


def vk_review_rating_keyboard(station_id: int, fuel: str) -> str:
    """Клавиатура для выбора рейтинга качества бензина (1-5 звёзд)."""
    return vk_keyboard([
        [
            _callback_button("⭐⭐⭐⭐⭐", {"a": "review_rating", "s": station_id, "f": fuel, "r": 5}, "positive"),
            _callback_button("⭐⭐⭐⭐", {"a": "review_rating", "s": station_id, "f": fuel, "r": 4}, "positive"),
        ],
        [
            _callback_button("⭐⭐⭐", {"a": "review_rating", "s": station_id, "f": fuel, "r": 3}, "secondary"),
            _callback_button("⭐⭐", {"a": "review_rating", "s": station_id, "f": fuel, "r": 2}, "secondary"),
        ],
        [
            _callback_button("⭐", {"a": "review_rating", "s": station_id, "f": fuel, "r": 1}, "negative"),
        ],
        [
            _callback_button("◀️ Назад", {"a": "review", "s": station_id}, "secondary"),
        ],
    ])
