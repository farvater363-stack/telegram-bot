"""
Referral tracking menu, leaderboard, and announcement helpers.
"""
from __future__ import annotations

import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from openpyxl import Workbook

from broadcast import broadcast_message
from config import settings
from database import db
from permissions import has_admin_access

router = Router(name="referrals")

_bot: Bot | None = None
_scheduler: AsyncIOScheduler | None = None


def _calc_bonus_cpm(referral_count: int) -> int:
    step = max(1, settings.referrals_per_cpm_step)
    steps = referral_count // step
    return steps * settings.cpm_step_value


def _calc_cash_bonus(referral_count: int) -> int:
    return referral_count * settings.referral_cash_bonus


def _parse_names(raw: str) -> List[str]:
    tokens = re.split(r"[,\n]+", raw)
    return [token.strip() for token in tokens if token.strip()]


async def setup_announcements(scheduler: AsyncIOScheduler, bot: Bot) -> None:
    """
    Configure automatic referral announcements according to config.
    """
    global _bot, _scheduler
    _bot = bot
    _scheduler = scheduler
    await _refresh_announcement_job()


async def _require_admin(callback: CallbackQuery) -> bool:
    chat_id = callback.message.chat.id if callback.message else None
    user_id = callback.from_user.id if callback.from_user else None
    if not await has_admin_access(user_id, chat_id):
        await callback.answer("You are not allowed to use this command.", show_alert=True)
        return False
    return True


async def _require_admin_message(message: Message) -> bool:
    user_id = message.from_user.id if message.from_user else None
    chat_id = message.chat.id if message.chat else None
    if not await has_admin_access(user_id, chat_id):
        await message.reply("You are not allowed to use this command.")
        return False
    return True


class ReferralStates(StatesGroup):
    menu = State()
    add_choose_referrer = State()
    add_new_referrer_name = State()
    add_new_referrer_cpm = State()
    add_collect_names = State()
    add_confirm = State()
    announcement_select_referrer = State()
    edit_cpm_select_referrer = State()
    edit_cpm_new_value = State()
    announcement_schedule_days = State()
    announcement_schedule_time = State()
    remove_refs_select_referrer = State()
    remove_refs_collect_names = State()
    remove_refs_confirm = State()
    remove_referrer_select = State()
    remove_referrer_confirm = State()


@router.message(Command("referrals"))
async def referrals_command(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await state.set_state(ReferralStates.menu)
    await message.answer("Referral menu:", reply_markup=_menu_keyboard())


@router.message(Command("miniapp"))
async def miniapp_command(message: Message) -> None:
    if not await _require_admin_message(message):
        return
    if settings.webapp_url.startswith("https://"):
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Launch Mini App",
                        web_app=WebAppInfo(url=settings.webapp_url),
                    )
                ]
            ]
        )
        await message.answer("Tap to open the admin mini app:", reply_markup=keyboard)
    else:
        await message.answer(
            "Telegram only allows HTTPS mini-app URLs. For now open the dashboard manually:\n"
            f"{settings.webapp_url}\n\nSet WEBAPP_URL to an HTTPS endpoint (e.g., via ngrok) to enable the in-app button."
        )


def _menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="➕ Add referral(s)", callback_data="referrals:add")],
        [InlineKeyboardButton(text="🏆 Leaderboard", callback_data="referrals:leaderboard")],
        [InlineKeyboardButton(text="📣 Send announcement now", callback_data="referrals:announce")],
        [InlineKeyboardButton(text="📊 Export Excel", callback_data="referrals:export")],
        [InlineKeyboardButton(text="⚙️ Edit CPM", callback_data="referrals:edit_cpm")],
        [InlineKeyboardButton(text="🗓 Auto announcement schedule", callback_data="referrals:schedule")],
        [InlineKeyboardButton(text="🗑 Remove referral(s)", callback_data="referrals:remove_refs")],
        [InlineKeyboardButton(text="🚫 Remove referrer", callback_data="referrals:remove_referrer")],
    ]
    if settings.webapp_url.startswith("https://"):
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🧭 Open Mini App",
                    web_app=WebAppInfo(url=settings.webapp_url),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "referrals:add")
async def add_referral_entry(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin(callback):
        return
    await state.set_state(ReferralStates.add_choose_referrer)
    await callback.message.answer("Who is the referrer?", reply_markup=await _referrer_keyboard("add", 0))
    await callback.answer()


@router.callback_query(F.data == "referrals:leaderboard")
async def leaderboard_entry(callback: CallbackQuery) -> None:
    if not await _require_admin(callback):
        return
    text = await build_leaderboard_text()
    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "referrals:announce")
async def manual_announcement_entry(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin(callback):
        return
    await state.set_state(ReferralStates.announcement_select_referrer)
    await callback.message.answer(
        "Which referrer do you want to highlight?", reply_markup=await _referrer_keyboard("announce", 0)
    )
    await callback.answer()


@router.callback_query(F.data == "referrals:export")
async def export_entry(callback: CallbackQuery) -> None:
    if not await _require_admin(callback):
        return
    path = await build_referral_workbook()
    try:
        document = FSInputFile(path)
        await callback.message.answer_document(document, caption="Referral export")
    finally:
        Path(path).unlink(missing_ok=True)
    await callback.answer()


@router.callback_query(F.data == "referrals:edit_cpm")
async def edit_cpm_entry(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin(callback):
        return
    await state.set_state(ReferralStates.edit_cpm_select_referrer)
    await callback.message.answer("Select referrer to edit CPM:", reply_markup=await _referrer_keyboard("edit", 0))
    await callback.answer()


@router.callback_query(F.data == "referrals:schedule")
async def schedule_entry(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin(callback):
        return
    schedule = await _get_schedule()
    await state.set_state(ReferralStates.announcement_schedule_days)
    await callback.message.answer(
        f"Current auto announcement schedule: {schedule}.\n"
        "Send new days (comma separated names, e.g. Monday,Thursday)."
    )
    await callback.answer()


@router.callback_query(F.data == "referrals:remove_refs")
async def remove_refs_entry(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin(callback):
        return
    await state.set_state(ReferralStates.remove_refs_select_referrer)
    await callback.message.answer(
        "Select referrer whose referrals you want to remove:",
        reply_markup=await _referrer_keyboard("remove_refs", 0),
    )
    await callback.answer()


@router.callback_query(F.data == "referrals:remove_referrer")
async def remove_referrer_entry(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin(callback):
        return
    await state.set_state(ReferralStates.remove_referrer_select)
    await callback.message.answer(
        "Select the referrer you want to remove entirely:",
        reply_markup=await _referrer_keyboard("remove_referrer", 0),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("refpage:"))
async def referrer_page(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin(callback):
        return
    _, action, page_str = callback.data.split(":")
    page = int(page_str)
    keyboard = await _referrer_keyboard(action, page)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("refselect:"))
async def referrer_selected(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not await _require_admin(callback):
        return
    _, action, referrer_id = callback.data.split(":")
    ref_id = int(referrer_id)
    referrer = await db.get_referrer(ref_id)
    if not referrer:
        await callback.answer("Referrer missing", show_alert=True)
        return
    if action == "add":
        await state.update_data(referrer_id=ref_id)
        await state.set_state(ReferralStates.add_collect_names)
        await callback.message.answer("Who did they refer? You can send multiple names separated by commas.")
    elif action == "announce":
        text = await build_referral_announcement(ref_id)
        await broadcast_message(text, _bot or bot)
        await callback.message.answer("Announcement sent to all chats.")
        await state.clear()
    elif action == "edit":
        await state.update_data(referrer_id=ref_id)
        await state.set_state(ReferralStates.edit_cpm_new_value)
        await callback.message.answer(f"New base CPM for {referrer['name']}?")
    elif action == "remove_refs":
        await state.update_data(referrer_id=ref_id, referrer_name=referrer["name"])
        await state.set_state(ReferralStates.remove_refs_collect_names)
        await callback.message.answer(
            "Send the referred names you want to remove (comma separated)."
        )
    elif action == "remove_referrer":
        await state.update_data(referrer_id=ref_id, referrer_name=referrer["name"])
        await state.set_state(ReferralStates.remove_referrer_confirm)
        await callback.message.answer(
            f"Type DELETE to remove {referrer['name']} and all of their referrals."
        )
    await callback.answer()


@router.callback_query(F.data.startswith("refnew:"))
async def new_referrer(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin(callback):
        return
    _, action = callback.data.split(":")
    if action != "add":
        await callback.answer("Create referrers from the add flow.", show_alert=True)
        return
    await state.set_state(ReferralStates.add_new_referrer_name)
    await callback.message.answer("Referrer name?")
    await callback.answer()


@router.message(ReferralStates.add_new_referrer_name)
async def new_referrer_name(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Name cannot be empty.")
        return
    await state.update_data(new_referrer_name=name)
    await state.set_state(ReferralStates.add_new_referrer_cpm)
    await message.answer("Base CPM? (e.g. 63)")


@router.message(ReferralStates.add_new_referrer_cpm)
async def new_referrer_cpm(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    try:
        base_cpm = float(message.text.strip())
    except Exception:
        await message.answer("Invalid CPM. Send a number.")
        return
    data = await state.get_data()
    referrer_id = await db.create_referrer(data["new_referrer_name"], base_cpm)
    await state.update_data(referrer_id=referrer_id)
    await state.set_state(ReferralStates.add_collect_names)
    await message.answer("Referrer saved. Now send referred names separated by commas.")


@router.message(ReferralStates.add_collect_names)
async def collect_referred_names(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    names = _parse_names(message.text or "")
    if not names:
        await message.answer("Send at least one name.")
        return
    data = await state.get_data()
    referrer_id = data["referrer_id"]
    referrer = await db.get_referrer(referrer_id)
    await state.update_data(pending_names=names)
    lines = "\n".join(f"• {name}" for name in names)
    await state.set_state(ReferralStates.add_confirm)
    await message.answer(
        f"You're about to add {len(names)} referral(s) for {referrer['name']}:\n{lines}\n\n"
        "Reply YES to confirm or NO to cancel."
    )


@router.message(ReferralStates.add_confirm)
async def confirm_add_referrals(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    text = (message.text or "").strip().upper()
    data = await state.get_data()
    names: List[str] = data.get("pending_names") or []
    referrer_id = data.get("referrer_id")
    if text == "NO":
        await message.answer("Operation cancelled.")
        await state.clear()
        return
    if text != "YES":
        await message.answer("Please reply YES to confirm or NO to cancel.")
        return
    if not names or not referrer_id:
        await message.answer("Session expired. Start again.")
        await state.clear()
        return
    added = await db.add_referrals(referrer_id, names)
    referrer = await db.get_referrer(referrer_id)
    await message.answer(f"Saved {added} referrals for {referrer['name']}.")
    await state.clear()


@router.message(ReferralStates.edit_cpm_new_value)
async def edit_cpm_value(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    try:
        value = float(message.text.strip())
    except Exception:
        await message.answer("Invalid CPM. Send a number.")
        return
    data = await state.get_data()
    referrer_id = data["referrer_id"]
    await db.update_referrer_cpm(referrer_id, value)
    referrer = await db.get_referrer(referrer_id)
    await message.answer(f"{referrer['name']}'s base CPM updated to {value}.")
    await state.clear()


@router.message(ReferralStates.announcement_schedule_days)
async def schedule_days(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    days = _parse_weekday_list(message.text or "")
    if not days:
        await message.answer("Could not parse days. Example: Monday, Thursday")
        return
    await state.update_data(announcement_days=days)
    await state.set_state(ReferralStates.announcement_schedule_time)
    await message.answer("Send time for announcements (HH:MM).")


@router.message(ReferralStates.announcement_schedule_time)
async def schedule_time(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    time_value = _parse_time(message.text or "")
    if not time_value:
        await message.answer("Invalid time. Example: 10:30")
        return
    data = await state.get_data()
    days = data.get("announcement_days") or []
    if not days:
        await message.answer("Schedule session expired. Start again.")
        await state.clear()
        return
    await db.set_announcement_schedule(days, time_value.strftime("%H:%M"))
    await state.clear()
    await _refresh_announcement_job()
    await message.answer(
        f"Announcement schedule updated to {_format_weekday_list(days)} at {time_value.strftime('%H:%M')}."
    )


@router.message(ReferralStates.remove_refs_collect_names)
async def remove_referrals(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    names = _parse_names(message.text or "")
    if not names:
        await message.answer("Send at least one name to remove.")
        return
    data = await state.get_data()
    referrer_id = data.get("referrer_id")
    if not referrer_id:
        await message.answer("Session expired. Start again.")
        await state.clear()
        return
    referrer = await db.get_referrer(referrer_id, include_removed=True)
    await state.update_data(remove_names=names)
    lines = "\n".join(f"• {name}" for name in names)
    await state.set_state(ReferralStates.remove_refs_confirm)
    await message.answer(
        f"Remove these referral(s) from {referrer['name'] if referrer else 'the referrer'}?\n"
        f"{lines}\n\nReply YES to confirm or NO to cancel."
    )


@router.message(ReferralStates.remove_refs_confirm)
async def confirm_remove_refs(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    data = await state.get_data()
    names: List[str] = data.get("remove_names") or []
    referrer_id = data.get("referrer_id")
    text = (message.text or "").strip().upper()
    if text == "NO":
        await message.answer("Operation cancelled.")
        await state.clear()
        return
    if text != "YES":
        await message.answer("Please reply YES to confirm or NO to cancel.")
        return
    if not names or not referrer_id:
        await message.answer("Session expired. Start again.")
        await state.clear()
        return
    removed = await db.remove_referrals_by_names(
        referrer_id, names, message.from_user.id or 0, None
    )
    referrer = await db.get_referrer(referrer_id, include_removed=True)
    if removed:
        await message.answer(
            f"Removed {removed} referral(s) for {referrer['name'] if referrer else 'the selected referrer'}."
        )
    else:
        await message.answer("No matching active referrals found. They may already be removed.")
    await state.clear()


@router.message(ReferralStates.remove_referrer_confirm)
async def confirm_remove_referrer(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    text = (message.text or "").strip().upper()
    data = await state.get_data()
    referrer_id = data.get("referrer_id")
    ref_name = data.get("referrer_name", "the referrer")
    if text == "NO":
        await message.answer("Operation cancelled.")
        await state.clear()
        return
    if text != "DELETE":
        await message.answer("Type DELETE to confirm or NO to cancel.")
        return
    if not referrer_id:
        await message.answer("Session expired. Start again.")
        await state.clear()
        return
    updated = await db.remove_referrer(referrer_id, message.from_user.id or 0)
    if updated:
        await message.answer(f"{ref_name} and their referrals have been removed.")
    else:
        await message.answer("Referrer already removed or missing.")
    await state.clear()


async def _referrer_keyboard(action: str, page: int) -> InlineKeyboardMarkup:
    limit = 5
    offset = page * limit
    referrers = await db.list_referrers(limit=limit, offset=offset)
    total = await db.count_referrers()
    buttons: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=ref["name"], callback_data=f"refselect:{action}:{ref['id']}")]
        for ref in referrers
    ]
    if action == "add":
        buttons.append([InlineKeyboardButton(text="➕ New referrer", callback_data=f"refnew:{action}")])
    elif not referrers:
        buttons.append([InlineKeyboardButton(text="No referrers yet", callback_data="refnoop")])
    nav_row: List[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"refpage:{action}:{page-1}"))
    if (offset + limit) < total:
        nav_row.append(InlineKeyboardButton(text="➡️ Next", callback_data=f"refpage:{action}:{page+1}"))
    if nav_row:
        buttons.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "refnoop")
async def referrer_noop(callback: CallbackQuery) -> None:
    if not await _require_admin(callback):
        return
    await callback.answer("No referrers yet. Add some first.", show_alert=True)


async def build_leaderboard_text(limit: int = 10) -> str:
    leaderboard = await db.get_leaderboard(limit=limit)
    if not leaderboard:
        return "<b>TOP Referrals:</b>\n\nNo referrers yet."
    lines = ["<b>TOP Referrals:</b>\n"]
    for idx, row in enumerate(leaderboard, start=1):
        count = row["referral_count"]
        bonus = _calc_bonus_cpm(count)
        new_cpm = row["base_cpm"] + bonus
        lines.append(
            f"{idx}. {row['name']} – {count} referrals, +{bonus} CPM ({row['base_cpm']} → {new_cpm})"
        )
    return "\n".join(lines)


async def build_referral_announcement(referrer_id: int) -> str:
    referrer = await db.get_referrer(referrer_id)
    if not referrer:
        return "Referral program update coming soon."
    referrals = await db.get_referrals_for_referrer(referrer_id)
    referral_count = len(referrals)
    bonus_cpm = _calc_bonus_cpm(referral_count)
    new_cpm = referrer["base_cpm"] + bonus_cpm
    cash_bonus = _calc_cash_bonus(referral_count)
    bullets = "\n".join(f"• {item['referred_name']}" for item in referrals[:10]) or "• No referrals yet."
    leaderboard = await db.get_leaderboard(limit=3)
    top_lines = []
    for idx in range(3):
        if idx < len(leaderboard):
            row = leaderboard[idx]
            top_lines.append(
                f"{idx+1}. {row['name']} – {row['referral_count']} referrals, +{_calc_bonus_cpm(row['referral_count'])} CPM!"
            )
        else:
            top_lines.append(f"{idx+1}. –")
    announcement = (
        f"🎉 <b>Congratulations to {referrer['name']} for bringing {referral_count} referrals!</b>\n\n"
        f"👥 <b>Referred Friends:</b>\n{bullets}\n\n"
        f"💵 <b>Referral Bonus:</b> ${cash_bonus:,} total (${settings.referral_cash_bonus:,} per driver)\n"
        f"📈 <b>CPM Bonus:</b> +{bonus_cpm} CPM ({referrer['base_cpm']} → {new_cpm})\n\n"
        "📝 <b>Program Highlights:</b>\n"
        f"• Bring {settings.referrals_per_cpm_step} friends → +{settings.cpm_step_value} CPM\n"
        f"• Bring {settings.referrals_per_cpm_step * 2} friends → +{settings.cpm_step_value * 2} CPM\n"
        "• Bring more friends and keep stacking your CPM + cash bonuses!\n\n"
        "🏆 <b>Top Referrals right now:</b>\n"
        f"{top_lines[0]}\n{top_lines[1]}\n{top_lines[2]}\n\n"
        "🎯 <b>Want to refer someone?</b> Contact your HR Team!"
    )
    return announcement


async def send_scheduled_announcement() -> None:
    bot = _bot
    if not bot:
        return
    top = await db.get_top_referrer()
    if not top or not top["referral_count"]:
        text = (
            "Referral reminder!\n\nBring your friends on board and increase your CPM.\n"
            f"Every {settings.referrals_per_cpm_step} friends = +{settings.cpm_step_value} CPM "
            f"and ${settings.referral_cash_bonus:,} cash per driver. Reach out to HR to get started."
        )
    else:
        text = await build_referral_announcement(top["id"])
    await broadcast_message(text, bot)


async def build_referral_workbook() -> Path:
    stats = await db.get_referrer_stats()
    referrals = await db.get_all_referrals_detailed()
    referrals_by_referrer: dict[int, List[dict]] = {}
    for item in referrals:
        referrals_by_referrer.setdefault(item["referrer_id"], []).append(item)
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    summary.append(
        [
            "Referrer Name",
            "Base CPM",
            "Total Referrals",
            "Bonus CPM",
            "New CPM",
            "Referrals (Name – Date)",
        ]
    )
    for row in stats:
        count = row["referral_count"]
        bonus = _calc_bonus_cpm(count)
        ref_entries = referrals_by_referrer.get(row["id"], [])
        detail_rows = [
            f"{entry['referred_name']} – {entry['created_at'][:10]}"
            + (" (REMOVED)" if entry["is_removed"] else "")
            for entry in ref_entries
        ]
        detail_text = "\n".join(detail_rows) or "No referrals yet"
        summary.append(
            [row["name"], row["base_cpm"], count, bonus, row["base_cpm"] + bonus, detail_text]
        )
    for column, width in zip("ABCDEF", (22, 12, 16, 12, 12, 40)):
        summary.column_dimensions[column].width = width
    detailed = wb.create_sheet("All Referrals")
    detailed.append(["Referrer Name", "Referred Friend", "Created At", "Status"])
    for row in referrals:
        status = "Removed" if row["is_removed"] else "Active"
        detailed.append([row["referrer_name"], row["referred_name"], row["created_at"], status])
    for column, width in zip("ABCD", (22, 24, 24, 12)):
        detailed.column_dimensions[column].width = width
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp.name)
    tmp.close()
    return Path(tmp.name)


async def _refresh_announcement_job() -> None:
    if not _scheduler:
        return
    job = _scheduler.get_job("referral_announcements")
    if job:
        job.remove()
    schedule = await db.get_announcement_schedule()
    if schedule and schedule["days"]:
        days = schedule["days"]
        time_text = schedule["time_of_day"]
    else:
        days = settings.announcement_days
        time_text = settings.announcement_time
    if not days:
        return
    hours, minutes = map(int, time_text.split(":"))
    trigger = CronTrigger(
        day_of_week=",".join(str(day) for day in days),
        hour=hours,
        minute=minutes,
        timezone=settings.timezone,
    )
    _scheduler.add_job(
        send_scheduled_announcement,
        trigger=trigger,
        id="referral_announcements",
        replace_existing=True,
    )


async def _get_schedule() -> str:
    schedule = await db.get_announcement_schedule()
    if schedule and schedule["days"]:
        days = _format_weekday_list(schedule["days"])
        time_text = schedule["time_of_day"]
    else:
        days = _format_weekday_list(settings.announcement_days)
        time_text = settings.announcement_time
    return f"{days} at {time_text}"


async def get_announcement_schedule_state() -> dict:
    schedule = await db.get_announcement_schedule()
    if schedule and schedule["days"]:
        days = schedule["days"]
        time_text = schedule["time_of_day"]
    else:
        days = settings.announcement_days
        time_text = settings.announcement_time
    return {"days": days, "time_of_day": time_text}


async def update_announcement_schedule(days: List[int], time_of_day: str) -> dict:
    await db.set_announcement_schedule(days, time_of_day)
    await _refresh_announcement_job()
    return await get_announcement_schedule_state()


def _format_weekday_list(days: List[int]) -> str:
    if not days:
        return "No days configured"
    return ", ".join(_weekday_name(day) for day in days)


def _weekday_name(value: int) -> str:
    mapping = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if 0 <= value < len(mapping):
        return mapping[value]
    return str(value)


def _parse_weekday_list(text: str) -> List[int]:
    mapping = {
        "MONDAY": 0,
        "MON": 0,
        "TUESDAY": 1,
        "TUE": 1,
        "WEDNESDAY": 2,
        "WED": 2,
        "THURSDAY": 3,
        "THU": 3,
        "FRIDAY": 4,
        "FRI": 4,
        "SATURDAY": 5,
        "SAT": 5,
        "SUNDAY": 6,
        "SUN": 6,
    }
    days: List[int] = []
    for part in text.split(","):
        token = part.strip().upper()
        if not token:
            continue
        if token.isdigit():
            idx = int(token)
            if 0 <= idx <= 6 and idx not in days:
                days.append(idx)
            continue
        mapped = mapping.get(token)
        if mapped is not None and mapped not in days:
            days.append(mapped)
    return days


def _parse_time(value: str):
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
        return parsed.time()
    except ValueError:
        return None
