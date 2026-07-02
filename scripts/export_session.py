#!/usr/bin/env python3
"""Экспортирует Telegram-сессию в строку для Render."""
import os
import sys

TG_API_ID = os.getenv("TG_API_ID", "")
TG_API_HASH = os.getenv("TG_API_HASH", "")
SESSION_PATH = os.path.join(os.path.dirname(__file__), "session")


def main():
    if not TG_API_ID or not TG_API_HASH:
        print("Установи переменные окружения TG_API_ID и TG_API_HASH")
        sys.exit(1)

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("pip install telethon")
        sys.exit(1)

    print(f"Загружаю сессию из {SESSION_PATH}...")

    async def export():
        client = TelegramClient(SESSION_PATH, int(TG_API_ID), TG_API_HASH)
        await client.start()
        session_string = StringSession.save(client.session)
        await client.disconnect()
        return session_string

    import asyncio
    session_string = asyncio.run(export())

    print("\n=== TG_SESSION_STRING ===")
    print(session_string)
    print("\nДобавь эту строку как TG_SESSION_STRING в Render Dashboard (секрет)")
    print("Скопируй ВСЁ от первой скобки до последней!")


if __name__ == "__main__":
    main()
