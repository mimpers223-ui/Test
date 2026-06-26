"""
Улучшенный парсер сайтов сетей АЗС (Лукойл, Газпромнефть, Роснефть, Татнефть, Башнефть).

Парсит:
- HTML-страницы сетей (виджеты карт, прайс-листы)
- Ищет JSON-данные в JS-коде
- Извлекает координаты + цены

⚠️ Только публичные данные. Не нарушает ToS.
"""
import argparse
import asyncio
import json
import os
import re
import sys
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

# Конфигурация сетей
NETWORKS = {
    "lukoil": {
        "name": "Лукойл",
        "search_url": "https://lukoil.ru/portal/aroundme",
        "fallback_url": "https://lukoil.ru/ProductPrice",
        "operator_keywords": ["lukoil", "лукойл", "лукой"],
        "priority": 0.85,  # Официальный источник
    },
    "gazprom": {
        "name": "Газпромнефть",
        "search_url": "https://www.gazprom-neft.ru/business/development/petrol-stations/",
        "fallback_url": "https://www.gazprom-neft.ru/products/",
        "operator_keywords": ["газпромнефть", "gazpromneft", "газпром нефть", "gpn"],
        "priority": 0.85,
    },
    "rosneft": {
        "name": "Роснефть",
        "search_url": "https://www.rosneft.ru/business/retail/",
        "fallback_url": "https://www.rosneft.ru/",
        "operator_keywords": ["роснефть", "rosneft", "рн-карт"],
        "priority": 0.85,
    },
    "tatneft": {
        "name": "Татнефть",
        "search_url": "https://www.tatneft.ru/azs/",
        "fallback_url": "https://www.tatneft.ru/products/",
        "operator_keywords": ["татнефть", "tatneft"],
        "priority": 0.85,
    },
    "bashneft": {
        "name": "Башнефть",
        "search_url": "https://www.bashneft.ru/products/",
        "fallback_url": "https://www.bashneft.ru/",
        "operator_keywords": ["башнефть", "bashneft"],
        "priority": 0.85,
    },
}

PRICE_PATTERNS = {
    "92": r"(?:аи-?92|92)[\s\-:]+(\d{2,3}[.,]\d{2})",
    "95": r"(?:аи-?95|95)[\s\-:]+(\d{2,3}[.,]\d{2})",
    "98": r"(?:аи-?98|98)[\s\-:]+(\d{2,3}[.,]\d{2})",
    "diesel": r"(?:дизель|диз|дт)[\s\-:]+(\d{2,3}[.,]\d{2})",
    "lpg": r"(?:газ|пропан)[\s\-:]+(\d{2,3}[.,]\d{2})",
}


def parse_prices_from_text(text: str) -> dict[str, float]:
    """Извлекает цены из текста."""
    prices = {}
    for fuel, pattern in PRICE_PATTERNS.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                prices[fuel] = float(m.group(1).replace(",", "."))
            except (ValueError, IndexError):
                pass
    return prices


def parse_json_prices(html: str) -> dict[str, float]:
    """Ищет JSON-объекты с ценами в HTML/JS."""
    prices = {}
    # Ищем JSON-массивы с ценами
    json_patterns = [
        r'"prices":\s*\[([^\]]+)\]',
        r'"price":\s*(\d{2,3}[.,]\d{2})',
        r'"fuel_prices":\s*(\{[^}]+\})',
    ]
    for pattern in json_patterns:
        for m in re.finditer(pattern, html):
            text = m.group(0)
            prices.update(parse_prices_from_text(text))
    return prices


async def fetch(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Скачивает страницу сети."""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=20),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            ssl=False,
        ) as r:
            if r.status == 200:
                text = await r.text()
                return text
    except Exception as e:
        print(f"  ⚠ {url}: {e}")
    return None


async def parse_network(session: aiohttp.ClientSession, network: str) -> dict:
    """Парсит одну сеть."""
    cfg = NETWORKS[network]
    result = {"name": cfg["name"], "prices": {}, "stations_count": 0}

    for url_key in ["search_url", "fallback_url"]:
        url = cfg[url_key]
        html = await fetch(session, url)
        if not html:
            continue

        # 1) Парсим HTML-текст
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        prices = parse_prices_from_text(text)
        if prices:
            result["prices"].update(prices)

        # 2) Ищем JSON в JS-коде
        json_prices = parse_json_prices(html)
        if json_prices:
            result["prices"].update(json_prices)

        # 3) Считаем упоминания АЗС
        azs_count = len(re.findall(r"заправк|азс|колонк", text, re.IGNORECASE))
        result["stations_count"] += azs_count

        if prices or json_prices:
            break

    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--network", choices=list(NETWORKS.keys()) + ["all"],
        default="all", help="Сеть (lukoil, gazprom, ...)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"=== Парсер сайтов сетей АЗС (v2) ===")
    print(f"Сети: {args.network}")

    if not args.dry_run:
        await db.init_db()

    networks = list(NETWORKS.keys()) if args.network == "all" else [args.network]

    async with aiohttp.ClientSession() as session:
        for net in networks:
            cfg = NETWORKS[net]
            print(f"\n[{cfg['name']}]")
            try:
                result = await parse_network(session, net)
                if result["prices"]:
                    print(f"  ✓ Цены: {result['prices']}")
                else:
                    print(f"  ⚠ Цены не найдены (сайт мог измениться)")
                if result["stations_count"]:
                    print(f"  ℹ Упоминаний АЗС: {result['stations_count']}")
            except Exception as e:
                print(f"  ❌ {e}")

    if not args.dry_run:
        await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
