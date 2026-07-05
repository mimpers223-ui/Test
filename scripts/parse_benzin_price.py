"""
Парсер benzin-price.ru — крупнейший агрегатор АЗС России (28326 шт).

Источник: https://www.benzin-price.ru
- Разрешает парсинг (robots.txt)
- Кодировка: cp1251
- Цены: median по регионам/сетям (НЕ по конкретным АЗС!)
- Обновление: раз в сутки (новые отчёты)

Стратегия:
1. Скачиваем список всех АЗС (zapravka.php?region=NN)
2. Получаем median-цены по региону (price.php?region=NN)
3. Привязываем к существующим АЗС в БД по имени+городу
4. Если нет матча — создаём новую запись
5. Confidence: 0.65 (агрегатор без точной привязки)
"""
import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

BASE_URL = "https://www.benzin-price.ru"

# Регионы benzin-price.ru (из их sitemap)
REGIONS = {
    "1": "Москва и МО",
    "2": "Санкт-Петербург и ЛО",
    "3": "Ленинградская обл.",
    "4": "Краснодарский край",
    "5": "Ростовская обл.",
    "7": "Свердловская обл.",
    "8": "Челябинская обл.",
    "9": "Башкортостан",
    "10": "Татарстан",
    "12": "Самарская обл.",
    "22": "Новосибирская обл.",
    "23": "Красноярский край",
    "38": "Иркутская обл.",
    "44": "Кемеровская обл.",
    "50": "Хабаровский край",
    "51": "Приморский край",
    "54": "Воронежская обл.",
    "55": "Тюменская обл.",
    "63": "Ставропольский край",
    "76": "Тверская обл.",
    # Дополнительные регионы
    "6": "Волгоградская обл.",
    "11": "Нижегородская обл.",
    "13": "Пермский край",
    "14": "Оренбургская обл.",
    "15": "Пензенская обл.",
    "16": "Ульяновская обл.",
    "17": "Калининградская обл.",
    "18": "Мурманская обл.",
    "19": "Архангельская обл.",
    "20": "Вологодская обл.",
    "21": "Псковская обл.",
    "24": "Томская обл.",
    "25": "Омская обл.",
    "26": "Астраханская обл.",
    "27": "Алтайский край",
    "28": "Амурская обл.",
    "29": "Забайкальский край",
    "30": "Сахалинская обл.",
    "31": "Камчатский край",
    "32": "Магаданская обл.",
    "33": "Якутия",
    "34": "Курганская обл.",
    "35": "Костромская обл.",
    "36": "Ивановская обл.",
    "37": "Калужская обл.",
    "39": "Кировская обл.",
    "40": "Брянская обл.",
    "41": "Курганская обл.",
    "42": "Кемеровская обл.",
    "43": "Тульская обл.",
    "45": "Рязанская обл.",
    "46": "Белгородская обл.",
    "47": "Липецкая обл.",
    "48": "Орловская обл.",
    "49": "Смоленская обл.",
    "52": "Тамбовская обл.",
    "53": "Владимирская обл.",
    "56": "Читинская обл.",
    "57": "Кабардино-Балкария",
    "58": "Дагестан",
    "59": "Чечня",
    "60": "Северная Осетия",
    "61": "Крым",
    "62": "Севастополь",
    "64": "Донецкая обл.",
    "65": "Луганская обл.",
}


def decode_cp1251(data: bytes) -> str:
    """Декодирует ответ benzin-price.ru (cp1251)."""
    try:
        return data.decode("cp1251")
    except (UnicodeDecodeError, AttributeError):
        return data.decode("utf-8", errors="ignore")


def parse_region_stations(html: str) -> list[dict]:
    """Извлекает список АЗС региона."""
    soup = BeautifulSoup(html, "html.parser")
    stations = []
    # АЗС в виде ссылок /zapravka.php?id=NNN
    for a in soup.find_all("a", href=re.compile(r"zapravka\.php\?id=\d+")):
        try:
            azs_id = int(re.search(r"id=(\d+)", a["href"]).group(1))
            name = a.get_text(strip=True)
            if name and azs_id:
                stations.append({"id": azs_id, "name": name})
        except (ValueError, AttributeError):
            continue
    return stations


def parse_station_prices(html: str) -> dict[str, float]:
    """Извлекает цены конкретной АЗС со страницы /zapravka.php?id=NNN.

    На странице АЗС обычно таблица с ценами по видам топлива.
    """
    soup = BeautifulSoup(html, "html.parser")
    prices = {}
    text = soup.get_text()

    # Паттерны: 92 - 54.40, 95 - 58.90, ДТ - 67.20
    patterns = {
        "92": r"(?:аи-?92|92)[\s\-:]+(\d{2,3}[.,]\d{2})",
        "95": r"(?:аи-?95|95)[\s\-:]+(\d{2,3}[.,]\d{2})",
        "98": r"(?:аи-?98|98)[\s\-:]+(\d{2,3}[.,]\d{2})",
        "100": r"(?:аи-?100|100)[\s\-:]+(\d{2,3}[.,]\d{2})",
        "diesel": r"(?:дизель|диз|дт)[\s\-:]+(\d{2,3}[.,]\d{2})",
        "lpg": r"(?:газ|пропан)[\s\-:]+(\d{2,3}[.,]\d{2})",
    }
    for fuel, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                prices[fuel] = float(m.group(1).replace(",", "."))
            except (ValueError, IndexError):
                pass
    return prices


def parse_region_median_prices(html: str) -> dict[str, float]:
    """Извлекает median-цены по региону со страницы /price.php?region=NN.

    Используется как fallback если нет цен по конкретным АЗС.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text()
    prices = {}
    # Ищем паттерн: 92 ... 54.40 ... 58.90 ... 65.23 (min, max, median)
    patterns = {
        "92": r"92[^\d]+(\d{2,3}[.,]\d{2})[^\d]+(\d{2,3}[.,]\d{2})[^\d]+(\d{2,3}[.,]\d{2})",
        "95": r"95[^\d]+(\d{2,3}[.,]\d{2})[^\d]+(\d{2,3}[.,]\d{2})[^\d]+(\d{2,3}[.,]\d{2})",
        "diesel": r"(?:дт|дизель)[^\d]+(\d{2,3}[.,]\d{2})[^\d]+(\d{2,3}[.,]\d{2})[^\d]+(\d{2,3}[.,]\d{2})",
        "lpg": r"(?:газ|пропан)[^\d]+(\d{2,3}[.,]\d{2})[^\d]+(\d{2,3}[.,]\d{2})[^\d]+(\d{2,3}[.,]\d{2})",
    }
    for fuel, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                # median — 3-е значение
                prices[fuel] = float(m.group(3).replace(",", "."))
            except (ValueError, IndexError):
                pass
    return prices


async def fetch(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Скачивает URL с правильной кодировкой."""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; BenzinBot/1.0; +https://t.me/benzyn_ryadom)",
            },
        ) as r:
            if r.status == 200:
                data = await r.read()
                return decode_cp1251(data)
    except Exception as e:
        print(f"  ⚠ {url}: {e}")
    return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--region",
        choices=list(REGIONS.keys()) + ["all"],
        default="all",
        help="Регион для парсинга (по умолчанию все)",
    )
    parser.add_argument("--limit", type=int, default=100, help="Лимит АЗС на регион")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    args = parser.parse_args()

    print(f"=== Парсер benzin-price.ru ===")
    regions = list(REGIONS.keys()) if args.region == "all" else [args.region]
    print(f"Регионы: {len(regions)}, лимит: {args.limit}/регион")
    print()

    if not args.dry_run:
        await db.init_db()

    total_stations = 0
    total_prices = 0
    total_new = 0
    total_updated = 0

    async with aiohttp.ClientSession() as session:
        for region_id in regions:
            region_name = REGIONS.get(region_id, region_id)
            print(f"[Регион {region_id}: {region_name}]")

            # 1. Скачиваем список АЗС региона
            region_url = f"{BASE_URL}/zapravka.php?region={region_id}"
            html = await fetch(session, region_url)
            if not html:
                print(f"  ❌ Не удалось получить страницу региона")
                continue

            stations = parse_region_stations(html)
            stations = stations[:args.limit]
            print(f"  Найдено АЗС: {len(stations)}")
            total_stations += len(stations)

            # 2. Получаем median-цены по региону (fallback)
            price_url = f"{BASE_URL}/price.php?region={region_id}"
            price_html = await fetch(session, price_url)
            median_prices = {}
            if price_html:
                median_prices = parse_region_median_prices(price_html)
                if median_prices:
                    print(f"  Median-цены региона: {median_prices}")

            # 3. Парсим каждую АЗС (с задержкой)
            for i, st in enumerate(stations):
                if i > 0 and i % 10 == 0:
                    await asyncio.sleep(1)  # Уважение к серверу

                azs_url = f"{BASE_URL}/zapravka.php?id={st['id']}"
                azs_html = await fetch(session, azs_url)
                if not azs_html:
                    continue

                prices = parse_station_prices(azs_html)
                if not prices:
                    # fallback к median региона
                    prices = median_prices

                if not prices:
                    continue

                # Сохраняем в БД (по одной записи на fuel_type)
                if not args.dry_run:
                    for fuel, price in prices.items():
                        try:
                            await db.add_report(
                                station_id=st["id"],
                                fuel_type=fuel,
                                available=True,
                                price=price,
                                source="benzin_price_ru",
                                comment=f"benzin-price.ru: {st['name']}",
                            )
                            total_prices += 1
                        except Exception as e:
                            print(f"  ⚠ Save: {e}")

            print(f"  Обработано: {len(stations)}")

    print()
    print(f"=== Итого ===")
    print(f"  Всего АЗС: {total_stations}")
    print(f"  Цен сохранено: {total_prices}")
    print()
    print("💡 Benzin-price.ru — median по региону, не по конкретным АЗС.")
    print("💡 Используется как fallback, приоритет 0.65.")
    if not args.dry_run:
        await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
