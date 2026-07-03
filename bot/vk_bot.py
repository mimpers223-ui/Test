"""
VK-бот «Бензин рядом» — полная копия Telegram-бота на vkbottle.
Запускается параллельно с TG-ботом.
"""
import json
import logging
import time
import re
import os

from vkbottle import Bot
from vkbottle.bot import Message

from config import settings
from db import (
    add_owner_station,
    add_report,
    add_review,
    add_subscription,
    find_nearest_stations,
    find_stations_by_address,
    find_stations_by_city,
    find_stations_by_name,
    get_or_create_user,
    get_owner_stations,
    get_premium_info,
    get_station_by_id,
    get_station_current_status,
    get_station_rating,
    get_stations_with_statuses,
    get_user_id_by_telegram_id,
    is_owner_of_station,
    is_premium,
    log_event,
    get_promoted_station_ids,
    get_user_stats_summary,
    get_stats,
    upsert_user,
)
from utils import format_distance, format_station_card
from vk_keyboards import (
    VK_BTN_HOME,
    vk_main_menu,
    vk_city_keyboard,
    vk_filters_keyboard,
    vk_station_actions,
    vk_fuel_type_keyboard,
    vk_report_status_keyboard,
    vk_subscribe_geo_keyboard,
    vk_subscribe_radius_keyboard,
    vk_report_city_keyboard,
    vk_report_station_keyboard,
    vk_report_address_results_keyboard,
    vk_review_fuel_keyboard,
    vk_review_rating_keyboard,
    vk_premium_keyboard,
    vk_donate_keyboard,
    _button,
    _link_button,
    vk_keyboard,
)

logger = logging.getLogger(__name__)

MAX_INLINE_ROWS = 6

_user_state: dict[int, dict] = {}
_owner_waiting_search: set[int] = set()
_owner_waiting_role: dict[int, int] = {}
_owner_waiting_inn: set[int] = set()
_owner_state_data: dict[int, dict] = {}

# Кеш проверки подписки VK
_vk_subscribe_cache: dict[int, tuple[bool, float]] = {}
_VK_SUBSCRIBE_TTL = 300  # 5 минут


async def _check_vk_subscription(user_id: int, api) -> bool:
    """Проверяет, подписан ли пользователь на сообщество VK."""
    import time
    now = time.time()
    cached = _vk_subscribe_cache.get(user_id)
    if cached and now - cached[1] < _VK_SUBSCRIBE_TTL:
        return cached[0]

    group_id = settings.SUBSCRIBE_COMMUNITY_VK
    if not group_id:
        logger.warning("_check_vk_subscription: SUBSCRIBE_COMMUNITY_VK is 0! Skipping check.")
        return True

    try:
        import aiohttp
        token = os.getenv("VK_TOKEN", "")
        url = "https://api.vk.com/method/groups.isMember"
        params = {"group_id": group_id, "user_id": user_id, "access_token": token, "v": "5.199"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                is_sub = bool(data.get("response", 0))
        logger.info("_check_vk_subscription: user=%d group=%s is_sub=%s", user_id, group_id, is_sub)
    except Exception as e:
        logger.warning("_check_vk_subscription FAILED: user=%d group=%s error=%s", user_id, group_id, e)
        is_sub = False

    _vk_subscribe_cache[user_id] = (is_sub, now)
    return is_sub


def _vk_subscribe_keyboard() -> str:
    """Клавиатура «Подпишись чтобы продолжить» для VK."""
    return vk_keyboard([
        [_link_button("📢 Подписаться", "https://vk.com/benzyn_ryadom")],
        [_button("✅ Я подписался", "positive")],
    ])


def _uid(msg: Message) -> int:
    return msg.peer_id


_cache: dict[tuple, tuple[float, list]] = {}
CACHE_TTL = 60


def _cache_get(lat: float, lon: float, radius_km: int) -> list | None:
    key = (round(lat, 2), round(lon, 2), radius_km)
    entry = _cache.get(key)
    if not entry:
        return None
    ts, results = entry
    if time.time() - ts > CACHE_TTL:
        _cache.pop(key, None)
        return None
    return results


def _cache_set(lat: float, lon: float, radius_km: int, results: list) -> None:
    key = (round(lat, 2), round(lon, 2), radius_km)
    _cache[key] = (time.time(), results)


def _limit_rows(rows: list, max_rows: int = MAX_INLINE_ROWS) -> list:
    return rows[:max_rows]


def _truncate(text: str, limit: int = 4090) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n... (обрезано)"


def _vk_text(text: str) -> str:
    """Strip HTML tags — VK doesn't render them."""
    return re.sub(r"<[^>]+>", "", text)


async def _ensure_user(msg: Message) -> int | None:
    uid = _uid(msg)
    try:
        return await get_or_create_user(msg)
    except Exception as e:
        logger.warning(f"_ensure_user failed: {e}")
        return None


async def _send(msg: Message, text: str, keyboard: str | None = None):
    kwargs = {"message": _truncate(_vk_text(text))}
    if keyboard:
        kwargs["keyboard"] = keyboard
    await msg.answer(**kwargs)


def _get_main_status_icon(statuses: list) -> str:
    if not statuses:
        return "❓"
    for fuel in ["92", "95", "98", "diesel"]:
        for st in statuses:
            if st.get("fuel_type") == fuel:
                avail = st.get("available")
                if avail is True:
                    return "✅"
                if avail is False:
                    return "❌"
                return "⚠️"
    for st in statuses:
        if st.get("fuel_type") == "all":
            continue
        avail = st.get("available")
        if avail is True:
            return "✅"
        if avail is False:
            return "❌"
        return "⚠️"
    return "❓"


def _sort_key(s, promoted_ids):
    statuses = s.get("statuses") or []
    non_all = [st for st in statuses if st.get("fuel_type") != "all"]
    has_available = any(st.get("available") is True for st in non_all)
    has_low = any(st.get("available") is None for st in non_all)
    has_unavailable = any(st.get("available") is False for st in non_all)
    if has_available:
        avail_rank = 0
    elif has_low:
        avail_rank = 1
    elif has_unavailable:
        avail_rank = 2
    else:
        avail_rank = 3
    return (
        0 if s["id"] in promoted_ids else 1,
        avail_rank,
        0 if s.get("is_verified") else 1,
        (s.get("name") or "").lower(),
    )


# ====================================================================
# HANDLERS
# ====================================================================

async def cmd_start(msg: Message):
    uid = await _ensure_user(msg)
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
    await _send(msg, text, vk_main_menu())


async def cmd_help(msg: Message):
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
    await _send(msg, text, vk_main_menu())


async def cmd_find(msg: Message):
    logger.info("cmd_find peer_id=%s", msg.peer_id)
    await _send(
        msg,
        "📍 <b>Выбери населённый пункт</b>\n\n"
        "Иваново, Москва, СПб, и другие. "
        "Или напиши свой город в сообщении — бот найдёт АЗС.",
        vk_city_keyboard(),
    )


async def cmd_subscribe(msg: Message):
    logger.info("cmd_subscribe peer_id=%s", msg.peer_id)
    _user_state[_uid(msg)] = {"awaiting": "subscribe_geo"}
    await _send(
        msg,
        "🔔 <b>Подписка на уведомления о завозе.</b>\n\n"
        "Отправь геолокацию — буду присылать уведомления, когда "
        "в радиусе 5 км от тебя появится бензин.",
        vk_subscribe_geo_keyboard(),
    )


async def cmd_profile(msg: Message):
    uid = await _ensure_user(msg)
    if not uid:
        await _send(msg, "Профиль не найден. Нажми «🏠 В начало».", vk_main_menu())
        return
    vk_id = _uid(msg)
    stats = await get_user_stats_summary(uid)
    if not stats:
        await _send(msg, "Профиль не найден.", vk_main_menu())
        return
    text = (
        f"👤 <b>Твой профиль:</b>\n\n"
        f"🆔 VK ID: <code>{vk_id}</code>\n"
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
    await _send(msg, text, vk_main_menu())


async def cmd_register_owner(msg: Message):
    uid = _uid(msg)
    _owner_waiting_search.add(uid)
    await _send(
        msg,
        "👋 <b>Регистрация владельца или работника АЗС.</b>\n\n"
        "Введи название, адрес или город АЗС.\n\n"
        "<i>Например: Лукойл Иваново, Ленина 45.</i>",
        vk_main_menu(),
    )


async def cmd_my_stations(msg: Message):
    uid = await _ensure_user(msg)
    if not uid:
        await _send(msg, "Сначала нажми «🏠 В начало»", vk_main_menu())
        return
    stations = await get_owner_stations(uid)
    if not stations:
        await _send(
            msg,
            "ℹ️ Ты не зарегистрирован как владелец АЗС.\n\nНажми «👤 Я владелец».",
            vk_main_menu(),
        )
        return
    text = "🏪 <b>Твои АЗС:</b>\n\n"
    rows = []
    for s in stations[:5]:
        name = (s.get("name") or "АЗС")[:25]
        verified = " ✓" if s.get("is_verified") else ""
        role_icon = "👑" if s.get("role") == "owner" else "👨\u200d🔧"
        label = f"{role_icon} {name}{verified}"
        rows.append([_button(label[:40], "primary", {"cmd": "mystation", "id": s["station_id"]})])
    rows.append([_button("🏠 В начало", "secondary", {"cmd": "home"})])
    text += f"Всего: {len(stations)}."
    await _send(msg, text, vk_keyboard(rows, inline=False))


async def cmd_premium(msg: Message):
    uid = await _ensure_user(msg)
    if uid and await is_premium(uid):
        info = await get_premium_info(uid)
        if info:
            from datetime import datetime
            days_left = (info["expires_at"] - datetime.now()).days if isinstance(info["expires_at"], datetime) else 30
            text = (
                f"💎 <b>Premium активен</b>\n\n"
                f"📅 Осталось дней: <b>{max(days_left, 0)}</b>\n\n"
                f"🔔 Push о завозе — каждый час\n"
                f"💎 Premium-бейдж\n"
            )
            await _send(msg, text, vk_main_menu())
            return
    text = (
        f"💎 Бензин рядом · Premium\n\n"
        f"🔔 Push о завозе — каждый час\n"
        f"💎 Premium-бейдж\n"
        f"📊 Расширенная аналитика\n\n"
        f"💰 Цена: 99₽\n\n"
        f"👇 Нажми кнопку ниже для поддержки:"
    )
    await _send(msg, text, vk_premium_keyboard())


async def cmd_donate(msg: Message):
    text = (
        "❤️ <b>Поддержать «Бензин рядом»</b>\n\n"
        "Проект бесплатный и работает на энтузиазме.\n\n"
        "💰 Нажми кнопку ниже:"
    )
    await _send(msg, text, vk_donate_keyboard())


async def cmd_stats(msg: Message):
    stats = await get_stats()
    text = (
        "📊 <b>Статистика:</b>\n\n"
        f"⛽ АЗС: <b>{stats.get('stations_count', 0):,}</b>\n"
        f"👥 Пользователей: <b>{stats.get('users_count', 0):,}</b>\n"
        f"📝 Отчётов за 24ч: <b>{stats.get('reports_24h', 0):,}</b>\n"
    )
    await _send(msg, text, vk_main_menu())


async def handle_geo_location(msg: Message):
    uid = _uid(msg)
    state = _user_state.get(uid, {})
    geo = msg.geo
    if not geo:
        await _send(msg, "⚠️ Не удалось определить координаты.", vk_main_menu())
        return
    lat = geo.coordinates.latitude
    lon = geo.coordinates.longitude
    if state.get("awaiting") == "subscribe_geo":
        _user_state[uid] = {"awaiting": "subscribe_radius", "lat": lat, "lon": lon}
        await _send(
            msg,
            f"📍 {lat:.4f}, {lon:.4f}\n\nВыбери радиус уведомлений:",
            vk_subscribe_radius_keyboard(),
        )
        return
    await _do_find_by_geo(msg, lat, lon)


async def _do_find_by_geo(msg: Message, lat: float, lon: float):
    cached = _cache_get(lat, lon, 30)
    stations = cached if cached is not None else await find_nearest_stations(lat=lat, lon=lon, limit=10, radius_km=30)
    if cached is None:
        _cache_set(lat, lon, 30, stations)
    if not stations:
        await _send(msg, "😔 <b>Рядом не нашёл АЗС.</b>\n\nПопробуй написать город.", vk_main_menu())
        return
    stations = await get_stations_with_statuses(stations)
    text = f"🔍 <b>Нашёл {len(stations)} АЗС рядом:</b>\n\n"
    rows = []
    for s in stations:
        statuses = s.get("statuses", [])
        dist = format_distance(s.get("distance_km", 0))
        icon = _get_main_status_icon(statuses)
        name = (s.get("name") or "АЗС")[:22]
        rows.append([_button(f"{icon} #{s['id']} {name} • {dist}"[:30], "primary")])
    rows.append([_button("🏠 В начало", "secondary")])
    await _send(msg, text, vk_keyboard(_limit_rows(rows), inline=False))


async def _show_station_list_from_msg(msg: Message, city: str, fuel=None):
    try:
        stations = await find_stations_by_city(city=city, fuel_type=fuel, has_stock=False, limit=20)
        if not stations:
            await _send(msg, f"🔍 В {city} ничего не найдено.", vk_main_menu())
            return
        stations_with_status = await get_stations_with_statuses(stations)
        promoted_ids = set(await get_promoted_station_ids(city) or [])
        stations_with_status.sort(key=lambda s: _sort_key(s, promoted_ids))
        title = f"⛽ <b>{city}</b> — {len(stations_with_status)} АЗС\n"
        rows = []
        for s in stations_with_status[:5]:
            statuses = s.get("statuses", [])
            name = (s.get("name") or "АЗС")[:22]
            icon = _get_main_status_icon(statuses)
            rows.append([_button(f"{icon} #{s['id']} {name}"[:40], "primary")])
        rows.append([_button("🚨 Экстренный", "negative")])
        rows.append([_button("🏠 В начало", "secondary")])
        await _send(msg, title, vk_keyboard(_limit_rows(rows), inline=False))
    except Exception as e:
        logger.exception(f"_show_station_list_from_msg: {e}")
        await _send(msg, "⚠️ Ошибка загрузки", vk_main_menu())


async def _do_text_search(msg: Message, query: str):
    uid = await _ensure_user(msg)
    if uid:
        await log_event(uid, "vk_text_search", {"query": query})
    stations = await find_stations_by_name(query, limit=5)
    if not stations:
        await _send(
            msg,
            f"😔 По <b>«{query}»</b> ничего не нашёл.\n\nПопробуй по-другому или 📍 геолокацию.",
            vk_main_menu(),
        )
        return
    stations = await get_stations_with_statuses(stations)
    text = f"🔍 По <b>«{query}»</b> — {len(stations)} АЗС:\n\n"
    rows = []
    for s in stations:
        statuses = s.get("statuses", [])
        icon = _get_main_status_icon(statuses)
        name = (s.get("name") or "АЗС")[:25]
        city = (s.get("city") or "")[:12]
        btn_text = f"{icon} #{s['id']} {name}" + (f" • {city}" if city else "")
        rows.append([_button(btn_text[:40], "primary")])
    rows.append([_button("🏠 В начало", "secondary")])
    await _send(msg, text, vk_keyboard(_limit_rows(rows), inline=False))


async def _owner_search_handler(msg: Message, query: str):
    uid = _uid(msg)
    _owner_waiting_search.discard(uid)
    stations = await find_stations_by_name(query, limit=5)
    if not stations:
        await _send(msg, f"😔 По «{query}» ничего не нашёл.", vk_main_menu())
        return
    _user_state[uid] = {"owner_pick_flow": True}
    rows = []
    for s in stations:
        name = (s.get("name") or "АЗС")[:25]
        rows.append([_button(f"#{s['id']} {name}"[:40], "primary")])
    rows.append([_button("❌ Отменить", "secondary")])
    await _send(msg, f"🔍 Нашёл <b>{len(stations)}</b> АЗС:", vk_keyboard(_limit_rows(rows), inline=False))


async def _owner_finish_text(msg: Message, station_id: int, role: str, inn: str | None = None):
    uid = _uid(msg)
    _owner_state_data.pop(uid, None)
    _owner_waiting_role.pop(uid, None)
    _owner_waiting_inn.discard(uid)
    _user_state.pop(uid, None)
    user_db_id = await _ensure_user(msg)
    if not user_db_id:
        await _send(msg, "Ошибка. Попробуй снова.", vk_main_menu())
        return
    result = await add_owner_station(user_id=user_db_id, station_id=station_id, inn=inn, role=role)
    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else f"#{station_id}"
    role_text = "владелец" if role == "owner" else "работник"
    if result == -1:
        text = f"ℹ️ Ты уже зарегистрирован на «{name}»."
    else:
        text = f"✅ <b>{role_text} «{name}».</b>\n\nПосле модерации — ✓ Verified."
    await _send(msg, text, vk_main_menu())


async def handle_station_detail_text(msg: Message, station_id: int):
    uid = _uid(msg)
    try:
        user_db_id = await get_or_create_user(msg)
        if user_db_id:
            await log_event(user_db_id, "vk_station_viewed", {"station_id": station_id})
    except Exception:
        pass
    station = await get_station_by_id(station_id)
    if not station:
        await _send(msg, "АЗС не найдена", vk_main_menu())
        return
    statuses = await get_station_current_status(station_id)
    # Добавляем рейтинг в station dict для отображения
    rating_info = await get_station_rating(station_id)
    station["avg_rating"] = rating_info["avg_rating"]
    station["total_reviews"] = rating_info["total_reviews"]
    text = format_station_card(station, statuses)
    kb = vk_station_actions(station_id, lat=station.get("lat"), lon=station.get("lon"))
    await _send(msg, text, kb)


async def handle_report_submit_text(msg: Message, station_id: int, fuel: str, status: str):
    available_map = {"yes": True, "low": None, "no": False}
    if status not in available_map:
        await _send(msg, "Неизвестный статус", vk_main_menu())
        return
    try:
        user_db_id = await get_or_create_user(msg)
        if user_db_id:
            await add_report(
                station_id=station_id, user_id=user_db_id, fuel_type=fuel,
                available=available_map[status], queue_size=None, source="user",
            )
    except Exception as e:
        logger.warning("report_submit failed: %s", e)
    status_text = {"yes": "✅ Есть", "low": "⚠️ Кончается", "no": "❌ Нет"}[status]
    await _send(msg, f"✅ Отчёт записан.\n\nАЗС #{station_id}, АИ-{fuel}: {status_text}", vk_main_menu())


async def handle_subscribe_radius_text(msg: Message, radius: int):
    uid = _uid(msg)
    state = _user_state.get(uid, {})
    lat = state.get("lat")
    lon = state.get("lon")
    if lat is None or lon is None:
        await _send(msg, "Сначала отправь геолокацию", vk_subscribe_geo_keyboard())
        return
    try:
        user_db_id = await get_or_create_user(msg)
        if user_db_id:
            await add_subscription(user_id=user_db_id, lat=lat, lon=lon, radius_km=radius)
    except Exception:
        pass
    _user_state.pop(uid, None)
    await _send(
        msg,
        f"🔔 Подписка оформлена.\n\nРадиус: {radius} км\nКоординаты: {lat:.4f}, {lon:.4f}\n\nПришлю уведомление о завозе.",
        vk_main_menu(),
    )


async def handle_report_city_text(msg: Message, city: str):
    stations = await find_stations_by_city(city=city, has_stock=None, limit=5)
    if not stations:
        await _send(msg, f"😔 В {city} АЗС не найдены.", vk_main_menu())
        return
    await _send(msg, f"⛽ Выбери АЗС в {city}:", vk_report_station_keyboard(stations))


async def handle_report_address_search(msg: Message, query: str):
    """Поиск АЗС по адресу (название + улица)."""
    if len(query) < 3:
        await _send(msg, "⚠️ Введи минимум 3 символа.", vk_main_menu())
        return
    stations = await find_stations_by_address(query, limit=10)
    if not stations:
        await _send(
            f"😔 АЗС по запросу «{query}» не найдены.\nПопробуй другой запрос.",
            vk_report_city_keyboard(),
        )
        return
    await _send(msg, f"🔍 Найдено {len(stations)} АЗС:", vk_report_address_results_keyboard(stations))


async def handle_review_submit(msg: Message, station_id: int, fuel: str, rating: int):
    """Отправка отзыва о качестве бензина."""
    try:
        user_db_id = await get_or_create_user(msg)
        if user_db_id:
            await add_review(
                station_id=station_id,
                user_id=user_db_id,
                fuel_type=fuel,
                rating=rating,
            )
    except Exception as e:
        logger.warning("review_submit failed: %s", e)
    stars = "⭐" * rating if rating > 0 else "Без звёзд"
    fuel_label = f"АИ-{fuel}" if fuel != "diesel" else "Дизель"
    await _send(
        f"✅ Отзыв принят!\n\nАЗС #{station_id}, {fuel_label}\nРейтинг: {stars}\n\nСпасибо за оценку!",
        vk_main_menu(),
    )


async def handle_sub_station_text(msg: Message, station_id: int):
    try:
        user_db_id = await get_or_create_user(msg)
        if user_db_id:
            await add_subscription(user_id=user_db_id, station_id=station_id, radius_km=0)
    except Exception:
        pass
    await _send(msg, "🔔 Подписался на АЗС", vk_main_menu())


# ====================================================================
# PAGINATION KEYBOARDS
# ====================================================================

def _station_list_keyboard(stations_page: list, total: int, page: int, pages: int) -> str:
    """Клавиатура списка АЗС: до 3 АЗС + навигация + утилиты. VK max 6 rows."""
    rows = []
    for s in stations_page:
        statuses = s.get("statuses", [])
        operator = (s.get("operator") or "")[:12]
        address = (s.get("address") or "")[:14]
        has_available = any(st.get("available") is True and st.get("fuel_type") != "all" for st in statuses)
        has_unavailable = any(st.get("available") is False and st.get("fuel_type") != "all" for st in statuses)
        icon = "✅" if has_available else ("❌" if has_unavailable else ("⚠️" if s.get("has_data") else "❓"))
        # Сеть → адрес
        if operator and address:
            label = f"{icon} #{s['id']} {operator} {address}"[:30]
        elif operator:
            label = f"{icon} #{s['id']} {operator}"[:30]
        elif address:
            label = f"{icon} #{s['id']} {address}"[:30]
        else:
            name = (s.get("name") or "АЗС")[:20]
            label = f"{icon} #{s['id']} {name}"[:30]
        rows.append([_button(label, "primary")])
    nav = []
    if page > 0:
        nav.append(_button("⬅️ Назад", "secondary"))
    if page < pages - 1:
        nav.append(_button("Далее ➡️", "secondary"))
    if nav:
        rows.append(nav)
    rows.append([_button("🚨 Экстренный", "negative"), _button("🏭 Сеть", "secondary")])
    rows.append([_button("🔄 Фильтры", "secondary"), _button(VK_BTN_HOME)])
    return vk_keyboard(rows)


def _network_filter_keyboard(networks: list[str], city: str) -> str:
    """Клавиатура фильтра по сети АЗС. Max 6 rows."""
    rows = []
    for i in range(0, min(len(networks), 6), 2):
        row = []
        for j in range(i, min(i + 2, len(networks))):
            row.append(_button(f"🏭 {networks[j]}"[:30], "primary"))
        rows.append(row)
    rows.append([_button("🏭 Все сети", "secondary")])
    rows.append([_button("⬅️ К списку", "secondary"), _button(VK_BTN_HOME)])
    return vk_keyboard(rows)


async def cmd_find_stations(msg: Message, city: str, fuel: str | None = None,
                            network: str | None = None, emergency: bool = False,
                            page: int = 0):
    """Find stations in city — paginated, max 3 per page."""
    try:
        if emergency:
            stations = await find_stations_by_city(city=city, has_stock=False, limit=50)
            if not stations:
                await _send(msg, f"🚨 {city}\n\n❌ Нет данных.", vk_main_menu())
                return
            stations_with_status = await get_stations_with_statuses(stations)
            stations_with_status = [s for s in stations_with_status if any(
                st.get("available") is not False and st.get("fuel_type") != "all"
                for st in (s.get("statuses") or [])
            )]
            if not stations_with_status:
                await _send(msg, f"🚨 {city}\n\n❌ Нет данных о наличии.", vk_main_menu())
                return
            lines = [f"🚨 {city} — {len(stations_with_status)} АЗС\n"]
            for s in stations_with_status[:5]:
                statuses = s.get("statuses", [])
                operator = (s.get("operator") or "")[:14]
                address = (s.get("address") or "")[:16]
                best = None
                for st in statuses:
                    if st.get("available") is True and st.get("fuel_type") != "all":
                        if not best or (st.get("price") is not None and (best.get("price") is None or st["price"] < best["price"])):
                            best = st
                # Сеть → адрес
                if operator and address:
                    short = f"{operator} — {address}"
                elif operator:
                    short = operator
                elif address:
                    short = address
                else:
                    name = (s.get("name") or "АЗС")[:22]
                    short = name
                if best and best.get("price") is not None:
                    short += f" · АИ-{best.get('fuel_type', '?')} {best['price']:.0f}₽"
                elif best:
                    short += f" · АИ-{best.get('fuel_type', '?')} ✅"
                lines.append(f"• #{s['id']} {short}")
            await _send(msg, "\n".join(lines), vk_main_menu())
            return

        logger.info("[cmd_find_stations] city=%s fuel=%s network=%s page=%s peer=%s", city, fuel, network, page, msg.peer_id)
        stations = await find_stations_by_city(city=city, fuel_type=fuel, network=network, has_stock=None, limit=50)
        logger.info("[cmd_find_stations] found %d stations", len(stations))
        if not stations:
            await _send(msg, f"🔍 В {city} ничего не найдено.", vk_main_menu())
            return

        stations_with_status = await get_stations_with_statuses(stations)
        promoted_ids = set(await get_promoted_station_ids(city) or [])
        stations_with_status.sort(key=lambda s: _sort_key(s, promoted_ids))

        PAGE_SIZE = 3
        total = len(stations_with_status)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        start = page * PAGE_SIZE
        page_stations = stations_with_status[start:start + PAGE_SIZE]

        fuel_label = f" (АИ-{fuel})" if fuel else ""
        net_label = f" [{network}]" if network else ""
        title = f"⛽ {city}{fuel_label}{net_label} — {total} АЗС (стр. {page + 1}/{pages})\n"

        kb = _station_list_keyboard(page_stations, total, page, pages)
        await _send(msg, title, kb)

        uid = _uid(msg)
        old = _user_state.get(uid, {})
        _user_state[uid] = {
            **old,
            "city": city, "fuel": fuel, "network": network,
            "page": page,
        }
    except Exception as e:
        logger.exception("[cmd_find_stations] FAILED: %s", e)
        await _send(msg, "⚠️ Произошла ошибка. Попробуй /start", vk_main_menu())


# ====================================================================
# MAIN
# ====================================================================

async def run_vk_bot():
    logger.info(">>> run_vk_bot() НАЧАЛСЯ")
    try:
        from dotenv import load_dotenv
        import os
        load_dotenv()

        vk_token = os.getenv("VK_TOKEN", "")
        if not vk_token:
            logger.warning("VK_TOKEN не задан — VK-бот НЕ запускается")
            return

        bot = Bot(token=vk_token)
        logger.info("VK-бот инициализирован")
    except Exception as e:
        logger.exception(f"run_vk_bot() CRASH during init: {e}")
        return

    async def _require_sub(msg: Message, handler) -> bool:
        """Проверяет подписку. Если не подписан — отправляет кнопку подписки. Возвращает True если ОК."""
        uid = _uid(msg)
        text = (msg.text or "").strip()
        if text in ("/start", "start"):
            return True
        is_sub = await _check_vk_subscription(uid, bot.api)
        if not is_sub:
            await _send(
                msg,
                "📢 <b>Подпишись на сообщество, чтобы пользоваться ботом!</b>\n\n"
                "Бот бесплатный. Взамен — подпишись на наше сообщество с новостями о топливе.",
                _vk_subscribe_keyboard(),
            )
            return False
        return True

    @bot.on.message(text=["/start", "start"])
    async def on_start(msg: Message):
        if await _require_sub(msg, cmd_start):
            await cmd_start(msg)

    @bot.on.message(text=["/help", "help"])
    async def on_help(msg: Message):
        if await _require_sub(msg, cmd_help):
            await cmd_help(msg)

    @bot.on.message(text=["/find", "find"])
    async def on_find(msg: Message):
        if await _require_sub(msg, cmd_find):
            await cmd_find(msg)

    @bot.on.message(text=["/subscribe", "subscribe"])
    async def on_subscribe(msg: Message):
        if await _require_sub(msg, cmd_subscribe):
            await cmd_subscribe(msg)

    @bot.on.message(text=["/register_owner", "register_owner"])
    async def on_owner(msg: Message):
        if await _require_sub(msg, cmd_register_owner):
            await cmd_register_owner(msg)

    @bot.on.message(text=["/profile", "profile"])
    async def on_profile(msg: Message):
        if await _require_sub(msg, cmd_profile):
            await cmd_profile(msg)

    @bot.on.message(text=["/my_stations", "my_stations"])
    async def on_my_stations(msg: Message):
        if await _require_sub(msg, cmd_my_stations):
            await cmd_my_stations(msg)

    @bot.on.message(text=["/premium", "premium"])
    async def on_premium(msg: Message):
        if await _require_sub(msg, cmd_premium):
            await cmd_premium(msg)

    @bot.on.message(text=["/donate", "donate"])
    async def on_donate(msg: Message):
        if await _require_sub(msg, cmd_donate):
            await cmd_donate(msg)

    @bot.on.message(text=["/stats", "stats"])
    async def on_stats(msg: Message):
        if await _require_sub(msg, cmd_stats):
            await cmd_stats(msg)

    # Catch-all: handle VK button labels + geo + text search
    @bot.on.message()
    async def on_geo_and_text(msg: Message):
        try:
            if msg.geo:
                await handle_geo_location(msg)
                return
            if msg.text:
                text = msg.text.strip()
                uid = _uid(msg)
                logger.info("VK text: %r", text)

                # --- Проверка подписки на сообщество ---
                # /start и "В начало" — пропускаем проверку
                if text not in ("/start", "start", "В начало"):
                    is_sub = await _check_vk_subscription(uid, None)
                    if not is_sub:
                        await _send(
                            msg,
                            "📢 <b>Подпишись на сообщество, чтобы пользоваться ботом!</b>\n\n"
                            "Бот бесплатный. Взамен — подпишись на наше сообщество с новостями о топливе.",
                            _vk_subscribe_keyboard(),
                        )
                        return

                # --- Main menu buttons ---
                if "Найти АЗС" in text:
                    await cmd_find(msg)
                    return
                if "Сообщить" in text:
                    old = _user_state.get(uid, {})
                    _user_state[uid] = {**old, "report_city_flow": True}
                    await _send(msg, "📝 Выбери город:", vk_report_city_keyboard())
                    return
                if "Уведомлени" in text:
                    await cmd_subscribe(msg)
                    return
                if "владелец" in text.lower():
                    await cmd_register_owner(msg)
                    return
                if "Профиль" in text:
                    await cmd_profile(msg)
                    return
                if "Мои АЗС" in text:
                    await cmd_my_stations(msg)
                    return
                if "Premium" in text:
                    await cmd_premium(msg)
                    return
                if "Поддержать" in text:
                    await cmd_donate(msg)
                    return
                if "Помощь" in text:
                    await cmd_help(msg)
                    return
                if "В начало" in text or text == "/start" or text == "start":
                    _owner_waiting_search.discard(uid)
                    _owner_waiting_role.pop(uid, None)
                    _owner_waiting_inn.discard(uid)
                    _owner_state_data.pop(uid, None)
                    _user_state.pop(uid, None)
                    await cmd_start(msg)
                    return
                if "Я подписался" in text:
                    _vk_subscribe_cache.pop(uid, None)
                    is_sub = await _check_vk_subscription(uid, None)
                    if is_sub:
                        await _send(msg, "✅ Подписка подтверждена! Пользуйся ботом бесплатно.", vk_main_menu())
                    else:
                        await _send(msg, "❌ Ты ещё не подписан. Подпишись и нажми снова.", _vk_subscribe_keyboard())
                    return

                # --- Owner role selection ---
                if uid in _owner_waiting_role and ("Я владелец" in text or "Я работник" in text):
                    role = "owner" if "владелец" in text else "employee"
                    station_id = _owner_waiting_role.pop(uid, 0)
                    if not station_id:
                        await _send(msg, "Ошибка. Попробуй сначала.", vk_main_menu())
                        return
                    _owner_state_data[uid] = {"station_id": station_id, "role": role}
                    _owner_waiting_inn.add(uid)
                    station = await get_station_by_id(station_id)
                    name = station.get("name", "АЗС") if station else f"#{station_id}"
                    role_text = "владельцем" if role == "owner" else "работником"
                    rows = [[_button("⏭ Пропустить", "secondary")]]
                    await _send(
                        msg,
                        f"⛽ <b>{name}</b> — <b>{role_text}</b>.\n\n📋 ИНН (опционально):",
                        vk_keyboard(rows, inline=False),
                    )
                    return

                # --- Owner INN skip ---
                if uid in _owner_waiting_inn and "Пропустить" in text:
                    _owner_waiting_inn.discard(uid)
                    state = _owner_state_data.pop(uid, {})
                    await _owner_finish_text(msg, state.get("station_id", 0), state.get("role", "owner"), inn=None)
                    return

                # --- City selection: "📍 Иваново" ---
                if text.startswith("📍"):
                    city = text.replace("📍", "").strip()
                    if city and city != "Другой город":
                        state = _user_state.get(uid, {})
                        if state.get("report_city_flow"):
                            _user_state.pop(uid, None)
                            await handle_report_city_text(msg, city)
                        else:
                            _user_state[uid] = {"city": city}
                            await cmd_find_stations(msg, city)
                        return

                # --- Pagination: exact match ---
                if text in ("Далее ➡️", "⬅️ Назад"):
                    state = _user_state.get(uid, {})
                    city = state.get("city", "")
                    if city:
                        page = state.get("page", 0)
                        if text == "Далее ➡️":
                            page += 1
                        else:
                            page -= 1
                        await cmd_find_stations(msg, city, fuel=state.get("fuel"),
                                                network=state.get("network"), page=page)
                    else:
                        await _send(msg, "Сначала выбери город", vk_city_keyboard())
                    return

                # --- Network filter: "🏭 Сеть" ---
                if text == "🏭 Сеть" or text == "🏭 сеть":
                    state = _user_state.get(uid, {})
                    city = state.get("city", "")
                    if city:
                        stations = await find_stations_by_city(city=city, has_stock=None, limit=50)
                        networks = []
                        seen = set()
                        for s in stations:
                            op = (s.get("operator") or "").strip()
                            if op and op not in seen:
                                seen.add(op)
                                networks.append(op)
                        if networks:
                            kb = _network_filter_keyboard(networks[:6], city)
                            await _send(msg, f"🏭 Выбери сеть АЗС в {city}:", kb)
                        else:
                            await _send(msg, f"🏭 В {city} нет данных о сетях.", vk_main_menu())
                    else:
                        await _send(msg, "Сначала найди АЗС", vk_main_menu())
                    return

                # --- Network filter select: "🏭 Лукойл" etc ---
                if text.startswith("🏭 ") and text != "🏭 Все сети":
                    network_name = text.replace("🏭", "").strip()
                    state = _user_state.get(uid, {})
                    city = state.get("city", "")
                    if city:
                        await cmd_find_stations(msg, city, fuel=state.get("fuel"),
                                                network=network_name)
                    else:
                        await _send(msg, "Сначала выбери город", vk_city_keyboard())
                    return

                # --- Network filter clear: "🏭 Все сети" ---
                if text == "🏭 Все сети":
                    state = _user_state.get(uid, {})
                    city = state.get("city", "")
                    if city:
                        await cmd_find_stations(msg, city, fuel=state.get("fuel"))
                    else:
                        await _send(msg, "Сначала выбери город", vk_city_keyboard())
                    return

                # --- Back to list: "⬅️ К списку" / "◀️ Назад к списку" ---
                if "К списку" in text:
                    state = _user_state.get(uid, {})
                    city = state.get("city", "")
                    if city:
                        await cmd_find_stations(msg, city, fuel=state.get("fuel"),
                                                network=state.get("network"),
                                                page=state.get("page", 0))
                    else:
                        await _send(msg, "Сначала выбери город", vk_city_keyboard())
                    return

                # --- Filters ---
                if "Фильтры" in text:
                    state = _user_state.get(uid, {})
                    city = state.get("city", "")
                    if city:
                        await _send(msg, f"Фильтры для {city}:", vk_filters_keyboard(city))
                    else:
                        await _send(msg, "Сначала выбери город", vk_city_keyboard())
                    return

                # --- Fuel filter: "⛽ АИ-92" (no #id — station buttons have #id) ---
                if not re.search(r"#\d+", text) and (text.startswith("⛽ АИ-") or text.startswith("🛢 Дизель")):
                    state = _user_state.get(uid, {})
                    city = state.get("city", "")
                    fuel_map = {
                        "АИ-92": "92", "АИ-95": "95", "АИ-98": "98", "Дизель": "diesel",
                    }
                    fuel = None
                    for key, val in fuel_map.items():
                        if key in text:
                            fuel = val
                            break
                    if fuel and city:
                        await cmd_find_stations(msg, city, fuel=fuel, network=state.get("network"))
                    elif city:
                        await cmd_find_stations(msg, city, network=state.get("network"))
                    else:
                        await _send(msg, "Сначала выбери город", vk_city_keyboard())
                    return

                # --- Emergency ---
                if "Экстренный" in text:
                    state = _user_state.get(uid, {})
                    city = state.get("city", "")
                    if city:
                        await cmd_find_stations(msg, city, emergency=True)
                    else:
                        await _send(msg, "Сначала выбери город", vk_city_keyboard())
                    return

                # --- #id detection (station detail, fuel report, status, owner pick) ---
                st_match = re.search(r"#(\d+)", text)
                if st_match:
                    station_id = int(st_match.group(1))
                    state = _user_state.get(uid, {})

                    # Fuel type for report: "⛽ 92 #123" (explicit fuel button)
                    fuel_btn_match = re.match(r"[⛽🛢]\s*(\d+|ДТ|Дизель)\s+#\d+", text)
                    if fuel_btn_match:
                        fuel_text = fuel_btn_match.group(1)
                        fuel_map = {"92": "92", "95": "95", "98": "98", "ДТ": "diesel", "Дизель": "diesel"}
                        fuel = fuel_map.get(fuel_text)
                        if fuel:
                            _user_state[uid] = {"report_station": station_id, "report_fuel": fuel}
                            await _send(msg, "Статус наличия:", vk_report_status_keyboard(station_id, fuel))
                        return

                    # Status for report: "✅ Есть #123:92"
                    status_match = re.search(r"(✅|⚠️|❌)\s+\S+\s+#(\d+):(\w+)", text)
                    if status_match:
                        emoji, sid, fuel = status_match.groups()
                        status_map = {"✅": "yes", "⚠️": "low", "❌": "no"}
                        status = status_map.get(emoji, "yes")
                        await handle_report_submit_text(msg, int(sid), fuel, status)
                        return

                    # Report start from station actions
                    if "Отчёт" in text or "report" in text.lower():
                        _user_state[uid] = {"report_station": station_id}
                        await _send(msg, "Выбери тип топлива:", vk_fuel_type_keyboard(station_id))
                        return

                    # Subscribe from station actions
                    if "Подписка" in text or "sub" in text.lower():
                        await handle_sub_station_text(msg, station_id)
                        return

                    # Owner pick flow
                    if state.get("owner_pick_flow"):
                        _user_state.pop(uid, None)
                        _owner_waiting_role[uid] = station_id
                        station = await get_station_by_id(station_id)
                        name = station.get("name", "АЗС") if station else "АЗС"
                        operator = station.get("operator") or ""
                        header = f"⛽ <b>{name}</b>" + (f" ({operator})" if operator else "")
                        rows = [
                            [_button("👑 Я владелец", "primary"), _button("👨\u200d🔧 Я работник", "secondary")],
                            [_button("❌ Отменить", "secondary")],
                        ]
                        await _send(msg, f"{header}\n\nКто ты на этой АЗС?", vk_keyboard(rows, inline=False))
                        return

                    # Default: station detail
                    await handle_station_detail_text(msg, station_id)
                    return

                # --- Radius: "3 км" / "5 км" / "10 км" ---
                radius_match = re.match(r"(\d+)\s*км", text)
                if radius_match:
                    radius = int(radius_match.group(1))
                    await handle_subscribe_radius_text(msg, radius)
                    return

                # --- Other city ---
                if "Другой город" in text:
                    state = _user_state.get(uid, {})
                    if state.get("report_city_flow"):
                        _user_state[uid] = {**state, "awaiting_report_city": True}
                    else:
                        _user_state[uid] = {"awaiting_city": True}
                    await _send(msg, "✏️ Введи название города:")
                    return

                # --- Address search: "🔍 Найти по адресу" ---
                if "Найти по адресу" in text:
                    _user_state[uid] = {"awaiting_address_query": True}
                    await _send(
                        msg,
                        "🔍 Напиши название АЗС и улицу:\n\n"
                        "Например:\n"
                        "• Лукойл Мира\n"
                        "• Газпром Ленина 42\n"
                        "• Роснефть Советская",
                    )
                    return

                # --- Awaiting address query ---
                state = _user_state.get(uid, {})
                if state.get("awaiting_address_query"):
                    _user_state.pop(uid, None)
                    await handle_report_address_search(msg, text)
                    return

                # --- Review: "⭐ Оценить качество бензина" ---
                if "Оценить качество" in text:
                    # Извлекаем station_id из кнопки "📝 Отчёт #123"
                    station_match = re.search(r"#(\d+)", text)
                    if station_match:
                        sid = int(station_match.group(1))
                        _user_state[uid] = {"review_station": sid}
                        await _send(msg, "⛽ Выбери тип топлива:", vk_review_fuel_keyboard(sid))
                    return

                # --- Review fuel type: "⛽ 92 #123" ---
                review_fuel_match = re.match(r"[⛽🛢]\s*(\d+|ДТ|Дизель)\s+#(\d+)", text)
                if review_fuel_match and state.get("review_station"):
                    fuel_text = review_fuel_match.group(1)
                    fuel_map = {"92": "92", "95": "95", "98": "98", "ДТ": "diesel", "Дизель": "diesel"}
                    fuel = fuel_map.get(fuel_text)
                    if fuel:
                        sid = state["review_station"]
                        _user_state[uid] = {"review_station": sid, "review_fuel": fuel}
                        await _send(msg, f"⛽ АИ-{fuel_text if fuel != 'diesel' else 'ДТ'} — оцени качество:", vk_review_rating_keyboard(sid, fuel))
                    return

                # --- Review rating: "⭐⭐⭐⭐⭐ #123:92" ---
                review_rating_match = re.match(r"((?:⭐|Без звёзд)+)\s+#(\d+):(\w+)", text)
                if review_rating_match and state.get("review_station"):
                    stars_text = review_rating_match.group(1)
                    sid = state["review_station"]
                    fuel = state.get("review_fuel", "92")
                    rating = stars_text.count("⭐")
                    _user_state.pop(uid, None)
                    await handle_review_submit(msg, sid, fuel, rating)
                    return

                # --- Cancel / back ---
                if "Отмена" in text or "Назад" in text:
                    _owner_waiting_search.discard(uid)
                    _owner_waiting_role.pop(uid, None)
                    _owner_waiting_inn.discard(uid)
                    _owner_state_data.pop(uid, None)
                    _user_state.pop(uid, None)
                    await cmd_start(msg)
                    return

                # --- Premium ---
                if "пробн" in text.lower() or "купить" in text.lower():
                    await cmd_premium(msg)
                    return

                # --- Awaiting city input ---
                state = _user_state.get(uid, {})
                if state.get("awaiting_city"):
                    _user_state.pop(uid, None)
                    city = text.strip()
                    _user_state[uid] = {"city": city}
                    await cmd_find_stations(msg, city)
                    return

                # --- Awaiting report city input ---
                if state.get("awaiting_report_city"):
                    _user_state.pop(uid, None)
                    city = text.strip()
                    await handle_report_city_text(msg, city)
                    return

                await handle_text_input(msg)
        except Exception as e:
            logger.exception("VK handler CRASH: %s", e)
            try:
                await _send(msg, "⚠️ Произошла ошибка. Попробуй /start", vk_main_menu())
            except Exception:
                pass

    # ====================================================================
    # TEXT-BASED HANDLERS (Message-based, no callback needed)
    # ====================================================================

    async def handle_text_input(msg: Message):
        uid = _uid(msg)
        text = (msg.text or "").strip()
        if len(text) < 2:
            return

        state = _user_state.get(uid, {})

        if state.get("awaiting") == "city_input":
            _user_state.pop(uid, None)
            await _show_station_list_from_msg(msg, text)
            return

        if state.get("awaiting") == "report_city_input":
            _user_state.pop(uid, None)
            stations = await find_stations_by_city(city=text, has_stock=None, limit=5)
            if not stations:
                await _send(msg, f"😔 В <b>{text}</b> АЗС не найдены.", vk_main_menu())
                return
            await _send(msg, f"⛽ <b>Выбери АЗС в {text}:</b>", vk_report_station_keyboard(stations))
            return

        if uid in _owner_waiting_search:
            await _owner_search_handler(msg, text)
            return

        if uid in _owner_waiting_inn:
            inn = text.strip()
            if inn and not inn.isdigit():
                await _send(msg, "ИНН — только цифры. Попробуй ещё раз.")
                return
            _owner_waiting_inn.discard(uid)
            state = _owner_state_data.pop(uid, {})
            await _owner_finish_text(msg, state.get("station_id", 0), state.get("role", "owner"), inn=inn or None)
            return

        await _do_text_search(msg, text)

    logger.info("VK-бот запущен, начинаем polling...")
    try:
        await bot.run_polling()
    except Exception as e:
        logger.exception("VK polling CRASHED: %s", e)
