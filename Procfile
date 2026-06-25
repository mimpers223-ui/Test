web: cd bot && python main.py
worker: cd bot && python -c "from push_worker import push_loop; from channel_poster import channel_loop; import asyncio; from aiogram import Bot; from config import settings; bot = Bot(token=settings.BOT_TOKEN); asyncio.run(asyncio.gather(push_loop(bot), channel_loop(bot)))"
