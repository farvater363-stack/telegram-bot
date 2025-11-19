"""
Broadcast helper ensures every message is sent to all tracked chats.
"""
from __future__ import annotations

import asyncio
import logging
from aiogram import Bot, exceptions
from aiogram.types import FSInputFile

from config import settings
from database import db

logger = logging.getLogger(__name__)


def _sanitize_title(title: str | None) -> str:
    if not title:
        return ""
    return title.replace("*", "").strip().upper()


async def _eligible_chats(bot: Bot, ignore_inactive: bool):
    chats = await db.get_active_chats()
    extra_admin_ids = set(await db.get_additional_admin_ids())
    admin_ids = set(settings.admin_ids) | extra_admin_ids
    approved_chats = set(settings.approved_chat_ids) | set(await db.get_approved_chat_ids())
    for chat in chats:
        chat_id = chat["chat_id"]
        if chat_id in approved_chats:
            continue
        if chat["type"] == "private" and chat_id in admin_ids:
            continue
        if ignore_inactive and chat["type"] in ("group", "supergroup"):
            title = chat.get("title")
            sanitized = _sanitize_title(title)
            if "INACTIVE" in sanitized:
                continue
            try:
                live_chat = await bot.get_chat(chat_id)
                live_title = getattr(live_chat, "title", title)
                if "INACTIVE" in _sanitize_title(live_title):
                    continue
            except Exception:
                pass
        yield chat


async def broadcast_message(text: str, bot: Bot, photo: str | None = None, ignore_inactive: bool = True) -> None:
    """
    Send ``text`` to every active chat.
    Automatically suspends inactive chats and logs errors.
    """
    async for chat in _eligible_chats(bot, ignore_inactive):
        chat_id = chat["chat_id"]
        for attempt in range(1, settings.broadcast_retry_count + 1):
            try:
                if photo:
                    await bot.send_photo(chat_id, photo=FSInputFile(photo), caption=text)
                else:
                    await bot.send_message(chat_id, text)
                break
            except exceptions.TelegramForbiddenError:
                logger.warning("Chat %s forbidden. Marking inactive.", chat_id)
                await db.set_chat_active(chat_id, False)
                break
            except exceptions.TelegramRetryAfter as exc:
                logger.warning(
                    "Rate limited for chat %s. Sleeping %.1f seconds (attempt %s).",
                    chat_id,
                    exc.retry_after,
                    attempt,
                )
                await asyncio.sleep(exc.retry_after + 0.5)
            except exceptions.TelegramBadRequest as exc:
                logger.error("Bad request sending to %s: %s", chat_id, exc)
                break
            except Exception as exc:  # pragma: no cover - depends on network
                logger.exception("Unexpected error broadcasting to %s: %s", chat_id, exc)
                await asyncio.sleep(settings.broadcast_retry_delay)
                continue
