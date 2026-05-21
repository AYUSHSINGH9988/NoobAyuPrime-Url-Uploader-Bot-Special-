"""
Progress bar UI — WZML-X style with box-drawing characters + system stats.
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

# psutil — optional but recommended
try:
    import psutil as _psutil
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False

# Version strings — filled at startup by main.py
ARIA2C_VERSION   = "N/A"
YTDLP_VERSION    = "N/A"
PYROGRAM_VERSION = "N/A"

_ENGINE_MAP = {
    "ytdlp":        lambda: f"yt-dlp {YTDLP_VERSION}",
    "MegaAPI":      lambda: "MegaSDK",
    "BunkrScript":  lambda: "BunkrScript",
    "DirectHTTP":   lambda: "DirectHTTP",
    "ScriptDL":     lambda: "ScriptDL Native Bypass",
    "TeraBoxAPI":   lambda: "TeraBoxAPI",
    "PyroTgfork":   lambda: f"PyroTgfork {PYROGRAM_VERSION}",
    "FFmpeg":       lambda: "FFmpeg",
    "aria2c":       lambda: f"aria2c {ARIA2C_VERSION}",
}

_MODE_MAP = {
    "ytdlp":        "#Download | #yt-dlp",
    "MegaAPI":      "#Leech | #Mega",
    "BunkrScript":  "#Leech | #Bunkr",
    "DirectHTTP":   "#Download | #HTTP",
    "ScriptDL":     "#Download | #ScriptDL",
    "TeraBoxAPI":   "#Download | #TeraBox",
    "PyroTgfork":   "#Download | #Telegram",
    "aria2c":       "#Leech | #aria2c",
    "FFmpeg":       "#Processing | #FFmpeg",
}


def _engine_str(engine) -> str:
    if engine is None:
        return f"aria2c {ARIA2C_VERSION}"
    fn = _ENGINE_MAP.get(str(engine))
    return fn() if fn else str(engine)


def _mode_str(engine) -> str:
    if engine is None:
        return "#Leech | #aria2c"
    return _MODE_MAP.get(str(engine), f"#Download | #{engine}")


def _elapsed(secs: float) -> str:
    secs = int(secs)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m}m{s}s"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def _build_bar(perc: float, cells: int = 13) -> str:
    filled = min(cells, int(perc * cells / 100))
    return "▥" * filled + "□" * (cells - filled)


def _sys_stats_lines() -> str:
    """Return CPU/RAM/Storage lines for the progress bar."""
    if not _PSUTIL_OK:
        return ""
    try:
        cpu  = _psutil.cpu_percent(interval=None)
        ram  = _psutil.virtual_memory()
        disk = _psutil.disk_usage("/")
        return (
            f"┠ <b>CPU:</b> {cpu}% | "
            f"<b>RAM:</b> {humanbytes(ram.used)} / {humanbytes(ram.total)}\n"
            f"┠ <b>Storage:</b> {humanbytes(disk.free)} free "
            f"of {humanbytes(disk.total)}\n"
        )
    except Exception:
        return ""


# ── Per-task progress bar (WZML-X box-drawing style) ─────────────────────────

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

    bar     = _build_bar(perc)
    elapsed = _elapsed(now - start_time)
    display = batch_info if batch_info else (task_info if task_info else filename)
    eng_str = _engine_str(engine)
    mod_str = _mode_str(engine)

    # Register in ACTIVE_TASKS for /status global view
    try:
        rm   = message.reply_to_message
        uid  = rm.from_user.id        if (rm and rm.from_user) else None
        name = rm.from_user.first_name if (rm and rm.from_user) else None
        ACTIVE_TASKS[message.id] = {
            "user_id":    uid,
            "user_name":  name,
            "name":       display,
            "action":     action,
            "current":    current,
            "total":      total,
            "speed":      speed,
            "eta":        eta,
            "start_time": start_time,
            "engine":     eng_str,
        }
        if total > 0 and current >= total:
            ACTIVE_TASKS.pop(message.id, None)
    except Exception:
        pass

    # Build WZML-X style text
    text  = f"<b>{clean_html(display[:70])}</b>\n"
    text += f"┃ <code>[{bar}]</code> {round(perc, 2)}%\n"
    text += f"┠ <b>Processed:</b> {humanbytes(current)} of {humanbytes(total)}\n"
    text += f"┠ <b>Status:</b> {action} | <b>ETA:</b> {eta}\n"
    text += f"┠ <b>Speed:</b> {humanbytes(speed)}/s | <b>Elapsed:</b> {elapsed}\n"
    text += f"┠ <b>Engine:</b> {eng_str}\n"
    text += f"┠ <b>Mode:</b> {mod_str}\n"
    text += _sys_stats_lines()

    # User info from the message context
    try:
        src = message.reply_to_message
        if src and src.from_user:
            uname   = src.from_user.first_name or "user"
            uid_val = src.from_user.id
            text += f"┠ <b>User:</b> {clean_html(uname)} | <b>ID:</b> <code>{uid_val}</code>\n"
    except Exception:
        pass

    text += f"┖ /cancel_{message.id}"

    try:
        await message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{message.id}")
            ]]),
        )
    except Exception:
        pass


# ── Global /status formatter ──────────────────────────────────────────────────

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
        bar  = _build_bar(perc)
        spd  = t.get("speed")     or 0
        eta  = t.get("eta")       or "—"
        action    = t.get("action")    or "Working..."
        name      = (t.get("name")     or "Task")[:55]
        user_name = t.get("user_name") or "user"
        engine    = t.get("engine")    or ""
        elapsed   = _elapsed(time.time() - (t.get("start_time") or time.time()))
        lines.append(
            f"📁 <b>{clean_html(name)}</b>\n"
            f"👤 {clean_html(str(user_name))}  •  {clean_html(action)}\n"
            f"┃ <code>[{bar}]</code> {round(perc, 1)}%\n"
            f"┠ {humanbytes(cur)} of {humanbytes(tot)}  "
            f"⚡ {humanbytes(spd)}/s  ETA: {eta}  Elapsed: {elapsed}\n"
            f"┖ <i>{clean_html(engine)}</i>\n"
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
