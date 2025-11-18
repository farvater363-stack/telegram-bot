"""
Admin permission helpers combining config defaults, DB stored admins, and approved chats.
"""
from __future__ import annotations

from config import settings, is_admin as is_base_admin
from database import db


async def has_admin_access(user_id: int | None, chat_id: int | None = None) -> bool:
    if chat_id:
        if chat_id in settings.approved_chat_ids:
            return True
        if await db.is_approved_chat(chat_id):
            return True
    if user_id and is_base_admin(user_id):
        return True
    if not user_id:
        return False
    return await db.is_additional_admin(user_id)
