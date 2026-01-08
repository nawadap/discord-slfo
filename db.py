import aiosqlite
import time
from typing import Optional

DB_PATH = "links.db"


# ==============================
# ===== DB INITIALISATION =====
# ==============================

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("""
        CREATE TABLE IF NOT EXISTS links (
            discord_id INTEGER PRIMARY KEY,
            roblox_user_id INTEGER NOT NULL,
            roblox_username TEXT NOT NULL,
            linked_at INTEGER NOT NULL
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS link_codes (
            code TEXT PRIMARY KEY,
            discord_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS admin_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roblox_user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            amount INTEGER NOT NULL,
            queued_at INTEGER NOT NULL,
            done INTEGER DEFAULT 0,
            done_at INTEGER,
            success INTEGER,
            result_text TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS player_profiles (
            roblox_user_id INTEGER PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """)

        await db.commit()


# ===========================
# ===== LINK SYSTEM ========
# ===========================

async def store_code(code: str, discord_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO link_codes VALUES (?, ?, ?)",
            (code, discord_id, int(time.time()))
        )
        await db.commit()


async def get_code(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT discord_id, created_at FROM link_codes WHERE code=?", (code,))
        return await cur.fetchone()


async def delete_code(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM link_codes WHERE code=?", (code,))
        await db.commit()


async def delete_unused_codes_for_user(discord_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM link_codes WHERE discord_id=?", (discord_id,))
        await db.commit()


async def store_link(discord_id: int, roblox_user_id: int, roblox_username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO links VALUES (?, ?, ?, ?)",
            (discord_id, roblox_user_id, roblox_username, int(time.time()))
        )
        await db.commit()


async def delete_link(discord_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM links WHERE discord_id=?", (discord_id,))
        await db.commit()
        return cur.rowcount > 0


async def get_link_by_discord(discord_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM links WHERE discord_id=?", (discord_id,))
        return await cur.fetchone()


async def get_link_by_roblox_user_id(roblox_user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM links WHERE roblox_user_id=?", (roblox_user_id,))
        return await cur.fetchone()


async def get_link_by_roblox_username(username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT * FROM links WHERE lower(roblox_username)=lower(?)",
            (username,)
        )
        return await cur.fetchone()


# ===========================
# ===== ADMIN ACTIONS ======
# ===========================

async def enqueue_admin_action(roblox_user_id: int, action: str, amount: int) -> int:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO admin_actions (roblox_user_id, action, amount, queued_at)
            VALUES (?, ?, ?, ?)
            """,
            (roblox_user_id, action, amount, now)
        )
        await db.commit()
        return cur.lastrowid


async def get_pending_admin_actions():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT * FROM admin_actions WHERE done=0 ORDER BY queued_at"
        )
        return await cur.fetchall()


async def mark_admin_action_done(action_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE admin_actions SET done=1, done_at=? WHERE id=?",
            (int(time.time()), action_id)
        )
        await db.commit()


async def set_admin_action_result(action_id: int, success: bool, result_text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE admin_actions
            SET done=1, done_at=?, success=?, result_text=?
            WHERE id=?
            """,
            (int(time.time()), 1 if success else 0, result_text, action_id)
        )
        await db.commit()


# ===========================
# ===== PLAYER PROFILE =====
# ===========================

async def save_player_profile(roblox_user_id: int, data_json: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO player_profiles VALUES (?, ?, ?)",
            (roblox_user_id, data_json, int(time.time()))
        )
        await db.commit()


import json

async def get_profile_by_roblox_user_id(roblox_user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT data, updated_at FROM player_profiles WHERE roblox_user_id=?",
            (roblox_user_id,)
        )
        row = await cur.fetchone()
        if not row:
            return None

        data_json, updated_at = row
        try:
            data = json.loads(data_json)
        except Exception:
            return None

        # ✅ renvoie un dict plat comme attend bot_commands.py
        data["updated_at"] = updated_at
        return data

import json
from typing import List, Dict, Any

async def list_links():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT discord_id, roblox_user_id, roblox_username, linked_at FROM links ORDER BY linked_at DESC"
        )
        return await cur.fetchall()

async def list_profiles():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT roblox_user_id, data, updated_at FROM player_profiles"
        )
        rows = await cur.fetchall()

    out = {}
    for roblox_user_id, data_json, updated_at in rows:
        try:
            data = json.loads(data_json)
        except Exception:
            data = {}
        data["updated_at"] = updated_at
        out[int(roblox_user_id)] = data
    return out
