"""
Парсер Telegram-каналов: вытаскивает упоминания о наличии топлива,
ценах, очередях и времени завоза. Записывает в БД как отчёты с source='tg'.

⚠️  ВАЖНО ПЕРЕД ЗАПУСКОМ:
1. Зарегистрируй приложение на https://my.telegram.org/apps
2. Получи api_id и api_hash
3. Положи их в bot/.env: TG_API_ID=... TG_API_HASH=...
4. При первом запуске попросит ввести телефон и SMS-код
5. После авторизации создаётся файл session.session — НЕ коммить его

Использование:
    python scripts/parse_tg_channels.py            # один проход
    python scripts/parse_tg_channels.py --watch    # слушать новые сообщения
    python scripts/parse_tg_channels.py --upload-url https://benzin-ryadom.onrender.com/api/import_prices

⚖️  Юридически: читай только публичные каналы. Не пости от их имени.
    Сохраняй анонимно (без user_id, только текст).
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Загружаем .env из bot/
ENV_PATH = Path(__file__).parent.parent / "bot" / ".env"
load_dotenv(ENV_PATH)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

TG_API_ID = os.getenv("TG_API_ID", "")
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING", "")
SESSION_PATH = Path(__file__).parent / "session"

# Каналы для мониторинга. Можно переопределить через TG_CHANNELS в env (через запятую)
DEFAULT_CHANNELS = [
    # === Общероссийские ===
    "benzin_price",        # Ежедневные цены по городам
    "benzoopt",            # Биржевые цены (СПИМФ)
    "fuelprice_ru",        # FuelPrice.ru
    "benzup_ru",           # BenzUp.ru
    "okolo_AZS",           # Аналитика OMT-Konsalt
    "toplivo_gsm_ru",      # Цены АЗС
    "toplivo_chat",        # Чат топливо
    "gdebenzru",           # Где бензин
    "azstatneft",          # Татнефть
    "azsdiller",           # Дилеры АЗС
    "azs_price",           # Цены АЗС
    "russiabase_ru",       # Сводки по регионам
    "gde_benz_rf",         # Где бензин РФ
    "toplivo_rf",          # Нефтепродукты РФ
    "toplivo_poisk",       # Поиск топлива
    "pro_zapravki",        # Скидки на заправках
    "benzinmap",           # Карта дефицита бензина РФ
    # === Региональные ===
    "toplivoufo",          # ЮФО (Краснодар, Ростов, Волгоград)
    "magistral116",        # Казань / Татарстан (М7)
    "umbokzn16",           # Казань (бойкот АЗС)
    "nottourists",         # Иваново
    "kineshemec_ru",       # Кинешма
    "tvernewsru",          # Тверь (новости + цены)
    "nizhny01",            # Нижний Новгород (новости + цены)
    "Neftexpert",          # Нефтяной рынок
    "toplivo_live",        # Чаты водителей (Воронеж и др.)
]

CHANNELS = [c.strip() for c in os.getenv("TG_CHANNELS", "").split(",") if c.strip()]
if not CHANNELS:
    CHANNELS = DEFAULT_CHANNELS

# Город по умолчанию для канала (если город не указан в сообщении)
CHANNEL_CITY_HINTS: dict[str, str] = {
    # === Общероссийские (None = определить из текста) ===
    "benzin_price": None,
    "benzoopt": None,
    "fuelprice_ru": None,
    "benzup_ru": None,
    "okolo_AZS": None,
    "toplivo_gsm_ru": None,
    "gdebenzru": None,
    "azstatneft": None,
    "azsdiller": None,
    "azs_price": None,
    "russiabase_ru": None,
    "gde_benz_rf": None,
    "toplivo_rf": None,
    "toplivo_poisk": None,
    "pro_zapravki": None,
    "Neftexpert": None,
    # === Региональные ===
    "toplivoufo": None,           # ЮФО
    "magistral116": "Казань",
    "umbokzn16": "Казань",
    "nottourists": "Иваново",
    "kineshemec_ru": "Кинешма",
    "toplivo_chat": None,
    "toplivo_live": None,         # Воронеж и др.
    "tvernewsru": "Тверь",
    "nizhny01": "Нижний Новгород",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("tg_parser")


# === Ключевые слова для извлечения данных ===
FUEL_KEYWORDS = {
    "92":     ["92", "аи-92", "аи92", "а92", "девяносто два"],
    "95":     ["95", "аи-95", "аи95", "а95", "девяносто пять"],
    "98":     ["98", "аи-98", "аи98"],
    "100":    ["100", "аи-100", "аи100"],
    "diesel": ["дизель", "диз", "солярка", "соляра", "дт", "дп"],
    "lpg":    ["газ", "пропан", "lpg", "суг", "кпг"],
    "cng":    ["метан", "cng", "кпг"],
}

NETWORK_KEYWORDS = {
    # Россия
    "Лукойл":            ["лукойл", "lukoil"],
    "Газпромнефть":      ["газпромнефть", "газпром", "gazprom"],
    "Роснефть":          ["роснефть", "rosneft"],
    "Татнефть":          ["татнефть", "tatneft", "танеко"],
    "Башнефть":          ["башнефть", "bashneft"],
    "Сургутнефтегаз":    ["сургутнефтегаз", "surgut"],
    "Тнефтепродукт":     ["тнефтепродукт"],
    "Нефтьмагистраль":   ["нефтьмагистраль"],
    "ТАИФ":              ["таиф", "taif"],
    "Сургутнефтегаз":    ["сургутнефтегаз"],
    # Украина
    "ОККО":              ["окко", "okko"],
    "WOG":               ["wog", "вог"],
    "UPG":               ["upg"],
    "Амик":              ["амик"],
    "Сан Ойл":           ["сан ойл", "сан-ойл", "sanoil", "sun oil"],
    "Мавекс":            ["мавекс"],
    "Параллель":         ["параллель"],
    "Авантаж":           ["авантаж"],
    "БРСМ-Нафтопродукт": ["брсм", "brsm"],
    "НК Укрнафта":       ["укрнафта", "ukrnafta"],
    "TNK":               ["tnk", "тнк"],
    "BP":                ["bp"],
    "Shell":             ["shell", "шелл"],
    "OMV":               ["omv"],
    # Прочие
    "Teboil":            ["teboil", "тебойл"],
    "Lukoil":            ["lukoil"],
}

YES_WORDS = ["есть", "завезли", "привезли", "появилось", "в наличии", "льют", "работает", "налили", "горит", "светится", "засветился", "открыли", "наливают"]
NO_WORDS = ["нет", "отсутствует", "пусто", "закончился", "кончился", "закончилось", "кончилось", "нету", "закончили"]
NO_EXCLUDE = ["нет очереди", "очереди нет", "без очереди", "очереди нету", "нет машин"]  # не считать как "нет топлива"
LOW_WORDS = ["мало", "заканчивается", "кончается", "осталось мало", "на исходе", "заканчивается", "почти нет"]

# Паттерн цены: "АИ-95 — 56.40", "95 = 58.30₽", "95 по 54", "дизель по 58", "горит дизель по 75", "95 67 рублей"
PRICE_PATTERN = re.compile(
    r"(?:аи-?)?(\d{2,3}|дизель|диз|дп|солярка|дт|газ|пропан)\s*(?:по|[-\-:=—–])\s*(\d{2,3}[.,]?\d{0,2})\s*(?:руб|грн|₽)?",
    re.IGNORECASE
)
# Альтернативный паттерн: "95 67 рублей", "92 63.50 руб"
PRICE_PATTERN_ALT = re.compile(
    r"(?:аи-?)?(\d{2,3}|дизель|диз|дп|солярка|дт|газ|пропан)\s+(\d{2,3}[.,]?\d{0,2})\s*(?:руб|грн|₽)",
    re.IGNORECASE
)
# Третий паттерн: просто "N рублей" (без привязки к виду топлива)
PRICE_PATTERN_RUB = re.compile(
    r"(\d{2,3}(?:[.,]\d{1,2})?)\s*(?:руб|грн|₽)",
    re.IGNORECASE
)

# Паттерн очереди: "очередь 5", "5 машин", "queue 3", "очередь в пределах заправки"
QUEUE_PATTERN = re.compile(
    r"(?:очередь|queue)\s*(?:в пределах заправки|не большая|небольшая)?\s*(\d{1,2})?\s*(?:машин|vehicle)?",
    re.IGNORECASE
)

# Паттерн времени завоза: "завоз в 14:00", "привезут в 15:30", "подвоз через час", "привезут через 2 часа"
DELIVERY_TIME_PATTERN = re.compile(
    r"(?:завоз|подвоз|привоз|привезут|привезут|завезут|ожидается)\s+"
    r"(?:в\s+(\d{1,2}):(\d{2})|через\s+(\d+)\s*(час|ч|минут|мин|h|m))",
    re.IGNORECASE
)

# Паттерн даты: "завтра", "послезавтра", "01.07", "01.07.2026"
DATE_WORDS = {
    "сегодня":      0,
    "завтра":       1,
    "послезавтра":  2,
}


def parse_fuel_status(text: str) -> list[dict]:
    """Извлекает упоминания топлива, статус, цену, очередь, время завоза.

    Возвращает [{fuel_type, available, price, queue, next_delivery, network}, ...]
    """
    text_lower = text.lower()
    results_dict: dict[str, dict] = {}  # fuel -> info

    # 1) Найти сеть
    network = None
    for net, kws in NETWORK_KEYWORDS.items():
        if any(kw in text_lower for kw in kws):
            network = net
            break

    # 2) Найти виды топлива и статусы
    for fuel, fuel_kws in FUEL_KEYWORDS.items():
        for kw in fuel_kws:
            idx = text_lower.find(kw)
            if idx == -1:
                continue
            # Контекст ±80 символов
            ctx_start = max(0, idx - 80)
            ctx_end = min(len(text), idx + len(kw) + 80)
            ctx = text_lower[ctx_start:ctx_end]

            available = None
            # Проверяем "нет топлива" с исключениями ("нет очереди" ≠ "нет топлива")
            has_no_word = any(w in ctx for w in NO_WORDS)
            has_no_exclude = any(e in ctx for e in NO_EXCLUDE)
            has_yes = any(w in ctx for w in YES_WORDS)
            has_low = any(w in ctx for w in LOW_WORDS)

            if has_yes:
                available = True  # "горит", "есть", "в наличии" побеждают "нет"
            elif has_no_word and not has_no_exclude:
                available = False
            elif has_low:
                available = None  # "кончается"
            # Если нет ни одного статуса — всё равно добавляем (ценовой отчёт)

            results_dict[fuel] = {
                "fuel_type": fuel,
                "available": available,
                "price": None,
                "queue": None,
                "next_delivery": None,
                "network": network,
            }
            break

    if not results_dict:
        return []

    # 3) Найти цены для каждого вида топлива
    for fuel, info in results_dict.items():
        # Ищем цену рядом с упоминанием этого топлива
        for m in list(PRICE_PATTERN.finditer(text)) + list(PRICE_PATTERN_ALT.finditer(text)):
            matched_fuel = m.group(1).lower()
            # Нормализуем
            if matched_fuel in ("диз", "дт", "солярка", "соляра", "дп"):
                matched_fuel = "diesel"
            elif matched_fuel in ("газ", "пропан"):
                matched_fuel = "lpg"
            if matched_fuel == fuel or matched_fuel == info["fuel_type"]:
                try:
                    price = float(m.group(2).replace(",", "."))
                    if 20 < price < 200:  # реалистичная цена
                        info["price"] = price
                except (ValueError, TypeError):
                    pass
                break
        # Если цена не найдена, ищем просто "N рублей" в тексте
        if not info["price"]:
            m = PRICE_PATTERN_RUB.search(text)
            if m:
                try:
                    price = float(m.group(1).replace(",", "."))
                    if 20 < price < 200:
                        info["price"] = price
                except (ValueError, TypeError):
                    pass

    # 4) Найти очереди
    for fuel, info in results_dict.items():
        m = QUEUE_PATTERN.search(text)
        if m and m.group(1):
            try:
                info["queue"] = int(m.group(1))
            except (ValueError, TypeError):
                pass

    # 5) Найти время следующего завоза
    for fuel, info in results_dict.items():
        nd = parse_delivery_time(text)
        if nd:
            info["next_delivery"] = nd

    return list(results_dict.values())


def parse_delivery_time(text: str) -> Optional[datetime]:
    """Извлекает дату/время следующего завоза из текста.

    Возвращает datetime в UTC.
    Поддерживает:
    - "завоз в 14:00" — сегодня в 14:00
    - "привезут через 2 часа" — через 2 часа
    - "привезут завтра в 10:00" — завтра в 10:00
    """
    text_lower = text.lower()
    now = datetime.now()  # local

    # Сначала ищем относительные выражения: "через N часов/минут"
    m = re.search(r"через\s+(\d+)\s*(час|ч|h)", text_lower)
    if m:
        from datetime import timezone
        return (now + timedelta(hours=int(m.group(1)))).astimezone(timezone.utc)

    m = re.search(r"через\s+(\d+)\s*(минут|мин|m)", text_lower)
    if m:
        from datetime import timezone
        return (now + timedelta(minutes=int(m.group(1)))).astimezone(timezone.utc)

    # Ищем дату/время
    day_offset = 0
    for word, offset in DATE_WORDS.items():
        if word in text_lower:
            day_offset = offset
            break

    # Ищем время "в HH:MM" или "HH:MM"
    m = re.search(r"(?:в\s+)?(\d{1,2}):(\d{2})", text)
    if m:
        try:
            from datetime import timezone
            hour, minute = int(m.group(1)), int(m.group(2))
            if 0 <= hour < 24 and 0 <= minute < 60:
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                target += timedelta(days=day_offset)
                # Если время уже прошло сегодня, предполагаем завтра
                if day_offset == 0 and target < now:
                    target += timedelta(days=1)
                return target.astimezone(timezone.utc)
        except ValueError:
            pass

    return None


# Маппинг вариантов названий городов → canonical name (как в БД)
# Ищем ключ в тексте, возвращаем значение
CITY_ALIASES: dict[str, str] = {
    # Москва
    "москва": "Москва", "москвы": "Москва", "москве": "Москва",
    "московской": "Москва", "московское": "Москва",
    # Санкт-Петербург
    "петербург": "Санкт-Петербург", "петербурга": "Санкт-Петербург",
    "петербурге": "Санкт-Петербург", "питер": "Санкт-Петербург",
    "питера": "Санкт-Петербург", "спб": "Санкт-Петербург",
    # Ростов-на-Дону
    "ростов": "Ростов-на-Дону", "ростова": "Ростов-на-Дону",
    "ростове": "Ростов-на-Дону", "ростове-на-дону": "Ростов-на-Дону",
    # Краснодар
    "краснодар": "Краснодар", "краснодара": "Краснодар",
    "краснодаре": "Краснодар",
    # Волгоград
    "волгоград": "Волгоград", "волгограда": "Волгоград",
    "волгограде": "Волгоград",
    # Казань
    "казань": "Казань", "казани": "Казань",
    # Екатеринбург
    "екатеринбург": "Екатеринбург", "екатеринбурга": "Екатеринбург",
    "екатеринбурге": "Екатеринбург", "екатербург": "Екатеринбург",
    # Новосибирск
    "новосибирск": "Новосибирск", "новосибирска": "Новосибирск",
    "новосибирске": "Новосибирск",
    # Нижний Новгород
    "нижний новгород": "Нижний Новгород", "нижнего новгорода": "Нижний Новгород",
    "нижнем новгороде": "Нижний Новгород", "нижний": None,  # skip — слишком короткое
    # Самара
    "самара": "Самара", "самары": "Самара", "самаре": "Самара",
    # Уфа
    "уфа": "Уфа", "уфы": "Уфа", "уфе": "Уфа",
    # Челябинск
    "челябинск": "Челябинск", "челябинска": "Челябинск",
    "челябинске": "Челябинск",
    # Пермь
    "пермь": "Пермь", "перми": "Пермь",
    # Красноярск
    "красноярск": "Красноярск", "красноярска": "Красноярск",
    "красноярске": "Красноярск",
    # Тюмень
    "тюмень": "Тюмень", "тюмени": "Тюмень",
    # Омск
    "омск": "Омск", "омска": "Омск", "омске": "Омск",
    # Воронеж
    "воронеж": "Воронеж", "воронежа": "Воронеж", "воронеже": "Воронеж",
    # Саратов
    "саратов": "Саратов", "саратова": "Саратов", "саратове": "Саратов",
    # Барнаул
    "барнаул": "Барнаул", "барнаула": "Барнаул", "барнауле": "Барнаул",
    # Иркутск
    "иркутск": "Иркутск", "иркутска": "Иркутск", "иркутске": "Иркутск",
    # Хабаровск
    "хабаровск": "Хабаровск", "хабаровска": "Хабаровск",
    "хабаровске": "Хабаровск",
    # Владивосток
    "владивосток": "Владивосток", "владивостока": "Владивосток",
    "владивостоке": "Владивосток",
    # Мурманск
    "мурманск": "Мурманск", "мурманска": "Мурманск", "мурманске": "Мурманск",
    # Архангельск
    "архангельск": "Архангельск", "архангельска": "Архангельск",
    "архангельске": "Архангельск",
    # Калининград
    "калининград": "Калининград", "калининграда": "Калининград",
    "калининграде": "Калининград",
    # Кемерово
    "кемерово": "Кемерово", "кемерова": "Кемерово", "кемерове": "Кемерово",
    # Рязань
    "рязань": "Рязань", "рязани": "Рязань",
    # Тула
    "тула": "Тула", "тулы": "Тула", "туле": "Тула",
    # Смоленск
    "смоленск": "Смоленск", "смоленска": "Смоленск", "смоленске": "Смоленск",
    # Брянск
    "брянск": "Брянск", "брянска": "Брянск", "брянске": "Брянск",
    # Курск
    "курск": "Курск", "курска": "Курск", "курске": "Курск",
    # Липецк
    "липецк": "Липецк", "липецка": "Липецк", "липецке": "Липецк",
    # Тамбов
    "тамбов": "Тамбов", "тамбова": "Тамбов", "тамбове": "Тамбов",
    # Пенза
    "пенза": "Пенза", "пензы": "Пенза", "пензе": "Пенза",
    # Ульяновск
    "ульяновск": "Ульяновск", "ульяновска": "Ульяновск",
    "ульяновске": "Ульяновск",
    # Саранск
    "саранск": "Саранск", "саранска": "Саранск", "саранске": "Саранск",
    # Чебоксары
    "чебоксары": "Чебоксары", "чебоксар": "Чебоксары",
    # Нижний Тагил
    "нижний тагил": "Нижний Тагил", "нижнего тагила": "Нижний Тагил",
    # Чита
    "чита": "Чита", "читы": "Чита", "чите": "Чита",
    # Якутск
    "якутск": "Якутск", "якутска": "Якутск", "якутске": "Якутск",
    # Махачкала
    "махачкала": "Махачкала", "махачкалы": "Махачкала",
    # Оренбург
    "оренбург": "Оренбург", "оренбурга": "Оренбург", "оренбурге": "Оренбург",
    # Новокузнецк
    "новокузнецк": "Новокузнецк", "новокузнецка": "Новокузнецк",
    # Томск
    "томск": "Томск", "томска": "Томск", "томске": "Томск",
    # Тверь
    "тверь": "Тверь", "твери": "Тверь",
    # Ярославль
    "ярославль": "Ярославль", "ярославля": "Ярославль",
    # Ижевск
    "ижевск": "Ижевск", "ижевска": "Ижевск",
    # Барнаул
    "барнаул": "Барнаул",
    # Крым
    "крым": "Крым", "крыму": "Крым", "крыме": "Крым",
    "севастополь": "Севастополь", "севастополя": "Севастополь",
    "симферополь": "Симферополь", "симферополя": "Симферополь",
    # Ивановская область
    "иваново": "Иваново", "иванова": "Иваново", "иванове": "Иваново",
    "ивановская": "Иваново", "ивановской": "Иваново",
    "кинешма": "Кинешма", "кинешмы": "Кинешма", "кинешме": "Кинешма",
    "куя": "Кинешма",
    "шуя": "Шуя", "шуи": "Шуя", "шую": "Шуя",
    "кохма": "Кохма", "кохмы": "Кохма", "кохме": "Кохма",
    "вичуга": "Вичуга", "вичуги": "Вичуга", "вичуге": "Вичуга",
    "фурманов": "Фурманов", "фурманова": "Фурманов",
    "приволжск": "Приволжск", "приволжска": "Приволжск",
    "пучеж": "Пучеж", "пучежа": "Пучеж",
    "заволжье": "Заволжье", "заволжья": "Заволжье",
    # Одесса (Украина)
    "одесса": "Одесса", "одессы": "Одесса", "одессе": "Одесса",
    "одеса": "Одесса",
    # Харьков
    "харьков": "Харьков", "харькова": "Харьков", "харькове": "Харьков",
    # Днепр
    "днепр": "Днепр", "днепра": "Днепр", "днепре": "Днепр",
    "днепропетровск": "Днепр", "днепропетровска": "Днепр",
    # Запорожье
    "запорожье": "Запорожье", "запорожья": "Запорожье",
    # Одесса (укр.)
    "odesa": "Одесса", "odessa": "Одесса",
}


def _extract_city_from_text(text: str) -> Optional[str]:
    """Извлекает город из текста сообщения.

    Приоритет: более длинные совпадения первыми.
    Возвращает canonical name или None.
    """
    text_lower = text.lower()
    # Сортируем по длине ключа (длинные первые) чтобы "нижний новгород" matched раньше "нижний"
    for alias, canonical in sorted(CITY_ALIASES.items(), key=lambda x: -len(x[0])):
        if canonical is None:
            continue
        if alias in text_lower:
            return canonical
    return None


async def find_station_by_text(network: Optional[str], text: str, city: Optional[str] = None) -> Optional[int]:
    """Ищет АЗС в БД по сети и городу (если указан).

    Приоритет:
    1) Сеть + город (точное совпадение)
    2) Только сеть
    3) Только город
    4) Fallback: случайная станция из БД

    Возвращает station_id или None.
    """
    # Если город не указан, пытаемся извлечь из текста
    if not city:
        city = _extract_city_from_text(text)

    # Нормализуем сеть для поиска в БД
    network_search = network.lower() if network else None

    # 1) Сеть + город (самый точный вариант)
    if network_search and city:
        if db.USE_SQLITE:
            rows = await db._fetch(
                """SELECT id FROM stations
                   WHERE (LOWER(operator) LIKE ? OR LOWER(name) LIKE ?)
                     AND py_lower(city) = py_lower(?)
                   ORDER BY is_verified DESC, id
                   LIMIT 1""",
                f"%{network_search}%",
                f"%{network_search}%",
                city,
            )
        else:
            async with db._db.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id FROM stations
                       WHERE (LOWER(operator) LIKE $1 OR LOWER(name) LIKE $1)
                         AND LOWER(city) = LOWER($2)
                       ORDER BY is_verified DESC, id
                       LIMIT 1""",
                    f"%{network_search}%",
                    city,
                )
        if rows:
            return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]

    # 2) Только сеть
    if network_search:
        if db.USE_SQLITE:
            rows = await db._fetch(
                """SELECT id FROM stations
                   WHERE LOWER(operator) LIKE ? OR LOWER(name) LIKE ?
                   ORDER BY is_verified DESC, id
                   LIMIT 1""",
                f"%{network_search}%",
                f"%{network_search}%",
            )
        else:
            async with db._db.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id FROM stations
                       WHERE LOWER(operator) LIKE $1 OR LOWER(name) LIKE $1
                       ORDER BY is_verified DESC, id
                       LIMIT 1""",
                    f"%{network_search}%",
                )
        if rows:
            return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]

    # 3) Только город
    if city:
        if db.USE_SQLITE:
            rows = await db._fetch(
                """SELECT id FROM stations
                   WHERE py_lower(city) = py_lower(?)
                   ORDER BY is_verified DESC, id
                   LIMIT 1""",
                city,
            )
        else:
            async with db._db.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id FROM stations
                       WHERE LOWER(city) = LOWER($1)
                       ORDER BY is_verified DESC, id
                       LIMIT 1""",
                    city,
                )
        if rows:
            return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]

    # 4) Fallback: случайная станция
    if db.USE_SQLITE:
        rows = await db._fetch("SELECT id FROM stations ORDER BY RANDOM() LIMIT 1")
    else:
        async with db._db.acquire() as conn:
            rows = await conn.fetch("SELECT id FROM stations ORDER BY RANDOM() LIMIT 1")
    if rows:
        return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]
    return None


async def save_telegram_report(
    station_id: int,
    fuel_type: str,
    available: Optional[bool],
    raw_text: str,
    price: Optional[float] = None,
    queue: Optional[int] = None,
    next_delivery: Optional[datetime] = None,
) -> int:
    """Сохраняет отчёт от парсера Telegram. Возвращает report_id."""
    report_id = await db.add_report(
        station_id=station_id,
        fuel_type=fuel_type,
        available=available,
        price=price,
        queue_size=queue,
        source="tg",
        comment=f"tg: {raw_text[:200]}",
        next_delivery_at=next_delivery,
    )
    logger.info(
        "✅ TG отчёт: station=%d fuel=%s avail=%s price=%s queue=%s next=%s",
        station_id, fuel_type, available, price, queue, next_delivery,
    )
    return report_id


async def upload_to_api(results: list, upload_url: str, api_key: str = "") -> bool:
    """Загружает в backend через /api/import_prices."""
    try:
        import aiohttp
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-Import-Key"] = api_key
        payload = {
            "source": "tg",
            "scraped_at": datetime.now().isoformat(),
            "results": results,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                upload_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    logger.info(f"✅ Загружено в API: {body}")
                    return True
                else:
                    text = await resp.text()
                    logger.warning(f"⚠ API {resp.status}: {text[:200]}")
                    return False
    except Exception as e:
        logger.warning(f"⚠ Upload: {e}")
        return False


async def handle_message(msg, upload_url: str = None, api_key: str = "", channel_name: str = "") -> int:
    """Обрабатывает одно сообщение: парсит и сохраняет.

    Возвращает количество сохранённых отчётов.
    """
    if not msg.text or len(msg.text) < 10:
        return 0
    parsed = parse_fuel_status(msg.text)
    if not parsed:
        return 0

    # Город по умолчанию для канала (если не извлечён из текста)
    channel_city = CHANNEL_CITY_HINTS.get(channel_name)

    saved = 0
    upload_results = []

    for p in parsed:
        city = _extract_city_from_text(msg.text) or channel_city
        station_id = await find_station_by_text(p.get("network"), msg.text, city=city)
        if not station_id:
            logger.debug("No station found for network=%s text=%r", p.get("network"), msg.text[:80])
            continue

        # Локальное сохранение
        if not upload_url:
            await save_telegram_report(
                station_id=station_id,
                fuel_type=p["fuel_type"],
                available=p["available"],
                raw_text=msg.text,
                price=p.get("price"),
                queue=p.get("queue"),
                next_delivery=p.get("next_delivery"),
            )
            saved += 1
        else:
            # Подготовим для upload в API
            upload_results.append({
                "external_id": f"tg_{msg.id}",
                "name": p.get("network", "Unknown") + f" #{station_id}",
                "region_name": p.get("network", "Unknown"),
                "city": None,
                "operator": p.get("network"),
                "lat": None,
                "lon": None,
                "prices": {p["fuel_type"]: p["price"]} if p.get("price") else {},
            })

    if upload_url and upload_results:
        await upload_to_api(upload_results, upload_url, api_key)

    return saved


async def run_once(upload_url: str = None, api_key: str = ""):
    """Один проход: читает последние N сообщений из каждого канала."""
    if not TG_API_ID or not TG_API_HASH:
        logger.error("TG_API_ID / TG_API_HASH не заданы. См. инструкции в начале файла.")
        sys.exit(1)
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if TG_SESSION_STRING:
        client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    else:
        client = TelegramClient(str(SESSION_PATH), int(TG_API_ID), TG_API_HASH)
    await client.start()
    logger.info("Authorized as %s", (await client.get_me()).username)
    import os
    if not os.getenv("_API_MODE"):
        await db.init_db()
    await db.stale_old_reports("tg")

    total_saved = 0
    for channel in CHANNELS:
        try:
            entity = await client.get_entity(channel)
        except Exception as e:
            logger.warning("Cannot find channel %s: %s", channel, e)
            continue
        logger.info("Scanning channel: %s", channel)
        count = 0
        for msg in await client.get_messages(entity, limit=200):
            saved = await handle_message(msg, upload_url, api_key, channel_name=channel)
            total_saved += saved
            count += 1
        logger.info("  Scanned %d messages in %s", count, channel)

    await client.disconnect()
    import os
    if not os.getenv("_API_MODE"):
        await db.close_db()
    logger.info("=== Total TG reports saved: %d ===", total_saved)
    return total_saved


async def run_watch(upload_url: str = None, api_key: str = ""):
    """Слушает новые сообщения в реальном времени."""
    if not TG_API_ID or not TG_API_HASH:
        logger.error("TG_API_ID / TG_API_HASH не заданы.")
        sys.exit(1)
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    if TG_SESSION_STRING:
        client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    else:
        client = TelegramClient(str(SESSION_PATH), int(TG_API_ID), TG_API_HASH)
    await client.start()
    logger.info("Authorized as %s", (await client.get_me()).username)
    logger.info("Watching for new messages in: %s", CHANNELS)
    await db.init_db()

    @client.on(events.NewMessage(chats=CHANNELS))
    async def handler(event):
        await handle_message(event.message, upload_url, api_key)

    await client.run_until_disconnected()


def main():
    parser = argparse.ArgumentParser(description="Парсер Telegram-каналов про бензин")
    parser.add_argument("--watch", action="store_true", help="Слушать новые сообщения в реальном времени")
    parser.add_argument("--upload-url", default=None, help="URL для POST с JSON (например backend /api/import_prices)")
    parser.add_argument("--api-key", default=os.environ.get("IMPORT_API_KEY", ""),
                        help="API ключ для upload-url")
    parser.add_argument("--channels", default=None,
                        help="Каналы через запятую (переопределяет TG_CHANNELS и DEFAULT_CHANNELS)")
    args = parser.parse_args()

    global CHANNELS
    if args.channels:
        CHANNELS = [c.strip() for c in args.channels.split(",") if c.strip()]

    if args.watch:
        asyncio.run(run_watch(args.upload_url, args.api_key))
    else:
        asyncio.run(run_once(args.upload_url, args.api_key))


if __name__ == "__main__":
    main()
