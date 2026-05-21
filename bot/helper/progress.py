"""
Progress bar UI + global /status formatter.
wzml-x style: single status message for ALL users' tasks, auto-refreshed.
"""

import asyncio
import time
from contextlib import suppress

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.helper.time_format import humanbytes, time_formatter, clean_html
from bot.helper.task_manager import (
    ACTIVE_TASKS, abort_dict, progress_status,
    GLOBAL_STATUS,
)

# Version strings — filled at startup by main.py
ARIA2C_VERSION   = "N/A"
YTDLP_VERSION    = "N/A"
PYROGRAM_VERSION = "N/A"

_ENGINE_MAP = {
    "ytdlp":        lambda: f"yt-dlp {YTDLP_VERSION}",
    "MegaAPI":      lambda: "MegaAPI",
    "BunkrScript":  lambda: "BunkrScript",
    "DirectHTTP":   lambda: "DirectHTTP",
    "ScriptDL":     lambda: "ScriptDL Native Bypass",
    "TeraBoxAPI":   lambda: "TeraBoxAPI",
    "PyroTgfork":   lambda: f"PyroTgfork {PYROGRAM_VERSION}",
    "FFmpeg":       lambda: "FFmpeg",
    "aria2c":       lambda: f"aria2c {ARIA2C_VERSION}",
}


def _engine_str(engine) -> str:
    if engine is None:
        return f"aria2c {ARIA2C_VERSION}"
    fn = _ENGINE_MAP.get(str(engine))
    return fn() if fn else str(engine)


# ── Per-task progress bar ─────────────────────────────────────────────────────

async def update_progress_ui(
    current, total, message, start_time, action,
    filename="Processing...", task_info=None, batch_info=None,
    engine=None, speed_override=None, eta_override=None,
):
    if message.id in abort_dict:
        ACTIVE_TASKS.pop(message.id, None)
        return
    now = time.time()
    if (now - progress_status.get(message.id, 0) < 5) and (current != total):
        return
    progress_status[message.id] = now

    perc  = current * 100 / total if total > 0 else 0
    speed = speed_override if (speed_override and speed_override > 0) \
            else (current / (now - start_time) if (now - start_time) > 0 else 0)
    eta   = time_formatter(int((total - current) / speed)) if speed > 0 else "0s"
    if eta_override is not None and eta_override > 0:
        eta = time_formatter(int(eta_override))

    bar_done = int(perc // 8.33)
    bar      = "⬢" * bar_done + "⬡" * (12 - bar_done)
    display  = batch_info if batch_info else filename

    # Register in ACTIVE_TASKS for /status global view
    try:
        rm = message.reply_to_message
        uid  = rm.from_user.id   if (rm and rm.from_user) else None
        name = rm.from_user.first_name if (rm and rm.from_user) else "user"
        ACTIVE_TASKS[message.id] = {
            "user_id":   uid,
            "user_name": name,
            "name":      display,
            "action":    action,
            "current":   current,
            "total":     total,
            "speed":     speed,
            "eta":       eta,
            "start_time": start_time,
            "engine":    _engine_str(engine),
        }
        if total > 0 and current >= total:
            ACTIVE_TASKS.pop(message.id, None)
    except Exception:
        pass

    text  = f"<b>{clean_html(display)}</b>\n"
    if task_info:
        text += f"🔢 <b>{task_info}</b>\n"
    text += (
        f"<b>{action}</b>\n"
        f"<code>[{bar}]</code>  {round(perc, 2)}%\n"
        f"<b>Processed:</b> {humanbytes(current)} / {humanbytes(total)}\n"
        f"<b>Speed:</b> {humanbytes(speed)}/s\n"
        f"<b>ETA:</b> {eta}\n"
        f"<b>Engine:</b> <code>{_engine_str(engine)}</code>"
    )
    try:
        await message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{message.id}")
            ]]),
        )
    except Exception:
        pass


# ── Global /status formatter (wzml-x style) ──────────────────────────────────

def format_global_status() -> str:
    if not ACTIVE_TASKS:
        return (
            "📭 <b>No Active Tasks</b>\n\n"
            "<i>Start a download with /dl, /ytdl, /leech, /scriptdl, etc.</i>"
        )
    lines = [f"📊 <b>Active Tasks — {len(ACTIVE_TASKS)}</b>\n"]
    for _tid, t in list(ACTIVE_TASKS.items()):
        cur  = t.get("current") or 0
        tot  = t.get("total")  or 0
        perc = (cur * 100 / tot) if tot else 0
        bar_done = int(perc // 8.33)
        bar  = "⬢" * bar_done + "⬡" * (12 - bar_done)
        spd  = t.get("speed")     or 0
        eta  = t.get("eta")       or "—"
        action   = t.get("action")    or "Working..."
        name     = (t.get("name")     or "Task")[:55]
        user_name = t.get("user_name") or "user"
        engine   = t.get("engine")    or ""
        lines.append(
            f"📁 <b>{clean_html(name)}</b>\n"
            f"👤 {clean_html(str(user_name))}  •  {clean_html(action)}\n"
            f"<code>[{bar}]</code>  {round(perc, 1)}%\n"
            f"📦 {humanbytes(cur)} / {humanbytes(tot)}  "
            f"⚡ {humanbytes(spd)}/s  ⏳ {eta}\n"
            f"<i>{clean_html(engine)}</i>\n"
        )
    return "\n".join(lines)


async def global_status_loop(chat_id):
    """Background updater — refreshes /status message every 5 s."""
    while True:
        try:
            entry = GLOBAL_STATUS.get(chat_id)
            if not entry:
                return
            msg = entry["message"]
            try:
                await msg.edit_text(format_global_status())
            except Exception:
                pass
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(5)
