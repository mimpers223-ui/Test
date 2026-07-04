#!/usr/bin/env python3
"""
Настройка сообщества VK для SEO (описание, статус, темы).

Требуется:
  VK_COMMUNITY_TOKEN = токен сообщества с правами管理者 (groups_manage)
  VK_COMMUNITY_ID = ID сообщества (239975253)

Запуск: python scripts/setup_vk_community.py
"""
import os
import sys
import asyncio
import aiohttp
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "bot/.env")

VK_COMMUNITY_TOKEN = os.getenv("VK_COMMUNITY_TOKEN", "")
VK_COMMUNITY_ID = os.getenv("VK_COMMUNITY_ID", "239975253")
VK_API_VERSION = "5.199"


async def set_community_info():
    if not VK_COMMUNITY_TOKEN:
        print("ERROR: Set VK_COMMUNITY_TOKEN in bot/.env")
        print("  Get it from: VK Community → Settings → API usage → Access tokens")
        return

    async with aiohttp.ClientSession() as session:
        # 1) Описание сообщества
        desc = (
            "🔍 Бензин рядом — найди АЗС с нужным топливом\n\n"
            "• АИ-92, АИ-95, АИ-98, АИ-100, Дизель, Газ\n"
            "• Реальные цены и наличие от водителей\n"
            "• Уведомления о наличии в твоём районе\n"
            "• 27 000+ АЗС в 16 городах России\n\n"
            "📱 Мини-приложение: https://benzin-ryadom.onrender.com/v2\n"
            "🤖 Telegram: @benzyn_ryadom_bot"
        )
        data = await _api(session, "groups.edit", {
            "group_id": VK_COMMUNITY_ID,
            "description": desc,
        })
        print(f"Description: {data}")

        # 2) Статус сообщества
        status = "🔍 Найди ближайшую АЗС с нужным топливом — 27 000+ АЗС в 16 городах"
        data = await _api(session, "status.set", {
            "group_id": VK_COMMUNITY_ID,
            "text": status,
        })
        print(f"Status: {data}")

        # 3)主题 (topics) — если доступно
        # groups.setCallbackSettings для callback API
        data = await _api(session, "groups.setCallbackSettings", {
            "group_id": VK_COMMUNITY_ID,
            "access_token": VK_COMMUNITY_TOKEN,
            "message_new": 1,
            "message_event": 1,
        })
        print(f"Callback settings: {data}")

        print("\n✅ Done! Check: https://vk.com/benzyn_ryadom")


async def _api(session, method, params):
    params["access_token"] = VK_COMMUNITY_TOKEN
    params["v"] = VK_API_VERSION
    url = f"https://api.vk.com/method/{method}"
    async with session.post(url, data=params) as resp:
        data = await resp.json()
        if "error" in data:
            print(f"  VK API error ({method}): {data['error']}")
        return data


if __name__ == "__main__":
    asyncio.run(set_community_info())
