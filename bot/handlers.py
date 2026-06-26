"""
Хэндлеры бота «Бензин рядом».
"""
import json
import logging

from aiogram import Dispatcher, F
from aiogram.filters import BaseFilter, Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    WebAppData,
    WebAppInfo,
)

from db import (
    add_owner_station,
    add_report,
    add_subscription,
    activate_premium,
    find_nearest_stations,
    find_stations_by_name,
    get_or_create_user,
    get_owner_stations,
    get_pending_owner_applications,
    get_premium_info,
    get_station_by_id,
    get_station_current_status,
    get_user_id_by_telegram_id,
    is_owner_of_station,
    is_premium,
    log_event,
    set_owner_station_verified,
)
from keyboards import (
    flow_keyboard,
    fuel_type_keyboard,
    main_menu_keyboard,
    report_status_keyboard,
    station_actions_keyboard,
    with_home_inline,
)
from utils import format_distance, format_fuel_status, format_station_card
from config import settings

MINI_APP_URL = settings.MINI_APP_URL


def escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

logger = logging.getLogger(__name__)


# === In-memory кеш для результатов поиска (TTL 60 сек) ===
# Ключ: (round(lat,2), round(lon,2), radius_km)
# Значение: (timestamp, results)
import time as _time

_cache: dict[tuple, tuple[float, list]] = {}
CACHE_TTL_SEC = 60


def _cache_get(lat: float, lon: float, radius_km: int) -> list | None:
    """Получить из кеша если свежий."""
    key = (round(lat, 2), round(lon, 2), radius_km)
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, results = entry
    if _time.time() - ts > CACHE_TTL_SEC:
        _cache.pop(key, None)
        return None
    return results


def _cache_set(lat: float, lon: float, radius_km: int, results: list) -> None:
    key = (round(lat, 2), round(lon, 2), radius_km)
    _cache[key] = (_time.time(), results)


# === Monkey-patch: автоматически добавляем inline-кнопку «🏠 В начало» во все сообщения ===
_original_message_answer = Message.answer


async def _patched_message_answer(self, text, **kwargs):
    """Обёртка над Message.answer — добавляет кнопку «В начало» в inline_markup если есть."""
    markup = kwargs.get("reply_markup")
    if isinstance(markup, InlineKeyboardMarkup):
        has_home = any(
            btn.callback_data == "go_home"
            for row in markup.inline_keyboard
            for btn in row
        )
        if not has_home:
            kwargs["reply_markup"] = with_home_inline(markup)
    return await _original_message_answer(self, text, **kwargs)


Message.answer = _patched_message_answer  # type: ignore[assignment]


# === FSM: подписки ===
class SubscribeStates(StatesGroup):
    waiting_geo = State()
    waiting_radius = State()


# Простое in-memory состояние для owner-режима (non-FSM)
# Ждём текстовый ввод названия/адреса АЗС от пользователя
_waiting_owner_search: set[int] = set()
# Ждём выбор роли (владелец/работник) после выбора АЗС
_waiting_owner_role: dict[int, int] = {}  # telegram_id -> station_id
# Ждём ИНН (опционально) перед финальной регистрацией
_waiting_inn_nosm: set[int] = set()
# Полное состояние owner-flow: {station_id, role, inn}
_owner_state: dict[int, dict] = {}


def _tg_id(message) -> int:
    """Возвращает telegram_id из сообщения."""
    return message.from_user.id


class _OwnerWaitingInnFilter(BaseFilter):
    """Фильтр: пользователь в процессе ввода ИНН в non-FSM owner-режиме."""
    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id in _waiting_inn_nosm


class _OwnerWaitingSearchFilter(BaseFilter):
    """Фильтр: пользователь в процессе поиска АЗС в non-FSM owner-режиме."""
    async def __call__(self, message: Message) -> bool:
        if message.from_user is None or not message.text:
            return False
        if message.text.startswith("/"):
            return False  # это команда
        return message.from_user.id in _waiting_owner_search


# === /start — Welcome-цепочка (3 сообщения) ===
async def cmd_start(message: Message):
    uid = await get_or_create_user(message)
    await log_event(uid, "bot_start")

    first_name = message.from_user.first_name or "друг"

    # === Сообщение 1: Hero ===
    hero = WELCOME_1
    hero_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗺 Открыть карту АЗС", web_app=WebAppInfo(url=MINI_APP_URL))],
        [InlineKeyboardButton(text="🔍 Попробовать inline-поиск", switch_inline_query="92 Иваново")],
        [InlineKeyboardButton(text="🏪 Я владелец АЗС", callback_data="go_register_owner")],
    ])
    await message.answer(hero, reply_markup=with_home_inline(hero_kb))

    # === Сообщение 2: Inline-фича ===
    inline_msg = WELCOME_2
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Попробовать здесь →", switch_inline_query_current_chat="95 Иваново")],
    ])
    await message.answer(inline_msg, reply_markup=with_home_inline(inline_kb))

    # === Сообщение 3: Crowdsource + бейджи ===
    crowdsource = WELCOME_3
    crowdsource_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Сообщить о наличии", web_app=WebAppInfo(url=MINI_APP_URL))],
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="cmd_profile"),
         InlineKeyboardButton(text="ℹ️ Все команды", callback_data="cmd_help")],
    ])
    await message.answer(crowdsource, reply_markup=with_home_inline(crowdsource_kb))


# === /help ===
async def cmd_help(message: Message):
    text = HELP_TEXT
    await message.answer(text, reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗺 Открыть Mini App", web_app=WebAppInfo(url=MINI_APP_URL))],
    ])))


# === /find ===
async def cmd_find(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "📍 <b>Нажми кнопку ниже, чтобы отправить геолокацию.</b>\n\n"
        "Или просто напиши город / сеть / название АЗС.",
        reply_markup=kb,
    )


# === /subscribe ===
async def cmd_subscribe(message: Message, state: FSMContext):
    await state.set_state(SubscribeStates.waiting_geo)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "🔔 <b>Подписка на уведомления о завозе.</b>\n\n"
        "Отправь геолокацию — буду присылать уведомления, когда "
        "в радиусе 5 км от тебя появится бензин.",
        reply_markup=kb,
    )


# === /register_owner — регистрация владельца/работника АЗС ===
async def cmd_register_owner(message: Message, state: FSMContext):
    """Начало регистрации владельца/работника. Просит название/адрес АЗС."""
    _waiting_owner_search.add(_tg_id(message))
    await state.clear()
    await message.answer(
        "👤 <b>Регистрация владельца или работника АЗС.</b>\n\n"
        "<b>Можно регистрироваться и владельцу, и работнику заправки</b> — "
        "обоим мы даём возможность одной кнопкой обновлять статус топлива.\n\n"
        "📝 <b>Введи название, адрес или город</b> АЗС, где ты работаешь.\n\n"
        "<i>Например: <code>Лукойл Иваново</code>, <code>Ленина 45</code>, "
        "<code>Газпром Шуя</code>.</i>",
        reply_markup=main_menu_keyboard(),
    )


async def owner_inn_input_nosm(message: Message):
    """Принимает ИНН от пользователя (non-FSM flow)."""
    telegram_id = _tg_id(message)
    if telegram_id not in _waiting_inn_nosm:
        return  # не наш случай
    state = _owner_state.get(telegram_id)
    if not state or "station_id" not in state:
        _waiting_inn_nosm.discard(telegram_id)
        return
    inn = (message.text or "").strip()
    if inn and not inn.isdigit():
        await message.answer("ИНН должен содержать только цифры. Попробуй ещё раз или нажми «Пропустить».")
        return
    _waiting_inn_nosm.discard(telegram_id)
    await owner_finish_no_fsm(message, state["station_id"], state.get("role", "owner"), inn=inn or None)


async def owner_inn_skip_nosm(callback: CallbackQuery):
    """Пропуск ИНН (non-FSM flow)."""
    telegram_id = _tg_id(callback.message)
    state = _owner_state.get(telegram_id)
    _waiting_inn_nosm.discard(telegram_id)
    if not state or "station_id" not in state:
        await callback.answer("Ошибка. Попробуй сначала.", show_alert=True)
        return
    await owner_finish_no_fsm(callback.message, state["station_id"], state.get("role", "owner"), inn=None)
    await callback.answer()


async def owner_search_input(message: Message):
    """Обрабатывает текстовый ввод — ищет АЗС по названию/адресу/городу."""
    telegram_id = _tg_id(message)
    if telegram_id not in _waiting_owner_search:
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введи минимум 2 символа.")
        return

    stations = await find_stations_by_name(query, limit=10)
    if not stations:
        await message.answer(
            f"😔 По запросу <b>«{query}»</b> ничего не нашёл.\n\n"
            f"Попробуй написать по-другому — например:\n"
            f"• <code>Лукойл</code> или <code>Газпром</code> (сеть)\n"
            f"• <code>Иваново</code> (город)\n"
            f"• <code>Ленина 45</code> (адрес)\n\n"
            f"Или нажми «👤 Я владелец» ещё раз, чтобы начать сначала.",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = f"🔍 Нашёл <b>{len(stations)}</b> АЗС по запросу «{query}». Выбери свою:"
    buttons = []
    for s in stations:
        name = (s.get("name") or "АЗС")[:30]
        operator = (s.get("operator") or "")[:15]
        city = (s.get("city") or "")[:12]
        label = f"⛽ {name}"
        if operator:
            label += f" · {operator}"
        if city:
            label += f" ({city})"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"owner_pick_search:{s['id']}")
        ])
    buttons.append([
        InlineKeyboardButton(text="❌ Отменить", callback_data="owner_search_cancel"),
    ])

    _waiting_owner_search.discard(telegram_id)
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def owner_pick_search(callback: CallbackQuery):
    """Пользователь выбрал АЗС из результатов поиска → спрашиваем роль."""
    station_id = int(callback.data.split(":", 1)[1])
    telegram_id = _tg_id(callback.message)

    station = await get_station_by_id(station_id)
    if not station:
        await callback.answer("АЗС не найдена", show_alert=True)
        return

    _waiting_owner_role[telegram_id] = station_id
    _owner_state[telegram_id] = {"station_id": station_id}

    name = station.get("name", "АЗС")
    operator = station.get("operator") or ""
    header = f"⛽ <b>{name}</b>"
    if operator:
        header += f" ({operator})"

    await callback.message.answer(
        f"{header}\n\n"
        f"Кто ты на этой АЗС?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Я владелец", callback_data="owner_role:owner")],
            [InlineKeyboardButton(text="👨‍🔧 Я работник", callback_data="owner_role:employee")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="owner_search_cancel")],
        ]),
    )
    await callback.answer()


async def owner_search_cancel(callback: CallbackQuery):
    """Отмена поиска/регистрации."""
    telegram_id = _tg_id(callback.message)
    _waiting_owner_search.discard(telegram_id)
    _waiting_owner_role.pop(telegram_id, None)
    _owner_state.pop(telegram_id, None)
    _waiting_inn_nosm.discard(telegram_id)
    await callback.message.answer(
        "Ок, отменил. Если захочешь зарегистрироваться — нажми «👤 Я владелец».",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


async def owner_role_picked(callback: CallbackQuery):
    """Пользователь выбрал роль (владелец/работник) → спрашиваем ИНН."""
    role = callback.data.split(":", 1)[1]
    if role not in ("owner", "employee"):
        await callback.answer("Неизвестная роль", show_alert=True)
        return

    telegram_id = _tg_id(callback.message)
    station_id = _waiting_owner_role.pop(telegram_id, None)
    if not station_id:
        await callback.answer("Ошибка. Попробуй сначала.", show_alert=True)
        return

    _owner_state[telegram_id] = {"station_id": station_id, "role": role}
    _waiting_inn_nosm.add(telegram_id)

    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else f"#{station_id}"
    role_text = "владельцем" if role == "owner" else "работником"

    await callback.message.answer(
        f"⛽ <b>{name}</b> — ты зарегистрирован как <b>{role_text}</b>.\n\n"
        f"📋 Укажи ИНН организации (10 или 12 цифр) — <i>опционально, "
        f"ускорит модерацию и получение ✓ Verified.</i>\n\n"
        f"Если не хочешь — нажми «Пропустить».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="owner_inn_nosm:skip")],
        ]),
    )
    await callback.answer()


async def owner_finish_no_fsm(message, station_id: int, role: str = "owner", inn: str | None = None):
    """Завершает регистрацию: создаёт owner_stations и users.is_owner=1."""
    telegram_id = _tg_id(message)
    _owner_state.pop(telegram_id, None)
    _waiting_owner_role.pop(telegram_id, None)
    _waiting_inn_nosm.discard(telegram_id)

    await get_or_create_user(message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await message.answer(
            "Ошибка. Нажми /start и попробуй снова.",
            reply_markup=main_menu_keyboard(),
        )
        return

    result = await add_owner_station(
        user_id=uid, station_id=station_id, inn=inn, role=role,
    )
    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else f"#{station_id}"
    role_text = "владелец" if role == "owner" else "работник"

    if result == -1:
        text = f"ℹ️ Ты уже зарегистрирован на АЗС «{name}»."
    else:
        text = (
            f"✅ <b>Готово! Ты зарегистрирован как {role_text} АЗС «{name}».</b>\n\n"
            f"Обновлять статус: /my_stations\n"
            f"После модерации появится значок ✓ Verified."
        )
    await message.answer(text, reply_markup=main_menu_keyboard())


# === /my_stations — мои АЗС ===
async def cmd_my_stations(message: Message):
    await get_or_create_user(message)
    telegram_id = _tg_id(message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await message.answer("Нажми /start сначала.", reply_markup=main_menu_keyboard())
        return

    stations = await get_owner_stations(uid)
    if not stations:
        await message.answer(
            "ℹ️ Ты не зарегистрирован как владелец/работник АЗС.\n\n"
            "Нажми «👤 Я владелец» или команду /register_owner.",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = "📊 <b>Твои АЗС:</b>\n\n"
    buttons = []
    for s in stations:
        name = (s.get("name") or "АЗС")[:30]
        verified = " ✓" if s.get("is_verified") else ""
        role = s.get("role") or "owner"
        role_icon = "👑" if role == "owner" else "👨‍🔧"
        operator = s.get("operator") or ""
        label = f"{role_icon} {name}{verified}"
        if operator:
            label += f" · {operator[:15]}"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"mystation:{s['station_id']}")
        ])

    text += f"Всего: {len(stations)}. Нажми на АЗС, чтобы обновить статус."
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def show_my_station(callback: CallbackQuery):
    """Показывает карточку своей АЗС с кнопками обновления статуса."""
    station_id = int(callback.data.split(":")[1])
    await get_or_create_user(callback.message)
    telegram_id = _tg_id(callback.message)
    uid = await get_user_id_by_telegram_id(telegram_id)

    if not uid or not await is_owner_of_station(uid, station_id):
        await callback.answer("Это не твоя АЗС", show_alert=True)
        return

    station = await get_station_by_id(station_id)
    if not station:
        await callback.answer("АЗС не найдена", show_alert=True)
        return

    statuses = await get_station_current_status(station_id)
    text = format_station_card(station, statuses)
    text = "👤 <b>Твоя АЗС — обновление статуса:</b>\n\n" + text

    # Кнопки быстрого обновления по типу топлива
    buttons = []
    for fuel in ["92", "95", "98", "diesel"]:
        buttons.append([
            InlineKeyboardButton(
                text=f"АИ-{fuel}: ✅",
                callback_data=f"oset:{station_id}:{fuel}:yes",
            ),
            InlineKeyboardButton(
                text=f"⏱",
                callback_data=f"oset:{station_id}:{fuel}:queue",
            ),
            InlineKeyboardButton(
                text=f"⚠️",
                callback_data=f"oset:{station_id}:{fuel}:low",
            ),
            InlineKeyboardButton(
                text=f"❌",
                callback_data=f"oset:{station_id}:{fuel}:no",
            ),
        ])
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="my_stations_back"),
    ])

    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


async def owner_quick_set(callback: CallbackQuery):
    """Быстрое обновление статуса от владельца."""
    parts = callback.data.split(":")
    station_id = int(parts[1])
    fuel = parts[2]
    status = parts[3]

    await get_or_create_user(callback.message)
    telegram_id = _tg_id(callback.message)
    uid = await get_user_id_by_telegram_id(telegram_id)

    if not uid or not await is_owner_of_station(uid, station_id):
        await callback.answer("Это не твоя АЗС", show_alert=True)
        return

    available_map = {"yes": True, "queue": True, "low": None, "no": False}
    queue_map = {"yes": None, "queue": 5, "low": None, "no": None}
    if status not in available_map:
        await callback.answer("Неизвестный статус", show_alert=True)
        return

    await add_report(
        station_id=station_id,
        user_id=uid,
        fuel_type=fuel,
        available=available_map[status],
        queue_size=queue_map[status],
        source="owner",
    )

    status_text = {"yes": "✅ есть", "queue": "🕐 очередь", "low": "⚠️ кончается", "no": "❌ нет"}[status]
    celebration = await _check_and_celebrate_badges(uid)
    await callback.answer(f"Записал: АИ-{fuel} — {status_text}{celebration[:200]}", show_alert=True)


async def my_stations_back(callback: CallbackQuery):
    """Возврат к списку своих АЗС."""
    await cmd_my_stations(callback.message)
    await callback.answer()


# === /moderate — модерация заявок (только для админов) ===
async def cmd_moderate(message: Message):
    if not settings.is_admin(user_id=message.from_user.id, username=message.from_user.username):
        return
    apps = await get_pending_owner_applications()
    if not apps:
        await message.answer("Нет заявок на модерацию.")
        return

    for app in apps[:5]:  # максимум 5 за раз
        name = app.get("station_name") or "АЗС"
        city = app.get("city") or ""
        inn = app.get("inn") or "—"
        first = app.get("first_name") or ""
        username = f"@{app['username']}" if app.get("username") else ""

        text = (
            f"📋 <b>Заявка #{app['id']}</b>\n\n"
            f"👤 {first} {username} (id={app['user_id']})\n"
            f"⛽ {name}" + (f" ({city})" if city else "") + "\n"
            f"📇 ИНН: {inn}\n"
            f"📅 {str(app.get('created_at', ''))[:16]}"
        )
        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{app['id']}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{app['id']}"),
                ],
            ]),
        )


async def approve_owner(callback: CallbackQuery):
    if not settings.is_admin(user_id=callback.from_user.id, username=callback.from_user.username):
        await callback.answer("Нет прав", show_alert=True)
        return
    app_id = int(callback.data.split(":")[1])
    await set_owner_station_verified(app_id, callback.from_user.id)
    await callback.message.edit_text("✅ Одобрено. ✓ Verified поставлен.")
    await callback.answer()


# === /my_id — показать свой telegram_id (для настройки ADMIN_IDS) ===
async def cmd_my_id(message: Message):
    user = message.from_user
    await message.answer(
        f"🆔 <b>Твой Telegram ID:</b> <code>{user.id}</code>\n\n"
        f"Username: @{user.username or '—'}\n\n"
        f"<i>Чтобы получить права админа, добавь этот ID в "
        f"<code>ADMIN_IDS</code> в <code>bot/.env</code>.</i>"
    )


# === /find_raw lat lon — поиск с произвольными координатами (для отладки) ===
async def cmd_find_raw(message: Message):
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(
            "Использование: <code>/find_raw 56.97 40.92</code>\n"
            "(lat lon через пробел)"
        )
        return
    try:
        lat = float(parts[1])
        lon = float(parts[2])
    except ValueError:
        await message.answer("Координаты должны быть числами")
        return

    stations = await find_nearest_stations(lat, lon, limit=10, radius_km=5)
    if not stations:
        await message.answer(f"В радиусе 5 км от ({lat}, {lon}) ничего нет.")
        return

    text = f"🔍 <b>Координаты:</b> {lat}, {lon}\n\nБлижайшие 10 (радиус 5 км):\n\n"
    for s in stations:
        d = s.get("distance_km", 0)
        op = s.get("operator") or "—"
        text += f"  {d:5.1f} км — {s.get('name', 'АЗС')[:25]} ({op[:15]})\n"
    await message.answer(text)


# === /premium — Telegram Stars ===
async def cmd_premium(message: Message):
    await get_or_create_user(message)
    telegram_id = _tg_id(message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    info = await get_premium_info(uid) if uid else None
    active = await is_premium(uid) if uid else False

    if active and info:
        days_left = (datetime.fromisoformat(info["expires_at"]) - datetime.now()).days
        text = PREMIUM_ACTIVE.format(
            days_left=max(days_left, 0),
            expires_at=info["expires_at"][:10],
        )
        await message.answer(text, reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=[])))
        return

    text = PREMIUM_OFFER.format(
        price=settings.PREMIUM_PRICE_STARS,
        days=settings.PREMIUM_DURATION_DAYS,
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎁 Попробовать 7 дней бесплатно",
            callback_data="premium_trial",
        )],
        [InlineKeyboardButton(
            text=f"💎 Купить за {settings.PREMIUM_PRICE_STARS} Stars",
            callback_data="buy_premium",
        )],
    ])
    await message.answer(text, reply_markup=with_home_inline(kb))


async def premium_trial_callback(callback: CallbackQuery):
    """Активирует 7-дневный trial Premium без оплаты."""
    await callback.answer()
    await get_or_create_user(callback.message)
    uid = await get_user_id_by_telegram_id(_tg_id(callback.message))
    if not uid:
        await callback.message.answer("Ошибка: пользователь не найден.")
        return
    if await is_premium(uid):
        await callback.message.answer("У тебя уже есть Premium. Используй /premium для проверки.")
        return
    # Trial на 7 дней
    result = await activate_premium(
        user_id=uid,
        days=7,
        charge_id="trial_7d",
        stars=0,
    )
    await callback.message.answer(
        f"🎁 <b>Trial Premium активирован!</b>\n\n"
        f"📅 На 7 дней (до {result['expires_at'][:10]})\n\n"
        f"<b>Что попробовать прямо сейчас:</b>\n"
        f"1️⃣ Открой карту — увидишь 500 АЗС вместо 100\n"
        f"2️⃣ Подпишись на АЗС — push придёт через час если будет завоз\n"
        f"3️⃣ Открой карточку АЗС — увидишь график цены\n\n"
        f"Если понравится — /premium для оплаты Stars.\n"
        f"Если нет — ничего не произойдёт, вернёшься на Free.",
    )
    await log_event(uid, "premium_trial_activated")


async def buy_premium_callback(callback: CallbackQuery):
    """Отправляет invoice на оплату Stars."""
    await get_or_create_user(callback.message)
    prices = [LabeledPrice(label=f"Premium · {settings.PREMIUM_DURATION_DAYS} дней", amount=settings.PREMIUM_PRICE_STARS)]
    try:
        await callback.message.answer_invoice(
            title="Бензин рядом · Premium",
            description=f"Premium-подписка на {settings.PREMIUM_DURATION_DAYS} дней: push без cooldown, расширенная аналитика, premium-бейдж.",
            payload="premium_30d",
            provider_token="",  # для Stars — пустая строка
            currency="XTR",  # XTR = Telegram Stars
            prices=prices,
        )
    except Exception as e:
        logger.exception("Invoice send failed: %s", e)
        await callback.answer("Ошибка отправки invoice", show_alert=True)
        return
    await callback.answer()


async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    """Подтверждает pre-checkout для Stars."""
    await pre_checkout.answer(ok=True)


async def successful_payment_handler(message: Message):
    """Обрабатывает успешную оплату Stars."""
    sp = message.successful_payment
    if not sp or sp.currency != "XTR":
        return
    if sp.invoice_payload != "premium_30d":
        await message.answer("⚠️ Неизвестный платёж. Напишите в поддержку.")
        return
    await get_or_create_user(message)
    uid = await get_user_id_by_telegram_id(_tg_id(message))
    if not uid:
        await message.answer("Ошибка: пользователь не найден.")
        return
    result = await activate_premium(
        user_id=uid,
        days=settings.PREMIUM_DURATION_DAYS,
        charge_id=sp.telegram_payment_charge_id,
        stars=sp.total_amount,
    )
    await message.answer(
        f"🎉 <b>Premium активирован!</b>\n\n"
        f"📅 Действует до: {result['expires_at'][:10]}\n"
        f"💎 Спасибо за поддержку «Бензин рядом»!\n\n"
        f"🔔 Push без cooldown, 📊 аналитика, 🚗 premium-бейдж — всё твоё.",
    )
    await log_event(uid, "premium_activated", payload={"stars": sp.total_amount})


# === /my_stations ===
async def cmd_my_stations(message: Message):
    await get_or_create_user(message)
    telegram_id = _tg_id(message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await message.answer("Сначала нажми /start")
        return
    stations = await get_owner_stations(uid)
    if not stations:
        await message.answer(
            "У тебя пока нет зарегистрированных АЗС.\n"
            "Нажми /register_owner, чтобы добавить.",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = "🏪 <b>Твои АЗС:</b>\n\n"
    kb_rows = []
    for s in stations:
        verified = "✅" if s.get("is_verified") else "⏳"
        name = s.get("name", "АЗС")
        address = s.get("address") or s.get("city") or "—"
        text += f"{verified} <b>{name}</b>\n   📍 {address}\n\n"
        kb_rows.append([
            InlineKeyboardButton(
                text=f"{verified} {name[:20]}",
                callback_data=f"mystation:{s.get('id', s.get('station_id'))}",
            )
        ])
    kb_rows.append([InlineKeyboardButton(text="🏪 Зарегистрировать ещё", callback_data="go_register_owner")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


# === /analytics — аналитика АЗС (только для владельца) ===
async def cmd_analytics(message: Message):
    await get_or_create_user(message)
    telegram_id = _tg_id(message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await message.answer("Сначала нажми /start")
        return
    stations = await get_owner_stations(uid)
    if not stations:
        await message.answer(
            "У тебя нет зарегистрированных АЗС.\n"
            "Нажми /register_owner, чтобы добавить и увидеть аналитику.",
        )
        return

    # Собираем аналитику по всем АЗС
    from db import get_station_analytics
    total_views = 0
    total_reports = 0
    total_subs = 0
    for s in stations:
        sid = s.get("id") or s.get("station_id")
        a = await get_station_analytics(sid, days=30)
        total_views += a.get("views", 0)
        total_reports += a.get("reports_30d", 0)
        total_subs += a.get("subscribers", 0)

    text = (
        f"📊 <b>Аналитика за 30 дней:</b>\n\n"
        f"👁 Просмотры: <b>{total_views}</b>\n"
        f"📝 Отчёты (все): <b>{total_reports}</b>\n"
        f"🔔 Подписчики: <b>{total_subs}</b>\n\n"
    )
    if total_views == 0 and total_reports == 0:
        text += "💡 <i>Данные появятся когда водители начнут открывать карточки и оставлять отчёты.</i>\n\n"

    text += "<b>По АЗС:</b>\n"
    for s in stations[:10]:
        sid = s.get("id") or s.get("station_id")
        a = await get_station_analytics(sid, days=30)
        text += (
            f"\n{ '✅' if s.get('is_verified') else '⏳' } <b>{s.get('name', 'АЗС')[:30]}</b>\n"
            f"   👁 {a.get('views', 0)} · 📝 {a.get('reports_30d', 0)} · 🔔 {a.get('subscribers', 0)}"
        )
        if a.get("avg_price"):
            text += f" · 💰 {a.get('avg_price'):.2f}₽"

    kb_rows = []
    for s in stations[:5]:
        sid = s.get("id") or s.get("station_id")
        kb_rows.append([InlineKeyboardButton(
            text=f"📊 {s.get('name', 'АЗС')[:25]}", callback_data=f"analy:{sid}",
        )])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


async def station_analytics_callback(callback: CallbackQuery):
    """Показывает детальную аналитику одной АЗС."""
    await callback.answer()
    try:
        station_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return
    from db import get_station_analytics
    a = await get_station_analytics(station_id, days=30)
    text = (
        f"📊 <b>Аналитика АЗС #{station_id} · 30 дней:</b>\n\n"
        f"👁 Просмотры: <b>{a.get('views', 0)}</b>\n"
        f"📝 Отчёты: <b>{a.get('reports_30d', 0)}</b>\n"
        f"🔔 Подписчики: <b>{a.get('subscribers', 0)}</b>\n"
    )
    if a.get("avg_price"):
        text += f"💰 Средняя цена: <b>{a.get('avg_price'):.2f}₽</b>\n"
    if a.get("last_report_at"):
        text += f"⏰ Последний отчёт: {str(a.get('last_report_at'))[:16]}\n"

    fuels = a.get("reports_by_fuel", {})
    if fuels:
        text += "\n<b>По топливу:</b>\n"
        for fuel, data in fuels.items():
            line = f"  ⛽ АИ-{fuel}: {data['count']} отчётов"
            if data.get("avg_price"):
                line += f", ~{data['avg_price']:.2f}₽"
            text += line + "\n"

    # Мини-график просмотров (последние 7 дней)
    chart = a.get("views_chart", [])[-7:]
    if chart:
        max_v = max((c["count"] for c in chart), default=1) or 1
        text += "\n<b>Просмотры по дням:</b>\n"
        for c in chart:
            bar = "█" * int(c["count"] / max_v * 10) if max_v > 0 else ""
            text += f"  {c['date']}: {bar} {c['count']}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗺 Открыть на карте", web_app=WebAppInfo(url=f"{MINI_APP_URL}?station={station_id}"))],
    ])
    await callback.message.answer(text, reply_markup=with_home_inline(kb))


# === Inline mode: @benzyn_ryadom_bot 92 Иваново ===
async def inline_search(inline_query: InlineQuery):
    """Поиск АЗС прямо в любом чате через @bot.

    Формат запроса:
    - @bot 92 Иваново         → 92-й в Иваново
    - @bot Иваново            → все АЗС в Иваново
    - @bot Лукойл              → все Лукойлы
    - @bot 95                   → все 95-е
    """
    query = (inline_query.query or "").strip()
    if len(query) < 2:
        await inline_query.answer(
            [],
            switch_pm_text="Введите запрос: город, сеть или тип топлива",
            switch_pm_parameter="inline_help",
            cache_time=10,
        )
        return

    # Парсим: выделяем числа (92/95/98/diesel) и остальное
    fuel_keywords = {"92", "95", "98", "100", "дизель", "diesel", "газ", "lpg"}
    tokens = query.lower().split()
    fuel = None
    city_tokens = []
    for t in tokens:
        if t in fuel_keywords or (t.isdigit() and t in {"92", "95", "98", "100"}):
            fuel = t
        else:
            city_tokens.append(t)
    city_query = " ".join(city_tokens).strip()

    # Ищем по city/operator
    if city_query:
        stations = await find_stations_by_name(city_query, limit=20)
    else:
        # Только fuel без города — выдаём пустой результат с подсказкой
        await inline_query.answer(
            [],
            switch_pm_text="Укажите город или сеть, например: 92 Иваново",
            switch_pm_parameter="inline_help",
            cache_time=10,
        )
        return

    # Bulk-получение статусов
    if stations:
        from db import get_stations_with_statuses
        stations = await get_stations_with_statuses(stations)

    # Фильтруем по fuel если указан
    if fuel:
        if fuel == "дизель":
            fuel = "diesel"
        elif fuel == "газ":
            fuel = "lpg"

        def has_fuel(s, fuel_type):
            for st in s.get("statuses", []):
                if st.get("fuel_type") == fuel_type:
                    if st.get("available") is True or st.get("available") == 1:
                        return True
            return False

        stations = [s for s in stations if has_fuel(s, fuel)]

    if not stations:
        await inline_query.answer(
            [],
            switch_pm_text="Ничего не найдено. Откройте бота для подробного поиска.",
            switch_pm_parameter="inline_help",
            cache_time=10,
        )
        return

    # Inline results
    results = []
    for i, s in enumerate(stations[:10]):
        statuses = s.get("statuses", [])
        status_icons = " ".join(
            {"92": "⛽92", "95": "⛽95", "98": "⛽98", "diesel": "🛢"}.get(
                st.get("fuel_type"), ""
            )
            for st in statuses
            if st.get("available") in (True, 1, None, 2)
        )
        address = s.get("address") or f"{s.get('lat', 0):.4f}, {s.get('lon', 0):.4f}"
        name = s.get("name") or "АЗС"
        operator = s.get("operator") or ""
        city = s.get("city") or ""
        lat = s.get("lat", 0)
        lon = s.get("lon", 0)
        verified = s.get("is_verified", False)

        text = f"{'✓ ' if verified else ''}⛽ <b>{name}</b>\n"
        if operator and operator != name:
            text += f"🏢 {operator}\n"
        if address:
            text += f"📍 {address}\n"
        if city:
            text += f"🏙 {city}\n"
        if status_icons:
            text += f"\n{status_icons}"

        # Inline-кнопки под результатом
        station_id = s["id"]
        yandex_url = f"https://yandex.ru/maps/?rtext=~{lat},{lon}&rtt=auto"
        buttons = [
            [
                InlineKeyboardButton(
                    text="🗺 Маршрут",
                    url=yandex_url,
                ),
                InlineKeyboardButton(
                    text="🔔 Подписаться",
                    callback_data=f"sub_station:{station_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 Подробнее",
                    callback_data=f"st:{station_id}",
                ),
            ],
        ]

        results.append(
            InlineQueryResultArticle(
                id=f"st:{station_id}:{i}",
                title=f"{'✓ ' if verified else ''}⛽ {name}",
                description=f"{address[:80]} | {status_icons[:30]}",
                input_message_content=InputTextMessageContent(
                    message_text=text,
                    parse_mode="HTML",
                ),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )
        )

    await inline_query.answer(results, cache_time=30, is_personal=False)


# === handle_main_button — обновляем под новые кнопки ===
async def handle_main_button(message: Message, state: FSMContext = None):
    text = (message.text or "").strip()

    # Сначала — глобальный «В начало»
    if text == "🏠 В начало":
        await go_home_text(message, state)
        return

    if text == "🔍 Найти АЗС":
        await cmd_find(message)
    elif text == "📝 Сообщить":
        await message.answer(
            "📝 <b>Сообщить о наличии топлива</b>\n\n"
            "Открой карточку АЗС через «🔍 Найти АЗС», затем нажми «📝 Сообщить».",
            reply_markup=main_menu_keyboard(),
        )
    elif text == "🔔 Подписки":
        await message.answer(
            "🔔 <b>Подписки на уведомления</b>\n\n"
            "Отправь команду /subscribe и геолокацию — буду присылать алерты о завозе.",
            reply_markup=main_menu_keyboard(),
        )
    elif text == "🗺 Карта":
        await message.answer(
            "🗺 Открой карту в Mini App:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🗺 Открыть карту",
                    web_app=WebAppInfo(url="https://benzin-mini.vercel.app"),
                )],
            ]),
        )
    elif text == "👤 Профиль":
        await cmd_profile(message)
    elif text in ("👤 Я владелец", "Я владелец", "👤 Я владелец/работник АЗС",
                  "👤 Владелец/Работник АЗС", "Владелец/Работник АЗС"):
        # Помечаем, что от этого юзера ждём текстовый ввод
        _waiting_owner_search.add(_tg_id(message))
        await message.answer(
            "👤 <b>Регистрация владельца или работника АЗС.</b>\n\n"
            "<b>Можно регистрироваться и владельцу, и работнику заправки</b> — "
            "обоим мы даём возможность одной кнопкой обновлять статус топлива.\n\n"
            "📝 <b>Введи название, адрес или город</b> АЗС, где ты работаешь.\n\n"
            "<i>Например: <code>Лукойл Иваново</code>, <code>Ленина 45</code>, "
            "<code>Газпром Шуя</code>.</i>",
            reply_markup=flow_keyboard(),
        )
    elif text in ("📊 Мои АЗС", "Мои АЗС"):
        await cmd_my_stations(message)
    else:
        await handle_text_search(message)


# === /profile ===
async def cmd_profile(message: Message):
    await get_or_create_user(message)
    telegram_id = _tg_id(message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await message.answer("Профиль не найден. Нажми /start")
        return

    from db import get_user_stats_summary
    stats = await get_user_stats_summary(uid)
    if not stats:
        await message.answer("Профиль не найден.")
        return

    text = (
        f"👤 <b>Твой профиль:</b>\n\n"
        f"🆔 Telegram ID: <code>{telegram_id}</code>\n"
        f"📊 Репутация: <b>{stats.get('reputation', 0)}</b>/100\n"
        f"📝 Отчётов сделано: <b>{stats.get('total_reports', 0)}</b>\n"
        f"✅ Подтверждено: <b>{stats.get('confirmed_reports', 0)}</b>\n"
    )
    if stats.get("region") or stats.get("city"):
        loc = ", ".join(filter(None, [stats.get("city"), stats.get("region")]))
        text += f"📍 Регион: {loc}\n"

    # Premium badge
    if await is_premium(uid):
        text += "\n⭐ <b>Premium</b> — push без cooldown, расширенная аналитика\n"

    badges = stats.get("badges", [])
    if badges:
        text += f"\n🏆 <b>Твои бейджи ({len(badges)}):</b>\n"
        for b in badges:
            text += f"  {b['emoji']} <b>{b['name']}</b> — {b['desc']}\n"
    else:
        text += "\n🎯 Сделай первый отчёт, чтобы получить бейдж 🥉 «Новичок»!"

    kb_rows = [
        [InlineKeyboardButton(text="🗺 Открыть карту", web_app=WebAppInfo(url=MINI_APP_URL))],
        [InlineKeyboardButton(text="🏪 Зарегистрировать АЗС", callback_data="go_register_owner")],
    ]
    if not await is_premium(uid):
        kb_rows.append([InlineKeyboardButton(text=f"⭐ Купить Premium за {settings.PREMIUM_PRICE_STARS} Stars", callback_data="cmd_premium")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer(text, reply_markup=with_home_inline(kb))


# === /profile callback (из /start) ===
async def profile_callback(callback: CallbackQuery):
    await callback.answer()
    await cmd_profile(callback.message)


# === help callback (из /start) ===
async def help_callback(callback: CallbackQuery):
    await callback.answer()
    await cmd_help(callback.message)


# === premium callback (из /profile) ===
async def premium_callback(callback: CallbackQuery):
    await callback.answer()
    await cmd_premium(callback.message)


# === go_register_owner callback (из /start) ===
async def go_register_owner_callback(callback: CallbackQuery):
    await callback.answer()
    await cmd_register_owner(callback.message, None)


# === /stats ===
async def cmd_stats(message: Message):
    from db import get_stats
    stats = await get_stats()
    text = (
        "📊 <b>Статистика «Бензин рядом»:</b>\n\n"
        f"⛽ АЗС в базе: <b>{stats.get('stations_count', 0):,}</b>\n"
        f"👥 Пользователей: <b>{stats.get('users_count', 0):,}</b>\n"
        f"📝 Отчётов за 24ч: <b>{stats.get('reports_24h', 0):,}</b>\n"
        f"🏙 Городов: <b>{stats.get('cities_count', 0)}</b>\n"
    )

    # === Источники (мониторинг) ===
    try:
        sources = await db._fetch("""
            SELECT source,
                   COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as h1,
                   COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as h24,
                   COUNT(*) as total,
                   MAX(created_at) as last_update
            FROM reports
            GROUP BY source
            ORDER BY total DESC
        """)
        if sources:
            text += "\n<b>📡 Источники:</b>\n"
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            for s in sources:
                hours = (now - s["last_update"]).total_seconds() / 3600
                status = "✅" if hours < 1 else ("🟡" if hours < 6 else "🔴")
                text += (
                    f"  {status} <code>{s['source']}</code>: "
                    f"{s['h24']}/24h, {s['total']} всего\n"
                )
    except Exception as e:
        text += f"\n⚠ Ошибка источников: {e}\n"

    text += (
        "\n🔗 <b>API:</b>\n"
        "  /api/health — health check\n"
        "  /api/admin/stats — статистика\n"
        "  /api/stations/by-city — поиск по городу"
    )
    await message.answer(text)


# === Геолокация ===
async def handle_location(message: Message, state: FSMContext):
    telegram_id = _tg_id(message)
    uid = await get_or_create_user(message)  # internal id
    await log_event(uid, "location_shared")

    location = message.location
    lat = location.latitude
    lon = location.longitude

    current_state = await state.get_state()

    # Если ждём гео для подписки
    if current_state == SubscribeStates.waiting_geo.state:
        await state.update_data(lat=lat, lon=lon)
        await state.set_state(SubscribeStates.waiting_radius)
        await message.answer(
            "📍 Геолокацию получил.\n\n"
            "Выбери радиус уведомлений:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="3 км", callback_data="sub_radius:3"),
                    InlineKeyboardButton(text="5 км", callback_data="sub_radius:5"),
                    InlineKeyboardButton(text="10 км", callback_data="sub_radius:10"),
                ],
            ]),
        )
        return

    # Обычный случай — поиск АЗС рядом
    await _do_find(message, lat, lon)


async def _do_find(message: Message, lat: float, lon: float):
    # Проверяем кеш
    cached = _cache_get(lat, lon, 30)
    if cached is not None:
        stations = cached
    else:
        # Radius 30 км — оптимально для города и пригородов.
        # 50 км захватывает соседние города (Кострома, Ярославль для Иванова).
        stations = await find_nearest_stations(lat=lat, lon=lon, limit=10, radius_km=30)
        _cache_set(lat, lon, 30, stations)

    if not stations:
        await message.answer(
            "😔 <b>Рядом не нашёл АЗС в базе.</b>\n\n"
            "Попробуй написать название города или сети.",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Bulk-запрос: 1 запрос вместо 10 (вместо N+1)
    from db import get_stations_with_statuses
    stations = await get_stations_with_statuses(stations)

    text = f"🔍 <b>Нашёл {len(stations)} АЗС рядом:</b>\n\n"
    buttons = []
    for s in stations:
        statuses = s.get("statuses", [])
        dist = format_distance(s.get("distance_km", 0))
        status_icon = _get_main_status_icon(statuses)
        name = (s.get("name") or "АЗС")[:22]
        city = (s.get("city") or "")[:10]
        btn_text = f"{status_icon} {name} • {dist}"
        if city:
            btn_text += f" • {city}"
        buttons.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"st:{s['id']}")
        ])
    buttons.append([
        InlineKeyboardButton(
            text="🗺 Открыть на карте",
            web_app=WebAppInfo(url="https://benzin-mini.vercel.app"),
        )
    ])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


def _get_main_status_icon(statuses: list) -> str:
    if not statuses:
        return "❓"
    for fuel in ["92", "95", "98", "diesel"]:
        for st in statuses:
            if st.get("fuel_type") == fuel:
                available = st.get("available")
                if available is True or available == 1:
                    return "✅"
                if available is False or available == 0:
                    return "❌"
                return "⚠️"  # None или неизвестно — "кончается"
    st = statuses[0]
    available = st.get("available")
    if available is True or available == 1:
        return "✅"
    if available is False or available == 0:
        return "❌"
    return "⚠️"


# === Поиск по тексту (город / сеть / название) ===
async def handle_text_search(message: Message):
    if not message.text:
        return
    query = message.text.strip()
    if len(query) < 2:
        return

    user_id = await get_or_create_user(message)
    await log_event(user_id, "text_search", {"query": query})

    stations = await find_stations_by_name(query, limit=8)
    if not stations:
        await message.answer(
            f"😔 По запросу <b>«{query}»</b> ничего не нашёл.\n\n"
            f"Попробуй написать по-другому или отправь 📍 геолокацию.",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Bulk-запрос: 1 запрос вместо 8
    from db import get_stations_with_statuses
    stations = await get_stations_with_statuses(stations)

    text = f"🔍 По запросу <b>«{query}»</b> нашёл {len(stations)} АЗС:\n\n"
    buttons = []
    for s in stations:
        statuses = s.get("statuses", [])
        status_icon = _get_main_status_icon(statuses)
        name = (s.get("name") or "АЗС")[:25]
        city = (s.get("city") or "")[:12]
        btn_text = f"{status_icon} {name}"
        if city:
            btn_text += f" • {city}"
        buttons.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"st:{s['id']}")
        ])
    buttons.append([
        InlineKeyboardButton(
            text="🗺 Открыть на карте",
            web_app=WebAppInfo(url="https://benzin-mini.vercel.app"),
        )
    ])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# === Карточка АЗС ===
async def show_station_details(callback: CallbackQuery):
    station_id = int(callback.data.split(":")[1])
    user_id = await get_or_create_user(callback.message)
    await log_event(user_id, "station_viewed", {"station_id": station_id})

    station = await get_station_by_id(station_id)
    if not station:
        await callback.answer("АЗС не найдена", show_alert=True)
        return

    statuses = await get_station_current_status(station_id)
    text = format_station_card(station, statuses)
    kb = station_actions_keyboard(station_id, has_statuses=len(statuses) > 0)
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


# === Report flow ===
async def report_start(callback: CallbackQuery):
    station_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        "⛽ <b>Выбери тип топлива:</b>",
        reply_markup=fuel_type_keyboard(station_id),
    )
    await callback.answer()


async def report_fuel(callback: CallbackQuery):
    parts = callback.data.split(":")
    station_id = int(parts[1])
    fuel = parts[2]
    await callback.message.answer(
        f"⛽ <b>АИ-{fuel}</b> — какой статус?",
        reply_markup=report_status_keyboard(station_id, fuel),
    )
    await callback.answer()


async def report_submit(callback: CallbackQuery):
    parts = callback.data.split(":")
    station_id = int(parts[1])
    fuel = parts[2]
    status = parts[3]  # yes / no / low / queue

    available_map = {"yes": True, "queue": True, "low": None, "no": False}
    queue_map = {"yes": None, "queue": 5, "low": None, "no": None}

    if status not in available_map:
        await callback.answer("Неизвестный статус", show_alert=True)
        return

    available = available_map[status]
    queue_size = queue_map[status]

    await get_or_create_user(callback.message)
    telegram_id = _tg_id(callback.message)
    uid = await get_user_id_by_telegram_id(telegram_id)

    report_id = await add_report(
        station_id=station_id,
        user_id=uid,
        fuel_type=fuel,
        available=available,
        queue_size=queue_size,
        source="user",
    )

    celebration = await _check_and_celebrate_badges(uid)
    status_text = {
        "yes": "✅ Есть",
        "queue": "🕐 Большая очередь",
        "low": "⚠️ Кончается",
        "no": "❌ Нет",
    }[status]

    await callback.message.answer(
        f"✅ <b>Спасибо! Отчёт записан.</b>\n\n"
        f"АЗС #{station_id}, АИ-{fuel}: {status_text}\n\n"
        f"Твой отчёт увидят другие водители.{celebration}",
    )
    await callback.answer()


# === Подписки: callback'и ===
async def subscribe_radius(callback: CallbackQuery, state: FSMContext):
    radius = int(callback.data.split(":")[1])
    data = await state.get_data()
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is None or lon is None:
        await callback.answer("Сначала отправь геолокацию", show_alert=True)
        return

    await get_or_create_user(callback.message)
    telegram_id = _tg_id(callback.message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await callback.answer("Ошибка. Нажми /start", show_alert=True)
        return

    sub_id = await add_subscription(
        user_id=uid,
        lat=lat,
        lon=lon,
        radius_km=radius,
    )

    await state.clear()
    await callback.message.answer(
        f"🔔 <b>Подписка оформлена.</b>\n\n"
        f"Радиус: {radius} км\n"
        f"Координаты: {lat:.4f}, {lon:.4f}\n\n"
        f"Пришлю уведомление, как только кто-то сообщит о наличии топлива рядом.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


# === Mini App data: приём отчётов с карты ===
async def handle_web_app_data(message: Message):
    """Получает данные из Mini App (report и т.п.)."""
    raw = message.web_app_data.data if isinstance(message.web_app_data, WebAppData) else ""
    if not raw:
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid web_app_data: %s", raw[:200])
        return

    data_type = data.get("type")
    if data_type == "report":
        station_id = data.get("station_id")
        fuel_type = str(data.get("fuel_type", ""))
        available_raw = data.get("available")
        if available_raw is None:
            available = None  # "кончается"
        elif isinstance(available_raw, bool):
            available = available_raw
        else:
            available = bool(int(available_raw))

        if not station_id or fuel_type not in ("92", "95", "98", "diesel", "100", "lpg"):
            await message.answer("⚠️ Не удалось обработать отчёт. Попробуй ещё раз.")
            return

        telegram_id = await get_or_create_user(message)
        uid = await get_user_id_by_telegram_id(telegram_id)
        await add_report(
            station_id=int(station_id),
            user_id=uid,
            fuel_type=fuel_type,
            available=available,
            source="miniapp",
        )
        celebration = await _check_and_celebrate_badges(uid)
        await message.answer(
            f"✅ <b>Спасибо! Отчёт с карты записан.</b>\n\n"
            f"АЗС #{station_id}, АИ-{fuel_type}{celebration}",
        )
    else:
        logger.info("Unknown web_app_data type: %s", data_type)


# === Back / cancel ===
async def handle_cancel(callback: CallbackQuery):
    await callback.message.answer("Ок, отменил.", reply_markup=main_menu_keyboard())
    await callback.answer()


async def handle_back_to_list(callback: CallbackQuery):
    await callback.message.answer(
        "🔍 Нажми «🔍 Найти АЗС» или напиши город.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


# === 🏠 "В начало" — глобальный сброс в любой точке ===
async def go_home_callback(callback: CallbackQuery, state: FSMContext = None):
    """Сбрасывает ВСЕ состояния (FSM + in-memory) и возвращает в главное меню."""
    telegram_id = _tg_id(callback.message)
    _waiting_owner_search.discard(telegram_id)
    _waiting_owner_role.pop(telegram_id, None)
    _waiting_inn_nosm.discard(telegram_id)
    _owner_state.pop(telegram_id, None)
    if state is not None:
        await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "🏠 <b>Главное меню</b>\n\n"
        "Выбери действие на клавиатуре внизу 👇",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


async def go_home_text(message: Message, state: FSMContext = None):
    """Тот же сброс, но по тексту '🏠 В начало'."""
    telegram_id = _tg_id(message)
    _waiting_owner_search.discard(telegram_id)
    _waiting_owner_role.pop(telegram_id, None)
    _waiting_inn_nosm.discard(telegram_id)
    _owner_state.pop(telegram_id, None)
    if state is not None:
        await state.clear()
    await message.answer(
        "🏠 <b>Главное меню</b>\n\n"
        "Выбери действие на клавиатуре внизу 👇",
        reply_markup=main_menu_keyboard(),
    )


# === Подписка на конкретную АЗС ===
async def subscribe_station(callback: CallbackQuery):
    station_id = int(callback.data.split(":")[1])
    await get_or_create_user(callback.message)
    telegram_id = _tg_id(callback.message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await callback.answer("Ошибка. Нажми /start", show_alert=True)
        return

    await add_subscription(user_id=uid, station_id=station_id, radius_km=0)
    await callback.answer("🔔 Подписался. Сообщу, как только появятся отчёты.", show_alert=True)


# === Регистрация ===
def register_all_handlers(dp: Dispatcher):
    # Команды
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_find, Command("find"))
    dp.message.register(cmd_subscribe, Command("subscribe"))
    dp.message.register(cmd_register_owner, Command("register_owner"))
    dp.message.register(cmd_my_stations, Command("my_stations"))
    dp.message.register(cmd_profile, Command("profile"))
    dp.message.register(cmd_stats, Command("stats"))
    dp.message.register(cmd_moderate, Command("moderate"))
    dp.message.register(cmd_my_id, Command("my_id"))
    dp.message.register(cmd_find_raw, Command("find_raw"))

    # FSM: подписки
    dp.message.register(handle_location, F.location, StateFilter(SubscribeStates.waiting_geo))
    dp.callback_query.register(subscribe_radius, F.data.startswith("sub_radius:"), StateFilter(SubscribeStates.waiting_radius))

    # Non-FSM owner flow (текстовый поиск → выбор АЗС → роль → ИНН → готово)
    dp.message.register(owner_inn_input_nosm, _OwnerWaitingInnFilter())
    dp.message.register(owner_search_input, _OwnerWaitingSearchFilter())
    dp.callback_query.register(owner_pick_search, F.data.startswith("owner_pick_search:"))
    dp.callback_query.register(owner_role_picked, F.data.startswith("owner_role:"))
    dp.callback_query.register(owner_inn_skip_nosm, F.data == "owner_inn_nosm:skip")
    dp.callback_query.register(owner_search_cancel, F.data == "owner_search_cancel")

    # Геолокация (общий случай — поиск АЗС)
    dp.message.register(handle_location, F.location)

    # Mini App data (отчёты с карты)
    dp.message.register(handle_web_app_data, F.web_app_data)

    # Текстовые кнопки главного меню
    dp.message.register(handle_main_button, F.text)

    # Callback (кнопки)
    dp.callback_query.register(show_station_details, F.data.startswith("st:"))
    dp.callback_query.register(report_start, F.data.startswith("report:") & ~F.data.contains("fuel:") & ~F.data.contains("status:"))
    dp.callback_query.register(report_fuel, F.data.startswith("report_fuel:"))
    dp.callback_query.register(report_submit, F.data.startswith("report_status:"))
    dp.callback_query.register(subscribe_station, F.data.startswith("sub_station:"))
    dp.callback_query.register(handle_cancel, F.data == "cancel")
    dp.callback_query.register(handle_back_to_list, F.data == "back_to_list")

    # Owner-режим: быстрое обновление статуса
    dp.callback_query.register(owner_quick_set, F.data.startswith("oset:"))
    dp.callback_query.register(show_my_station, F.data.startswith("mystation:"))
    dp.callback_query.register(my_stations_back, F.data == "my_stations_back")

    # Модерация
    dp.callback_query.register(approve_owner, F.data.startswith("approve:"))

    # Глобальная кнопка «В начало»
    dp.callback_query.register(go_home_callback, F.data == "go_home")

    # === Фаза 2 callbacks ===
    # Из welcome-цепочки
    dp.callback_query.register(go_register_owner_callback, F.data == "go_register_owner")
    dp.callback_query.register(profile_callback, F.data == "cmd_profile")
    dp.callback_query.register(help_callback, F.data == "cmd_help")

    # === Premium (Telegram Stars) ===
    dp.message.register(cmd_premium, Command("premium"))
    dp.callback_query.register(buy_premium_callback, F.data == "buy_premium")
    dp.callback_query.register(premium_callback, F.data == "cmd_premium")
    dp.callback_query.register(premium_trial_callback, F.data == "premium_trial")
    dp.pre_checkout_query.register(pre_checkout_handler)
    dp.message.register(successful_payment_handler, F.successful_payment)

    # Inline mode
    dp.inline_query.register(inline_search)

    # Аналитика владельца
    dp.callback_query.register(station_analytics_callback, F.data.startswith("analy:"))
