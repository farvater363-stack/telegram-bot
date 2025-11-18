# Install dependencies: `pip install -r requirements.txt`
# Configure BOT_TOKEN, ADMIN_IDS, timezone, and announcement days via environment variables or `config.py`.
# Run the bot with `python bot.py`.
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    CallbackQuery,
    MenuButtonWebApp,
    Message,
    TelegramObject,
    WebAppInfo,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from database import db
from admin import router as admin_router
from general import router as general_router
from referrals import router as referrals_router, setup_announcements as setup_referral_announcements
from reminders import restore_reminders, router as reminders_router, setup_scheduler as setup_reminder_scheduler
from webapp_server import WebAppServer


class ChatTrackingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Any],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        chat = None
        from_user = None
        if isinstance(event, Message):
            chat = event.chat
            from_user = event.from_user
        elif isinstance(event, CallbackQuery) and event.message:
            chat = event.message.chat
            from_user = event.from_user
        if chat:
            title = getattr(chat, "title", None)
            await db.upsert_chat(chat.id, chat.type, title)
        if from_user:
            await db.upsert_user(
                from_user.id,
                from_user.username,
                from_user.first_name,
                from_user.last_name,
            )
        return await handler(event, data)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN must be configured in environment variables.")

    await db.connect()
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()

    tracking = ChatTrackingMiddleware()
    dp.message.middleware(tracking)
    dp.callback_query.middleware(tracking)

    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    setup_reminder_scheduler(scheduler, bot)
    await setup_referral_announcements(scheduler, bot)
    scheduler.start()

    await configure_menu_button(bot)
    webapp_server = WebAppServer(bot)
    await webapp_server.start()

    dp.include_router(reminders_router)
    dp.include_router(referrals_router)
    dp.include_router(admin_router)
    dp.include_router(general_router)

    await restore_reminders()

    try:
        await dp.start_polling(bot)
    finally:
        await webapp_server.close()
        await db.close()


async def configure_menu_button(bot: Bot) -> None:
    if not settings.webapp_url.startswith("https://"):
        return
    button = MenuButtonWebApp(
        text="Open Control Center",
        web_app=WebAppInfo(url=settings.webapp_url),
    )
    try:
        await bot.set_chat_menu_button(menu_button=button)
        for chat_id in settings.approved_chat_ids:
            try:
                await bot.set_chat_menu_button(chat_id=chat_id, menu_button=button)
            except Exception:
                continue
    except Exception:
        return


if __name__ == "__main__":
    asyncio.run(main())
