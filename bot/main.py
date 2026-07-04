"""
Бот «Бензин рядом» — точка входа.
Запускает одновременно:
- Telegram-бота (polling)
- VK-бота (polling)
- HTTP API для Mini App (порт 8080)
"""
import asyncio
import logging
import os
import signal
import ssl
import sys
from pathlib import Path

import certifi
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

# Загружаем .env ДО импорта остальных модулей
ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

from api import create_app
from config import settings
from db import close_db, init_db
from handlers import register_all_handlers
from push_worker import push_loop
from channel_poster import channel_loop
from vk_bot import run_vk_bot

# Логирование
BOT_DIR = Path(__file__).parent
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BOT_DIR / "bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def make_session() -> AiohttpSession:
    """Создаёт aiohttp session с устойчивым SSL (обход macOS SSL handshake issue)."""
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    class _CustomSession(AiohttpSession):
        async def create_session(self):
            import aiohttp
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(
                    ssl=ssl_context,
                    force_close=True,
                    enable_cleanup_closed=True,
                    ttl_dns_cache=300,
                )
                self._session = aiohttp.ClientSession(
                    connector=connector,
                    headers={"User-Agent": "aiogram/3.4"},
                )
            return self._session

    return _CustomSession()


async def run_api():
    """Запускает HTTP API для Mini App."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("API сервер запущен: http://0.0.0.0:%d", port)
    return runner


async def run_bot():
    """Запускает Telegram-бота."""
    import re as _re
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN пуст. Проверь bot/.env")

    # Security: валидация формата токена
    if not _re.match(r'^\d{10}:[A-Za-z0-9_-]{35}$', settings.BOT_TOKEN):
        logger.warning("BOT_TOKEN format looks unusual — check if it's correct")

    if settings.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN is placeholder! Set real token in bot/.env")

    session = make_session()
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
        session=session,
    )
    dp = Dispatcher()
    register_all_handlers(dp)
    settings.bot = bot

    try:
        # drop_pending_updates=False — НЕ сбрасываем сообщения при перезапуске
        # (если Render перезапустился, а пользователь нажал /start — сообщение потеряется)
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as e:
        logger.warning("delete_webhook failed (continuing): %s", e)

    # === Menu button для Mini App ===
    web_app_url = settings.WEB_APP_URL or "https://benzin-ryadom.onrender.com/v2"
    try:
        from aiogram.types import MenuButtonWebApp
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="📱 Приложение",
                web_app=MenuButtonWebApp.WebAppInfo(url=web_app_url),
            )
        )
        logger.info("Menu button set: '📱 Приложение' → %s", web_app_url)
    except Exception as e:
        logger.warning("set_chat_menu_button failed: %s", e)

    # === Команды в меню TG ===
    try:
        from aiogram.types import BotCommand
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 Главное меню"),
            BotCommand(command="find", description="🔍 Найти АЗС по городу или адресу"),
            BotCommand(command="app", description="📱 Открыть приложение с картой"),
            BotCommand(command="subscribe", description="🔔 Уведомления о наличии топлива"),
            BotCommand(command="profile", description="👤 Мой профиль и статистика"),
            BotCommand(command="my_stations", description="🏪 Мои АЗС (избранное)"),
            BotCommand(command="help", description="❓ Как пользоваться ботом"),
        ])
        # English commands for international users
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 Main menu"),
            BotCommand(command="find", description="🔍 Find gas station by city or address"),
            BotCommand(command="app", description="📱 Open map app"),
            BotCommand(command="subscribe", description="🔔 Fuel availability alerts"),
            BotCommand(command="profile", description="👤 My profile"),
            BotCommand(command="my_stations", description="🏪 My stations (favorites)"),
            BotCommand(command="help", description="❓ How to use"),
        ], language_code="en")
    except Exception as e:
        logger.warning("set_my_commands failed: %s", e)

    # === Описание бота (для поиска в Telegram) ===
    try:
        await bot.set_my_description(
            "🔍 Найди ближайшую АЗС с нужным топливом\n\n"
            "• АИ-92, АИ-95, АИ-98, АИ-100, Дизель, Газ\n"
            "• Реальные цены и наличие от водителей\n"
            "• Уведомления о наличии в твоём районе\n"
            "• Отмечай АЗС и делись ценами с сообществом\n\n"
            "27 000+ АЗС в 16 городах России"
        )
        await bot.set_my_short_description(
            "🔍 Найти АЗС с бензином, ценами и наличием от водителей"
        )
        await bot.set_my_description(
            "🔍 Find the nearest gas station with available fuel\n\n"
            "• AI-92, AI-95, AI-98, AI-100, Diesel, LPG\n"
            "• Real prices and availability from drivers\n"
            "• Fuel availability alerts in your area\n"
            "• Report stations and share prices with community\n\n"
            "27,000+ gas stations in 16 Russian cities",
            language_code="en"
        )
        await bot.set_my_short_description(
            "🔍 Find gas stations with fuel prices and availability from drivers",
            language_code="en"
        )
        logger.info("Bot description set")
    except Exception as e:
        logger.warning("set_my_description failed: %s", e)

    logger.info("Бот запущен")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def _safe_worker(coro, name: str) -> None:
    """Обёртка для worker'а — логирует исключения, не падает."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception(f"Worker {name} crashed: {e}")


async def _run_workers(bot: Bot):
    """Запускает push_loop и channel_loop независимо (каждый в своей задаче)."""
    # Каждый worker в отдельной задаче с try/except — если один упадёт,
    # второй продолжит работать, и бот НЕ ОТМЕНИТСЯ (исправляет критический баг)
    await asyncio.gather(
        _safe_worker(push_loop(bot), "push_loop"),
        _safe_worker(channel_loop(bot), "channel_loop"),
    )


async def main():
    logger.info("=" * 60)
    logger.info("Бот «Бензин рядом» запускается...")
    logger.info("=" * 60)

    await init_db()
    logger.info("БД готова")

    api_runner = await run_api()
    bot_task: asyncio.Task | None = None
    vk_task: asyncio.Task | None = None
    workers_task: asyncio.Task | None = None
    stop_event = asyncio.Event()

    # Обработка SIGTERM/SIGINT для graceful shutdown (важно для Render)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            pass

    try:
        bot_task = asyncio.create_task(run_bot())
        # VK-бот запускается параллельно (если задан VK_TOKEN)
        logger.info(">>> Создаю задачу VK-бота...")
        vk_task = asyncio.create_task(_safe_worker(run_vk_bot(), "vk_bot"))
        logger.info(">>> Задача VK-бота создана")
        # Ждём готовности bot (макс 5 сек)
        for _ in range(50):
            if settings.bot:
                break
            await asyncio.sleep(0.1)
        if settings.bot:
            workers_task = asyncio.create_task(_run_workers(settings.bot))

        # Ждём ТОЛЬКО сигнал остановки — бот и workers продолжают
        # работать независимо, если в worker'е exception — он не убьёт бота
        await stop_event.wait()
    finally:
        logger.info("Останавливаюсь...")
        for t in (bot_task, vk_task, workers_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        if settings.bot:
            try:
                await settings.bot.session.close()
            except Exception:
                pass
        await api_runner.cleanup()
        await close_db()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Прервано пользователем")

