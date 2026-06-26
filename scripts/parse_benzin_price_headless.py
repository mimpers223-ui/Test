"""
Парсер benzin-price.ru через Playwright (headless browser).

⚠️ benzin-price.ru использует JS-challenge (anti-bot).
Без headless browser парсинг не работает.

Использование:
  pip install playwright
  python -m playwright install chromium
  python scripts/parse_benzin_price_headless.py --region 1 --limit 10

⚠️ НЕ ЗАПУСКАТЬ на Render Free (тяжёлый, требует Chromium).
Только на локальной машине / VPS.
"""
import argparse
import asyncio
import os
import re
import sys
from datetime import datetime

try:
    from playwright.async_api import async_playwright
    from bs4 import BeautifulSoup
except ImportError:
    print("pip install playwright beautifulsoup4")
    print("python -m playwright install chromium")
    sys.exit(1)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

BASE_URL = "https://www.benzin-price.ru"
SOURCE_NAME = "benzin_price_ru"


# Регионы benzin-price.ru
REGIONS = {
    "1": "Москва и МО",
    "2": "Санкт-Петербург",
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
}


async def scrape_region(page, region_id: str, limit: int) -> list[dict]:
    """Собирает АЗС региона через Playwright."""
    # Получаем список АЗС
    list_url = f"{BASE_URL}/zapravka.php?region={region_id}"
    await page.goto(list_url, timeout=30000, wait_until="networkidle")
    await asyncio.sleep(2)  # Дополнительное ожидание

    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")

    # Ищем ссылки на конкретные АЗС
    stations = []
    for a in soup.find_all("a", href=re.compile(r"zapravka\.php\?id=\d+")):
        try:
            azs_id = int(re.search(r"id=(\d+)", a["href"]).group(1))
            name = a.get_text(strip=True)
            if name and azs_id:
                stations.append({"id": azs_id, "name": name})
        except (ValueError, AttributeError):
            continue

    stations = stations[:limit]
    print(f"  Найдено АЗС: {len(stations)}")

    # Получаем цены для каждой АЗС
    results = []
    for st in stations:
        url = f"{BASE_URL}/zapravka.php?id={st['id']}"
        try:
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            await asyncio.sleep(0.5)

            html = await page.content()
            text = BeautifulSoup(html, "html.parser").get_text()

            prices = parse_prices(text)
            if prices:
                results.append({
                    "id": st["id"],
                    "name": st["name"],
                    "prices": prices,
                })
                if len(results) <= 3:
                    print(f"    {st['name']}: {prices}")
        except Exception as e:
            print(f"    ⚠ {st['name']}: {e}")

        await asyncio.sleep(0.3)

    return results


def parse_prices(text: str) -> dict[str, float]:
    """Парсит цены из HTML."""
    prices = {}
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


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="all", help="ID региона или 'all'")
    parser.add_argument("--limit", type=int, default=20, help="Лимит АЗС")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    args = parser.parse_args()

    print(f"=== benzin-price.ru (Playwright) ===")

    if not args.dry_run:
        await db.init_db()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        regions = list(REGIONS.keys()) if args.region == "all" else [args.region]
        total = 0

        for region_id in regions:
            region_name = REGIONS.get(region_id, region_id)
            print(f"\n[Регион {region_id}: {region_name}]")
            try:
                results = await scrape_region(page, region_id, args.limit)
                total += len(results)
                # TODO: сохранение в БД
            except Exception as e:
                print(f"  ❌ {e}")

        await browser.close()

    print(f"\n=== Итого ===")
    print(f"  АЗС с ценами: {total}")
    if not args.dry_run:
        await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
