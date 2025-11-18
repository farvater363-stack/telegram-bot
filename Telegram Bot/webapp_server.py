from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from aiohttp import web
from aiogram import Bot
from aiogram.types import FSInputFile

from broadcast import broadcast_message
from config import settings, is_primary_admin
from database import db
from permissions import has_admin_access
from referrals import (
    build_referral_announcement,
    build_referral_workbook,
    get_announcement_schedule_state,
    update_announcement_schedule,
)
from reminders import (
    format_reminder_schedule,
    schedule_reminder,
    serialize_reminder,
    unschedule_reminder,
    _weekday_name,
)

STATIC_DIR = Path(__file__).parent / "webapp" / "static"
INDEX_FILE = STATIC_DIR / "index.html"
REMINDER_UPLOAD_DIR = Path(__file__).parent / "webapp" / "uploads" / "reminders"
REMINDER_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)


def _parse_names(raw: str) -> List[str]:
    return [item.strip() for item in re.split(r"[,\n]+", raw) if item.strip()]


def _validate_time(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        datetime.strptime(value, "%H:%M")
        return value
    except ValueError:
        return None


def _parse_run_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=settings.timezone)
    return dt.isoformat()


def _parse_date_only(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=settings.timezone)
    return dt.isoformat()


def _is_primary_request(request: web.Request) -> bool:
    user = request.get("tg_user") or {}
    return bool(user) and is_primary_admin(user.get("id"))


def _forbidden() -> web.Response:
    return web.json_response({"ok": False, "error": "Primary admin only"}, status=403)


def _resolve_media_token(token: str | None) -> str | None:
    if not token:
        return None
    candidate = (REMINDER_UPLOAD_DIR / Path(token).name).resolve()
    try:
        candidate.relative_to(REMINDER_UPLOAD_DIR.resolve())
    except ValueError:
        return None
    if not candidate.exists():
        return None
    return str(candidate)


def _verify_init_data(init_data: str) -> Dict[str, Any] | None:
    if not init_data:
        return None
    parsed = urllib.parse.parse_qsl(init_data, strict_parsing=True)
    data = dict(parsed)
    hash_value = data.pop("hash", None)
    if not hash_value:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, hash_value):
        return None
    if "user" in data:
        try:
            data["user"] = json.loads(data["user"])
        except json.JSONDecodeError:
            return None
    if "chat" in data:
        try:
            data["chat"] = json.loads(data["chat"])
        except json.JSONDecodeError:
            return None
    return data


def _is_debug_request(request: web.Request) -> Dict[str, Any] | None:
    secret = settings.webapp_debug_secret
    if not secret:
        return None
    header_secret = request.headers.get("X-Debug-Secret")
    if header_secret != secret:
        return None
    user_id = request.headers.get("X-Debug-User-Id")
    try:
        user_id = int(user_id) if user_id else settings.primary_admin_id or 0
    except ValueError:
        user_id = settings.primary_admin_id or 0
    chat_id = request.headers.get("X-Debug-Chat-Id")
    try:
        chat_id = int(chat_id) if chat_id else None
    except ValueError:
        chat_id = None
    return {"id": user_id or 0, "chat_id": chat_id, "is_debug": True}


@web.middleware
async def telegram_auth_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    debug_user = _is_debug_request(request)
    if debug_user:
        request["tg_user"] = {"id": debug_user["id"]}
        request["tg_chat_id"] = debug_user.get("chat_id")
        return await handler(request)
    init_data = request.headers.get("X-Telegram-Init-Data") or request.query.get("init_data", "")
    parsed = _verify_init_data(init_data)
    if not parsed:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
    user = parsed.get("user") or {}
    chat = parsed.get("chat") or {}
    user_id = user.get("id")
    chat_id = chat.get("id")
    if not await has_admin_access(user_id, chat_id):
        return web.json_response({"ok": False, "error": "Forbidden"}, status=403)
    request["tg_user"] = user
    if chat_id:
        request["tg_chat_id"] = chat_id
    return await handler(request)


async def handle_index(_: web.Request) -> web.StreamResponse:
    return web.FileResponse(INDEX_FILE)


async def handle_me(request: web.Request) -> web.Response:
    user = request["tg_user"]
    return web.json_response(
        {"ok": True, "user": user, "is_primary": is_primary_admin(user.get("id")) if user else False}
    )


async def _build_dashboard_payload() -> Dict[str, Any]:
    stats = await db.get_referrer_stats()
    referrals = await db.get_all_referrals_detailed()
    mapping: Dict[int, List[dict]] = {}
    for referral in referrals:
        mapping.setdefault(referral["referrer_id"], []).append(referral)
    leaderboard = await db.get_leaderboard(limit=3)
    total_referrals = sum(row["referral_count"] for row in stats)
    total_cash = total_referrals * settings.referral_cash_bonus
    referrer_payload = []
    for row in stats:
        ref_entries = mapping.get(row["id"], [])
        ref_entries_sorted = sorted(ref_entries, key=lambda item: item["created_at"], reverse=True)
        bonus = _calc_bonus(row["referral_count"])
        referrer_payload.append(
            {
                "id": row["id"],
                "name": row["name"],
                "base_cpm": row["base_cpm"],
                "referral_count": row["referral_count"],
                "bonus_cpm": bonus,
                "new_cpm": row["base_cpm"] + bonus,
                "referrals": [
                    {
                        "id": item["referral_id"],
                        "name": item["referred_name"],
                        "created_at": item["created_at"],
                        "is_removed": bool(item["is_removed"]),
                        "removed_at": item.get("removed_at"),
                        "removed_reason": item.get("removed_reason"),
                    }
                    for item in ref_entries_sorted
                ],
            }
        )
    return {
        "ok": True,
        "summary": {
            "total_referrals": total_referrals,
            "total_cash": total_cash,
            "program": {
                "step": settings.referrals_per_cpm_step,
                "step_bonus": settings.cpm_step_value,
                "cash_per_referral": settings.referral_cash_bonus,
            },
        },
        "referrers": referrer_payload,
        "leaderboard": [
            {
                "name": row["name"],
                "count": row["referral_count"],
                "bonus": _calc_bonus(row["referral_count"]),
            }
            for row in leaderboard
        ],
    }


def _calc_bonus(count: int) -> int:
    if count <= 0:
        return 0
    steps = count // max(1, settings.referrals_per_cpm_step)
    return steps * settings.cpm_step_value


async def handle_referrers(_: web.Request) -> web.Response:
    payload = await _build_dashboard_payload()
    return web.json_response(payload)


async def handle_create_referrer(request: web.Request) -> web.Response:
    data = await request.json()
    name = (data.get("name") or "").strip()
    base_cpm = float(data.get("base_cpm") or 0)
    if not name:
        return web.json_response({"ok": False, "error": "Name required"}, status=400)
    await db.create_referrer(name, base_cpm)
    payload = await _build_dashboard_payload()
    return web.json_response(payload)


async def handle_update_referrer(request: web.Request) -> web.Response:
    referrer_id = int(request.match_info["referrer_id"])
    data = await request.json()
    base_cpm = data.get("base_cpm")
    if base_cpm is None:
        return web.json_response({"ok": False, "error": "base_cpm required"}, status=400)
    await db.update_referrer_cpm(referrer_id, float(base_cpm))
    payload = await _build_dashboard_payload()
    return web.json_response(payload)


async def handle_add_referrals(request: web.Request) -> web.Response:
    referrer_id = int(request.match_info["referrer_id"])
    data = await request.json()
    names = data.get("names") or []
    if isinstance(names, str):
        names = _parse_names(names)
    if not isinstance(names, list):
        return web.json_response({"ok": False, "error": "names must be list or string"}, status=400)
    cleaned = [name.strip() for name in names if name.strip()]
    if not cleaned:
        return web.json_response({"ok": False, "error": "No names provided"}, status=400)
    await db.add_referrals(referrer_id, cleaned)
    payload = await _build_dashboard_payload()
    return web.json_response(payload)


async def handle_remove_referrals(request: web.Request) -> web.Response:
    referrer_id = int(request.match_info["referrer_id"])
    data = await request.json()
    reason = (data.get("reason") or "").strip() or None
    user_id = request["tg_user"].get("id") or 0
    removed_at = _parse_date_only(data.get("removed_at")) if data.get("removed_at") else None
    referral_ids = data.get("referral_ids")
    if referral_ids:
        if not isinstance(referral_ids, list):
            return web.json_response({"ok": False, "error": "referral_ids must be a list"}, status=400)
        await db.remove_referrals_by_ids(referral_ids, user_id, reason, removed_at)
    else:
        names = data.get("names") or []
        if isinstance(names, str):
            names = _parse_names(names)
        cleaned = [name.strip() for name in names if name.strip()]
        if not cleaned:
            return web.json_response({"ok": False, "error": "No names provided"}, status=400)
        await db.remove_referrals_by_names(referrer_id, cleaned, user_id, reason, removed_at)
    payload = await _build_dashboard_payload()
    return web.json_response(payload)


async def handle_send_announcement(request: web.Request) -> web.Response:
    referrer_id = int(request.match_info["referrer_id"])
    bot: Bot = request.app["bot"]
    announcement = await build_referral_announcement(referrer_id)
    await broadcast_message(announcement, bot)
    return web.json_response({"ok": True, "message": "Announcement sent"})


async def handle_preview_announcement(request: web.Request) -> web.Response:
    user = request["tg_user"]
    chat_id = request.get("tg_chat_id") or user.get("id")
    if not chat_id:
        return web.json_response({"ok": False, "error": "Missing chat context"}, status=400)
    data = await request.json()
    days = data.get("days") or []
    time_of_day = data.get("time_of_day")
    if not isinstance(days, list) or not time_of_day:
        return web.json_response({"ok": False, "error": "Invalid payload"}, status=400)
    human_days = ", ".join(_weekday_name(int(day)) for day in days)
    text = (
        "Auto announcement preview\n"
        f"Days: {human_days or 'not set'}\n"
        f"Time: {time_of_day}"
    )
    bot: Bot = request.app["bot"]
    await bot.send_message(chat_id, text)
    return web.json_response({"ok": True})


async def handle_export_referrals(request: web.Request) -> web.Response:
    path = await build_referral_workbook()
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    headers = {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": "attachment; filename=referrals.xlsx",
    }
    return web.Response(body=data, headers=headers)


async def handle_upload_reminder_media(request: web.Request) -> web.Response:
    reader = await request.multipart()
    if not reader:
        return web.json_response({"ok": False, "error": "No file uploaded"}, status=400)
    field = await reader.next()
    if not field or field.name != "file":
        return web.json_response({"ok": False, "error": "Invalid field"}, status=400)
    filename = Path(field.filename or "photo.jpg")
    suffix = filename.suffix or ".jpg"
    target = (REMINDER_UPLOAD_DIR / f"{uuid4().hex}{suffix}").resolve()
    with target.open("wb") as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            f.write(chunk)
    return web.json_response({"ok": True, "path": target.name})


async def handle_get_schedule(_: web.Request) -> web.Response:
    schedule = await get_announcement_schedule_state()
    return web.json_response({"ok": True, "schedule": schedule})


async def handle_update_schedule(request: web.Request) -> web.Response:
    data = await request.json()
    days = data.get("days") or []
    time_of_day = (data.get("time_of_day") or "").strip()
    if not isinstance(days, list) or not time_of_day:
        return web.json_response({"ok": False, "error": "Invalid payload"}, status=400)
    try:
        days_int = sorted({int(day) for day in days})
    except ValueError:
        return web.json_response({"ok": False, "error": "Invalid days"}, status=400)
    if not _validate_time(time_of_day):
        return web.json_response({"ok": False, "error": "Invalid time"}, status=400)
    schedule = await update_announcement_schedule(days_int, time_of_day)
    return web.json_response({"ok": True, "schedule": schedule})


async def handle_get_reminders(_: web.Request) -> web.Response:
    payload = await _build_reminders_payload()
    return web.json_response(payload)


async def handle_create_reminder(request: web.Request) -> web.Response:
    data = await request.json()
    text = (data.get("text") or "").strip()
    mode = data.get("mode")
    if not text or mode not in {"once", "schedule"}:
        return web.json_response({"ok": False, "error": "Invalid reminder payload"}, status=400)
    user_id = request["tg_user"].get("id") or 0
    media_path = _resolve_media_token(data.get("media_path"))
    if mode == "once":
        if data.get("send_now"):
            bot: Bot = request.app["bot"]
            await broadcast_message(text, bot, photo=media_path)
            return web.json_response(await _build_reminders_payload())
        run_at = _parse_run_at(data.get("run_at"))
        if not run_at:
            return web.json_response({"ok": False, "error": "Invalid datetime"}, status=400)
        reminder_id = await db.insert_reminder(
            text=text,
            reminder_type="once",
            run_at=run_at,
            time_of_day=None,
            weekday=None,
            weekdays=None,
            every_n_weeks=1,
            created_by=user_id,
            media_path=media_path,
        )
    else:
        raw_days = data.get("days") or []
        if not isinstance(raw_days, list):
            return web.json_response({"ok": False, "error": "Days required"}, status=400)
        try:
            day_set = sorted({int(day) for day in raw_days})
        except ValueError:
            return web.json_response({"ok": False, "error": "Invalid days"}, status=400)
        if not day_set:
            return web.json_response({"ok": False, "error": "Select at least one day"}, status=400)
        time_of_day = _validate_time(data.get("time_of_day"))
        if not time_of_day:
            return web.json_response({"ok": False, "error": "Invalid time"}, status=400)
        reminder_id = await db.insert_reminder(
            text=text,
            reminder_type="twice",
            run_at=None,
            time_of_day=time_of_day,
            weekday=None,
            weekdays=",".join(str(day) for day in day_set),
            every_n_weeks=1,
            created_by=user_id,
            media_path=media_path,
        )
    reminder = await db.get_reminder(reminder_id)
    await schedule_reminder(reminder)
    payload = await _build_reminders_payload()
    return web.json_response(payload)


async def handle_toggle_reminder(request: web.Request) -> web.Response:
    reminder_id = int(request.match_info["reminder_id"])
    data = await request.json()
    active = bool(data.get("active"))
    await db.set_reminder_active(reminder_id, active)
    reminder = await db.get_reminder(reminder_id)
    if reminder:
        if active:
            await schedule_reminder(reminder)
        else:
            await unschedule_reminder(reminder_id)
    payload = await _build_reminders_payload()
    return web.json_response(payload)


async def handle_delete_reminder(request: web.Request) -> web.Response:
    reminder_id = int(request.match_info["reminder_id"])
    await db.delete_reminder(reminder_id)
    await unschedule_reminder(reminder_id)
    payload = await _build_reminders_payload()
    return web.json_response(payload)


async def handle_preview_reminder(request: web.Request) -> web.Response:
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return web.json_response({"ok": False, "error": "Text required"}, status=400)
    chat_id = request.get("tg_chat_id") or (request["tg_user"].get("id") if request.get("tg_user") else None)
    if not chat_id:
        return web.json_response({"ok": False, "error": "Missing chat context"}, status=400)
    lines = ["Reminder preview", text]
    media_path = _resolve_media_token(data.get("media_path"))
    if data.get("mode") == "schedule":
        days = data.get("days") or []
        readable = ", ".join(_weekday_name(int(day)) for day in days)
        lines.append(f"Days: {readable or 'not set'} at {data.get('time_of_day')}")
    bot: Bot = request.app["bot"]
    if media_path:
        await bot.send_photo(chat_id, photo=FSInputFile(media_path), caption="\n".join(lines))
    else:
        await bot.send_message(chat_id, "\n".join(lines))
    return web.json_response({"ok": True})


async def _build_reminders_payload() -> Dict[str, Any]:
    reminders = await db.get_reminders()
    return {"ok": True, "reminders": [serialize_reminder(rem) for rem in reminders]}


async def handle_admins_list(request: web.Request) -> web.Response:
    if not _is_primary_request(request):
        return _forbidden()
    base = []
    for admin_id in settings.admin_ids:
        user = await db.get_user(admin_id)
        base.append(
            {"id": admin_id, "username": user.get("username") if user else None, "is_primary": admin_id == settings.primary_admin_id}
        )
    extras = await db.list_admins()
    return web.json_response({"ok": True, "base": base, "extras": extras})


async def handle_admins_add(request: web.Request) -> web.Response:
    if not _is_primary_request(request):
        return _forbidden()
    data = await request.json()
    username = (data.get("username") or "").strip().lstrip("@")
    if not username:
        return web.json_response({"ok": False, "error": "Username required"}, status=400)
    user = await db.get_user_by_username(username)
    if not user:
        return web.json_response({"ok": False, "error": "User not found. Ask them to message the bot first."}, status=400)
    if user["user_id"] in settings.admin_ids:
        return web.json_response({"ok": False, "error": "Already a base admin."}, status=400)
    await db.add_admin_user(user["user_id"], user.get("username"), request["tg_user"].get("id") or 0)
    return await handle_admins_list(request)


async def handle_admins_delete(request: web.Request) -> web.Response:
    if not _is_primary_request(request):
        return _forbidden()
    user_id = int(request.match_info["user_id"])
    if user_id in settings.admin_ids:
        return web.json_response({"ok": False, "error": "Cannot remove base admins."}, status=400)
    await db.remove_admin_user(user_id)
    return await handle_admins_list(request)


async def handle_chats_list(request: web.Request) -> web.Response:
    if not _is_primary_request(request):
        return _forbidden()
    base = [{"chat_id": cid, "title": f"Chat {cid}"} for cid in settings.approved_chat_ids]
    entries = await db.list_approved_chats()
    return web.json_response({"ok": True, "base": base, "entries": entries})


async def handle_chats_add(request: web.Request) -> web.Response:
    if not _is_primary_request(request):
        return _forbidden()
    data = await request.json()
    try:
        chat_id = int(data.get("chat_id"))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "chat_id required"}, status=400)
    title = (data.get("title") or "").strip() or None
    if chat_id in settings.approved_chat_ids:
        return web.json_response({"ok": False, "error": "Chat already configured."}, status=400)
    await db.add_approved_chat(chat_id, title, request["tg_user"].get("id") or 0)
    return await handle_chats_list(request)


async def handle_chats_delete(request: web.Request) -> web.Response:
    if not _is_primary_request(request):
        return _forbidden()
    chat_id = int(request.match_info["chat_id"])
    if chat_id in settings.approved_chat_ids:
        return web.json_response({"ok": False, "error": "Cannot remove configured chats."}, status=400)
    await db.remove_approved_chat(chat_id)
    return await handle_chats_list(request)


@dataclass
class WebAppServer:
    bot: Bot
    runner: web.AppRunner | None = None
    site: web.TCPSite | None = None

    async def start(self) -> None:
        app = web.Application(middlewares=[telegram_auth_middleware])
        app.router.add_get("/", handle_index)
        app.router.add_static("/static/", path=STATIC_DIR, name="static")
        app.router.add_get("/api/me", handle_me)
        app.router.add_get("/api/referrers", handle_referrers)
        app.router.add_post("/api/referrers", handle_create_referrer)
        app.router.add_patch("/api/referrers/{referrer_id}", handle_update_referrer)
        app.router.add_post("/api/referrers/{referrer_id}/referrals", handle_add_referrals)
        app.router.add_post("/api/referrers/{referrer_id}/referrals/remove", handle_remove_referrals)
        app.router.add_post("/api/referrers/{referrer_id}/announce", handle_send_announcement)
        app.router.add_post("/api/announcements/preview", handle_preview_announcement)
        app.router.add_get("/api/referrals/export", handle_export_referrals)
        app.router.add_post("/api/uploads/reminder_media", handle_upload_reminder_media)
        app.router.add_get("/api/announcements/schedule", handle_get_schedule)
        app.router.add_post("/api/announcements/schedule", handle_update_schedule)
        app.router.add_get("/api/reminders", handle_get_reminders)
        app.router.add_post("/api/reminders", handle_create_reminder)
        app.router.add_patch("/api/reminders/{reminder_id}", handle_toggle_reminder)
        app.router.add_delete("/api/reminders/{reminder_id}", handle_delete_reminder)
        app.router.add_post("/api/reminders/preview", handle_preview_reminder)
        app.router.add_get("/api/admins", handle_admins_list)
        app.router.add_post("/api/admins", handle_admins_add)
        app.router.add_delete("/api/admins/{user_id}", handle_admins_delete)
        app.router.add_get("/api/approved_chats", handle_chats_list)
        app.router.add_post("/api/approved_chats", handle_chats_add)
        app.router.add_delete("/api/approved_chats/{chat_id}", handle_chats_delete)
        app["bot"] = self.bot
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, settings.webapp_host, settings.webapp_port)
        await self.site.start()
        logger.info("WebApp available at %s", settings.webapp_url)

    async def close(self) -> None:
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
            self.site = None
