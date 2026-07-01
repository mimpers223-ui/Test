"""
Конфигурация — загрузка .env и доступ к настройкам.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)


@dataclass
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    ADMIN_IDS: list = None
    ADMIN_USERNAMES: list = None
    DEFAULT_LANGUAGE: str = "ru"
    # Радиус поиска по умолчанию (км)
    DEFAULT_SEARCH_RADIUS: int = 10
    # Макс. результатов в /find
    MAX_FIND_RESULTS: int = 5
    # Срок жизни отчёта (минуты)
    REPORT_TTL_MINUTES: int = 120
    # Cooldown между push-уведомлениями (часы)
    PUSH_COOLDOWN_HOURS: int = 4
    # Лимит на отчёты новых пользователей (в день)
    NEW_USER_DAILY_REPORT_LIMIT: int = 5
    # Chat ID канала для автопубликации (опционально)
    CHANNEL_CHAT_ID: str = os.getenv("CHANNEL_CHAT_ID", "")
    # Premium-подписка через Telegram Stars
    PREMIUM_PRICE_STARS: int = int(os.getenv("PREMIUM_PRICE_STARS", "149"))
    PREMIUM_DURATION_DAYS: int = int(os.getenv("PREMIUM_DURATION_DAYS", "30"))
    WEB_APP_URL: str = os.getenv("WEB_APP_URL", "")  # URL Mini App для Telegram WebApp
    # Рекламный баннер (показывается в главном меню)
    AD_BANNER_TEXT: str = os.getenv("AD_BANNER_TEXT", "")
    AD_BANNER_URL: str = os.getenv("AD_BANNER_URL", "")
    # Канал/сообщество для обязательной подписки
    SUBSCRIBE_CHANNEL_TG: str = os.getenv("SUBSCRIBE_CHANNEL_TG", "")  # @channel_username или chat_id
    SUBSCRIBE_COMMUNITY_VK: int = int(os.getenv("SUBSCRIBE_COMMUNITY_VK", "0"))  # ID сообщества VK

    def __post_init__(self):
        if self.ADMIN_IDS is None:
            admin_str = os.getenv("ADMIN_IDS", "")
            self.ADMIN_IDS = [int(x) for x in admin_str.split(",") if x.strip()]
        if self.ADMIN_USERNAMES is None:
            users_str = os.getenv("ADMIN_USERNAMES", "")
            self.ADMIN_USERNAMES = [x.strip().lstrip("@") for x in users_str.split(",") if x.strip()]

    def is_admin(self, user_id: int | None = None, username: str | None = None) -> bool:
        """Проверяет, является ли пользователь админом (по ID или username)."""
        if user_id and user_id in self.ADMIN_IDS:
            return True
        if username and username in self.ADMIN_USERNAMES:
            return True
        return False

    bot = None  # инициализируется в main.py


settings = Settings()
