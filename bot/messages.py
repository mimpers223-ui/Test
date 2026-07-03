"""
Все текстовые шаблоны бота «Бензин рядом».

Все тексты — единый источник правды.
Стиль: кратко, на «ты», с эмодзи в меру, конкретные цифры.
"""

# === Welcome цепочка (3 сообщения) ===

WELCOME_1 = (
    "👋 Привет! Я — Бензин рядом.\n\n"
    "Помогу найти бензин за 5 секунд. 26 000+ АЗС в России.\n\n"
    "🟢 live · цены · очереди · push о завозе"
)

WELCOME_2 = (
    "🔍 Ищи АЗС прямо в чате\n\n"
    "В любом чате набери:\n"
    "<code>@benzyn_ryadom_bot 92 Иваново</code>\n\n"
    "Я покажу топливо в городе. Можно отправить другу."
)

WELCOME_3 = (
    "📢 Помогай другим — получай бейджи\n\n"
    "Отмечай ✅ есть / ⚠️ кончается / ❌ нет.\n"
    "За это — репутация и бейджи.\n\n"
    "🏆 Новичок · Активный · Эксперт · Топ региона"
)


# === /start fallback (повторное) ===
START_AGAIN = "👋 С возвращением! Чем помочь?"


# === /help ===
HELP_TEXT = (
    "ℹ️ <b>Команды</b>\n\n"
    "🔍 <b>Найти АЗС</b> — /find или в любом чате "
    "<code>@benzyn_ryadom_bot 92 Иваново</code>\n"
    "📝 <b>Сообщить</b> —выбери город или найди АЗС по адресу\n"
    "⭐ <b>Отзыв</b> — оцени качество бензина на АЗС (0-5 звёзд)\n"
    "🔔 <b>Подписки</b> — /subscribe → push о завозе\n"
    "👤 <b>Профиль</b> — /profile → репутация, бейджи\n"
    "🏪 <b>Владелец</b> — /register_owner → verified-бейдж\n"
    "💎 <b>Premium</b> — /premium → push без задержек\n"
    "📊 <b>Статистика</b> — /stats → данные по РФ\n\n"
    "💡 В любом чате: <code>@benzyn_ryadom_bot 92 Иваново</code>"
)


# === /find ===
FIND_PROMPT = (
    "📍 <b>Найти АЗС рядом</b>\n\n"
    "Нажми кнопку ниже, чтобы отправить геолокацию.\n"
    "Или напиши город / сеть / название АЗС."
)

FIND_NOTHING = "🔍 Не нашли АЗС в этом районе. Попробуй увеличить радиус."

FIND_RESULTS_HEADER = "🗺 <b>Найдено {count} АЗС в радиусе {radius} км</b>\n\n"


# === /premium ===
PREMIUM_OFFER = (
    "💎 <b>Бензин рядом · Premium</b>\n\n"
    "💳 <b>{price} Stars</b> · {days} дней\n"
    "≈ 300₽/мес — дешевле чашки кофе ☕\n\n"
    "<b>Что ты получишь вместо бесплатного:</b>\n\n"
    "🔔 <b>Push о завозе — каждый час</b>\n"
    "   Free: раз в 4 часа (≤6 push в день)\n"
    "   Premium: раз в 1 час (≤24 push в день)\n\n"
    "💸 <b>Push о падении цены &gt;2₽</b>\n"
    "   Free: ❌\n"
    "   Premium: ✅ — узнаешь когда АИ-95 упал с 58 до 55₽\n\n"
    "🗺 <b>Радиус карты</b>\n"
    "   Free: 30 км (≤100 АЗС)\n"
    "   Premium: 100 км (≤500 АЗС) — для дальних поездок\n\n"
    "📊 <b>Графики цен за 30 дней</b>\n"
    "   Free: ❌\n"
    "   Premium: ✅ — история цены + среднее за месяц\n\n"
    "💎 <b>Premium-бейдж в профиле</b>\n"
    "   Free: ❌\n"
    "   Premium: ✅ — выделяет тебя в отчётах\n\n"
    "🎁 <b>7 дней бесплатно</b>\n"
    "   Trial без оплаты — попробуй всё и реши сам\n\n"
    "💡 <i>Если бот помог найти АЗС хотя бы 1 раз — Premium окупится за месяц.</i>"
)

PREMIUM_ACTIVE = (
    "💎 <b>Premium активен</b>\n\n"
    "📅 Осталось дней: <b>{days_left}</b>\n"
    "⏰ Подписка до: {expires_at}\n\n"
    "<b>Что у тебя работает:</b>\n"
    "🔔 Push о завозе — каждый час (вместо 4ч)\n"
    "💸 Push о падении цены &gt;2₽ в твоём районе\n"
    "🗺 Карта в радиусе 100 км (вместо 30)\n"
    "📊 Графики цен за 30 дней\n"
    "💎 Premium-бейдж в профиле\n\n"
    "Спасибо за поддержку! 🙏"
)

PREMIUM_TRIAL_ACTIVATED = (
    "🎁 <b>Trial Premium активирован!</b>\n\n"
    "📅 На 7 дней (до {expires_at})\n\n"
    "<b>Что попробовать прямо сейчас:</b>\n"
    "1️⃣ Открой карту — увидишь 500 АЗС вместо 100\n"
    "2️⃣ Подпишись на АЗС — push придёт через час если будет завоз\n"
    "3️⃣ Открой карточку АЗС — увидишь график цены\n\n"
    "Если понравится — /premium для оплаты Stars.\n"
    "Если нет — ничего не произойдёт, вернёшься на Free."
)

PREMIUM_PAYMENT_SUCCESS = (
    "🎉 <b>Premium активирован!</b>\n\n"
    "📅 Действует до: {expires_at}\n"
    "💎 Спасибо за поддержку «Бензин рядом»!\n\n"
    "🔔 Push без cooldown, 📊 аналитика, 🚗 premium-бейдж — всё твоё."
)


# === /profile ===
PROFILE_TEMPLATE = (
    "👤 <b>Твой профиль</b>\n\n"
    "🆔 Telegram ID: <code>{telegram_id}</code>\n"
    "📊 Репутация: <b>{reputation}</b>/100\n"
    "📝 Отчётов сделано: <b>{total_reports}</b>\n"
    "✅ Подтверждено: <b>{confirmed_reports}</b>\n"
    "{region}\n"
    "{premium_block}\n"
    "{badges_block}"
)

PREMIUM_IN_PROFILE = "⭐ <b>Premium</b> — push без cooldown, расширенная аналитика\n\n"

NO_BADGES = "\n🎯 Сделай первый отчёт, чтобы получить бейдж 🥉 «Новичок»!"

BADGES_HEADER = "\n🏆 <b>Твои бейджи ({count}):</b>\n"
BADGES_ITEM = "  {emoji} <b>{name}</b> — {desc}\n"


# === /stats ===
STATS_TEMPLATE = (
    "📊 <b>Бензин рядом</b>\n\n"
    "⛽ АЗС в базе: <b>{stations}</b>\n"
    "👥 Пользователей: <b>{users}</b>\n"
    "📝 Отчётов за 24ч: <b>{reports_24h}</b>\n"
    "🏙 Городов: <b>{cities}</b>"
)


# === /register_owner ===
OWNER_WELCOME = (
    "🏪 <b>Регистрация владельца АЗС</b>\n\n"
    "Verified-бейдж + аналитика + push.\n\n"
    "Введи <b>название</b> или <b>адрес</b> АЗС:"
)

OWNER_PROMPT_NAME = "Введи название или адрес АЗС (например: «Лукойл ул Ленина 5»):"

OWNER_NOT_FOUND = "🔍 Не нашли. Попробуй другой запрос — только город или точное название:"

OWNER_FOUND_HEADER = "🔍 <b>Найдено {count} АЗС</b>\n\nВыбери свою:"

OWNER_ROLE_PROMPT = (
    "Отлично! <b>{station_name}</b>\n\n"
    "Кто ты?"
)

OWNER_INN_PROMPT = (
    "Опционально: введи <b>ИНН</b> для подтверждения юр. лица.\n"
    "Если не хочешь — нажми «Пропустить»."
)

OWNER_APPROVED = (
    "🎉 <b>Заявка одобрена!</b>\n\n"
    "✅ <b>{station_name}</b> теперь подтверждена\n"
    "💎 Verified-бейдж появился у АЗС в поиске\n"
    "📊 Аналитика: /analytics\n\n"
    "Спасибо что делаете рынок прозрачнее!"
)

OWNER_PENDING = (
    "📝 <b>Заявка отправлена на модерацию</b>\n\n"
    "Станция: <b>{station_name}</b>\n"
    "Роль: <b>{role}</b>\n"
    "{inn}\n\n"
    "Обычно одобрение за 24 часа. Спасибо!"
)


# === /my_stations ===
MY_STATIONS_EMPTY = (
    "У тебя пока нет зарегистрированных АЗС.\n"
    "Нажми /register_owner, чтобы добавить."
)

MY_STATIONS_HEADER = "🏪 <b>Твои АЗС:</b>\n\n"

MY_STATIONS_ITEM = (
    "{verified} <b>{name}</b>\n"
    "   📍 {address}\n\n"
)

# === /analytics ===
ANALYTICS_HEADER = (
    "📊 <b>Аналитика за 30 дней:</b>\n\n"
    "👁 Просмотры: <b>{views}</b>\n"
    "📝 Отчёты (все): <b>{reports}</b>\n"
    "🔔 Подписчики: <b>{subs}</b>\n\n"
)

ANALYTICS_NO_DATA = (
    "💡 <i>Данные появятся когда водители начнут открывать карточки "
    "и оставлять отчёты.</i>\n\n"
)

ANALYTICS_STATION_ITEM = (
    "{verified} <b>{name}</b>\n"
    "   👁 {views} · 📝 {reports} · 🔔 {subs}"
)

ANALYTICS_PRICE = " · 💰 {price:.2f}₽"

ANALYTICS_PER_STATION_HEADER = "<b>По АЗС:</b>\n"

ANALYTICS_DETAIL_HEADER = (
    "📊 <b>Аналитика АЗС #{station_id} · 30 дней:</b>\n\n"
    "👁 Просмотры: <b>{views}</b>\n"
    "📝 Отчёты: <b>{reports}</b>\n"
    "🔔 Подписчики: <b>{subs}</b>\n"
)

ANALYTICS_PER_FUEL_HEADER = "\n<b>По топливу:</b>\n"
ANALYTICS_PER_FUEL_ITEM = "  ⛽ АИ-{fuel}: {count} отчётов{price_part}\n"
ANALYTICS_VIEWS_HEADER = "\n<b>Просмотры по дням:</b>\n"
ANALYTICS_VIEWS_ROW = "  {date}: {bar} {count}\n"


# === /subscribe ===
SUBSCRIBE_PROMPT = (
    "🔔 <b>Подписка на push о завозе</b>\n\n"
    "Отправь геолокацию — и я уведомлю когда в твоём "
    "районе появится топливо.\n\n"
    "Или напиши город."
)

SUBSCRIBE_GEOLOCATION_OK = (
    "✅ Подписка создана!\n\n"
    "📍 Радиус: {radius} км\n"
    "🔔 Push: о завозе + о цене −2₽ (Premium)\n\n"
    "Отписаться: /unsubscribe"
)


# === Кнопка «🏠 В начало» ===
HOME_LABEL = "🏠 В начало"


# === Уведомления (push) ===
PUSH_FORMATS = {
    "fuel_yes": "⛽ <b>Завезли!</b>\n{name}\n📍 {address}",
    "fuel_low": "⚠️ <b>Кончается</b>\n{name}\n📍 {address}",
    "fuel_no": "❌ <b>Закончилось</b>\n{name}\n📍 {address}",
    "price_drop": "💸 <b>Цена упала!</b>\n{name}\nАИ-{fuel}: <b>{price}₽</b> (было {prev}₽)\n📍 {address}",
}


# === Канал: автопост ===
CHANNEL_POST_TEMPLATE = (
    "⛽ <b>Где есть бензин — {city}</b>\n\n"
    "{items}\n\n"
    "💡 Открой @benzyn_ryadom_bot чтобы сообщить о наличии\n"
    "📊 Источник: краудсорс водителей в реальном времени"
)

CHANNEL_POST_ITEM_PRICE = "{icon} {verified}<b>{name}</b> — АИ-{fuel} · <b>{price}₽</b>"
CHANNEL_POST_ITEM_NOPRICE = "{icon} {verified}<b>{name}</b> — АИ-{fuel}"


# === Сообщения об ошибках ===
ERROR_GENERIC = "⚠️ Что-то пошло не так. Попробуй ещё раз."
ERROR_LOCATION = "⚠️ Не удалось определить местоположение. Введи город."
ERROR_NO_DATA = "🔍 В базе пока нет данных. Попробуй позже."


# === Inline mode ===
INLINE_NO_RESULTS = (
    "🔍 Ничего не найдено. Попробуй: "
    "<code>@benzyn_ryadom_bot Лукойл</code> или "
    "<code>@benzyn_ryadom_bot 95 Иваново</code>"
)

INLINE_HINT = (
    "💡 Попробуй: <code>@benzyn_ryadom_bot 92 Иваново</code> или "
    "<code>@benzyn_ryadom_bot Лукойл</code>"
)


# === Ценовые диапазоны для мотивации (Premium) ===
PRICE_EXAMPLES = {
    "lukoil_down": "55₽ → 53₽ (скидка 2₽)",
    "gazprom_up": "58₽ → 60₽ (новый прайс)",
    "rosneft_yes": "Дизель на Лукойл-Сити появился!",
}
