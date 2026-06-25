"""
Бот «Бензин рядом» — точка входа.
Запускает одновременно:
- Telegram-бота (polling)
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
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN пуст. Проверь bot/.env")
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
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.warning("delete_webhook failed (continuing): %s", e)
    logger.info("Бот запущен")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def _run_workers(bot: Bot):
    """Запускает push_loop и channel_loop."""
    await asyncio.gather(
        push_loop(bot),
        channel_loop(bot),
    )


async def main():
    logger.info("=" * 60)
    logger.info("Бот «Бензин рядом» запускается...")
    logger.info("=" * 60)

    await init_db()
    logger.info("БД готова")

    api_runner = await run_api()
    bot_task: asyncio.Task | None = None
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
        # Ждём готовности bot (макс 5 сек)
        for _ in range(50):
            if settings.bot:
                break
            await asyncio.sleep(0.1)
        if settings.bot:
            workers_task = asyncio.create_task(_run_workers(settings.bot))

        # Ждём сигнал остановки или ошибку
        done, pending = await asyncio.wait(
            [bot_task, workers_task, asyncio.create_task(stop_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        logger.info("Останавливаюсь...")
        for t in (bot_task, workers_task):
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

