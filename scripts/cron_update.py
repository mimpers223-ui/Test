"""
Cron Job для Render — обновление ВСЕХ данных каждые 6 часов.

Использует все доступные парсеры:
- fuelprice.ru (главный источник, 60+ городов)
- parse_availability.py (gdebenz.ru + fuelprice.ru + benzup.ru)
- parse_tg_channels.py (304 TG каналов + приватные чаты)
- parse_vk_groups.py (557 VK групп)
- parse_all_sources.py (все источники: качество, очереди, лимиты)
- parse_fuel_quality.py (качество топлива)
- parse_queue_data.py (данные об очередях)
- parse_fuel_limits.py (данные о лимитах)
- parse_official_networks.py (18+ официальных сетей АЗС)
- benzin-status.tech (если доступен)

Шлёт в TG отчёт админу.

Render Cron Job schedule: "0 */6 * * *"
"""
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Добавляем bot/ в path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

from parse_fuelprice import main as parse_fuelprice_main


# Топ-12 городов (для fuelprice)
TOP_CITIES = [
    "moskva", "sankt-peterburg", "novosibirsk", "ekaterinburg",
    "kazan", "krasnodar", "chelyabinsk", "nizhniy-novgorod",
    "samara", "rostov-na-donu", "ufa", "krasnoyarsk",
    # Крым, ЛНР, ДНР
    "simferopol", "sevastopol", "kerch", "yalta",
    "evpatoriya", "feodosiya", "alushta", "bahchisaray",
    "saki", "dzhankoy",
    # Дополнительные крупные города
    "orenburg", "penza", "ryazan", "smolensk", "tula",
    "voronezh", "lipetsk", "kursk", "belgorod",
    "izhevsk", "cheboksary", "perm", "kirov",
    "tobolsk", "tyumen", "surgut", "nizhnevartovsk",
    "irkutsk", "bratsk", "angarsk",
    "habarovsk", "nahodka", "ussuriysk",
    "blagoveshchensk", "chita",
    "yuzhno-sahalinsk", "petropavlovsk-kamchatskiy",
    "yakutsk", "magadan",
    "stavropol", "pyatigorsk", "kislovodsk",
    "nalchik", "vladikavkaz", "grozny",
    "mahachkala", "derbent",
    "elenburg", "orsk", "novotroitsk",
    "sterlitamak", "salavat",
    "naberezhnye-chelny", "nizhnekamsk", "almetevsk",
    "berdnik", "berdsk", "ob",
    "kemerovo", "novokuznetsk", "prokopevsk",
    "barnaul", "biysk", "rubtsovsk",
    "omsk", "tomsk", "seversk",
    "abakan", "kyzyl",
]


# === Telegram уведомления ===
async def notify_admin(bot_token: str, chat_id: str, message: str) -> None:
    """Шлёт сообщение админу через Telegram Bot API."""
    if not bot_token or not chat_id:
        return
    try:
        import aiohttp
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        async with aiohttp.ClientSession() as s:
            await s.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
    except Exception as e:
        print(f"  ⚠ notify_admin: {e}")


async def run_fuelprice_for_all_cities() -> dict:
    """Запускает fuelprice.ru по всем городам."""
    print(f"\n[fuelprice.ru] {len(TOP_CITIES)} городов")
    print(f"  Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = {"matched": 0, "created": 0, "saved": 0, "errors": 0}

    for city in TOP_CITIES:
        try:
            # Запускаем парсер в subprocess чтобы изолировать
            import subprocess
            cmd = [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "parse_fuelprice.py"),
                "--city", city,
                "--create-new",
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
            )
            # Парсим вывод
            output = result.stdout + result.stderr
            for line in output.split("\n"):
                if "сохранено" in line.lower():
                    try:
                        num = int(line.split(":")[-1].strip())
                        results["saved"] += num
                    except (ValueError, IndexError):
                        pass
                elif "матч" in line.lower():
                    try:
                        num = int(line.split(":")[-1].strip())
                        results["matched"] += num
                    except (ValueError, IndexError):
                        pass
                elif "новых азс" in line.lower():
                    try:
                        num = int(line.split(":")[-1].strip())
                        results["created"] += num
                    except (ValueError, IndexError):
                        pass
                elif "error" in line.lower() or "timeout" in line.lower():
                    results["errors"] += 1
        except subprocess.TimeoutExpired:
            print(f"  ⏱ {city}: timeout")
            results["errors"] += 1
        except Exception as e:
            print(f"  ❌ {city}: {e}")
            results["errors"] += 1

    return results


async def run_availability_parsers() -> dict:
    """Запускает парсеры наличия (gdebenz.ru + fuelprice.ru + benzup.ru)."""
    print(f"\n=== Availability Parsers ===")
    results = {"gdebenz": 0, "fuelprice": 0, "benzup": 0, "errors": 0}

    # Запускаем parse_availability.py
    try:
        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "parse_availability.py"),
            "--all",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "gdebenz" in line.lower() and "found" in line.lower():
                try:
                    num = int(line.split("found")[-1].strip().split()[0])
                    results["gdebenz"] += num
                except (ValueError, IndexError):
                    pass
            elif "fuelprice" in line.lower() and "found" in line.lower():
                try:
                    num = int(line.split("found")[-1].strip().split()[0])
                    results["fuelprice"] += num
                except (ValueError, IndexError):
                    pass
            elif "benzup" in line.lower() and "found" in line.lower():
                try:
                    num = int(line.split("found")[-1].strip().split()[0])
                    results["benzup"] += num
                except (ValueError, IndexError):
                    pass
            elif "error" in line.lower():
                results["errors"] += 1
    except subprocess.TimeoutExpired:
        print("  ⏱ Availability parsers: timeout")
        results["errors"] += 1
    except Exception as e:
        print(f"  ❌ Availability parsers: {e}")
        results["errors"] += 1

    return results


async def run_tg_parser() -> dict:
    """Запускает TG парсер каналов."""
    print(f"\n=== TG Channels Parser ===")
    results = {"saved": 0, "channels_found": 0, "errors": 0}

    try:
        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "parse_tg_channels.py"),
            "--discover",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "total tg reports saved" in line.lower():
                try:
                    num = int(line.split(":")[-1].strip())
                    results["saved"] = num
                except (ValueError, IndexError):
                    pass
            elif "scanning channel" in line.lower():
                results["channels_found"] += 1
            elif "error" in line.lower() or "cannot find channel" in line.lower():
                results["errors"] += 1
    except subprocess.TimeoutExpired:
        print("  ⏱ TG parser: timeout")
        results["errors"] += 1
    except Exception as e:
        print(f"  ❌ TG parser: {e}")
        results["errors"] += 1

    return results


async def run_vk_parser() -> dict:
    """Запускает VK парсер групп."""
    print(f"\n=== VK Groups Parser ===")
    results = {"saved": 0, "groups_found": 0, "errors": 0}

    try:
        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "parse_vk_groups.py"),
            "--all",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "vk:" in line.lower() and "saved" in line.lower():
                try:
                    num = int(line.split("saved")[-1].strip().split()[0])
                    results["saved"] = num
                except (ValueError, IndexError):
                    pass
            elif "vk city:" in line.lower():
                results["groups_found"] += 1
            elif "error" in line.lower():
                results["errors"] += 1
    except subprocess.TimeoutExpired:
        print("  ⏱ VK parser: timeout")
        results["errors"] += 1
    except Exception as e:
        print(f"  ❌ VK parser: {e}")
        results["errors"] += 1

    return results


async def run_all_sources_parser() -> dict:
    """Запускает парсер всех источников (качество, очереди, лимиты)."""
    print(f"\n=== All Sources Parser ===")
    results = {"quality": 0, "queues": 0, "limits": 0, "errors": 0}

    try:
        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "parse_all_sources.py"),
            "--all-cities",
            "--source", "everything",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "total saved:" in line.lower():
                try:
                    num = int(line.split(":")[-1].strip())
                    results["quality"] = num
                    results["queues"] = num
                    results["limits"] = num
                except (ValueError, IndexError):
                    pass
            elif "error" in line.lower():
                results["errors"] += 1
    except subprocess.TimeoutExpired:
        print("  ⏱ All sources parser: timeout")
        results["errors"] += 1
    except Exception as e:
        print(f"  ❌ All sources parser: {e}")
        results["errors"] += 1

    return results


async def run_fuel_quality_parser() -> dict:
    """Запускает парсер качества топлива."""
    print(f"\n=== Fuel Quality Parser ===")
    results = {"saved": 0, "errors": 0}

    try:
        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "parse_fuel_quality.py"),
            "--all-cities",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "total quality reports saved:" in line.lower():
                try:
                    num = int(line.split(":")[-1].strip())
                    results["saved"] = num
                except (ValueError, IndexError):
                    pass
            elif "error" in line.lower():
                results["errors"] += 1
    except subprocess.TimeoutExpired:
        print("  ⏱ Fuel quality parser: timeout")
        results["errors"] += 1
    except Exception as e:
        print(f"  ❌ Fuel quality parser: {e}")
        results["errors"] += 1

    return results


async def run_queue_parser() -> dict:
    """Запускает парсер данных об очередях."""
    print(f"\n=== Queue Data Parser ===")
    results = {"saved": 0, "errors": 0}

    try:
        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "parse_queue_data.py"),
            "--all-cities",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "total queue reports saved:" in line.lower():
                try:
                    num = int(line.split(":")[-1].strip())
                    results["saved"] = num
                except (ValueError, IndexError):
                    pass
            elif "error" in line.lower():
                results["errors"] += 1
    except subprocess.TimeoutExpired:
        print("  ⏱ Queue parser: timeout")
        results["errors"] += 1
    except Exception as e:
        print(f"  ❌ Queue parser: {e}")
        results["errors"] += 1

    return results


async def run_limits_parser() -> dict:
    """Запускает парсер лимитов и запретов на канистры."""
    print(f"\n=== Limits & Canisters Parser ===")
    results = {"saved": 0, "errors": 0}

    try:
        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "parse_limits_canisters.py"),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "лимитов сохранено:" in line.lower():
                try:
                    num = int(line.split(":")[-1].strip())
                    results["saved"] += num
                except (ValueError, IndexError):
                    pass
            elif "error" in line.lower():
                results["errors"] += 1
    except subprocess.TimeoutExpired:
        print("  ⏱ Limits parser: timeout")
        results["errors"] += 1
    except Exception as e:
        print(f"  ❌ Limits parser: {e}")
        results["errors"] += 1

    return results


async def run_gdebenz_parser() -> dict:
    """Запускает gdebenz.ru парсер (760+ городов)."""
    print(f"\n=== GdeBenz Parser (760+ городов) ===")
    results = {"saved": 0, "errors": 0}

    try:
        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "parse_gdebenz.py"),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 минут на 760+ городов
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "отчётов сохранено:" in line.lower() or "total gdebenz reports saved" in line.lower():
                try:
                    num = int(line.split(":")[-1].strip())
                    results["saved"] += num
                except (ValueError, IndexError):
                    pass
            elif "error" in line.lower():
                results["errors"] += 1
    except subprocess.TimeoutExpired:
        print("  ⏱ GdeBenz parser: timeout (600s)")
        results["errors"] += 1
    except Exception as e:
        print(f"  ❌ GdeBenz parser: {e}")
        results["errors"] += 1

    return results


async def run_quick_parser() -> dict:
    """Запускает быстрый парсер (2GIS, weather, news)."""
    print(f"\n=== Quick Parser ===")
    results = {"saved": 0, "errors": 0}

    try:
        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "parse_quick.py"),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "отчётов в бд:" in line.lower():
                try:
                    num = int(line.split(":")[-1].strip())
                    results["saved"] = num
                except (ValueError, IndexError):
                    pass
            elif "error" in line.lower():
                results["errors"] += 1
    except subprocess.TimeoutExpired:
        print("  ⏱ Quick parser: timeout")
        results["errors"] += 1
    except Exception as e:
        print(f"  ❌ Quick parser: {e}")
        results["errors"] += 1

    return results


async def run_vk_search_parser() -> dict:
    """Запускает VK парсер через newsfeed.search."""
    print(f"\n=== VK Search Parser ===")
    results = {"saved": 0, "errors": 0}

    token = os.getenv("VK_SERVICE_TOKEN", "")
    if not token:
        print("  ⏭ VK_SERVICE_TOKEN не задан, пропускаю")
        return results

    try:
        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "parse_vk.py"),
            "--api", "--search",
            "--query", "АИ-95 цена руб",
            "--limit", "50",
        ]
        env = os.environ.copy()
        env["VK_SERVICE_TOKEN"] = token
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "сохранено:" in line.lower():
                try:
                    num = int(line.split(":")[-1].strip())
                    results["saved"] += num
                except (ValueError, IndexError):
                    pass
            elif "error" in line.lower():
                results["errors"] += 1
    except subprocess.TimeoutExpired:
        print("  ⏱ VK parser: timeout")
        results["errors"] += 1
    except Exception as e:
        print(f"  ❌ VK parser: {e}")
        results["errors"] += 1

    return results


async def main():
    start_time = time.time()
    print("=" * 60)
    print(f"⛽ CRON UPDATE — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # === Инициализация БД ===
    await db.init_db()

    # === Запуск рабочих парсеров ===
    fuelprice_results = await run_fuelprice_for_all_cities()
    gdebenz_results = await run_gdebenz_parser()
    limits_results = await run_limits_parser()  # Лимиты и запреты на канистры
    tg_results = await run_tg_parser()
    vk_results = await run_vk_search_parser()
    quick_results = await run_quick_parser()

    elapsed = time.time() - start_time

    # === Статистика из БД ===
    stats = await db._fetch("""
        SELECT source, COUNT(*) as cnt
        FROM reports
        WHERE created_at > NOW() - INTERVAL '24 hours'
        GROUP BY source
        ORDER BY cnt DESC
    """)

    total_recent = sum(s["cnt"] for s in stats)

    # === Отчёт ===
    report = (
        f"⛽ <b>Cron Update отчёт</b>\n\n"
        f"⏱ Время: {elapsed:.0f} сек\n"
        f"📊 Обновлено за 24ч: <b>{total_recent}</b> отчётов\n\n"
        f"<b>Источники (24ч):</b>\n"
    )
    for s in stats:
        report += f"  • {s['source']}: {s['cnt']}\n"

    report += f"\n<b>Этот запуск:</b>\n"
    report += f"  📈 fuelprice.ru: {fuelprice_results['saved']} цен ({len(TOP_CITIES)} городов)\n"
    report += f"  🗺 GdeBenz: {gdebenz_results['saved']} отчётов (760+ городов)\n"
    report += f"  ⛽ Лимиты/канистры: {limits_results['saved']} отчётов\n"
    report += f"  📱 TG каналы: {tg_results['saved']} отчётов ({tg_results['channels_found']} каналов)\n"
    report += f"  🔗 VK поиск: {vk_results['saved']} отчётов\n"
    report += f"  ⚡ Быстрый: {quick_results['saved']} отчётов\n"
    total_errors = (fuelprice_results['errors'] + gdebenz_results['errors'] +
                   limits_results['errors'] + tg_results['errors'] + vk_results['errors'] + quick_results['errors'])
    if total_errors:
        report += f"  ⚠ Ошибок: {total_errors}\n"

    print("\n" + report.replace("<b>", "").replace("</b>", ""))

    # === TG уведомление ===
    bot_token = os.getenv("BOT_TOKEN", "")
    chat_id = os.getenv("ADMIN_CHAT_ID", os.getenv("CHANNEL_CHAT_ID", ""))
    if bot_token and chat_id:
        await notify_admin(bot_token, chat_id, report)

    await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
