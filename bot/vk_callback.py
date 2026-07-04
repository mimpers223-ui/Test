"""
VK Callback API — обработка событий от сообщества через webhook.

Эндпоинт: /api/vk/callback (POST)

События:
  - confirmation: вернуть токен подтверждения
  - message_new: новое сообщение от пользователя
  - message_event: нажатие inline-кнопки (callback)

Все ответы отправляются через VK API напрямую.
"""
import asyncio
import json
import logging
import os
import time
from typing import Any

import aiohttp

from db import (
    find_nearest_stations,
    find_stations_by_address,
    find_stations_by_city,
    find_stations_by_name,
    get_or_create_user,
    get_premium_info,
    get_station_by_id,
    get_station_current_status,
    get_station_rating,
    get_user_id_by_telegram_id,
    is_premium,
    log_event,
    get_user_stats_summary,
    add_report,
    add_review,
    add_subscription,
    upsert_user,
)
from vk_keyboards import (
    VK_BTN_HOME,
    vk_main_menu,
    vk_city_keyboard,
    vk_fuel_type_keyboard,
    vk_report_status_keyboard,
    vk_subscribe_geo_keyboard,
    vk_subscribe_radius_keyboard,
    vk_station_actions,
    vk_review_fuel_keyboard,
    vk_review_rating_keyboard,
    vk_premium_keyboard,
    vk_donate_keyboard,
    _callback_button,
    _button,
    _link_button,
    _location_button,
    vk_keyboard,
)

logger = logging.getLogger("vk_callback")

VK_API_VERSION = "5.199"
USER_STATE_TTL = 1800  # 30 минут


# === State management с TTL ===
_user_state: dict[int, tuple[dict, float]] = {}
_vk_subscribe_cache: dict[int, tuple[bool, float]] = {}
_VK_SUBSCRIBE_TTL = 300  # 5 минут

# Event deduplication (чтобы не обработать одно и то же дважды)
_processed_events: dict[str, float] = {}
_EVENT_DEDUP_TTL = 60  # 1 минута


def _set_state(peer_id: int, state: dict) -> None:
    """Устанавливает состояние пользователя с TTL."""
    _user_state[peer_id] = (state, time.time() + USER_STATE_TTL)


def _get_state(peer_id: int) -> dict:
    """Получает состояние (None если истекло)."""
    entry = _user_state.get(peer_id)
    if not entry:
        return {}
    state, expires_at = entry
    if time.time() > expires_at:
        _user_state.pop(peer_id, None)
        return {}
    return state


def _clear_state(peer_id: int) -> None:
    _user_state.pop(peer_id, None)


def _cleanup_states() -> None:
    """Периодическая очистка истёкших state'ов."""
    now = time.time()
    expired = [pid for pid, (_, exp) in _user_state.items() if now > exp]
    for pid in expired:
        _user_state.pop(pid, None)
    if expired:
        logger.debug("Cleaned up %d expired user states", len(expired))


# === VK API wrapper ===
async def _vk_api_call(method: str, params: dict) -> dict:
    """Вызов метода VK API через aiohttp."""
    token = os.getenv("VK_TOKEN", "")
    if not token:
        return {"error": "no token"}
    params["access_token"] = token
    params["v"] = VK_API_VERSION
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.vk.com/method/{method}",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if "error" in data:
                    logger.warning("VK API %s error: %s", method, data["error"])
                return data
    except Exception as e:
        logger.warning("VK API %s failed: %s", method, e)
        return {"error": str(e)}


async def _vk_send(peer_id: int, text: str, keyboard: str | None = None) -> dict:
    """Отправляет сообщение пользователю."""
    params = {
        "peer_id": peer_id,
        "message": text,
        "random_id": int(time.time() * 1000) % (2**31),
    }
    if keyboard:
        params["keyboard"] = keyboard
    result = await _vk_api_call("messages.send", params)
    if "error" in result:
        logger.warning("VK messages.send error to peer=%d: %s", peer_id, result.get("error"))
    else:
        logger.info("VK messages.send OK to peer=%d (msg_id=%s)", peer_id, result.get("response"))
    return result


async def _vk_send_event_answer(event_id: str, user_id: int, peer_id: int,
                                 text: str = "", toast: str = "") -> bool:
    """Отвечает на message_event (callback) — обязательно в течение 5 сек.

    Возвращает True если успешно, False при ошибке.
    НЕ выбрасывает исключение — просто логирует.
    """
    params = {
        "event_id": event_id,
        "user_id": user_id,
        "peer_id": peer_id,
    }
    if text:
        params["text"] = text
    if toast:
        params["toast"] = toast
    result = await _vk_api_call("messages.sendMessageEventAnswer", params)
    if "error" in result:
        logger.warning("VK sendMessageEventAnswer error: %s | event_id=%r", result.get("error"), event_id)
        return False
    return True


async def _vk_edit_message(peer_id: int, conversation_message_id: int, text: str,
                            keyboard: str | None = None) -> dict:
    """Редактирует сообщение."""
    params = {
        "peer_id": peer_id,
        "conversation_message_id": conversation_message_id,
        "message": text,
    }
    if keyboard:
        params["keyboard"] = keyboard
    return await _vk_api_call("messages.edit", params)


# === Проверка подписки ===
async def _check_vk_subscription(user_id: int) -> bool:
    from config import settings
    now = time.time()
    cached = _vk_subscribe_cache.get(user_id)
    if cached and now - cached[1] < _VK_SUBSCRIBE_TTL:
        return cached[0]
    group_id = settings.SUBSCRIBE_COMMUNITY_VK
    if not group_id:
        return True
    try:
        data = await _vk_api_call("groups.isMember", {
            "group_id": group_id, "user_id": user_id,
        })
        is_sub = bool(data.get("response", 0))
    except Exception as e:
        logger.warning("subscription check failed: %s", e)
        is_sub = False
    _vk_subscribe_cache[user_id] = (is_sub, now)
    return is_sub


def _vk_subscribe_keyboard() -> str:
    return vk_keyboard([
        [_link_button("📢 Подписаться", "https://vk.com/benzyn_ryadom")],
        [_callback_button("✅ Я подписался", {"a": "check_sub"}, "positive")],
    ])


# === Helpers ===
async def _get_user_id(peer_id: int) -> int | None:
    """Получает внутренний user_id из peer_id (используем как telegram_id)."""
    return await get_user_id_by_telegram_id(peer_id)


async def _ensure_user(peer_id: int, first_name: str = "VK") -> int | None:
    """Создаёт/обновляет пользователя, возвращает user_id."""
    return await upsert_user(telegram_id=peer_id, first_name=first_name)


# === Text handlers ===
async def handle_start(peer_id: int) -> None:
    uid = await _ensure_user(peer_id)
    if uid:
        await log_event(uid, "vk_start")
    text = (
        "👋 Привет! Я — Бензин рядом.\n\n"
        "Помогу найти бензин за 5 секунд. 26 000+ АЗС в России.\n\n"
        "🟢 live · цены · очереди · push о завозе\n\n"
        "❤️ Если бот помог — поддержи проект:\n"
        "☕ 50₽ · ⛽ 100₽ · 🔧 250₽ · 💎 500₽ · 👑 Шейх 10 000₽\n"
        "👉 vk.com/donut/benzyn_ryadom\n\n"
        "👇 <b>Главное меню:</b>"
    )
    await _vk_send(peer_id, text, vk_main_menu())


async def handle_help(peer_id: int) -> None:
    text = (
        "ℹ️ <b>Команды</b>\n\n"
        "🔍 <b>Найти АЗС</b> — нажми кнопку или напиши город\n"
        "📝 <b>Сообщить</b> — отметь наличие топлива\n"
        "🔔 <b>Подписки</b> — push о завозе рядом\n"
        "👤 <b>Профиль</b> — репутация, бейджи\n"
        "🏪 <b>Владелец</b> — verified-бейдж\n"
        "💎 <b>Premium</b> — push без задержек\n\n"
        "💡 Напиши название АЗС, город или сеть — я покажу результат."
    )
    await _vk_send(peer_id, text, vk_main_menu())


async def handle_find(peer_id: int) -> None:
    _clear_state(peer_id)
    await _vk_send(
        peer_id,
        "📍 <b>Выбери населённый пункт</b>\n\n"
        "Иваново, Москва, СПб, и другие. "
        "Или напиши свой город в сообщении — бот найдёт АЗС.",
        vk_city_keyboard(),
    )


async def handle_subscribe(peer_id: int) -> None:
    _set_state(peer_id, {"awaiting": "subscribe_geo"})
    await _vk_send(
        peer_id,
        "🔔 <b>Подписка на уведомления о завозе.</b>\n\n"
        "Отправь геолокацию — буду присылать уведомления, когда "
        "в радиусе 5 км от тебя появится бензин.",
        vk_subscribe_geo_keyboard(),
    )


async def handle_profile(peer_id: int) -> None:
    uid = await _get_user_id(peer_id)
    if not uid:
        await _vk_send(peer_id, "Профиль не найден. Нажми 🏠 В начало.", vk_main_menu())
        return
    stats = await get_user_stats_summary(uid)
    if not stats:
        await _vk_send(peer_id, "Профиль не найден.", vk_main_menu())
        return
    text = (
        f"👤 <b>Твой профиль:</b>\n\n"
        f"🆔 VK ID: <code>{peer_id}</code>\n"
        f"📊 Репутация: <b>{stats.get('reputation', 0)}</b>/100\n"
        f"📝 Отчётов сделано: <b>{stats.get('total_reports', 0)}</b>\n"
        f"✅ Подтверждено: <b>{stats.get('confirmed_reports', 0)}</b>\n"
    )
    if stats.get("region") or stats.get("city"):
        loc = ", ".join(filter(None, [stats.get("city"), stats.get("region")]))
        text += f"📍 Регион: {loc}\n"
    if await is_premium(uid):
        text += "\n⭐ <b>Premium</b> — push без cooldown\n"
    badges = stats.get("badges", [])
    if badges:
        text += f"\n🏆 <b>Бейджи ({len(badges)}):</b>\n"
        for b in badges:
            text += f"  {b['emoji']} <b>{b['name']}</b> — {b['desc']}\n"
    else:
        text += "\n🎯 Сделай первый отчёт, чтобы получить бейдж 🥉 «Новичок»!"
    await _vk_send(peer_id, text, vk_main_menu())


async def handle_donate(peer_id: int) -> None:
    text = (
        "❤️ <b>Поддержать проект</b>\n\n"
        "Бот бесплатный. Сервер, парсеры, база данных — всё стоит денег.\n"
        "Любая сумма поможет:\n\n"
        "👉 vk.com/donut/benzyn_ryadom"
    )
    await _vk_send(peer_id, text, vk_donate_keyboard())


async def handle_owner_info(peer_id: int) -> None:
    """Информация о регистрации владельца."""
    text = (
        "🏪 <b>Регистрация владельца АЗС</b>\n\n"
        "Открой мини-приложение для регистрации — это быстрее и удобнее.\n\n"
        "📱 В мини-приложении:\n"
        "• Загрузка документов (ИНН, ОГРН)\n"
        "• Привязка Telegram/VK\n"
        "• Управление verified-бейджем\n\n"
        "👉 Или напиши в поддержку: vk.me/benzyn_ryadom"
    )
    kb = vk_keyboard([
        [_callback_button("📱 Открыть приложение", {"a": "open_app"})],
        [_callback_button("◀️ Назад", {"a": "home"}, "secondary")],
    ])
    await _vk_send(peer_id, text, kb)


# === Search ===
async def handle_text_search(peer_id: int, query: str) -> None:
    if not query or len(query) < 2:
        await _vk_send(peer_id, "Введи минимум 2 символа.", vk_main_menu())
        return
    # 1) Сначала пробуем как город
    stations = await find_stations_by_city(city=query, has_stock=False, limit=5)
    if not stations:
        # 2) Как название/сеть
        stations = await find_stations_by_name(query, limit=5)
    if not stations:
        # 3) Как адрес
        stations = await find_stations_by_address(query, limit=5)
    if not stations:
        await _vk_send(peer_id, f"😔 По «{query}» ничего не нашёл.", vk_main_menu())
        return
    await show_station(peer_id, stations[0])


async def show_station(peer_id: int, station: dict) -> None:
    """Показывает детали АЗС."""
    from utils import format_station_card
    sid = station.get("id")
    if not sid:
        return
    statuses = await get_station_current_status(sid)
    text = format_station_card(station, statuses)
    await _vk_send(peer_id, text[:4000], vk_station_actions(
        sid, lat=station.get("lat"), lon=station.get("lon"),
    ))


# === Report flow ===
async def handle_report_start(peer_id: int) -> None:
    """Начало flow отчёта — выбрать АЗС."""
    _set_state(peer_id, {"flow": "report", "step": "choose_station"})
    await _vk_send(
        peer_id,
        "📝 <b>Сообщить о наличии топлива</b>\n\n"
        "1️⃣ Напиши название АЗС, сеть или адрес\n"
        "2️⃣ Выбери АЗС из списка\n"
        "3️⃣ Укажи тип топлива и статус\n\n"
        "💡 Можно просто отправить геолокацию!",
        vk_city_keyboard(),
    )


async def handle_report_fuel(peer_id: int, station_id: int, fuel: str) -> None:
    """Шаг: выбрано топливо → спрашиваем статус."""
    _set_state(peer_id, {"flow": "report", "step": "status", "station_id": station_id, "fuel": fuel})
    fuel_name = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100",
                 "diesel": "Дизель", "lpg": "Газ"}.get(fuel, fuel)
    await _vk_send(
        peer_id,
        f"📝 <b>Отчёт для #{station_id}</b>\n\n"
        f"Топливо: <b>{fuel_name}</b>\n\n"
        f"Какой статус?",
        vk_report_status_keyboard(station_id, fuel),
    )


async def handle_report_status(peer_id: int, station_id: int, fuel: str, value: str) -> None:
    """Шаг: выбран статус → сохраняем отчёт."""
    avail = {"yes": True, "low": None, "no": False}.get(value, None)
    try:
        await add_report(
            station_id=station_id,
            fuel_type=fuel,
            available=avail,
            source="vk_user",
        )
    except Exception as e:
        logger.warning("add_report failed: %s", e)
    state = _get_state(peer_id)
    user_id = state.get("user_id") or await _get_user_id(peer_id)
    if user_id:
        await log_event(user_id, "vk_report")
    fuel_name = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100",
                 "diesel": "Дизель", "lpg": "Газ"}.get(fuel, fuel)
    status_text = {"yes": "✅ Есть", "low": "⚠️ Кончается", "no": "❌ Нет"}.get(value, "?")
    await _vk_send(
        peer_id,
        f"✅ <b>Спасибо! Отчёт записан.</b>\n\n"
        f"АЗС #{station_id}, {fuel_name}: {status_text}\n\n"
        f"Твой вклад помогает другим водителям!",
        vk_main_menu(),
    )
    _clear_state(peer_id)


# === Subscribe to station ===
async def handle_subscribe_station(peer_id: int, station_id: int) -> None:
    """Подписаться на завоз конкретной АЗС."""
    user_id = await _get_user_id(peer_id)
    if not user_id:
        await _vk_send(peer_id, "Сначала нажми /start", vk_main_menu())
        return
    try:
        await add_subscription(
            user_id=user_id,
            kind="station",
            target_id=station_id,
        )
    except Exception as e:
        logger.warning("add_subscription failed: %s", e)
    await _vk_send(
        peer_id,
        f"🔔 <b>Подписка оформлена</b>\n\n"
        f"АЗС #{station_id} — будем присылать уведомления о завозе топлива.\n\n"
        f"💎 Premium: push без задержек (5₽ через VK Донат)",
        vk_station_actions(station_id),
    )


# === Review flow ===
async def handle_review_start(peer_id: int, station_id: int) -> None:
    """Начало отзыва — выбрать тип топлива."""
    _set_state(peer_id, {"flow": "review", "step": "fuel", "station_id": station_id})
    await _vk_send(
        peer_id,
        f"⭐ <b>Оценить качество топлива</b>\n\n"
        f"АЗС #{station_id}\n\n"
        f"Какое топливо оцениваем?",
        vk_review_fuel_keyboard(station_id),
    )


async def handle_review_fuel(peer_id: int, station_id: int, fuel: str) -> None:
    """Шаг: выбрано топливо → выбрать рейтинг."""
    _set_state(peer_id, {"flow": "review", "step": "rating", "station_id": station_id, "fuel": fuel})
    fuel_name = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100",
                 "diesel": "Дизель", "lpg": "Газ"}.get(fuel, fuel)
    await _vk_send(
        peer_id,
        f"⭐ <b>Оценка качества</b>\n\n"
        f"АЗС #{station_id}, {fuel_name}\n\n"
        f"Сколько звёзд?",
        vk_review_rating_keyboard(station_id, fuel),
    )


async def handle_review_rating(peer_id: int, station_id: int, fuel: str, rating: int) -> None:
    """Сохраняет отзыв."""
    user_id = await _get_user_id(peer_id)
    if not user_id:
        await _vk_send(peer_id, "Сначала нажми /start", vk_main_menu())
        return
    try:
        await add_review(
            station_id=station_id,
            user_id=user_id,
            fuel_type=fuel,
            rating=rating,
        )
    except Exception as e:
        logger.warning("add_review failed: %s", e)
    await _vk_send(
        peer_id,
        f"✅ <b>Спасибо за отзыв!</b>\n\n"
        f"АЗС #{station_id}, {fuel}: {'⭐' * rating}",
        vk_station_actions(station_id),
    )
    _clear_state(peer_id)


# === Geo handler ===
async def handle_geo(peer_id: int, geo: dict) -> None:
    """Обрабатывает геолокацию."""
    state = _get_state(peer_id)
    if state.get("awaiting") == "subscribe_geo":
        # Подписка на уведомления
        lat = geo.get("coordinates", {}).get("latitude") or geo.get("latitude")
        lon = geo.get("coordinates", {}).get("longitude") or geo.get("longitude")
        if not lat or not lon:
            await _vk_send(peer_id, "⚠️ Не удалось получить координаты.", vk_subscribe_geo_keyboard())
            return
        user_id = await _get_user_id(peer_id)
        if user_id:
            try:
                await add_subscription(
                    user_id=user_id,
                    kind="geo",
                    lat=lat,
                    lon=lon,
                    radius_km=5,
                )
            except Exception as e:
                logger.warning("add_subscription geo failed: %s", e)
        await _vk_send(
            peer_id,
            f"🔔 <b>Подписка оформлена!</b>\n\n"
            f"Координаты: {lat:.4f}, {lon:.4f}\n"
            f"Радиус: 5 км\n\n"
            f"Будем присылать push о завозе в этом районе.",
            vk_main_menu(),
        )
        _clear_state(peer_id)
        return
    # Иначе — ищем ближайшие АЗС
    lat = geo.get("coordinates", {}).get("latitude") or geo.get("latitude")
    lon = geo.get("coordinates", {}).get("longitude") or geo.get("longitude")
    if not lat or not lon:
        await _vk_send(peer_id, "⚠️ Не удалось получить координаты.", vk_main_menu())
        return
    stations = await find_nearest_stations(lat=lat, lon=lon, radius_km=10, limit=5)
    if not stations:
        await _vk_send(peer_id, "😔 Рядом АЗС не найдено.", vk_main_menu())
        return
    # Берём ближайшую
    nearest = stations[0]
    dist = nearest.get("distance_km", 0)
    text = (
        f"📍 <b>Ближайшая АЗС ({dist:.1f} км):</b>\n\n"
    )
    op = nearest.get("operator") or nearest.get("name") or "АЗС"
    addr = nearest.get("address") or ""
    text += f"⛽ <b>{op}</b>\n"
    if addr:
        text += f"📍 {addr}\n"
    statuses = await get_station_current_status(nearest.get("id"))
    if statuses:
        text += "\n<b>Наличие:</b>\n"
        for s in statuses[:5]:
            ft = s.get("fuel_type")
            av = s.get("available")
            pr = s.get("price")
            av_text = "✅" if av is True else "❌" if av is False else "⚠️"
            pr_text = f" · {pr:.2f}₽" if pr else ""
            text += f"  {av_text} {ft.upper()}{pr_text}\n"
    await _vk_send(peer_id, text, vk_station_actions(
        nearest.get("id"),
        lat=nearest.get("lat"),
        lon=nearest.get("lon"),
    ))


# === Message router ===
async def process_message_new(event: dict) -> None:
    """Обрабатывает message_new от VK Callback API."""
    msg = event.get("object", {}).get("message", {})
    if not msg:
        return
    peer_id = msg.get("peer_id", 0)
    if not peer_id or peer_id < 0:
        return  # групповые чаты игнорируем

    # Дедупликация
    msg_id = str(msg.get("id", ""))
    if msg_id:
        last_seen = _processed_events.get(f"msg:{msg_id}", 0)
        if time.time() - last_seen < _EVENT_DEDUP_TTL:
            return
        _processed_events[f"msg:{msg_id}"] = time.time()

    text = (msg.get("text") or "").strip()
    geo = msg.get("geo")
    has_attachments = bool(msg.get("attachments"))

    if not text and not geo and not has_attachments:
        return
    logger.info("[vk-cb] peer=%d text=%r geo=%s", peer_id, text[:50], bool(geo))

    # Регистрация пользователя
    user_info = msg.get("from") or {}
    first_name = user_info.get("first_name", "VK")
    await _ensure_user(peer_id, first_name)

    # Проверка подписки (пропускаем /start)
    if text.lower() not in ("/start", "start", "начать"):
        is_sub = await _check_vk_subscription(peer_id)
        if not is_sub:
            await _vk_send(peer_id,
                "📢 <b>Подпишись на сообщество, чтобы пользоваться ботом!</b>\n\n"
                "Бот бесплатный. Взамен — подпишись на наше сообщество с новостями о топливе.",
                _vk_subscribe_keyboard())
            return

    # Geo
    if geo:
        await handle_geo(peer_id, geo)
        return

    # Текстовые команды
    low = text.lower()
    if low in ("/start", "start", "начать"):
        await handle_start(peer_id)
    elif low in ("/help", "help", "помощь"):
        await handle_help(peer_id)
    elif low in ("/find", "find", "искать"):
        await handle_find(peer_id)
    elif low in ("/subscribe", "subscribe", "подписаться"):
        await handle_subscribe(peer_id)
    elif low in ("/profile", "profile", "профиль"):
        await handle_profile(peer_id)
    elif low in ("/donate", "donate", "донат", "поддержать"):
        await handle_donate(peer_id)
    elif low in ("/owner", "owner", "владелец", "я владелец"):
        await handle_owner_info(peer_id)
    elif low in ("/home", "home", "в начало", "главное меню"):
        _clear_state(peer_id)
        await _vk_send(peer_id, "Главное меню:", vk_main_menu())
    elif low in ("/menu", "menu"):
        _clear_state(peer_id)
        await _vk_send(peer_id, "Главное меню:", vk_main_menu())
    else:
        # Контекстный ввод
        state = _get_state(peer_id)
        if state.get("awaiting") == "city_input":
            _clear_state(peer_id)
            await handle_text_search(peer_id, text)
        elif state.get("flow") == "report" and state.get("step") == "choose_station":
            # Поиск АЗС для отчёта
            stations = await find_stations_by_name(text, limit=5)
            if not stations:
                stations = await find_stations_by_address(text, limit=5)
            if not stations:
                await _vk_send(peer_id, f"😔 По «{text}» АЗС не найдено.", vk_main_menu())
                return
            # Показываем первую
            await show_station(peer_id, stations[0])
        else:
            await handle_text_search(peer_id, text)


# === Callback event router ===
async def process_message_event(event: dict) -> None:
    """Обрабатывает message_event (нажатие inline-кнопки)."""
    obj = event.get("object", {})

    # ДИАГНОСТИКА: логируем весь object чтобы понять структуру
    logger.info("VK msg_event raw object: %s", json.dumps(obj, ensure_ascii=False)[:500])

    peer_id = obj.get("peer_id", 0)
    user_id = obj.get("user_id", 0)
    event_id = obj.get("event_id", "")
    payload_str = obj.get("payload", "")
    conversation_msg_id = obj.get("conversation_message_id", 0)

    if not peer_id or not event_id:
        logger.warning("VK msg_event: missing peer_id or event_id. obj=%s", obj)
        return

    # Дедупликация по event_id
    if event_id in _processed_events:
        return
    _processed_events[event_id] = time.time()
    # Очистка старых
    now = time.time()
    for k in list(_processed_events.keys()):
        if not k.startswith("msg:") and now - _processed_events[k] > _EVENT_DEDUP_TTL * 2:
            _processed_events.pop(k, None)

    # Парсим payload (может быть строкой или dict)
    payload = {}
    if isinstance(payload_str, dict):
        payload = payload_str
    elif isinstance(payload_str, str) and payload_str:
        try:
            payload = json.loads(payload_str)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("VK msg_event: failed to parse payload=%r: %s", payload_str, e)
            payload = {}
    action = payload.get("a", "")
    logger.info("[vk-cb-evt] peer=%d action=%r payload=%r", peer_id, action, payload)

    # Регистрация пользователя
    await _ensure_user(peer_id)

    # Сразу отвечаем (event_answer обязателен в течение 5 сек)
    # Если ответ не пройдёт — не страшно, главное отправить сообщение
    ack_ok = await _vk_send_event_answer(
        event_id, user_id, peer_id,
        toast="⏳",
    )
    logger.info("[vk-cb-evt] peer=%d action=%r ack_ok=%s", peer_id, action, ack_ok)

    # === Роутер по action ===
    logger.info("[vk-cb-router] entering router with action=%r", action)
    if action == "home":
        _clear_state(peer_id)
        await _vk_send(peer_id, "Главное меню:", vk_main_menu())

    elif action == "find":
        logger.info("[vk-cb-router] calling handle_find for peer=%d", peer_id)
        await handle_find(peer_id)

    elif action == "help":
        await handle_help(peer_id)

    elif action == "profile":
        await handle_profile(peer_id)

    elif action == "donate":
        await handle_donate(peer_id)

    elif action == "owner":
        await handle_owner_info(peer_id)

    elif action == "subscribe":
        await handle_subscribe(peer_id)

    elif action == "check_sub":
        # Принудительная перепроверка подписки
        _vk_subscribe_cache.pop(peer_id, None)
        is_sub = await _check_vk_subscription(peer_id)
        if is_sub:
            await _vk_send(peer_id, "✅ Спасибо! Подписка подтверждена.", vk_main_menu())
        else:
            await _vk_send(peer_id, "❌ Не вижу подписки. Подпишись и нажми ещё раз.", _vk_subscribe_keyboard())

    elif action == "city":
        city = payload.get("c", "")
        if city:
            _clear_state(peer_id)
            await handle_text_search(peer_id, city)

    elif action == "city_input":
        _set_state(peer_id, {"awaiting": "city_input"})
        await _vk_send(peer_id, "✏️ Напиши название города:", vk_main_menu())

    elif action == "report_start":
        await handle_report_start(peer_id)

    elif action == "report":
        station_id = payload.get("s")
        if station_id:
            await handle_report_fuel(peer_id, int(station_id), "")

    elif action == "report_fuel":
        station_id = payload.get("s")
        fuel = payload.get("f")
        if station_id and fuel:
            await handle_report_fuel(peer_id, int(station_id), fuel)

    elif action == "report_status":
        station_id = payload.get("s")
        fuel = payload.get("f")
        value = payload.get("v")
        if station_id and fuel and value:
            await handle_report_status(peer_id, int(station_id), fuel, value)

    elif action == "review":
        station_id = payload.get("s")
        if station_id:
            await handle_review_start(peer_id, int(station_id))

    elif action == "review_fuel":
        station_id = payload.get("s")
        fuel = payload.get("f")
        if station_id and fuel:
            await handle_review_fuel(peer_id, int(station_id), fuel)

    elif action == "review_rating":
        station_id = payload.get("s")
        fuel = payload.get("f")
        rating = payload.get("r")
        if station_id and fuel and rating:
            await handle_review_rating(peer_id, int(station_id), fuel, int(rating))

    elif action == "sub_station":
        station_id = payload.get("s")
        if station_id:
            await handle_subscribe_station(peer_id, int(station_id))

    elif action == "sub_radius":
        radius = payload.get("r", 5)
        # Обновляем radius последней geo-подписки
        user_id = await _get_user_id(peer_id)
        if user_id:
            try:
                from db import _execute
                if db.USE_SQLITE:
                    await _execute(
                        "UPDATE subscriptions SET radius_km = ? WHERE user_id = ? AND kind = 'geo' ORDER BY id DESC LIMIT 1",
                        radius, user_id,
                    )
                else:
                    async with db._db.acquire() as conn:
                        await conn.execute(
                            "UPDATE subscriptions SET radius_km = $1 WHERE user_id = $2 AND kind = 'geo' ORDER BY id DESC LIMIT 1",
                            radius, user_id,
                        )
            except Exception as e:
                logger.warning("update sub radius: %s", e)
        await _vk_send(peer_id, f"✅ Радиус подписки обновлён: {radius} км", vk_main_menu())

    elif action == "station":
        # Возврат к карточке АЗС
        station_id = payload.get("s")
        if station_id:
            station = await get_station_by_id(int(station_id))
            if station:
                await show_station(peer_id, station)

    elif action == "open_app":
        # Открыть приложение (отправляем ссылку, т.к. open_link работает надёжнее open_app)
        import os
        direct_url = os.getenv("VK_MINI_APP_DIRECT_URL", "https://benzin-ryadom.onrender.com/v2")
        await _vk_send(peer_id, f"👉 Открой приложение:\n{direct_url}", vk_main_menu())

    else:
        logger.warning("Unknown action: %r", action)
