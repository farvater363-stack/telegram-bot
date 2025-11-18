"""
Admin management commands (list/add/remove).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from config import is_primary_admin, settings
from database import db
from permissions import has_admin_access

router = Router(name="admins")


@router.message(Command("admins"))
async def admins_list(message: Message) -> None:
    if not await has_admin_access(_user_id(message), _chat_id(message)):
        await message.reply("You are not allowed to use this command.")
        return
    base_admins = settings.admin_ids
    extra_admins = await db.list_admins()
    lines = ["Admins:"]
    if base_admins:
        base_list = ", ".join(str(admin) for admin in base_admins)
        lines.append(f"- Primary/admin IDs: {base_list}")
    if extra_admins:
        for admin in extra_admins:
            username = admin.get("username") or "unknown"
            lines.append(f"- @{username} ({admin['user_id']})")
    else:
        lines.append("- No additional admins yet.")
    if settings.approved_chat_ids:
        lines.append(f"- Approved group chats: {', '.join(str(cid) for cid in settings.approved_chat_ids)}")
    await message.answer("\n".join(lines))


@router.message(Command("addadmin"))
async def add_admin(message: Message) -> None:
    if not is_primary_admin(_user_id(message)):
        await message.reply("Only the primary admin can add new admins.")
        return
    username = _argument_text(message)
    if not username:
        await message.answer("Usage: /addadmin @username")
        return
    username = username.lstrip("@").lower()
    user = await db.get_user_by_username(username)
    if not user:
        await message.answer("User not found. Ask them to send any message to this bot first.")
        return
    await db.add_admin_user(user["user_id"], user.get("username"), _user_id(message) or 0)
    await message.answer(f"@{user.get('username') or username} is now an admin.")


@router.message(Command("removeadmin"))
async def remove_admin(message: Message) -> None:
    if not is_primary_admin(_user_id(message)):
        await message.reply("Only the primary admin can remove admins.")
        return
    username = _argument_text(message)
    if not username:
        await message.answer("Usage: /removeadmin @username")
        return
    username = username.lstrip("@").lower()
    user = await db.get_admin_by_username(username)
    if not user:
        await message.answer("Admin not found.")
        return
    await db.remove_admin_user(user["user_id"])
    await message.answer(f"@{user.get('username') or username} removed from admin list.")


def _user_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


def _chat_id(message: Message) -> int | None:
    return message.chat.id if message.chat else None


def _argument_text(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()
