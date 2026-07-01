"""
VK-бот «Бензин рядом» — полная копия Telegram-бота на vkbottle.
Запускается параллельно с TG-ботом.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from vkbottle import Bot, VKAPIError
from vkbottle.bot import Message, MessageEvent

from config import settings
from db import (
    _fetch,
    _execute,
    add_owner_station,
    add_report,
    add_subscription,
    activate_premium,
    find_nearest_stations,
    find_stations_by_city,
    find_stations_by_name,
    get_or_create_user,
    get_owner_stations,
    get_pending_owner_applications,
    get_premium_info,
    get_station_by_id,
    get_station_current_status,
    get_stations_with_statuses,
    get_user_id_by_telegram_id,
    is_owner_of_station,
    is_premium,
    log_event,
    set_owner_station_verified,
    get_promoted_station_ids,
    get_user_stats_summary,
)
from utils import format_distance, format_station_card
from vk_keyboards import (
    vk_main_menu,
    vk_city_keyboard,
    vk_filters_keyboard,
    vk_station_list_keyboard,
    vk_station_actions,
    vk_fuel_type_keyboard,
    vk_report_status_keyboard,
    vk_subscribe_geo_keyboard,
    vk_subscribe_radius_keyboard,
    vk_premium_keyboard,
    vk_report_city_keyboard,
    vk_report_station_keyboard,
    _button,
    _callback_button,
    vk_keyboard,
)

logger = logging.getLogger(__name__)

# In-memory state management (VK doesn't have FSM like aiogram)
_user_state: dict[int, dict] = {}
_owner_waiting_search: set[int] = set()
_owner_waiting_role: dict[int, int] = {}
_owner_waiting_inn: set[int] = set()
_owner_state_data: dict[int, dict] = {}


def _uid(msg: Message) -> int:
    return msg.peer_id


def _parse_payload(event: MessageEvent) -> dict:
    """Parse VK callback payload."""
    raw = event.object.payload
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


# === Cache for search results ===
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


# === Helpers ===
async def _ensure_user(msg: Message) -> int | None:
    """Create/update user from VK message. Returns DB user_id."""
    uid = _uid(msg)
    try:
        await get_or_create_user(msg)
        return await get_user_id_by_telegram_id(uid)
    except Exception as e:
        logger.warning(f"_ensure_user failed: {e}")
        return None


async def _send(msg: Message, text: str, keyboard: str | None = None):
    """Send message with optional keyboard."""
    kwargs = {"message": text}
    if keyboard:
        kwargs["keyboard"] = keyboard
    await msg.answer(**kwargs)


async def _edit(event: MessageEvent, text: str, keyboard: str | None = None):
    """Edit message (for callback events)."""
    try:
        await event.edit_message(message=text, keyboard=keyboard)
    except Exception as e:
        logger.debug(f"edit_message failed: {e}, sending new")
        await event.send_message(message=text, keyboard=keyboard)


async def _notify(event: MessageEvent, text: str, show_alert: bool = False):
    """Send notification popup."""
    try:
        if show_alert:
            # VK doesn't have a simple show_alert; use snackbar as fallback
            await event.show_snackbar(text)
        else:
            await event.show_snackbar(text)
    except Exception:
        pass


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


# ====================================================================
# HANDLERS
# ====================================================================

# === /start, /help, кнопка "В начало" ===
async def cmd_start(msg: Message):
    uid = await _ensure_user(msg)
    if uid:
        await log_event(uid, "vk_start")

    text = (
        "👋 Привет! Я — Бензин рядом.\n\n"
        "Помогу найти бензин за 5 секунд. 26 000+ АЗС в России.\n\n"
        "🟢 live · цены · очереди · push о завозе\n\n"
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


async def handle_home(event: MessageEvent):
    """Callback: home button."""
    uid = _uid_from_event(event)
    _owner_waiting_search.discard(uid)
    _owner_waiting_role.pop(uid, None)
    _owner_waiting_inn.discard(uid)
    _owner_state_data.pop(uid, None)
    _user_state.pop(uid, None)
    await _edit(
        event,
        "🏠 <b>Главное меню</b>\n\nВыбери действие на клавиатуре 👇",
        vk_main_menu(),
    )
    await _notify(event, "🏠 В начало")


def _uid_from_event(event: MessageEvent) -> int:
    return event.peer_id


# === Find stations ===
async def cmd_find(msg: Message):
    await _send(
        msg,
        "📍 <b>Выбери населённый пункт</b>\n\n"
        "Иваново, Москва, СПб, и другие. "
        "Или напиши свой город в сообщении — бот найдёт АЗС.",
        vk_city_keyboard(),
    )


async def handle_city_select(event: MessageEvent):
    payload = _parse_payload(event)
    city = payload.get("city", "")
    if city == "other":
        _user_state[_uid_from_event(event)] = {"awaiting": "city_input"}
        await event.answer(text="✏️ Напиши название города в следующем сообщении")
        return
    await _show_filters(event, city)


async def _show_filters(event_or_msg, city: str):
    text = f"📍 <b>{city}</b>\n\nВыбери тип топлива или фильтры:"
    kb = vk_filters_keyboard(city)
    if isinstance(event_or_msg, MessageEvent):
        await _edit(event_or_msg, text, kb)
    else:
        await _send(event_or_msg, text, kb)


async def handle_fuel_filter(event: MessageEvent):
    payload = _parse_payload(event)
    city = payload.get("city", "")
    fuel = payload.get("fuel", "")
    await _show_station_list(event, city, fuel=fuel)


async def handle_emergency(event: MessageEvent):
    payload = _parse_payload(event)
    city = payload.get("city", "")
    if not city:
        await event.answer(text="Выбери город для экстренного поиска")
        return
    await _do_emergency(event, city)


# === Station list ===
async def _show_station_list(event_or_msg, city: str, fuel: str = None, network: str = None, max_price: float = None):
    """Show stations in city with filters."""
    try:
        stations = await find_stations_by_city(
            city=city, fuel_type=fuel, network=network,
            max_price=max_price, has_stock=False, limit=20,
        )
        if not stations:
            text = f"🔍 <b>В городе {city} ничего не найдено</b>\n\nПопробуй сбросить фильтры."
            kb = vk_keyboard([
                [_callback_button("🔄 Сбросить фильтры", "primary", {"cmd": "filters", "city": city})],
                [_callback_button("🏠 В начало", "secondary", {"cmd": "home"})],
            ], inline=True)
            if isinstance(event_or_msg, MessageEvent):
                await _edit(event_or_msg, text, kb)
            else:
                await _send(event_or_msg, text, kb)
            return

        stations_with_status = await get_stations_with_statuses(stations)

        promoted_ids = set(await get_promoted_station_ids(city) or [])

        def _sort_key(s):
            return (
                0 if s["id"] in promoted_ids else 1,
                0 if s.get("is_verified") else 1,
                0 if s.get("has_data") else 1,
                (s.get("name") or "").lower(),
            )
        stations_with_status.sort(key=_sort_key)

        filter_desc = []
        if fuel:
            filter_desc.append(f"топливо АИ-{fuel}")
        if max_price:
            filter_desc.append(f"до {max_price}₽")
        if network:
            filter_desc.append(f"сеть: {network}")

        title = f"⛽ <b>{city}</b> — найдено {len(stations_with_status)} АЗС"
        if filter_desc:
            title += f"\n<i>Фильтры: {', '.join(filter_desc)}</i>"
        title += "\n"

        rows = []
        for s in stations_with_status[:10]:
            statuses = s.get("statuses", [])
            name = (s.get("name") or "АЗС")[:22]
            operator = (s.get("operator") or "")[:14]
            short = f"{name} · {operator}" if operator and operator != name else name

            has_available = any(st.get("available") is True and st.get("fuel_type") != "all" for st in statuses)
            has_unavailable = any(st.get("available") is False and st.get("fuel_type") != "all" for st in statuses)
            if has_available:
                short += " · ✅"
            elif has_unavailable:
                short += " · ❌"
            elif s.get("has_data"):
                short += " · ⚠️"
            else:
                short += " · ❓"

            rows.append([_callback_button(short[:40], "primary", {"cmd": "st", "id": s["id"]})])

        rows.append([_callback_button("🚨 Экстренный", "negative", {"cmd": "emergency", "city": city})])
        rows.append([_callback_button("🔄 Фильтры", "secondary", {"cmd": "filters", "city": city})])
        rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
        kb = vk_keyboard(rows, inline=True)

        if isinstance(event_or_msg, MessageEvent):
            await _edit(event_or_msg, title, kb)
        else:
            await _send(event_or_msg, title, kb)
    except Exception as e:
        logger.exception(f"_show_station_list: {e}")
        text = f"⚠️ Ошибка: {e}"
        if isinstance(event_or_msg, MessageEvent):
            await event_or_msg.answer(text=text)
        else:
            await _send(event_or_msg, text, vk_main_menu())


async def handle_station_detail(event: MessageEvent):
    payload = _parse_payload(event)
    station_id = payload.get("id", 0)

    uid = await _ensure_user_from_event(event)
    if uid:
        await log_event(uid, "vk_station_viewed", {"station_id": station_id})

    station = await get_station_by_id(station_id)
    if not station:
        await _notify(event, "АЗС не найдена", show_alert=True)
        return

    statuses = await get_station_current_status(station_id)
    text = format_station_card(station, statuses)
    lat = station.get("lat")
    lon = station.get("lon")
    kb = vk_station_actions(station_id)

    # Owner promo button
    if uid and await is_owner_of_station(uid, station_id):
        from db import is_station_promoted, get_owner_station_by_user_and_station, PROMO_PRICE_STARS
        owner_station = await get_owner_station_by_user_and_station(uid, station_id)
        if owner_station:
            is_promo = await is_station_promoted(station_id)
            if is_promo:
                promo_text = f"🌟 Продвижение активно"
            else:
                promo_text = f"🌟 Продвинуть ({PROMO_PRICE_STARS}⭐)"
            # Insert promo button at the top
            rows = [[_callback_button(promo_text, "positive", {"cmd": "promote", "id": station_id})]]
            # Parse existing keyboard buttons and rebuild
            # VK keyboards can't be easily modified, so we build fresh
            kb = vk_keyboard(rows + [
                [_callback_button("📝 Сообщить", "positive", {"cmd": "report_start", "id": station_id})],
                [_callback_button("🔔 Подписаться", "primary", {"cmd": "sub_station", "id": station_id})],
                [_callback_button("◀️ Назад", "secondary", {"cmd": "back_to_list"})],
                [_callback_button("🏠 В начало", "secondary", {"cmd": "home"})],
            ], inline=True)

    await _edit(event, text, kb)
    await _notify(event, "✅")


async def handle_station_list_back(event: MessageEvent):
    await _edit(
        event,
        "🔍 Нажми «🔍 Найти АЗС» или напиши город.",
        vk_main_menu(),
    )


# === Report flow ===
async def handle_report_start(event: MessageEvent):
    payload = _parse_payload(event)
    station_id = payload.get("id", 0)
    text = "⛽ <b>Выбери тип топлива:</b>"
    kb = vk_fuel_type_keyboard(station_id)
    await _edit(event, text, kb)
    await _notify(event, "📝")


async def handle_report_fuel(event: MessageEvent):
    payload = _parse_payload(event)
    station_id = payload.get("id", 0)
    fuel = payload.get("fuel", "")
    text = f"⛽ <b>АИ-{fuel}</b> — какой статус?"
    kb = vk_report_status_keyboard(station_id, fuel)
    await _edit(event, text, kb)


async def handle_report_submit(event: MessageEvent):
    payload = _parse_payload(event)
    station_id = payload.get("id", 0)
    fuel = payload.get("fuel", "")
    status = payload.get("status", "")

    available_map = {"yes": True, "low": None, "no": False}
    queue_map = {"yes": None, "low": None, "no": None}

    if status not in available_map:
        await _notify(event, "Неизвестный статус", show_alert=True)
        return

    available = available_map[status]
    queue_size = queue_map[status]

    uid = await _ensure_user_from_event(event)
    if uid:
        await add_report(
            station_id=station_id,
            user_id=uid,
            fuel_type=fuel,
            available=available,
            queue_size=queue_size,
            source="user",
        )

    status_text = {"yes": "✅ Есть", "low": "⚠️ Кончается", "no": "❌ Нет"}[status]
    await _edit(
        event,
        f"✅ <b>Спасибо! Отчёт записан.</b>\n\n"
        f"АЗС #{station_id}, АИ-{fuel}: {status_text}\n\n"
        f"Твой отчёт увидят другие водители.",
        vk_main_menu(),
    )
    await _notify(event, "✅ Отчёт записан")


async def handle_report_city_menu(event: MessageEvent):
    """Report: city selection menu."""
    await _edit(
        event,
        "📝 <b>Выбери город, чтобы сообщить о наличии:</b>",
        vk_report_city_keyboard(),
    )


async def handle_report_city(event: MessageEvent):
    payload = _parse_payload(event)
    city = payload.get("city", "")
    if city == "other":
        _user_state[_uid_from_event(event)] = {"awaiting": "report_city_input"}
        await event.answer(text="✏️ Напиши название города")
        return

    stations = await find_stations_by_city(city=city, has_stock=None, limit=15)
    if not stations:
        await _edit(event, f"😔 В <b>{city}</b> АЗС не найдены.", vk_main_menu())
        return

    kb = vk_report_station_keyboard(stations)
    await _edit(event, f"⛽ <b>Выбери АЗС в {city}:</b>", kb)


async def handle_report_pick(event: MessageEvent):
    payload = _parse_payload(event)
    station_id = payload.get("id", 0)
    text = "⛽ <b>Выбери тип топлива:</b>"
    kb = vk_fuel_type_keyboard(station_id)
    await _edit(event, text, kb)


# === Emergency ===
async def _do_emergency(event_or_msg, city: str):
    """Emergency mode — show stations with fuel."""
    try:
        stations = await find_stations_by_city(city=city, has_stock=False, limit=50)
        if not stations:
            text = f"🚨 <b>Экстренный: {city}</b>\n\n❌ Нет данных о наличии топлива."
            if isinstance(event_or_msg, MessageEvent):
                await _edit(event_or_msg, text, vk_main_menu())
            else:
                await _send(event_or_msg, text, vk_main_menu())
            return

        stations_with_status = await get_stations_with_statuses(stations)
        stations_with_status = [s for s in stations_with_status if any(
            st.get("available") is not False and st.get("fuel_type") != "all"
            for st in (s.get("statuses") or [])
        )]

        if not stations_with_status:
            text = f"🚨 <b>Экстренный: {city}</b>\n\n❌ Нет данных о наличии."
            if isinstance(event_or_msg, MessageEvent):
                await _edit(event_or_msg, text, vk_main_menu())
            else:
                await _send(event_or_msg, text, vk_main_menu())
            return

        def _sort_key(s):
            statuses = s.get("statuses", [])
            has_price = any(st.get("price") is not None for st in statuses)
            return (
                0 if s.get("is_verified") else 1,
                0 if has_price else 1,
                0 if s.get("has_data") else 1,
                (s.get("name") or "").lower(),
            )
        stations_with_status.sort(key=_sort_key)

        lines = [f"🚨 <b>{city}</b> — {len(stations_with_status)} АЗС с топливом\n"]
        rows = []
        for s in stations_with_status[:10]:
            statuses = s.get("statuses", [])
            name = (s.get("name") or "АЗС")[:22]
            operator = (s.get("operator") or "")[:14]

            best = None
            for st in statuses:
                if st.get("available") is True and st.get("fuel_type") != "all":
                    if not best or (st.get("price") is not None and (best.get("price") is None or st["price"] < best["price"])):
                        best = st

            short = f"{name} · {operator}" if operator and operator != name else name
            if best and best.get("price") is not None:
                short += f" · АИ-{best.get('fuel_type', '?')} {best['price']:.2f}₽"
            elif best:
                short += f" · АИ-{best.get('fuel_type', '?')} ✅"
            rows.append([_callback_button(short[:40], "primary", {"cmd": "st", "id": s["id"]})])

        rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
        kb = vk_keyboard(rows, inline=True)

        text = "\n".join(lines) + "\n💡 Без фильтров — здесь точно есть топливо."
        if isinstance(event_or_msg, MessageEvent):
            await _edit(event_or_msg, text, kb)
        else:
            await _send(event_or_msg, text, kb)
    except Exception as e:
        logger.exception(f"_do_emergency: {e}")
        text = f"⚠️ Ошибка: {e}"
        if isinstance(event_or_msg, MessageEvent):
            await event_or_msg.answer(text=text)
        else:
            await _send(event_or_msg, text, vk_main_menu())


# === Subscribe ===
async def cmd_subscribe(msg: Message):
    uid = await _ensure_user(msg)
    _user_state[_uid(msg)] = {"awaiting": "subscribe_geo"}
    await _send(
        msg,
        "🔔 <b>Подписка на уведомления о завозе.</b>\n\n"
        "Отправь геолокацию — буду присылать уведомления, когда "
        "в радиусе 5 км от тебя появится бензин.",
        vk_subscribe_geo_keyboard(),
    )


async def handle_geo_location(msg: Message):
    """Handle geolocation from VK."""
    uid = _uid(msg)
    state = _user_state.get(uid, {})

    # Check if user is in subscribe flow
    if state.get("awaiting") == "subscribe_geo":
        geo = msg.geo
        if not geo:
            await _send(msg, "⚠️ Не удалось определить координаты. Попробуй ещё раз.")
            return
        lat = geo.coordinates.latitude
        lon = geo.coordinates.longitude
        _user_state[uid] = {"awaiting": "subscribe_radius", "lat": lat, "lon": lon}
        await _send(
            msg,
            f"📍 Геолокацию получил: {lat:.4f}, {lon:.4f}\n\nВыбери радиус уведомлений:",
            vk_subscribe_radius_keyboard(),
        )
        return

    # Default: find nearest stations
    geo = msg.geo
    if not geo:
        await _send(msg, "⚠️ Не удалось определить координаты.")
        return
    lat = geo.coordinates.latitude
    lon = geo.coordinates.longitude
    await _do_find_by_geo(msg, lat, lon)


async def _do_find_by_geo(msg: Message, lat: float, lon: float):
    cached = _cache_get(lat, lon, 30)
    if cached is not None:
        stations = cached
    else:
        stations = await find_nearest_stations(lat=lat, lon=lon, limit=10, radius_km=30)
        _cache_set(lat, lon, 30, stations)

    if not stations:
        await _send(msg, "😔 <b>Рядом не нашёл АЗС в базе.</b>\n\nПопробуй написать город.", vk_main_menu())
        return

    stations = await get_stations_with_statuses(stations)

    text = f"🔍 <b>Нашёл {len(stations)} АЗС рядом:</b>\n\n"
    rows = []
    for s in stations:
        statuses = s.get("statuses", [])
        dist = format_distance(s.get("distance_km", 0))
        icon = _get_main_status_icon(statuses)
        name = (s.get("name") or "АЗС")[:22]
        btn_text = f"{icon} {name} • {dist}"
        rows.append([_callback_button(btn_text[:40], "primary", {"cmd": "st", "id": s["id"]})])
    rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
    await _send(msg, text, vk_keyboard(rows, inline=True))


async def handle_subscribe_radius(event: MessageEvent):
    payload = _parse_payload(event)
    radius = payload.get("radius", 5)
    uid = _uid_from_event(event)
    state = _user_state.get(uid, {})
    lat = state.get("lat")
    lon = state.get("lon")

    if lat is None or lon is None:
        await _notify(event, "Сначала отправь геолокацию", show_alert=True)
        return

    user_db_id = await _ensure_user_from_event(event)
    if user_db_id:
        await add_subscription(user_id=user_db_id, lat=lat, lon=lon, radius_km=radius)

    _user_state.pop(uid, None)
    await _edit(
        event,
        f"🔔 <b>Подписка оформлена.</b>\n\n"
        f"Радиус: {radius} км\n"
        f"Координаты: {lat:.4f}, {lon:.4f}\n\n"
        f"Пришлю уведомление, как только кто-то сообщит о наличии топлива рядом.",
        vk_main_menu(),
    )


async def handle_sub_station(event: MessageEvent):
    payload = _parse_payload(event)
    station_id = payload.get("id", 0)
    uid = await _ensure_user_from_event(event)
    if uid:
        await add_subscription(user_id=uid, station_id=station_id, radius_km=0)
    await _notify(event, "🔔 Подписался на АЗС. Сообщу о наличии.", show_alert=True)


# === Profile ===
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
        text += "\n⭐ <b>Premium</b> — push без cooldown, расширенная аналитика\n"

    badges = stats.get("badges", [])
    if badges:
        text += f"\n🏆 <b>Твои бейджи ({len(badges)}):</b>\n"
        for b in badges:
            text += f"  {b['emoji']} <b>{b['name']}</b> — {b['desc']}\n"
    else:
        text += "\n🎯 Сделай первый отчёт, чтобы получить бейдж 🥉 «Новичок»!"

    await _send(msg, text, vk_main_menu())


# === Owner registration ===
async def cmd_register_owner(msg: Message):
    uid = _uid(msg)
    _owner_waiting_search.add(uid)
    await _send(
        msg,
        "👋 <b>Регистрация владельца или работника АЗС.</b>\n\n"
        "Введи название, адрес или город АЗС, где ты работаешь.\n\n"
        "<i>Например: Лукойл Иваново, Ленина 45, Газпром Шуя.</i>",
        vk_main_menu(),
    )


async def handle_text_input(msg: Message):
    """Handle arbitrary text input — search or state-dependent."""
    uid = _uid(msg)
    text = (msg.text or "").strip()
    if len(text) < 2:
        return

    state = _user_state.pop(uid, {})

    # City input for find
    if state.get("awaiting") == "city_input":
        await _do_city_search(msg, text)
        return

    # City input for report
    if state.get("awaiting") == "report_city_input":
        stations = await find_stations_by_city(city=text, has_stock=None, limit=15)
        if not stations:
            await _send(msg, f"😔 В <b>{text}</b> АЗС не найдены.", vk_main_menu())
            return
        kb = vk_report_station_keyboard(stations)
        await _send(msg, f"⛽ <b>Выбери АЗС в {text}:</b>", kb)
        return

    # Owner search
    if uid in _owner_waiting_search:
        await _owner_search_handler(msg, text)
        return

    # INN input
    if uid in _owner_waiting_inn:
        inn = text.strip()
        if inn and not inn.isdigit():
            await _send(msg, "ИНН должен содержать только цифры. Попробуй ещё раз.")
            return
        _owner_waiting_inn.discard(uid)
        state = _owner_state_data.pop(uid, {})
        await _owner_finish(msg, state.get("station_id", 0), state.get("role", "owner"), inn=inn or None)
        return

    # Default: text search
    await _do_text_search(msg, text)


async def _do_city_search(msg: Message, city: str):
    await _show_station_list_from_msg(msg, city)


async def _show_station_list_from_msg(msg: Message, city: str, fuel: str = None):
    try:
        stations = await find_stations_by_city(city=city, fuel_type=fuel, has_stock=False, limit=20)
        if not stations:
            await _send(msg, f"🔍 В городе {city} ничего не найдено.", vk_main_menu())
            return

        stations_with_status = await get_stations_with_statuses(stations)
        promoted_ids = set(await get_promoted_station_ids(city) or [])

        def _sort_key(s):
            return (
                0 if s["id"] in promoted_ids else 1,
                0 if s.get("is_verified") else 1,
                0 if s.get("has_data") else 1,
                (s.get("name") or "").lower(),
            )
        stations_with_status.sort(key=_sort_key)

        title = f"⛽ <b>{city}</b> — найдено {len(stations_with_status)} АЗС\n"
        rows = []
        for s in stations_with_status[:10]:
            statuses = s.get("statuses", [])
            name = (s.get("name") or "АЗС")[:22]
            icon = _get_main_status_icon(statuses)
            rows.append([_callback_button(f"{icon} {name}"[:40], "primary", {"cmd": "st", "id": s["id"]})])
        rows.append([_callback_button("🚨 Экстренный", "negative", {"cmd": "emergency", "city": city})])
        rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
        await _send(msg, title, vk_keyboard(rows, inline=True))
    except Exception as e:
        logger.exception(f"_show_station_list_from_msg: {e}")
        await _send(msg, f"⚠️ Ошибка: {e}", vk_main_menu())


async def _do_text_search(msg: Message, query: str):
    uid = await _ensure_user(msg)
    if uid:
        await log_event(uid, "vk_text_search", {"query": query})

    stations = await find_stations_by_name(query, limit=8)
    if not stations:
        await _send(
            msg,
            f"😔 По запросу <b>«{query}»</b> ничего не нашёл.\n\n"
            f"Попробуй написать по-другому или отправь 📍 геолокацию.",
            vk_main_menu(),
        )
        return

    stations = await get_stations_with_statuses(stations)

    text = f"🔍 По запросу <b>«{query}»</b> нашёл {len(stations)} АЗС:\n\n"
    rows = []
    for s in stations:
        statuses = s.get("statuses", [])
        icon = _get_main_status_icon(statuses)
        name = (s.get("name") or "АЗС")[:25]
        city = (s.get("city") or "")[:12]
        btn_text = f"{icon} {name}"
        if city:
            btn_text += f" • {city}"
        rows.append([_callback_button(btn_text[:40], "primary", {"cmd": "st", "id": s["id"]})])
    rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
    await _send(msg, text, vk_keyboard(rows, inline=True))


async def _owner_search_handler(msg: Message, query: str):
    uid = _uid(msg)
    _owner_waiting_search.discard(uid)

    stations = await find_stations_by_name(query, limit=10)
    if not stations:
        await _send(
            msg,
            f"😔 По запросу <b>«{query}»</b> ничего не нашёл.\n\n"
            f"Попробуй: Лукойл, Газпром, Иваново, Ленина 45.",
            vk_main_menu(),
        )
        return

    rows = []
    for s in stations:
        name = (s.get("name") or "АЗС")[:30]
        operator = (s.get("operator") or "")[:15]
        city = (s.get("city") or "")[:12]
        label = f"⛽ {name}"
        if operator:
            label += f" · {operator}"
        if city:
            label += f" ({city})"
        rows.append([_callback_button(label[:40], "primary", {"cmd": "owner_pick", "id": s["id"]})])
    rows.append([_callback_button("❌ Отменить", "secondary", {"cmd": "home"})])
    await _send(msg, f"🔍 Нашёл <b>{len(stations)}</b> АЗС. Выбери свою:", vk_keyboard(rows, inline=True))


async def handle_owner_pick(event: MessageEvent):
    payload = _parse_payload(event)
    station_id = payload.get("id", 0)
    uid = _uid_from_event(event)
    _owner_waiting_role[uid] = station_id

    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else "АЗС"
    operator = station.get("operator") or ""
    header = f"⛽ <b>{name}</b>"
    if operator:
        header += f" ({operator})"

    rows = [
        [_callback_button("👑 Я владелец", "primary", {"cmd": "owner_role", "role": "owner"})],
        [_callback_button("👨‍🔧 Я работник", "secondary", {"cmd": "owner_role", "role": "employee"})],
        [_callback_button("❌ Отменить", "secondary", {"cmd": "home"})],
    ]
    await _edit(event, f"{header}\n\nКто ты на этой АЗС?", vk_keyboard(rows, inline=True))


async def handle_owner_role(event: MessageEvent):
    payload = _parse_payload(event)
    role = payload.get("role", "owner")
    uid = _uid_from_event(event)
    station_id = _owner_waiting_role.pop(uid, 0)
    if not station_id:
        await _notify(event, "Ошибка. Попробуй сначала.", show_alert=True)
        return

    _owner_state_data[uid] = {"station_id": station_id, "role": role}
    _owner_waiting_inn.add(uid)

    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else f"#{station_id}"
    role_text = "владельцем" if role == "owner" else "работником"

    rows = [
        [_callback_button("⏭ Пропустить", "secondary", {"cmd": "owner_inn_skip"})],
    ]
    await _edit(
        event,
        f"⛽ <b>{name}</b> — ты зарегистрирован как <b>{role_text}</b>.\n\n"
        f"📋 Укажи ИНН организации (10 или 12 цифр) — <i>опционально</i>.\n"
        f"Если не хочешь — нажми «Пропустить».",
        vk_keyboard(rows, inline=True),
    )


async def handle_owner_inn_skip(event: MessageEvent):
    uid = _uid_from_event(event)
    _owner_waiting_inn.discard(uid)
    state = _owner_state_data.pop(uid, {})
    await _owner_finish_from_event(event, state.get("station_id", 0), state.get("role", "owner"), inn=None)


async def _owner_finish_from_event(event: MessageEvent, station_id: int, role: str, inn: str | None = None):
    uid = _uid_from_event(event)
    user_db_id = await _ensure_user_from_event(event)
    if not user_db_id:
        await _edit(event, "Ошибка. Попробуй снова.", vk_main_menu())
        return

    result = await add_owner_station(user_id=user_db_id, station_id=station_id, inn=inn, role=role)
    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else f"#{station_id}"
    role_text = "владелец" if role == "owner" else "работник"

    if result == -1:
        text = f"ℹ️ Ты уже зарегистрирован на АЗС «{name}»."
    else:
        text = (
            f"✅ <b>Готово! Ты зарегистрирован как {role_text} АЗС «{name}».</b>\n\n"
            f"После модерации появится значок ✓ Verified."
        )
    await _edit(event, text, vk_main_menu())


async def _owner_finish(msg: Message, station_id: int, role: str, inn: str | None = None):
    uid = _uid(msg)
    _owner_state_data.pop(uid, None)
    _owner_waiting_role.pop(uid, None)
    _owner_waiting_inn.discard(uid)

    user_db_id = await _ensure_user(msg)
    if not user_db_id:
        await _send(msg, "Ошибка. Попробуй снова.", vk_main_menu())
        return

    result = await add_owner_station(user_id=user_db_id, station_id=station_id, inn=inn, role=role)
    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else f"#{station_id}"
    role_text = "владелец" if role == "owner" else "работник"

    if result == -1:
        text = f"ℹ️ Ты уже зарегистрирован на АЗС «{name}»."
    else:
        text = (
            f"✅ <b>Готово! Ты зарегистрирован как {role_text} АЗС «{name}».</b>\n\n"
            f"После модерации появится значок ✓ Verified."
        )
    await _send(msg, text, vk_main_menu())


async def _ensure_user_from_event(event: MessageEvent) -> int | None:
    """Create/update user from VK callback event."""
    uid = _uid_from_event(event)
    try:
        # VK events don't have from_user in the same way
        # We use peer_id as the identifier
        from db import upsert_user
        user_id = await upsert_user(
            telegram_id=uid,
            username=f"vk_{uid}",
            first_name=None,
            last_name=None,
            language_code="ru",
        )
        return user_id
    except Exception as e:
        logger.warning(f"_ensure_user_from_event failed: {e}")
        return None


# === My stations ===
async def cmd_my_stations(msg: Message):
    uid = await _ensure_user(msg)
    if not uid:
        await _send(msg, "Сначала нажми «🏠 В начало»", vk_main_menu())
        return

    stations = await get_owner_stations(uid)
    if not stations:
        await _send(
            msg,
            "ℹ️ Ты не зарегистрирован как владелец/работник АЗС.\n\n"
            "Нажми «👤 Я владелец».",
            vk_main_menu(),
        )
        return

    text = "🏪 <b>Твои АЗС:</b>\n\n"
    rows = []
    for s in stations:
        name = (s.get("name") or "АЗС")[:30]
        verified = " ✓" if s.get("is_verified") else ""
        role = s.get("role") or "owner"
        role_icon = "👑" if role == "owner" else "👨‍🔧"
        label = f"{role_icon} {name}{verified}"
        rows.append([_callback_button(label[:40], "primary", {"cmd": "mystation", "id": s["station_id"]})])

    text += f"Всего: {len(stations)}. Нажми на АЗС, чтобы обновить статус."
    rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
    await _send(msg, text, vk_keyboard(rows, inline=True))


async def handle_my_station(event: MessageEvent):
    payload = _parse_payload(event)
    station_id = payload.get("id", 0)
    uid = await _ensure_user_from_event(event)

    if not uid or not await is_owner_of_station(uid, station_id):
        await _notify(event, "Это не твоя АЗС", show_alert=True)
        return

    station = await get_station_by_id(station_id)
    if not station:
        await _notify(event, "АЗС не найдена", show_alert=True)
        return

    statuses = await get_station_current_status(station_id)
    text = format_station_card(station, statuses)
    text = "👤 <b>Твоя АЗС — обновление статуса:</b>\n\n" + text

    rows = []
    for fuel in ["92", "95", "98", "diesel"]:
        rows.append([
            _callback_button(f"АИ-{fuel}: ✅", "positive", {"cmd": "oset", "id": station_id, "fuel": fuel, "status": "yes"}),
            _callback_button(f"⚠️", "secondary", {"cmd": "oset", "id": station_id, "fuel": fuel, "status": "low"}),
            _callback_button(f"❌", "negative", {"cmd": "oset", "id": station_id, "fuel": fuel, "status": "no"}),
        ])
    rows.append([_callback_button("◀️ Назад", "secondary", {"cmd": "my_stations"})])
    rows.append([_callback_button("🏠 В начало", "secondary", {"cmd": "home"})])
    await _edit(event, text, vk_keyboard(rows, inline=True))


async def handle_owner_quick_set(event: MessageEvent):
    payload = _parse_payload(event)
    station_id = payload.get("id", 0)
    fuel = payload.get("fuel", "")
    status = payload.get("status", "")

    uid = await _ensure_user_from_event(event)
    if not uid or not await is_owner_of_station(uid, station_id):
        await _notify(event, "Это не твоя АЗС", show_alert=True)
        return

    available_map = {"yes": True, "low": None, "no": False}
    if status not in available_map:
        await _notify(event, "Неизвестный статус", show_alert=True)
        return

    await add_report(
        station_id=station_id,
        user_id=uid,
        fuel_type=fuel,
        available=available_map[status],
        source="owner",
    )
    status_text = {"yes": "✅ есть", "low": "⚠️ кончается", "no": "❌ нет"}[status]
    await _notify(event, f"Записал: АИ-{fuel} — {status_text}", show_alert=True)


# === Premium ===
async def cmd_premium(msg: Message):
    uid = await _ensure_user(msg)
    info = await get_premium_info(uid) if uid else None
    active = await is_premium(uid) if uid else False

    if active and info:
        days_left = (info["expires_at"] - datetime.now()).days if isinstance(info["expires_at"], datetime) else 30
        text = (
            f"💎 <b>Premium активен</b>\n\n"
            f"📅 Осталось дней: <b>{max(days_left, 0)}</b>\n\n"
            f"🔔 Push о завозе — каждый час\n"
            f"💎 Premium-бейдж в профиле\n\n"
            f"Спасибо за поддержку! 🙏"
        )
        await _send(msg, text, vk_main_menu())
        return

    text = (
        f"💎 <b>Бензин рядом · Premium</b>\n\n"
        f"💳 <b>{settings.PREMIUM_PRICE_STARS} Stars</b> · {settings.PREMIUM_DURATION_DAYS} дней\n\n"
        f"🔔 <b>Push о завозе — каждый час</b>\n"
        f"💎 <b>Premium-бейдж</b>\n\n"
        f"🎁 <b>7 дней бесплатно</b> — попробуй!"
    )
    kb = vk_premium_keyboard()
    await _send(msg, text, kb)


# === Donate ===
async def cmd_donate(msg: Message):
    text = (
        "❤️ <b>Поддержать «Бензин рядом»</b>\n\n"
        "Проект бесплатный и работает на энтузиазме. "
        "Твоя поддержка помогает развивать сервис!\n\n"
        "VK Pay пока не подключен. Следи за обновлениями!"
    )
    await _send(msg, text, vk_main_menu())


# === Help text ===
async def cmd_stats(msg: Message):
    from db import get_stats
    stats = await get_stats()
    text = (
        "📊 <b>Статистика «Бензин рядом»:</b>\n\n"
        f"⛽ АЗС в базе: <b>{stats.get('stations_count', 0):,}</b>\n"
        f"👥 Пользователей: <b>{stats.get('users_count', 0):,}</b>\n"
        f"📝 Отчётов за 24ч: <b>{stats.get('reports_24h', 0):,}</b>\n"
        f"🏙 Городов: <b>{stats.get('cities_count', 0)}</b>\n"
    )
    await _send(msg, text, vk_main_menu())


# ====================================================================
# MAIN — VK bot runner
# ====================================================================

async def run_vk_bot():
    """Runs VK bot polling."""
    logger.info(">>> run_vk_bot() НАЧАЛСЯ")
    try:
        from dotenv import load_dotenv
        import os
        load_dotenv()

        vk_token = os.getenv("VK_TOKEN", "")
        logger.info(">>> VK_TOKEN length=%d, prefix=%s", len(vk_token), vk_token[:10] if vk_token else "EMPTY")
        if not vk_token:
            logger.warning("VK_TOKEN не задан — VK-бот НЕ запускается")
            return

        bot = Bot(token=vk_token)
        logger.info("VK-бот инициализирован")
    except Exception as e:
        logger.exception(f">>> run_vk_bot() CRASH during init: {e}")
        return

    # Register handlers
    @bot.on.message(text=["/start", "start"])
    async def on_start(msg: Message):
        await cmd_start(msg)

    @bot.on.message(text=["/help", "help"])
    async def on_help(msg: Message):
        await cmd_help(msg)

    @bot.on.message(text=["/find", "find", "🔍 Найти АЗС"])
    async def on_find(msg: Message):
        await cmd_find(msg)

    @bot.on.message(text=["/subscribe", "subscribe", "🔔 Уведомления"])
    async def on_subscribe(msg: Message):
        await cmd_subscribe(msg)

    @bot.on.message(text=["/register_owner", "register_owner", "👤 Я владелец АЗС"])
    async def on_owner(msg: Message):
        await cmd_register_owner(msg)

    @bot.on.message(text=["/profile", "profile", "👤 Профиль"])
    async def on_profile(msg: Message):
        await cmd_profile(msg)

    @bot.on.message(text=["/my_stations", "my_stations", "🏪 Мои АЗС"])
    async def on_my_stations(msg: Message):
        await cmd_my_stations(msg)

    @bot.on.message(text=["/premium", "premium", "💎 Premium"])
    async def on_premium(msg: Message):
        await cmd_premium(msg)

    @bot.on.message(text=["/donate", "donate", "❤️ Поддержать"])
    async def on_donate(msg: Message):
        await cmd_donate(msg)

    @bot.on.message(text=["/stats", "stats"])
    async def on_stats(msg: Message):
        await cmd_stats(msg)

    @bot.on.message(text=["🏠 В начало"])
    async def on_home_text(msg: Message):
        await cmd_start(msg)

    @bot.on.message(text=["📝 Сообщить о наличии", "/report"])
    async def on_report(msg: Message):
        await _send(
            msg,
            "📝 <b>Выбери город, чтобы сообщить о наличии:</b>",
            vk_report_city_keyboard(),
        )

    # Geolocation
    @bot.on.message()
    async def on_geo_and_text(msg: Message):
        if msg.geo:
            await handle_geo_location(msg)
            return
        if msg.text:
            await handle_text_input(msg)

    # Callback events
    @bot.on.raw_event(MessageEvent)
    async def on_message_event(event: MessageEvent):
        payload = _parse_payload(event)
        cmd = payload.get("cmd", "")

        handlers = {
            "home": handle_home,
            "filters": lambda e: _show_filters(e, payload.get("city", "")),
            "fuel": handle_fuel_filter,
            "emergency": handle_emergency,
            "st": handle_station_detail,
            "back_to_list": handle_station_list_back,
            "report_start": handle_report_start,
            "report_fuel": handle_report_fuel,
            "report_submit": handle_report_submit,
            "report_city_menu": handle_report_city_menu,
            "report_city": handle_report_city,
            "report_pick": handle_report_pick,
            "sub_radius": handle_subscribe_radius,
            "sub_station": handle_sub_station,
            "owner_pick": handle_owner_pick,
            "owner_role": handle_owner_role,
            "owner_inn_skip": handle_owner_inn_skip,
            "mystation": handle_my_station,
            "oset": handle_owner_quick_set,
            "my_stations": lambda e: None,  # placeholder
            "premium_trial": lambda e: None,
            "buy_premium": lambda e: None,
            "promote": lambda e: None,
        }

        handler = handlers.get(cmd)
        if handler:
            try:
                await handler(event)
            except Exception as e:
                logger.exception(f"VK callback error: cmd={cmd} error={e}")
                await event.answer(text=f"⚠️ Ошибка: {e}")
        else:
            await event.answer(text=f"❓ Неизвестная команда: {cmd}")

    logger.info("VK-бот запущен, начинаем polling...")
    try:
        await bot.run_polling()
    except Exception as e:
        logger.exception(f"VK-бот polling CRASHED: {e}")
