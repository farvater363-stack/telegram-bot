"""
General-purpose handlers for /start and silent fallback.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="general")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer("Bot is active ✅")


@router.message(F.text)
async def fallback(_: Message) -> None:
    # We intentionally skip responding to arbitrary messages but mark them as handled to avoid warnings.
    return
