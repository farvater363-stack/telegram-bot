"""
SQLite data access helpers used by the bot.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Sequence

import sqlite3

import aiosqlite

from config import settings


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.conn:
            return
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON;")
        await self.create_tables()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def create_tables(self) -> None:
        assert self.conn is not None
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER UNIQUE NOT NULL,
                type TEXT NOT NULL,
                title TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                type TEXT NOT NULL,
                run_at TEXT,
                time_of_day TEXT,
                weekday INTEGER,
                weekdays TEXT,
                every_n_weeks INTEGER DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                media_path TEXT,
                ignore_inactive INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS referrers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                base_cpm REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                is_removed INTEGER NOT NULL DEFAULT 0,
                removed_at TEXT,
                removed_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_removed INTEGER NOT NULL DEFAULT 0,
                removed_at TEXT,
                removed_by INTEGER,
                removed_reason TEXT,
                FOREIGN KEY(referrer_id) REFERENCES referrers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                username_lower TEXT,
                first_name TEXT,
                last_name TEXT,
                last_seen_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users(username_lower);

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                username_lower TEXT,
                added_by INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_admins_username_lower ON admins(username_lower);

            CREATE TABLE IF NOT EXISTS announcement_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                days TEXT NOT NULL,
                time_of_day TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approved_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER UNIQUE NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                added_by INTEGER
            );
            """
        )
        await self.conn.commit()
        await self._migrate_schema()

    async def execute(self, query: str, params: Sequence[Any] | None = None) -> None:
        assert self.conn is not None
        params = params or []
        async with self._lock:
            await self.conn.execute(query, params)
            await self.conn.commit()

    async def fetchone(self, query: str, params: Sequence[Any] | None = None) -> Optional[dict]:
        assert self.conn is not None
        params = params or []
        async with self._lock:
            cursor = await self.conn.execute(query, params)
            row = await cursor.fetchone()
            await cursor.close()
        return dict(row) if row else None

    async def fetchall(self, query: str, params: Sequence[Any] | None = None) -> List[dict]:
        assert self.conn is not None
        params = params or []
        async with self._lock:
            cursor = await self.conn.execute(query, params)
            rows = await cursor.fetchall()
            await cursor.close()
        return [dict(row) for row in rows]

    # Chat helpers ---------------------------------------------------------
    async def upsert_chat(self, chat_id: int, chat_type: str, title: Optional[str]) -> None:
        await self.execute(
            """
            INSERT INTO chats (chat_id, type, title, is_active, last_seen_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                type=excluded.type,
                title=excluded.title,
                last_seen_at=excluded.last_seen_at;
            """,
            (chat_id, chat_type, title, _utcnow()),
        )

    async def set_chat_active(self, chat_id: int, is_active: bool) -> None:
        await self.execute("UPDATE chats SET is_active=? WHERE chat_id=?", (1 if is_active else 0, chat_id))

    async def get_active_chats(self) -> List[dict]:
        return await self.fetchall(
            "SELECT chat_id, type, title FROM chats WHERE is_active=1 ORDER BY last_seen_at DESC"
        )

    # User helpers ---------------------------------------------------------
    async def upsert_user(
        self, user_id: int, username: str | None, first_name: str | None, last_name: str | None
    ) -> None:
        username_value = (username or "").strip()
        username_lower = username_value.lower() if username_value else None
        await self.execute(
            """
            INSERT INTO users (user_id, username, username_lower, first_name, last_name, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                username_lower=excluded.username_lower,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                last_seen_at=excluded.last_seen_at
            """,
            (user_id, username_value or None, username_lower, first_name, last_name, _utcnow()),
        )

    async def get_user_by_username(self, username: str) -> Optional[dict]:
        uname = username.strip().lower()
        if not uname:
            return None
        return await self.fetchone("SELECT * FROM users WHERE username_lower=?", (uname,))

    async def get_user(self, user_id: int) -> Optional[dict]:
        return await self.fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))

    # Reminder helpers -----------------------------------------------------
    async def insert_reminder(
        self,
        text: str,
        reminder_type: str,
        *,
        run_at: Optional[str],
        time_of_day: Optional[str],
        weekday: Optional[int],
        weekdays: Optional[str],
        every_n_weeks: int,
        created_by: int,
        media_path: Optional[str] = None,
        ignore_inactive: bool = True,
    ) -> int:
        assert self.conn is not None
        async with self._lock:
            cursor = await self.conn.execute(
                """
                INSERT INTO reminders
                (text, type, run_at, time_of_day, weekday, weekdays, every_n_weeks, active, created_by, created_at, media_path, ignore_inactive)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    text,
                    reminder_type,
                    run_at,
                    time_of_day,
                    weekday,
                    weekdays,
                    every_n_weeks,
                    created_by,
                    _utcnow(),
                    media_path,
                    1 if ignore_inactive else 0,
                ),
            )
            await self.conn.commit()
            reminder_id = cursor.lastrowid
            await cursor.close()
        return int(reminder_id)

    async def get_reminders(self) -> List[dict]:
        return await self.fetchall("SELECT * FROM reminders ORDER BY id DESC")

    async def get_active_reminders(self) -> List[dict]:
        return await self.fetchall("SELECT * FROM reminders WHERE active=1")

    async def get_reminder(self, reminder_id: int) -> Optional[dict]:
        return await self.fetchone("SELECT * FROM reminders WHERE id=?", (reminder_id,))

    async def set_reminder_active(self, reminder_id: int, active: bool) -> None:
        await self.execute("UPDATE reminders SET active=? WHERE id=?", (1 if active else 0, reminder_id))

    async def delete_reminder(self, reminder_id: int) -> None:
        await self.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))

    # Referrers and referrals -----------------------------------------------
    async def count_referrers(self) -> int:
        row = await self.fetchone("SELECT COUNT(1) AS total FROM referrers WHERE is_removed=0")
        return int(row["total"]) if row else 0

    async def list_referrers(self, limit: int, offset: int) -> List[dict]:
        return await self.fetchall(
            "SELECT * FROM referrers WHERE is_removed=0 ORDER BY name ASC LIMIT ? OFFSET ?",
            (limit, offset),
        )

    async def create_referrer(self, name: str, base_cpm: float) -> int:
        assert self.conn is not None
        async with self._lock:
            cursor = await self.conn.execute(
                "INSERT INTO referrers (name, base_cpm, created_at) VALUES (?, ?, ?)",
                (name.strip(), base_cpm, _utcnow()),
            )
            await self.conn.commit()
            referrer_id = cursor.lastrowid
            await cursor.close()
        return int(referrer_id)

    async def get_referrer(self, referrer_id: int, include_removed: bool = False) -> Optional[dict]:
        sql = "SELECT * FROM referrers WHERE id=?"
        params: List[Any] = [referrer_id]
        if not include_removed:
            sql += " AND is_removed=0"
        return await self.fetchone(sql, params)

    async def update_referrer_cpm(self, referrer_id: int, base_cpm: float) -> None:
        await self.execute("UPDATE referrers SET base_cpm=? WHERE id=?", (base_cpm, referrer_id))

    async def add_referrals(self, referrer_id: int, names: Iterable[str]) -> int:
        assert self.conn is not None
        cleaned = [name.strip() for name in names if name.strip()]
        if not cleaned:
            return 0
        now = _utcnow()
        async with self._lock:
            await self.conn.executemany(
                "INSERT INTO referrals (referrer_id, referred_name, created_at) VALUES (?, ?, ?)",
                ((referrer_id, name, now) for name in cleaned),
            )
            await self.conn.commit()
        return len(cleaned)

    async def get_referrals_for_referrer(
        self, referrer_id: int, limit: Optional[int] = None, include_removed: bool = False
    ) -> List[dict]:
        sql = """
            SELECT id, referrer_id, referred_name, created_at, is_removed, removed_at, removed_reason
            FROM referrals WHERE referrer_id=?
        """
        params: List[Any] = [referrer_id]
        if not include_removed:
            sql += " AND is_removed=0"
        sql += " ORDER BY created_at DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return await self.fetchall(sql, params)

    async def get_leaderboard(self, limit: int = 10) -> List[dict]:
        return await self.fetchall(
            """
            SELECT r.id, r.name, r.base_cpm, COUNT(f.id) AS referral_count
            FROM referrers r
            LEFT JOIN referrals f ON f.referrer_id = r.id AND f.is_removed = 0
            WHERE r.is_removed = 0
            GROUP BY r.id
            ORDER BY referral_count DESC, r.name ASC
            LIMIT ?
            """,
            (limit,),
        )

    async def get_top_referrer(self) -> Optional[dict]:
        rows = await self.get_leaderboard(limit=1)
        return rows[0] if rows else None

    async def get_referrer_stats(self) -> List[dict]:
        return await self.fetchall(
            """
            SELECT r.id, r.name, r.base_cpm, COUNT(f.id) AS referral_count
            FROM referrers r
            LEFT JOIN referrals f ON f.referrer_id = r.id AND f.is_removed = 0
            WHERE r.is_removed = 0
            GROUP BY r.id
            ORDER BY r.name ASC
            """
        )

    async def get_all_referrals_detailed(self) -> List[dict]:
        return await self.fetchall(
            """
            SELECT r.id AS referrer_id,
                   r.name AS referrer_name,
                   f.id AS referral_id,
                   f.referred_name,
                   f.created_at,
                   f.is_removed,
                   f.removed_at,
                   f.removed_reason
            FROM referrals f
            INNER JOIN referrers r ON r.id = f.referrer_id
            ORDER BY f.created_at DESC
            """
        )

    # Admin helpers --------------------------------------------------------
    async def list_admins(self) -> List[dict]:
        return await self.fetchall(
            "SELECT user_id, username FROM admins ORDER BY username_lower ASC"
        )

    async def get_additional_admin_ids(self) -> List[int]:
        rows = await self.fetchall("SELECT user_id FROM admins")
        return [row["user_id"] for row in rows]

    async def add_admin_user(self, user_id: int, username: str | None, added_by: int) -> None:
        username_value = (username or "").strip()
        username_lower = username_value.lower() if username_value else None
        await self.execute(
            """
            INSERT INTO admins (user_id, username, username_lower, added_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                username_lower=excluded.username_lower,
                added_by=excluded.added_by,
                created_at=excluded.created_at
            """,
            (user_id, username_value or None, username_lower, added_by, _utcnow()),
        )

    async def remove_admin_user(self, user_id: int) -> None:
        await self.execute("DELETE FROM admins WHERE user_id=?", (user_id,))

    async def get_admin_by_username(self, username: str) -> Optional[dict]:
        uname = username.strip().lower()
        return await self.fetchone("SELECT * FROM admins WHERE username_lower=?", (uname,))

    async def is_additional_admin(self, user_id: int) -> bool:
        row = await self.fetchone("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
        return bool(row)

    async def list_approved_chats(self) -> List[dict]:
        return await self.fetchall(
            "SELECT chat_id, title, created_at FROM approved_chats ORDER BY title ASC"
        )

    async def add_approved_chat(self, chat_id: int, title: str | None, added_by: int) -> None:
        await self.execute(
            """
            INSERT INTO approved_chats (chat_id, title, created_at, added_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title
            """,
            (chat_id, title, _utcnow(), added_by),
        )

    async def remove_approved_chat(self, chat_id: int) -> None:
        await self.execute("DELETE FROM approved_chats WHERE chat_id=?", (chat_id,))

    async def get_approved_chat_ids(self) -> List[int]:
        rows = await self.fetchall("SELECT chat_id FROM approved_chats")
        return [row["chat_id"] for row in rows]

    async def is_approved_chat(self, chat_id: int) -> bool:
        row = await self.fetchone("SELECT 1 FROM approved_chats WHERE chat_id=?", (chat_id,))
        return bool(row)

    # Announcement settings ------------------------------------------------
    async def get_announcement_schedule(self) -> Optional[dict]:
        row = await self.fetchone("SELECT days, time_of_day FROM announcement_settings WHERE id=1")
        if not row:
            return None
        days = [int(part) for part in row["days"].split(",") if part]
        return {"days": days, "time_of_day": row["time_of_day"]}

    async def set_announcement_schedule(self, days: List[int], time_of_day: str) -> None:
        payload = ",".join(str(day) for day in days)
        await self.execute(
            """
            INSERT INTO announcement_settings (id, days, time_of_day, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                days=excluded.days,
                time_of_day=excluded.time_of_day,
                updated_at=excluded.updated_at
            """,
            (payload, time_of_day, _utcnow()),
        )
    # Removal helpers ------------------------------------------------------
    async def remove_referrer(self, referrer_id: int, removed_by: int) -> int:
        now = _utcnow()
        assert self.conn is not None
        async with self._lock:
            cursor = await self.conn.execute(
                """
                UPDATE referrers
                SET is_removed=1, removed_at=?, removed_by=?
                WHERE id=? AND is_removed=0
                """,
                (now, removed_by, referrer_id),
            )
            await self.conn.execute(
                """
                UPDATE referrals
                SET is_removed=1, removed_at=?, removed_by=?
                WHERE referrer_id=? AND is_removed=0
                """,
                (now, removed_by, referrer_id),
            )
            await self.conn.commit()
            updated = cursor.rowcount
            await cursor.close()
        return updated

    async def remove_referrals_by_names(
        self,
        referrer_id: int,
        names: List[str],
        removed_by: int,
        reason: str | None = None,
        removed_at_override: str | None = None,
    ) -> int:
        cleaned = [name.strip().lower() for name in names if name.strip()]
        if not cleaned:
            return 0
        placeholders = ",".join("?" for _ in cleaned)
        removed_at = removed_at_override or _utcnow()
        params: List[Any] = [removed_at, removed_by, reason, referrer_id, *cleaned]
        assert self.conn is not None
        async with self._lock:
            cursor = await self.conn.execute(
                f"""
                UPDATE referrals
                SET is_removed=1, removed_at=?, removed_by=?, removed_reason=?
                WHERE referrer_id=? AND is_removed=0 AND LOWER(referred_name) IN ({placeholders})
                """,
                params,
            )
            await self.conn.commit()
            updated = cursor.rowcount
            await cursor.close()
        return updated

    async def remove_referrals_by_ids(
        self,
        referral_ids: List[int],
        removed_by: int,
        reason: str | None = None,
        removed_at_override: str | None = None,
    ) -> int:
        ids = [int(rid) for rid in referral_ids if rid]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        removed_at = removed_at_override or _utcnow()
        params: List[Any] = [removed_at, removed_by, reason, *ids]
        assert self.conn is not None
        async with self._lock:
            cursor = await self.conn.execute(
                f"""
                UPDATE referrals
                SET is_removed=1, removed_at=?, removed_by=?, removed_reason=?
                WHERE is_removed=0 AND id IN ({placeholders})
                """,
                params,
            )
            await self.conn.commit()
            updated = cursor.rowcount
            await cursor.close()
        return updated

    async def _migrate_schema(self) -> None:
        assert self.conn is not None
        await self._add_column("referrers", "is_removed", "INTEGER NOT NULL DEFAULT 0")
        await self._add_column("referrers", "removed_at", "TEXT")
        await self._add_column("referrers", "removed_by", "INTEGER")
        await self._add_column("referrals", "is_removed", "INTEGER NOT NULL DEFAULT 0")
        await self._add_column("referrals", "removed_at", "TEXT")
        await self._add_column("referrals", "removed_by", "INTEGER")
        await self._add_column("referrals", "removed_reason", "TEXT")
        await self._add_column("reminders", "media_path", "TEXT")
        await self._add_column("reminders", "ignore_inactive", "INTEGER NOT NULL DEFAULT 1")

    async def _add_column(self, table: str, column: str, definition: str) -> None:
        assert self.conn is not None
        try:
            await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition};")
            await self.conn.commit()
        except sqlite3.OperationalError as exc:  # type: ignore[attr-defined]
            if "duplicate column name" in str(exc).lower():
                return
            raise


db = Database(settings.database_path)
