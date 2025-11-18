"""
Reminder command handlers and scheduling helpers.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import List, Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from broadcast import broadcast_message
from config import settings
from database import db
from permissions import has_admin_access

logger = logging.getLogger(__name__)
router = Router(name="reminders")

_scheduler: AsyncIOScheduler | None = None
_bot: Bot | None = None


class ReminderStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_frequency = State()
    waiting_for_once_datetime = State()
    waiting_for_time = State()
    waiting_for_weekday_time = State()
    waiting_for_twice_weekly = State()


def setup_scheduler(scheduler: AsyncIOScheduler, bot: Bot) -> None:
    global _scheduler, _bot
    _scheduler = scheduler
    _bot = bot


async def _ensure_admin(callback: CallbackQuery) -> bool:
    chat_id = callback.message.chat.id if callback.message else None
    user_id = callback.from_user.id if callback.from_user else None
    if not await has_admin_access(user_id, chat_id):
        await callback.answer("You are not allowed to use this command.", show_alert=True)
        return False
    return True


def _frequency_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="Once", callback_data="remfreq:once"),
            InlineKeyboardButton(text="Daily", callback_data="remfreq:daily"),
        ],
        [
            InlineKeyboardButton(text="Weekly", callback_data="remfreq:weekly"),
            InlineKeyboardButton(text="Bi-weekly", callback_data="remfreq:biweekly"),
        ],
        [InlineKeyboardButton(text="Twice a week", callback_data="remfreq:twice")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("reminder"))
async def reminder_command(message: Message, state: FSMContext) -> None:
    if not await has_admin_access(
        message.from_user.id if message.from_user else None,
        message.chat.id if message.chat else None,
    ):
        await message.reply("You are not allowed to use this command.")
        return
    await state.clear()
    await state.set_state(ReminderStates.waiting_for_text)
    await message.answer("What is the reminder text?")


@router.message(ReminderStates.waiting_for_text)
async def reminder_text_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Please send the reminder text.")
        return
    await state.update_data(text=text)
    await state.set_state(ReminderStates.waiting_for_frequency)
    await message.answer("How often?", reply_markup=_frequency_keyboard())


@router.callback_query(ReminderStates.waiting_for_frequency, F.data.startswith("remfreq:"))
async def reminder_frequency_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return
    if not callback.data:
        return
    freq = callback.data.split(":", 1)[1]
    await state.update_data(frequency=freq)
    await callback.answer()
    if freq == "once":
        await state.set_state(ReminderStates.waiting_for_once_datetime)
        await callback.message.answer("Send date & time (YYYY-MM-DD HH:MM)")
    elif freq == "daily":
        await state.set_state(ReminderStates.waiting_for_time)
        await callback.message.answer("Send time (HH:MM)")
    elif freq in {"weekly", "biweekly"}:
        await state.set_state(ReminderStates.waiting_for_weekday_time)
        await callback.message.answer("Send weekday and time (e.g. Monday 09:00)")
    elif freq == "twice":
        await state.set_state(ReminderStates.waiting_for_twice_weekly)
        await callback.message.answer("Send two weekdays and time (e.g. Monday,Thursday 09:00)")


@router.message(ReminderStates.waiting_for_once_datetime)
async def reminder_once_datetime(message: Message, state: FSMContext) -> None:
    run_at = _parse_datetime(message.text or "")
    if not run_at:
        await message.answer("Invalid datetime. Use YYYY-MM-DD HH:MM.")
        return
    await _finalize_reminder(state, message.from_user.id, run_at=run_at.isoformat())
    await state.clear()
    await message.answer("Reminder saved and scheduled.")


@router.message(ReminderStates.waiting_for_time)
async def reminder_daily_time(message: Message, state: FSMContext) -> None:
    time_of_day = _parse_time(message.text or "")
    if not time_of_day:
        await message.answer("Invalid time. Use HH:MM.")
        return
    await _finalize_reminder(state, message.from_user.id, time_of_day=time_of_day.strftime("%H:%M"))
    await state.clear()
    await message.answer("Reminder saved and scheduled.")


@router.message(ReminderStates.waiting_for_weekday_time)
async def reminder_weekly_time(message: Message, state: FSMContext) -> None:
    weekday, parsed_time = _parse_weekday_time(message.text or "")
    if weekday is None or not parsed_time:
        await message.answer("Invalid input. Example: Monday 09:00")
        return
    await _finalize_reminder(
        state,
        message.from_user.id,
        time_of_day=parsed_time.strftime("%H:%M"),
        weekday=weekday,
    )
    await state.clear()
    await message.answer("Reminder saved and scheduled.")


@router.message(ReminderStates.waiting_for_twice_weekly)
async def reminder_twice_weekly(message: Message, state: FSMContext) -> None:
    weekdays, parsed_time = _parse_twice_weekly(message.text or "")
    if not weekdays or not parsed_time:
        await message.answer("Invalid input. Example: Monday,Thursday 11:00")
        return
    await _finalize_reminder(
        state,
        message.from_user.id,
        time_of_day=parsed_time.strftime("%H:%M"),
        weekdays=",".join(str(day) for day in weekdays),
    )
    await state.clear()
    await message.answer("Reminder saved and scheduled.")


async def _finalize_reminder(
    state: FSMContext,
    user_id: int,
    *,
    run_at: Optional[str] = None,
    time_of_day: Optional[str] = None,
    weekday: Optional[int] = None,
    weekdays: Optional[str] = None,
    media_path: Optional[str] = None,
) -> None:
    data = await state.get_data()
    freq = data.get("frequency")
    reminder_id = await db.insert_reminder(
        text=data["text"],
        reminder_type=freq,
        run_at=run_at,
        time_of_day=time_of_day,
        weekday=weekday,
        weekdays=weekdays,
        every_n_weeks=2 if freq == "biweekly" else 1,
        created_by=user_id,
        media_path=media_path,
    )
    reminder = await db.get_reminder(reminder_id)
    if reminder:
        await schedule_reminder(reminder)


@router.message(Command("reminders"))
async def reminders_list(message: Message) -> None:
    if not await has_admin_access(
        message.from_user.id if message.from_user else None,
        message.chat.id if message.chat else None,
    ):
        await message.reply("You are not allowed to use this command.")
        return
    await _send_reminders_list(message)


async def _send_reminders_list(message: Message | CallbackQuery) -> None:
    reminders = await db.get_reminders()
    if not reminders:
        text = "Reminders:\n– No reminders configured yet."
        await message.answer(text) if isinstance(message, Message) else await message.message.edit_text(text)
        return
    lines: List[str] = ["Reminders:"]
    for reminder in reminders:
        schedule = format_reminder_schedule(reminder)
        status = "Active" if reminder["active"] else "Disabled"
        lines.append(f"{reminder['id']}) {schedule} – \"{reminder['text']}\" ({status})")
    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("Disable" if reminder["active"] else "Enable"),
                    callback_data=f"reminders:toggle:{reminder['id']}",
                ),
                InlineKeyboardButton(
                    text="Delete",
                    callback_data=f"reminders:delete:{reminder['id']}",
                ),
            ]
            for reminder in reminders
        ]
    )
    if isinstance(message, Message):
        await message.answer(text, reply_markup=keyboard)
    else:
        try:
            await message.message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest:
            await message.message.answer(text, reply_markup=keyboard)


def format_reminder_schedule(reminder: dict) -> str:
    r_type = reminder["type"]
    timezone_name = settings.timezone.key
    if r_type == "once":
        run_at = reminder.get("run_at")
        return f"[Once] {run_at} {timezone_name}"
    if r_type == "daily":
        return f"[Daily] {reminder['time_of_day']}"
    if r_type == "weekly":
        weekday = _weekday_name(reminder.get("weekday"))
        return f"[Weekly {weekday}] {reminder['time_of_day']}"
    if r_type == "biweekly":
        weekday = _weekday_name(reminder.get("weekday"))
        return f"[Bi-weekly {weekday}] {reminder['time_of_day']}"
    if r_type == "twice":
        weekdays = reminder.get("weekdays") or ""
        readable = ", ".join(_weekday_name(int(day)) for day in weekdays.split(",") if day)
        return f"[Schedule {readable}] {reminder['time_of_day']}"
    return reminder["type"]


def serialize_reminder(reminder: dict) -> dict:
    return {
        "id": reminder["id"],
        "text": reminder["text"],
        "type": reminder["type"],
        "schedule": format_reminder_schedule(reminder),
        "active": bool(reminder["active"]),
        "time_of_day": reminder.get("time_of_day"),
        "weekday": reminder.get("weekday"),
        "weekdays": reminder.get("weekdays"),
        "run_at": reminder.get("run_at"),
        "has_media": bool(reminder.get("media_path")),
    }


@router.callback_query(F.data.startswith("reminders:toggle:"))
async def reminders_toggle(callback: CallbackQuery) -> None:
    if not await _ensure_admin(callback):
        return
    reminder_id = int(callback.data.split(":")[2])
    reminder = await db.get_reminder(reminder_id)
    if not reminder:
        await callback.answer("Reminder missing.", show_alert=True)
        return
    new_state = not bool(reminder["active"])
    await db.set_reminder_active(reminder_id, new_state)
    if new_state:
        await schedule_reminder(reminder)
    else:
        await unschedule_reminder(reminder_id)
    await callback.answer("Reminder updated.")
    await _send_reminders_list(callback)


@router.callback_query(F.data.startswith("reminders:delete:"))
async def reminders_delete(callback: CallbackQuery) -> None:
    if not await _ensure_admin(callback):
        return
    reminder_id = int(callback.data.split(":")[2])
    await db.delete_reminder(reminder_id)
    await unschedule_reminder(reminder_id)
    await callback.answer("Reminder deleted.")
    await _send_reminders_list(callback)


async def restore_reminders() -> None:
    reminders = await db.get_active_reminders()
    for reminder in reminders:
        await schedule_reminder(reminder)


async def schedule_reminder(reminder: dict) -> None:
    if not _scheduler:
        return
    job_id = _job_id(reminder["id"])
    await unschedule_reminder(reminder["id"])
    r_type = reminder["type"]
    if r_type == "once":
        run_at = reminder.get("run_at")
        if not run_at:
            return
        run_dt = datetime.fromisoformat(run_at)
        now = datetime.now(run_dt.tzinfo or settings.timezone)
        if run_dt < now:
            await db.set_reminder_active(reminder["id"], False)
            return
        _scheduler.add_job(
            _fire_reminder,
            trigger=DateTrigger(run_date=run_dt),
            id=job_id,
            args=(reminder["id"],),
            replace_existing=True,
        )
        return
    time_of_day = reminder.get("time_of_day")
    if not time_of_day:
        return
    hours, minutes = map(int, time_of_day.split(":"))
    if r_type == "daily":
        trigger = CronTrigger(hour=hours, minute=minutes, timezone=settings.timezone)
        _scheduler.add_job(_fire_reminder, trigger=trigger, id=job_id, args=(reminder["id"],), replace_existing=True)
    elif r_type == "weekly":
        day = reminder.get("weekday", 0)
        trigger = CronTrigger(day_of_week=str(day), hour=hours, minute=minutes, timezone=settings.timezone)
        _scheduler.add_job(_fire_reminder, trigger=trigger, id=job_id, args=(reminder["id"],), replace_existing=True)
    elif r_type == "biweekly":
        day = reminder.get("weekday", 0)
        start = _next_occurrence(day, time(hour=hours, minute=minutes))
        trigger = IntervalTrigger(weeks=reminder.get("every_n_weeks", 2), start_date=start)
        _scheduler.add_job(_fire_reminder, trigger=trigger, id=job_id, args=(reminder["id"],), replace_existing=True)
    elif r_type == "twice":
        weekdays = reminder.get("weekdays") or ""
        dow = ",".join(part for part in weekdays.split(",") if part)
        if not dow:
            return
        trigger = CronTrigger(day_of_week=dow, hour=hours, minute=minutes, timezone=settings.timezone)
        _scheduler.add_job(_fire_reminder, trigger=trigger, id=job_id, args=(reminder["id"],), replace_existing=True)


async def unschedule_reminder(reminder_id: int) -> None:
    if not _scheduler:
        return
    job_id = _job_id(reminder_id)
    job = _scheduler.get_job(job_id)
    if job:
        job.remove()


async def _fire_reminder(reminder_id: int) -> None:
    if not _bot:
        return
    reminder = await db.get_reminder(reminder_id)
    if not reminder or not reminder["active"]:
        return
    photo = reminder.get("media_path")
    await broadcast_message(reminder["text"], _bot, photo=photo)
    if reminder["type"] == "once":
        await db.set_reminder_active(reminder_id, False)
        await unschedule_reminder(reminder_id)


def _job_id(reminder_id: int) -> str:
    return f"reminder_{reminder_id}"


def _parse_datetime(value: str) -> Optional[datetime]:
    try:
        dt = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=settings.timezone)
    except ValueError:
        return None


def _parse_time(value: str) -> Optional[time]:
    try:
        dt = datetime.strptime(value.strip(), "%H:%M")
        return time(hour=dt.hour, minute=dt.minute)
    except ValueError:
        return None


def _weekday_name(value: Optional[int]) -> str:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    if value is None or value < 0 or value >= len(names):
        return "?"
    return names[int(value)]


def _weekday_from_name(name: str) -> Optional[int]:
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
    upper = name.strip().upper()
    if upper.isdigit():
        idx = int(upper)
        if 0 <= idx <= 6:
            return idx
    return mapping.get(upper)


def _parse_weekday_time(value: str) -> tuple[Optional[int], Optional[time]]:
    parts = value.split()
    if len(parts) != 2:
        return None, None
    weekday = _weekday_from_name(parts[0])
    parsed_time = _parse_time(parts[1])
    return weekday, parsed_time


def _parse_twice_weekly(value: str) -> tuple[List[int], Optional[time]]:
    text = value.strip()
    last_space = text.rfind(" ")
    if last_space == -1:
        return [], None
    days_part = text[:last_space]
    time_part = text[last_space + 1 :]
    time_val = _parse_time(time_part.strip())
    days = []
    for name in days_part.split(","):
        idx = _weekday_from_name(name)
        if idx is not None:
            if idx not in days:
                days.append(idx)
    return days, time_val


def _next_occurrence(target_weekday: int, target_time: time) -> datetime:
    now = datetime.now(settings.timezone)
    today = now.date()
    days_ahead = (target_weekday - today.weekday()) % 7
    next_date = today + timedelta(days=days_ahead)
    candidate = datetime.combine(next_date, target_time, tzinfo=settings.timezone)
    if candidate <= now:
        candidate += timedelta(weeks=1)
    return candidate
