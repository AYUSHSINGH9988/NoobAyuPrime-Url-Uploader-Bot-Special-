import os
import time
import asyncio
import aiohttp
import aiofiles
import yt_dlp
import aria2p
import subprocess
import shutil
import traceback
import re
import urllib.parse
import mimetypes
import secrets
import sys
import json
from mega import Mega
from math import floor
from base64 import b64decode
from bs4 import BeautifulSoup
from urllib.parse import urlparse, unquote
from contextlib import suppress
from scripts.phub import get_direct_info, download_phub, get_profile_videos
from scripts.hc import get_hc_data, download_hc
from scripts.wh import get_wh_data
from scripts.dm import get_dm_data
from scripts.xh import get_xh_data, get_xh_profile_videos
from scripts.gdrive_utils import gdown_blocking as _gdown_blocking_ext, gdrive_upload_with_token as _gdrive_upload_ext
from scripts.mega_utils import megacmd_login as _megacmd_login_ext, megacmd_download as _megacmd_download_ext
from datetime import datetime

bot_start_time = time.time()

def get_aria2c_version():
    try:
        result = subprocess.run(["aria2c", "--version"], capture_output=True, text=True)
        match = re.search(r"aria2 version (\S+)", result.stdout)
        return match.group(1) if match else "unknown"
    except:
        return "N/A"

def get_pyrogram_version():
    try:
        import pyrogram
        return getattr(pyrogram, "__version__", "unknown")
    except:
        return "N/A"

def get_ytdlp_version():
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except:
        return "N/A"

ARIA2C_VERSION = get_aria2c_version()
PYROGRAM_VERSION = get_pyrogram_version()
YTDLP_VERSION = get_ytdlp_version()  # <--- Ye line main add karni hai

def get_readable_time(seconds: int) -> str:
    count = 0
    ping_time = ""
    time_list = []
    time_suffix_list = ["s", "m", "h", "days"]
    while count < 4:
        count += 1
        remainder, result = divmod(seconds, 60) if count < 3 else divmod(seconds, 24)
        if seconds == 0 and remainder == 0:
            break
        time_list.append(int(result))
        seconds = int(remainder)
    for x in range(len(time_list)):
        time_list[x] = str(time_list[x]) + time_suffix_list[x]
    if len(time_list) == 4:
        ping_time += time_list.pop() + ", "
    time_list.reverse()
    ping_time += ":".join(time_list)
    return ping_time

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")
RCLONE_PATH = os.environ.get("RCLONE_PATH", "remote:")
PORT = int(os.environ.get("PORT", 8080))
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

# yt-dlp cookie/proxy config
COOKIES_FILE = None
for _p in ["cookies.txt", os.path.expanduser("~/cookies.txt")]:
    if os.path.exists(_p):
        COOKIES_FILE = _p
        break
PROXY_URL = os.environ.get("PROXY_URL", None)

# ─── Strict group lock (Task 1) ───
ALLOWED_GROUP_ID = int(os.environ.get("ALLOWED_GROUP_ID", "-1003426116090"))

# ─── Aliases used by /start — inhe define karna ZAROORI hai, warna NameError aata hai ───
AUTH_GROUP = ALLOWED_GROUP_ID                        # int; 0 = disabled
JOIN_LINK  = os.environ.get("JOIN_LINK", "")         # members ko dikhaya jata hai
DEV_NAME   = os.environ.get("DEV_NAME", "Admin")     # restriction msg mein naam

# ─── User session for restricted-channel downloads (Task 6) ───
USER_SESSION_STRING = os.environ.get("USER_SESSION_STRING") or os.environ.get("STRING_SESSION")

# ─── Auto-delete timeout for uploaded files (Task 2) ───
AUTO_DELETE_SECONDS = int(os.environ.get("AUTO_DELETE_SECONDS", "60"))

if not MONGO_URL:
    print("Error: MONGO_URL is missing!")
    exit(1)

app = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML,
    workers=16,
    max_concurrent_transmissions=5
)

# ─── Pyrogram User Client for restricted-channel downloads (Task 6) ───
user_app = None
if USER_SESSION_STRING:
    try:
        user_app = Client(
            "user_session",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=USER_SESSION_STRING,
            parse_mode=enums.ParseMode.HTML,
            no_updates=True,
            in_memory=True,
        )
    except Exception as _ue:
        print(f"[user_app] Failed to construct: {_ue}")
        user_app = None

# ─── Strict group lock (Task 1) ───
# Only respond in the allowed group OR in any private chat. Silently ignore
# every other group/channel by stopping handler propagation.
@app.on_message(group=-3)
async def _strict_group_lock(_, m):
    try:
        ct = getattr(m.chat, "type", None)
        is_private = ct == enums.ChatType.PRIVATE
        is_allowed_group = (m.chat.id == ALLOWED_GROUP_ID)
        if not (is_private or is_allowed_group):
            m.stop_propagation()
    except Exception:
        # Be safe: if check fails, just let it through
        pass

@app.on_callback_query(group=-3)
async def _strict_group_lock_cb(_, cb):
    try:
        msg = cb.message
        ct = getattr(msg.chat, "type", None) if msg else None
        is_private = ct == enums.ChatType.PRIVATE
        is_allowed_group = bool(msg) and (msg.chat.id == ALLOWED_GROUP_ID)
        if not (is_private or is_allowed_group):
            with suppress(Exception):
                await cb.answer()
            cb.stop_propagation()
    except Exception:
        pass

mongo_client, db, users_col, bot_settings_col = None, None, None, None

async def init_db():
    global mongo_client, db, users_col, bot_settings_col
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URL)
        db = mongo_client["URL_Uploader_Bot"]
        users_col = db["users"]
        bot_settings_col = db["bot_settings"]
        print("MongoDB Connected!")
    except Exception as e:
        print(f"MongoDB Failed: {e}")

# ─────────────────────────────────────────
# Dump helpers
# ─────────────────────────────────────────
async def add_dump(user_id, chat_id, chat_title):
    user = await users_col.find_one({"_id": user_id})
    new_dump = {"id": chat_id, "title": chat_title}
    if not user:
        await users_col.insert_one({"_id": user_id, "dumps": [new_dump], "active_dump": chat_id})
    else:
        dumps = user.get("dumps", [])
        if not any(d["id"] == chat_id for d in dumps):
            dumps.append(new_dump)
            await users_col.update_one({"_id": user_id}, {"$set": {"dumps": dumps}})
            if not user.get("active_dump"):
                await users_col.update_one({"_id": user_id}, {"$set": {"active_dump": chat_id}})

async def get_user_dumps(user_id):
    user = await users_col.find_one({"_id": user_id})
    return user.get("dumps", []) if user else []

async def set_active_dump(user_id, chat_id):
    await users_col.update_one({"_id": user_id}, {"$set": {"active_dump": chat_id}})

async def get_active_dump(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return None
    active_id = user.get("active_dump")
    dumps = user.get("dumps", [])
    for d in dumps:
        if d["id"] == active_id:
            return d
    if dumps:
        await set_active_dump(user_id, dumps[0]["id"])
        return dumps[0]
    return None

async def delete_dump(user_id, chat_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return
    dumps = user.get("dumps", [])
    new_dumps = [d for d in dumps if d["id"] != chat_id]
    update = {"dumps": new_dumps}
    if user.get("active_dump") == chat_id:
        update["active_dump"] = new_dumps[0]["id"] if new_dumps else None
    await users_col.update_one({"_id": user_id}, {"$set": update})

# ─────────────────────────────────────────
# User Settings helpers
# ─────────────────────────────────────────
async def get_user_settings(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return {"send_as": "media", "thumbnail": None}
    return {
        "send_as": user.get("send_as", "media"),
        "thumbnail": user.get("thumbnail", None),
    }

async def set_user_setting(user_id, key, value):
    await users_col.update_one({"_id": user_id}, {"$set": {key: value}}, upsert=True)

async def get_user_thumbnail(user_id):
    user = await users_col.find_one({"_id": user_id})
    return user.get("thumbnail", None) if user else None

async def set_user_thumbnail(user_id, file_id):
    await users_col.update_one({"_id": user_id}, {"$set": {"thumbnail": file_id}}, upsert=True)

async def clear_user_thumbnail(user_id):
    await users_col.update_one({"_id": user_id}, {"$unset": {"thumbnail": ""}})

# ─────────────────────────────────────────
# Ban / Warn helpers
# ─────────────────────────────────────────
async def is_user_banned(user_id):
    if users_col is None:
        return False
    user = await users_col.find_one({"_id": user_id})
    return bool(user and user.get("is_banned"))

# ─────────────────────────────────────────
# Mega login helpers (MegaAPI Engine)
# ─────────────────────────────────────────
mega_api = Mega()
mega_client = mega_api.login() # Default anonymous
mega_creds = {"email": None}

async def mega_login(email, password):
    """
    1. Use MegaCMD (mega-login) as primary auth engine.
    2. Capture session token via mega-session.
    3. Bridge token into mega.py client for API-speed operations.
    4. On any failure, continue — MegaCMD CLI still works for rename ops.
    """
    global mega_client

    QUOTA_ENV = {
        "MEGA_IGNORE_UPLOAD_QUOTA":      "1",
        "MEGA_FORCE_FULL_ACCOUNT_CACHE": "1",
    }

    def _megacmd_login():
        """Delegates to scripts/mega_utils.py (force-logout + clean login)."""
        return _megacmd_login_ext(email, password)

    try:
        success, err = await asyncio.to_thread(_megacmd_login)
    except Exception as e:
        return False, str(e)

    if not success:
        return False, err or "mega-login failed"

    mega_creds["email"] = email

    # ── Session bridge: capture token and init mega.py client ──
    def _get_session():
        try:
            sr = subprocess.run(["mega-session"], capture_output=True, text=True, timeout=10)
            token = (sr.stdout or "").strip()
            return token if token else None
        except Exception:
            return None

    try:
        token = await asyncio.to_thread(_get_session)
        if token:
            def _bridge():
                from mega import Mega
                m = Mega()
                m.login_session(token)
                return m
            bridged = await asyncio.to_thread(_bridge)
            mega_client = bridged
            print(f"[mega] Session bridge OK → API client ready")
        else:
            print("[mega] mega-session returned no token — MegaCMD CLI only mode")
    except Exception as _se:
        print(f"[mega] Session bridge failed ({_se}) — MegaCMD CLI only mode")

    return True, f"Login successful: {email}"

def _parse_size_to_bytes(num_str: str, unit: str) -> int:
    """Convert e.g. ('123.45', 'MB') -> bytes."""
    try:
        n = float(num_str)
    except Exception:
        return 0
    u = (unit or "").strip().upper()
    mult = {
        "B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4,
        "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
    }.get(u, 1)
    return int(n * mult)


# Matches MegaCMD progress lines like:
#   "TRANSFERRING ||##########---|| (123.45/456.78 MB: 27.05 %)"
#   "TRANSFERRING ||#-----||  (12 / 100 MB: 12.00 %)"
_MEGA_GET_PROG_RE = re.compile(
    r"\(\s*([\d.]+)\s*/\s*([\d.]+)\s*([KMGT]?i?B)\s*:\s*([\d.]+)\s*%?\s*\)",
    re.IGNORECASE,
)


async def mega_download(url_or_path, download_dir, message):
    """Download via MegaCMD `mega-get` subprocess so we can parse real
    progress from stdout (mega.py cached files in cwd which made
    os.walk-based progress always read 0 bytes)."""
    os.makedirs(download_dir, exist_ok=True)
    file_name = "Mega_File"
    start_time = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            "mega-get", url_or_path, download_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        stderr_acc = []  # capture textual output for error reporting

        async def _pump_progress():
            assert proc.stdout is not None
            buf = bytearray()
            last_ui = 0.0
            while True:
                chunk = await proc.stdout.read(256)
                if not chunk:
                    # Flush any pending line in the buffer
                    if buf:
                        line = buf.decode(errors="ignore").strip()
                        stderr_acc.append(line)
                        buf.clear()
                    break
                buf.extend(chunk)
                # MegaCMD progress lines are terminated with \r, real
                # log lines with \n — split on either.
                while True:
                    nl = -1
                    for i, b in enumerate(buf):
                        if b in (0x0A, 0x0D):  # \n or \r
                            nl = i
                            break
                    if nl < 0:
                        break
                    line = bytes(buf[:nl]).decode(errors="ignore").strip()
                    del buf[: nl + 1]
                    if not line:
                        continue
                    stderr_acc.append(line)

                    if message.id in abort_dict:
                        with suppress(Exception):
                            proc.terminate()
                        return

                    m = _MEGA_GET_PROG_RE.search(line)
                    if not m:
                        continue
                    cur_n, tot_n, unit, _pct = m.group(1), m.group(2), m.group(3), m.group(4)
                    cur_b = _parse_size_to_bytes(cur_n, unit)
                    tot_b = _parse_size_to_bytes(tot_n, unit)
                    if tot_b <= 0:
                        continue

                    now = time.time()
                    if now - last_ui < 2.0:
                        continue
                    last_ui = now
                    try:
                        await update_progress_ui(
                            cur_b, tot_b, message, start_time,
                            "📥 Mega Downloading...", file_name
                        )
                    except Exception:
                        pass

        pump_task = asyncio.create_task(_pump_progress())
        await proc.wait()
        with suppress(Exception):
            await pump_task

        if message.id in abort_dict:
            return [], "CANCELLED"

        if proc.returncode != 0:
            tail = " | ".join(stderr_acc[-6:]) if stderr_acc else "mega-get failed"
            return [], f"MegaCMD Error: {tail}"

        downloaded_files = []
        for root, _dirs, files in os.walk(download_dir):
            for fname in files:
                downloaded_files.append(os.path.join(root, fname))

        if not downloaded_files:
            return [], "MegaCMD: No files downloaded."

        return downloaded_files, None

    except Exception as e:
        return [], f"MegaCMD Exception: {str(e)}"

# ─────────────────────────────────────────
# State dicts
# ─────────────────────────────────────────
abort_dict = {}
user_queues = {}
is_processing = {}
progress_status = {}
ytdl_session = {}
aria2 = None
pending_selections = {}
seeding_gids = {}
waiting_for_thumbnail = {}  # user_id -> True
# user_id -> "rclone" | "gdrive"  — what config file we're awaiting
waiting_for_config_upload = {}
USER_CONFIG_DIR = "user_configs"

# ─── WZML-X style global progress (Task 3) ───
# msg_id -> dict(user_id, user_name, name, action, current, total, speed, eta, start_time, engine)
ACTIVE_TASKS = {}
# global status message bookkeeping; chat_id -> {"message": Message, "task": asyncio.Task}
GLOBAL_STATUS = {}

# ─── Per-user proxy input wait & admin bsettings input wait ───
waiting_for_proxy = {}      # user_id -> True
waiting_for_bsetting = {}   # user_id -> setting_key


# ─────────────────────────────────────────
# Bot settings (admin-configurable limits) — Task 4
# ─────────────────────────────────────────
DEFAULT_BSETTINGS = {
    "max_tasks_per_user": 3,
    "max_size_gb_ytdl":   8,
    "max_size_gb_mdl":    8,
    "max_size_gb_bdl":    8,
    "max_size_gb_leech":  8,
}

async def get_bsettings():
    if bot_settings_col is None:
        return dict(DEFAULT_BSETTINGS)
    try:
        doc = await bot_settings_col.find_one({"_id": "global"}) or {}
    except Exception:
        doc = {}
    out = dict(DEFAULT_BSETTINGS)
    for k in DEFAULT_BSETTINGS.keys():
        if k in doc:
            try:
                out[k] = float(doc[k]) if "size" in k else int(doc[k])
            except Exception:
                pass
    return out

async def set_bsetting(key, value):
    if bot_settings_col is None:
        return False
    if key not in DEFAULT_BSETTINGS:
        return False
    try:
        await bot_settings_col.update_one(
            {"_id": "global"}, {"$set": {key: value}}, upsert=True
        )
        return True
    except Exception:
        return False


def _count_user_tasks(user_id) -> int:
    """How many tasks this user already has running."""
    if not user_id:
        return 0
    return sum(1 for t in ACTIVE_TASKS.values() if t.get("user_id") == user_id)


async def _enforce_limits(message, kind: str) -> bool:
    """Enforce per-user task count limit + per-command size limit kind.
    `kind` is one of: 'ytdl' | 'mdl' | 'bdl' | 'leech'.
    On violation, replies with a helpful error and returns False."""
    s = await get_bsettings()
    user_id = message.from_user.id if message.from_user else None
    max_tasks = int(s.get("max_tasks_per_user") or 0)
    if max_tasks > 0:
        active = _count_user_tasks(user_id)
        if active >= max_tasks:
            try:
                await message.reply_text(
                    f"❌ <b>Task limit reached.</b>\n"
                    f"You have <code>{active}</code> running tasks "
                    f"(max <code>{max_tasks}</code>).\n"
                    f"Wait for them to finish or cancel via /status."
                )
            except Exception:
                pass
            return False
    return True


def _size_limit_bytes(s, kind: str) -> int:
    key = f"max_size_gb_{kind}"
    try:
        gb = float(s.get(key) or 0)
    except Exception:
        gb = 0
    return int(gb * 1024 * 1024 * 1024)


# ─────────────────────────────────────────
# Per-user proxy (Task 5)
# ─────────────────────────────────────────
async def get_user_proxy(user_id):
    """Return the user's saved proxy URL, or None."""
    if users_col is None or not user_id:
        return None
    try:
        u = await users_col.find_one({"_id": user_id})
    except Exception:
        return None
    if not u:
        return None
    p = u.get("proxy")
    return p or None

async def set_user_proxy(user_id, proxy_url):
    await users_col.update_one(
        {"_id": user_id}, {"$set": {"proxy": proxy_url}}, upsert=True
    )

async def clear_user_proxy(user_id):
    await users_col.update_one(
        {"_id": user_id}, {"$unset": {"proxy": ""}}
    )

def _resolve_proxy_sync(user_proxy):
    """Pick user_proxy if given, else env PROXY_URL, else None.
    Use this in sync code paths where you've already fetched user_proxy."""
    return user_proxy or PROXY_URL


# ─────────────────────────────────────────
# Torrent URL validator (Task 7)
# ─────────────────────────────────────────
def _is_torrent_link(url: str) -> bool:
    """Recognise magnet links, .torrent files AND private-tracker
    download URLs (Gazelle/Ocelot style: torrents.php?action=download…)."""
    if not url:
        return False
    u = url.lower()
    if u.startswith("magnet:"):
        return True
    if ".torrent" in u:
        return True
    # Private tracker download URLs
    if "torrents.php?action=download" in u:
        return True
    if "action=download" in u and ("authkey=" in u or "torrent_pass=" in u or "passkey=" in u):
        return True
    return False


# ─────────────────────────────────────────
# WZML-X global progress helpers (Task 3)
# ─────────────────────────────────────────
def _format_global_status() -> str:
    if not ACTIVE_TASKS:
        return "🔕 <b>No active tasks.</b>"
    lines = [f"📊 <b>Active Tasks ({len(ACTIVE_TASKS)})</b>\n"]
    for tid, t in list(ACTIVE_TASKS.items()):
        cur = t.get("current") or 0
        tot = t.get("total") or 0
        perc = (cur * 100 / tot) if tot else 0
        bar_done = int(perc // 8.33)
        bar = "⬢" * bar_done + "⬡" * (12 - bar_done)
        spd = t.get("speed") or 0
        eta = t.get("eta") or "—"
        action = t.get("action") or "Working..."
        name = (t.get("name") or "Task")[:60]
        user_name = t.get("user_name") or "user"
        engine = t.get("engine") or ""
        lines.append(
            f"<b>{clean_html(name)}</b>\n"
            f"👤 {clean_html(str(user_name))}  •  {clean_html(action)}\n"
            f"<code>[{bar}]</code>  {round(perc, 1)}%\n"
            f"📦 {humanbytes(cur)} / {humanbytes(tot)}  •  ⚡ {humanbytes(spd)}/s  •  ⏳ {eta}\n"
            f"<i>{clean_html(engine)}</i>\n"
        )
    return "\n".join(lines)

async def _global_status_loop(chat_id):
    """Background updater: refreshes the global status message every ~5s."""
    while True:
        try:
            entry = GLOBAL_STATUS.get(chat_id)
            if not entry:
                return
            msg = entry["message"]
            try:
                await msg.edit_text(_format_global_status())
            except Exception:
                pass
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(5)


def _user_proxy_for_yt_dlp_opts(opts: dict, user_proxy: str | None):
    """Inject the right proxy into a yt-dlp opts dict in place."""
    p = user_proxy or PROXY_URL
    if p:
        opts["proxy"] = p
    elif "proxy" in opts:
        opts.pop("proxy", None)


def _user_config_path(user_id, kind):
    """kind: 'rclone' -> rclone.conf  |  'gdrive' -> token.pickle"""
    base = os.path.join(USER_CONFIG_DIR, str(user_id))
    os.makedirs(base, exist_ok=True)
    if kind == "rclone":
        return os.path.join(base, "rclone.conf")
    if kind == "gdrive":
        return os.path.join(base, "token.pickle")
    raise ValueError(f"Unknown config kind: {kind}")


def _has_user_config(user_id, kind):
    p = os.path.join(
        USER_CONFIG_DIR, str(user_id),
        "rclone.conf" if kind == "rclone" else "token.pickle"
    )
    return os.path.exists(p) and os.path.getsize(p) > 0

def humanbytes(size):
    if not size:
        return "0B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{round(size, 2)} {unit}"
        size /= 1024
    return f"{round(size, 2)} PB"

def time_formatter(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return "{:02d}:{:02d}:{:02d}".format(int(hours), int(minutes), int(seconds))

def clean_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def get_best_thumbnail(info: dict) -> str | None:
    """
    yt-dlp ke 'thumbnails' list se sabse achhi quality ka thumbnail URL return karta hai.
    YouTube ke liye maxresdefault prefer karta hai.
    Fallback: info['thumbnail'] (yt-dlp ka default, jo aksar chhota hota hai).
    """
    thumbnails = info.get("thumbnails") or []
    if thumbnails:
        # Try to find maxresdefault (YouTube highest quality)
        for t in reversed(thumbnails):
            url = t.get("url", "")
            if "maxresdefault" in url:
                return url
        # Try sddefault / hqdefault as next best
        for t in reversed(thumbnails):
            url = t.get("url", "")
            if "sddefault" in url or "hqdefault" in url:
                return url
        # Sort by resolution (width*height) if available, pick largest
        scored = []
        for t in thumbnails:
            w = t.get("width") or 0
            h = t.get("height") or 0
            url = t.get("url", "")
            if url:
                scored.append((w * h, url))
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[0][1]
        # Fallback to last item (usually largest for YouTube)
        last_url = thumbnails[-1].get("url")
        if last_url:
            return last_url
    # Final fallback: yt-dlp default thumbnail key
    return info.get("thumbnail")

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

async def take_screenshot(video_path, duration=1):
    try:
        thumb_path = f"{video_path}.jpg"
        ss_time = int(duration // 2) if duration > 10 else 1
        cmd = ["ffmpeg", "-ss", str(ss_time), "-i", video_path, "-vframes", "1", "-q:v", "2", thumb_path, "-y"]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await process.wait()
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except:
        pass
    try:
        thumb_path = f"{video_path}.jpg"
        cmd = ["ffmpeg", "-ss", "1", "-i", video_path, "-vframes", "1", "-q:v", "2", thumb_path, "-y"]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await process.wait()
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except:
        pass
    return None

async def get_video_duration(video_path):
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", video_path]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        return int(float(stdout.decode().strip()))
    except:
        return 0

async def convert_to_streamable(file_path, message):
    ext = os.path.splitext(file_path)[1].lower()
    output_path = os.path.splitext(file_path)[0] + "_conv.mp4"
    try:
        await message.edit_text(
            f"🔄 <b>Converting {ext} → .mp4...</b>\n<code>{clean_html(os.path.basename(file_path))}</code>"
        )
        cmd = ["ffmpeg", "-y", "-i", file_path,
               "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", output_path]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            os.remove(file_path)
            return output_path, True
        await message.edit_text(f"🔄 <b>Re-encoding {ext} → .mp4...</b>")
        cmd2 = ["ffmpeg", "-y", "-i", file_path,
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "aac", "-b:a", "128k", output_path]
        proc2 = await asyncio.create_subprocess_exec(
            *cmd2, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc2.communicate()
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            os.remove(file_path)
            return output_path, True
    except Exception as e:
        print(f"convert_to_streamable error ({ext}): {e}")
    return file_path, False

# ─────────────────────────────────────────
# Bunkr helpers
# ─────────────────────────────────────────
BUNKR_VS_API_URL = "https://bunkr.cr/api/vs"
SECRET_KEY_BASE = "SECRET_KEY_"
BUNKR_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
    'Referer': 'https://bunkr.sk/',
}

def bunkr_remove_illegal_chars(string):
    return re.sub(r'[<>:"/\\|?*\']|[\0-\31]', "-", string).strip()

async def bunkr_get_encryption_data(session, slug):
    try:
        async with session.post(BUNKR_VS_API_URL, json={'slug': slug}, headers=BUNKR_HEADERS) as r:
            if r.status != 200:
                return None
            return await r.json()
    except Exception as e:
        print(f"Bunkr encryption data error: {e}")
        return None

def bunkr_decrypt_url(encryption_data):
    try:
        secret_key = f"{SECRET_KEY_BASE}{floor(encryption_data['timestamp'] / 3600)}"
        encrypted_bytes = list(b64decode(encryption_data['url']))
        key_bytes = list(secret_key.encode('utf-8'))
        return "".join(chr(encrypted_bytes[i] ^ key_bytes[i % len(key_bytes)]) for i in range(len(encrypted_bytes)))
    except Exception as e:
        print(f"Bunkr decrypt error: {e}")
        return None

async def bunkr_get_real_url(session, url, item_name=None):
    try:
        full_url = url if 'https' in url else f'https://bunkr.sk{url}'
        async with session.get(full_url, headers=BUNKR_HEADERS) as r:
            if r.status != 200:
                return None
            slug_match = re.search(r'\/f\/(.*?)$', full_url)
            if not slug_match:
                return None
            slug = unquote(slug_match.group(1))
        enc_data = await bunkr_get_encryption_data(session, slug)
        if not enc_data:
            return None
        real_url = bunkr_decrypt_url(enc_data)
        if not real_url:
            return None
        return {'url': real_url, 'name': item_name or os.path.basename(real_url)}
    except Exception as e:
        print(f"Bunkr get_real_url error: {e}")
        return None

async def bunkr_get_album_items(session, url):
    items_result = []
    try:
        async with session.get(url, headers=BUNKR_HEADERS) as r:
            if r.status != 200:
                return [], f"HTTP {r.status}"
            html = await r.text()
        soup = BeautifulSoup(html, 'html.parser')
        title_tag = soup.find('title')
        if not title_tag or "| Bunkr" not in title_tag.text:
            return [], "Not a Bunkr page"
        is_single = soup.find('span', {'class': 'ic-videos'}) is not None or \
                    soup.find('div', {'class': 'lightgallery'}) is not None
        if is_single:
            item = await bunkr_get_real_url(session, url)
            if item:
                items_result.append(item)
        else:
            # ── New robust extraction (Bunkr changed HTML often) ──
            # Find every <a href> that points to a Bunkr file/video page (/f/ or /v/).
            base_origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
            seen_urls = set()
            collected = []  # list[(item_url, item_name)]

            # Primary: regex over raw HTML — survives class-name churn.
            href_re = re.compile(
                r'<a[^>]+href=["\'](?P<href>(?:https?:)?(?://[^"\']+)?/[fv]/[^"\'#?]+)["\'][^>]*>',
                re.IGNORECASE,
            )
            for hm in href_re.finditer(html):
                href = hm.group("href").strip()
                if href.startswith("//"):
                    item_url = "https:" + href
                elif href.startswith("/"):
                    item_url = base_origin + href
                else:
                    item_url = href
                if item_url in seen_urls:
                    continue
                seen_urls.add(item_url)
                collected.append((item_url, None))

            # Secondary: BeautifulSoup pass to recover names where possible.
            for a in soup.find_all('a', href=True):
                href = a['href'].strip()
                if not re.search(r'/[fv]/', href):
                    continue
                if href.startswith("//"):
                    item_url = "https:" + href
                elif href.startswith("/"):
                    item_url = base_origin + href
                else:
                    item_url = href
                # Try to pull a human-readable name nearby.
                name_tag = a.find('p') or a.find(attrs={"class": re.compile("truncate", re.I)})
                item_name = name_tag.get_text(strip=True) if name_tag else (
                    a.get("title") or a.get_text(strip=True) or None
                )
                if item_url in seen_urls:
                    # Backfill the name on the existing entry if we now have one.
                    if item_name:
                        for i, (u, n) in enumerate(collected):
                            if u == item_url and not n:
                                collected[i] = (u, item_name)
                                break
                    continue
                seen_urls.add(item_url)
                collected.append((item_url, item_name))

            for item_url, item_name in collected:
                real_item = await bunkr_get_real_url(session, item_url, item_name)
                if real_item:
                    items_result.append(real_item)

            # ── Pagination: follow real "Next Page" links (rel=next or text) ──
            visited_pages = {url}
            cur_page_url = url
            while True:
                # Re-parse current page once already done above; for subsequent
                # pages we need to re-fetch + re-parse.
                if cur_page_url == url:
                    page_soup = soup
                else:
                    try:
                        async with session.get(cur_page_url, headers=BUNKR_HEADERS) as pr:
                            if pr.status != 200:
                                break
                            page_html = await pr.text()
                        page_soup = BeautifulSoup(page_html, 'html.parser')
                    except Exception as _pe:
                        print(f"Bunkr pagination fetch error: {_pe}")
                        break
                    # Extract items on this subsequent page using same logic.
                    sub_items, _serr = await bunkr_get_album_items(session, cur_page_url)
                    items_result.extend(sub_items)

                next_link = None
                # rel="next"
                rl = page_soup.find('a', attrs={"rel": "next"})
                if rl and rl.get('href'):
                    next_link = rl['href']
                else:
                    # text-based "Next" / "›" / arrow link inside pagination nav.
                    nav = page_soup.find('nav', attrs={"class": re.compile("pagination", re.I)}) or page_soup
                    for a in nav.find_all('a', href=True):
                        txt = (a.get_text(strip=True) or "").lower()
                        if txt in ("next", "next »", "»", "›", ">", "next page"):
                            next_link = a['href']
                            break
                if not next_link:
                    break
                if next_link.startswith("//"):
                    next_url = "https:" + next_link
                elif next_link.startswith("/"):
                    next_url = base_origin + next_link
                elif next_link.startswith("http"):
                    next_url = next_link
                else:
                    # Relative path on current page.
                    base = cur_page_url.rsplit("/", 1)[0]
                    next_url = f"{base}/{next_link}"
                if next_url in visited_pages:
                    break
                visited_pages.add(next_url)
                cur_page_url = next_url
        return items_result, None
    except Exception as e:
        return [], str(e)

async def bunkr_download_file(session, item, download_dir, message, index, total, overall_start):
    real_url = item['url']
    file_name = item.get('name') or os.path.basename(urlparse(real_url).path)
    file_name = unquote(file_name)
    if not file_name or '.' not in file_name:
        file_name = f"bunkr_file_{index}.mp4"
    file_path = os.path.join(download_dir, file_name)
    try:
        async with session.get(real_url, headers=BUNKR_HEADERS) as r:
            if r.status != 200:
                return None
            if r.url.path == "/maintenance.mp4":
                return None
            total_size = int(r.headers.get('content-length', 0))
            dl_size = 0
            start_time = time.time()
            async with aiofiles.open(file_path, 'wb') as f:
                async for chunk in r.content.iter_chunked(512 * 1024):
                    if message.id in abort_dict:
                        return None
                    await f.write(chunk)
                    dl_size += len(chunk)
                    await update_progress_ui(dl_size, total_size, message, start_time, f"📥 Downloading [{index}/{total}]", file_name)
        if total_size > 0 and os.path.getsize(file_path) != total_size:
            os.remove(file_path)
            return None
        return file_path
    except Exception as e:
        print(f"Bunkr download exception: {e}")
        return None

async def download_bunkr(url, message, task_info=None):
    connector = aiohttp.TCPConnector(limit=10, force_close=False, enable_cleanup_closed=True, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        await message.edit_text("🔍 <b>Fetching Bunkr items...</b>")
        items, err = await bunkr_get_album_items(session, url)
        if err:
            return [], f"Bunkr Error: {err}"
        if not items:
            return [], "No downloadable items found!"
        total = len(items)
        await message.edit_text(f"📥 <b>Found {total} file(s). Downloading...</b>")
        download_dir = os.path.join("downloads", f"bunkr_{int(time.time())}")
        os.makedirs(download_dir, exist_ok=True)
        downloaded_files = []
        overall_start = time.time()
        for i, item in enumerate(items, 1):
            if message.id in abort_dict:
                break
            try:
                await message.edit_text(f"📥 <b>Bunkr Download [{i}/{total}]</b>\n<code>{clean_html(item.get('name', 'Unknown'))}</code>")
                fp = await bunkr_download_file(session, item, download_dir, message, i, total, overall_start)
                if fp:
                    downloaded_files.append(fp)
            except Exception as e:
                print(f"Item {i} failed: {e}")
                continue
        return downloaded_files, None

# ─────────────────────────────────────────
# Progress UI
# ─────────────────────────────────────────
async def update_progress_ui(current, total, message, start_time, action,
                              filename="Processing...", task_info=None, batch_info=None,
                              engine=None, speed_override=None, eta_override=None):
    if message.id in abort_dict:
        ACTIVE_TASKS.pop(message.id, None)
        return
    now = time.time()
    if (now - progress_status.get(message.id, 0) < 5) and (current != total):
        return
    progress_status[message.id] = now
    perc  = current * 100 / total if total > 0 else 0
    # Task 3: use yt-dlp's own speed if provided (sliding-window avg), else compute cumulative
    speed = speed_override if (speed_override and speed_override > 0) \
            else (current / (now - start_time) if (now - start_time) > 0 else 0)
    if eta_override is not None and eta_override > 0:
        eta = time_formatter(int(eta_override))
    else:
        eta = time_formatter(int((total - current) / speed)) if speed > 0 else "0s"
    completed = int(perc // 8.33)
    bar  = "⬢" * completed + "⬡" * (12 - completed)
    display_name = batch_info if batch_info else filename

    # ─── Task 3: register/update in ACTIVE_TASKS for /status global view ───
    try:
        try:
            owner_uid = message.reply_to_message.from_user.id if message.reply_to_message and message.reply_to_message.from_user else None
        except Exception:
            owner_uid = None
        try:
            owner_name = message.reply_to_message.from_user.first_name if message.reply_to_message and message.reply_to_message.from_user else None
        except Exception:
            owner_name = None
        ACTIVE_TASKS[message.id] = {
            "user_id":    owner_uid,
            "user_name":  owner_name or "user",
            "name":       display_name,
            "action":     action,
            "current":    current,
            "total":      total,
            "speed":      speed,
            "eta":        eta,
            "start_time": start_time,
            "engine":     engine or "",
        }
        # Auto-clear when finished
        if total > 0 and current >= total:
            ACTIVE_TASKS.pop(message.id, None)
    except Exception:
        pass
    # Dynamic engine string
    if engine is None:
        engine_str = f"aria2c {ARIA2C_VERSION}"
    elif engine == "ytdlp":
        engine_str = f"yt-dlp {YTDLP_VERSION}"
    elif engine == "MegaAPI":
        engine_str = "MegaAPI"
    elif engine == "BunkrScript":
        engine_str = "BunkrScript"
    elif engine == "DirectHTTP":
        engine_str = "DirectHTTP"
    elif engine == "ScriptDL":
        engine_str = "ScriptDL Native Bypass"
    elif engine == "TeraBoxAPI":
        engine_str = "TeraBoxAPI"
    elif engine == "PyroTgfork":
        engine_str = f"PyroTgfork {PYROGRAM_VERSION}"
    else:
        engine_str = str(engine)
    text  = f"<b>{clean_html(urllib.parse.unquote(display_name))}</b>\n"
    if task_info:
        text += f"🔢 <b>{task_info}</b>\n"
    text += f"<b>{action}</b>\n"
    text += f"<code>[{bar}]</code>\n"
    text += f"<b>Progress:</b> {round(perc, 2)}%\n"
    text += f"<b>Processed:</b> {humanbytes(current)}\n"
    text += f"<b>Total:</b> {humanbytes(total)}\n"
    text += f"<b>Speed:</b> {humanbytes(speed)}/s\n"
    text += f"<b>ETA:</b> {eta}\n"
    text += f"<b>Engine:</b> <code>{engine_str}</code>"
    try:
        await message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{message.id}")]])
        )
    except:
        pass

# ─────────────────────────────────────────
# Archive / Compress / Split helpers
# ─────────────────────────────────────────
def extract_archive(file_path):
    output_dir = f"extracted_{int(time.time())}"
    os.makedirs(output_dir, exist_ok=True)
    if not shutil.which("7z"):
        return [], None, "7z missing!"
    cmd = ["7z", "x", str(file_path), f"-o{output_dir}", "-y"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files_list = []
    for root, _, files in os.walk(output_dir):
        for file in files:
            files_list.append(os.path.join(root, file))
    files_list.sort(key=natural_sort_key)
    return files_list, output_dir, None

def create_archive(file_path):
    if not shutil.which("7z"):
        return file_path, False
    zip_path = file_path + ".zip"
    cmd = ["7z", "a", zip_path, file_path, "-mx1"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return zip_path, True

async def compress_video(file_path, message):
    if not shutil.which("ffmpeg"):
        return file_path, False
    output_path = f"{os.path.splitext(file_path)[0]}_480p.mp4"
    cmd = ["ffmpeg", "-i", file_path, "-vf", "scale=-2:480", "-c:v", "libx264", "-crf", "28",
           "-preset", "ultrafast", "-c:a", "aac", "-b:a", "64k", output_path, "-y"]
    await message.edit_text("📉 <b>Compressing to 480p...</b>\nThis may take time.")
    process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await process.wait()
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path, True
    return file_path, False

def split_large_file(file_path):
    limit = 2000 * 1024 * 1024
    if os.path.getsize(file_path) <= limit:
        return [file_path], False
    out_dir = f"split_{int(time.time())}"
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["7z", "a", f"-v{2000}m", os.path.join(out_dir, os.path.basename(file_path) + ".7z"), file_path, "-mx0"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL)
    parts = [os.path.join(out_dir, f) for f in os.listdir(out_dir)]
    parts.sort(key=natural_sort_key)
    return parts, True

# ─────────────────────────────────────────
# Download a thumbnail from Telegram to disk
# ─────────────────────────────────────────
async def download_thumb_from_file_id(client, file_id, user_id):
    try:
        thumb_dir = "thumbnails"
        os.makedirs(thumb_dir, exist_ok=True)
        path = os.path.join(thumb_dir, f"thumb_{user_id}.jpg")
        await client.download_media(file_id, file_name=path)
        return path
    except Exception as e:
        print(f"Thumb download error: {e}")
        return None

# ─────────────────────────────────────────
# Upload helpers
# ─────────────────────────────────────────
def _is_gdrive_link(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return ("drive.google.com" in u) or ("docs.google.com" in u)


def _gdown_blocking(url: str, out_dir: str) -> str | None:
    """Delegates to scripts/gdrive_utils.py — fixes usercontent URLs + fuzzy TypeError."""
    return _gdown_blocking_ext(url, out_dir)


async def _gdrive_upload_with_token(local_path: str, token_path: str) -> str:
    """Delegates to scripts/gdrive_utils.py — includes token.pickle corruption check."""
    return await _gdrive_upload_ext(local_path, token_path)


async def _rclone_upload_with_conf(local_path: str, conf_path: str,
                                    remote: str = None) -> bool:
    """Run `rclone copy <local> <remote>: --config <conf>` using the user's conf.
    If remote isn't given, use the first remote name found in the conf."""
    if remote is None:
        # Read the first [section] from the conf as the default remote.
        try:
            with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("[") and line.endswith("]"):
                        remote = line[1:-1]
                        break
        except Exception:
            remote = None
        if not remote:
            raise Exception("No [remote] section found in rclone.conf")

    cmd = ["rclone", "copy", local_path, f"{remote}:",
           "--config", conf_path, "-P"]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _out, _err = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(_err.decode(errors="ignore") or _out.decode(errors="ignore"))
    return True


async def handle_upload_split(client, message, file_path, user_mention,
                              task_info=None, batch_info=None,
                              start_time=None, user_id=None, target_chat=None):
    """
    Universal 2GB-split-aware upload wrapper for Telegram.
    - If file_path > 2000 MB, split it into Telegram-safe parts first.
    - Forwards each part to upload_file with proper overall_current/overall_total
      so the progress UI accurately reflects the whole job.
    Returns True if every part uploaded successfully, False otherwise.
    """
    if not os.path.exists(file_path):
        return False

    upload_list = [file_path]
    if os.path.getsize(file_path) > 2000 * 1024 * 1024:
        try:
            await message.edit_text(
                f"✂️ <b>Splitting...</b>\n<code>{clean_html(os.path.basename(file_path))}</code>"
            )
        except Exception:
            pass
        parts, success = split_large_file(file_path)
        if success and parts:
            upload_list = parts
            with suppress(Exception):
                os.remove(file_path)
        # If splitting failed, we still try to upload the original (TG may reject).

    overall_total = sum(os.path.getsize(f) for f in upload_list if os.path.exists(f))
    if start_time is None:
        start_time = time.time()

    uploaded_so_far = 0
    all_ok = True
    for item in upload_list:
        if not os.path.exists(item):
            all_ok = False
            continue
        size = os.path.getsize(item)
        ok = await upload_file(
            client, message, item, user_mention,
            task_info=task_info, batch_info=batch_info,
            overall_current=uploaded_so_far, overall_total=overall_total,
            start_time=start_time, custom_name=None,
            user_id=user_id, target_chat=target_chat,
        )
        if ok is False:
            all_ok = False
        uploaded_so_far += size
    return all_ok


async def upload_file(client, message, file_path, user_mention, task_info=None, batch_info=None,
                      overall_current=0, overall_total=0, start_time=None, custom_name=None,
                      user_id=None, target_chat=None):
    """
    Upload file to target_chat. If target_chat is None, upload to message.chat.id (PM).
    Respects user settings: send_as (media/document), custom thumbnail.
    """
    try:
        if message.id in abort_dict:
            return False
        file_path = str(file_path)
        file_name = custom_name or os.path.basename(file_path)

        CONVERT_EXTS = ('.wvm', '.wmv', '.m4v', '.avi', '.f4v')
        if os.path.splitext(file_name)[1].lower() in CONVERT_EXTS:
            converted_path, success = await convert_to_streamable(file_path, message)
            if success:
                orig_stem = os.path.splitext(file_name)[0]
                file_path = converted_path
                new_name = orig_stem + '.mp4'
                named_path = os.path.join(os.path.dirname(file_path), new_name)
                try:
                    os.rename(file_path, named_path)
                    file_path = named_path
                    file_name = new_name
                except:
                    file_name = os.path.basename(file_path)

        # Get user settings
        uid = user_id or message.chat.id
        settings = await get_user_settings(uid)
        send_as = settings.get("send_as", "media")
        thumb_file_id = settings.get("thumbnail", None)

        thumb_path = None
        duration = 0
        VIDEO_EXTS = ('.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.m4v')
        AUDIO_EXTS = ('.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wav')
        is_video = file_name.lower().endswith(VIDEO_EXTS)
        is_audio = file_name.lower().endswith(AUDIO_EXTS)

        # Get duration via ffprobe only (yt-dlp must NOT be used on local paths)
        if is_video or is_audio:
            duration = await get_video_duration(file_path)

        # Determine thumbnail — priority order:
        #  1. yt-dlp native (_t.jpg or .jpg) — YouTube, Hanime etc. (avoids WEBP rejection)
        #  2. _web.jpg — bypass sites (HC, WH, Phub m3u8 streams)
        #  3. User's custom thumb from Telegram settings
        #  4. Auto-generated screenshot via ffmpeg (last resort)
        base_no_ext    = os.path.splitext(file_path)[0]
        ytdl_thumb     = f"{base_no_ext}_t.jpg"   # yt-dlp outtmpl thumbnail key pattern
        ytdl_thumb_alt = f"{base_no_ext}.jpg"      # flat / embedded-then-stripped thumbnail
        web_thumb      = f"{file_path}_web.jpg"    # manually downloaded for bypass streams

        if is_video or is_audio:
            if os.path.exists(ytdl_thumb) and os.path.getsize(ytdl_thumb) > 0:
                # Priority 1a: yt-dlp converted thumbnail (_t.jpg)
                thumb_path = ytdl_thumb
            elif os.path.exists(ytdl_thumb_alt) and os.path.getsize(ytdl_thumb_alt) > 0:
                # Priority 1b: flat yt-dlp thumbnail (.jpg)
                thumb_path = ytdl_thumb_alt
            elif os.path.exists(web_thumb) and os.path.getsize(web_thumb) > 0:
                # Priority 2: web thumbnail for bypass/stream downloads
                thumb_path = web_thumb
            elif thumb_file_id:
                # Priority 3: user's custom thumbnail from Telegram
                thumb_path = await download_thumb_from_file_id(client, thumb_file_id, uid)
            elif is_video:
                # Priority 4: auto-generate screenshot via ffmpeg
                thumb_path = await take_screenshot(file_path, duration)
        elif thumb_file_id:
            # Non-video: still allow user custom thumbnail (e.g. for documents)
            thumb_path = await download_thumb_from_file_id(client, thumb_file_id, uid)

        caption = clean_html(file_name)

        # Determine destination
        if target_chat is None:
            # No dump selected — send to PM
            dest_chat = message.chat.id
        else:
            dest_chat = target_chat

        current_total = overall_total if overall_total > 0 else os.path.getsize(file_path)
        file_size = os.path.getsize(file_path)

        async def progress_func(current, total):
            if file_size > 10 * 1024 * 1024:
                actual_current = overall_current + current
                await update_progress_ui(actual_current, current_total, message, start_time,
                                         "📤 Uploading...", filename=file_name, task_info=task_info, batch_info=batch_info)

        sent_msg = None
        try:
            if send_as == "document":
                # Send as document (with thumbnail if any)
                sent_msg = await client.send_document(
                    chat_id=dest_chat,
                    document=file_path,
                    caption=caption,
                    thumb=thumb_path,
                    progress=progress_func if file_size > 10 * 1024 * 1024 else None
                )
            else:
                # Send as media (video/document based on type)
                if is_video:
                    sent_msg = await client.send_video(
                        chat_id=dest_chat, video=file_path, caption=caption,
                        thumb=thumb_path, duration=duration, supports_streaming=True,
                        progress=progress_func if file_size > 10 * 1024 * 1024 else None
                    )
                else:
                    sent_msg = await client.send_document(
                        chat_id=dest_chat, document=file_path, caption=caption,
                        thumb=thumb_path,
                        progress=progress_func if file_size > 10 * 1024 * 1024 else None
                    )
        except Exception as e:
            try:
                await message.reply_text(f"❌ <b>Upload Error for {clean_html(file_name)}:</b>\n<code>{clean_html(str(e))}</code>")
            except:
                pass
            return False

        # Cleanup all thumbnail sidecar files
        if thumb_path and os.path.exists(thumb_path) and not thumb_file_id:
            with suppress(Exception): os.remove(thumb_path)

        for _t in (ytdl_thumb, ytdl_thumb_alt, web_thumb):
            if os.path.exists(_t) and _t != thumb_path:
                with suppress(Exception): os.remove(_t)

        # ─── Task 2: auto-delete uploaded message after AUTO_DELETE_SECONDS ───
        # Only auto-delete uploads that were sent into the chat where the user
        # commanded the bot. Files routed to a separate dump channel are kept.
        try:
            if sent_msg is not None and AUTO_DELETE_SECONDS > 0 and dest_chat == message.chat.id:
                async def _auto_del(_m):
                    try:
                        await asyncio.sleep(AUTO_DELETE_SECONDS)
                        await _m.delete()
                    except Exception:
                        pass
                asyncio.create_task(_auto_del(sent_msg))
        except Exception:
            pass

        return True
    except Exception as e:
        try:
            await message.reply_text(f"❌ <b>Upload Error:</b>\n<code>{clean_html(str(e))}</code>")
        except:
            pass
        return False

async def rclone_upload_file(message, file_path, task_info=None, batch_info=None):
    if message.id in abort_dict:
        return False
    if not os.path.exists("rclone.conf"):
        return await message.edit_text("❌ rclone.conf missing!")
    file_name = os.path.basename(file_path)
    cmd = ["rclone", "copy", file_path, RCLONE_PATH, "--config", "rclone.conf", "-P"]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    last_update = 0
    while True:
        if message.id in abort_dict:
            process.kill()
            return await message.edit_text("❌ Cancelled")
        line = await process.stdout.readline()
        if not line:
            break
        decoded = line.decode().strip()
        now = time.time()
        if "%" in decoded and (now - last_update) > 5:
            match = re.search(r"(\d+)%", decoded)
            if match:
                try:
                    await message.edit_text(f"☁️ <b>Cloud Upload</b>\n📂 {file_name}\n📊 {match.group(1)}% Done")
                except:
                    pass
                last_update = now
    await process.wait()
    return True

# ─────────────────────────────────────────
# Download logic (torrent / ytdl / direct)
# ─────────────────────────────────────────
async def download_logic(url, message, user_id, mode, task_info=None, format_id=None, rename=None, seed=False):
    try:
        file_path = None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive"
        }

        if mode == "leech" or mode == "leech_file" or (url and _is_torrent_link(url)):
            if not aria2:
                return "ERROR: Aria2 Not Connected. Please restart bot."

            tracker_list = [
                "http://tracker.opentrackr.org:1337/announce",
                "udp://tracker.opentrackr.org:1337/announce",
                "udp://open.tracker.cl:1337/announce",
                "udp://exodus.desync.com:6969/announce"
            ]
            options = {
                'bt-tracker': ",".join(tracker_list),
                'seed-time': '0'
            }

            if seed:
                options['seed-time'] = '525600'

            try:
                download = None
                if url and url.startswith("http"):
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, headers=headers) as resp:
                            if resp.status == 200:
                                torrent_bytes = await resp.read()
                                with open("task.torrent", "wb") as f:
                                    f.write(torrent_bytes)
                                download = aria2.add_torrent("task.torrent", options=options)
                            else:
                                return f"ERROR: HTTP {resp.status}"
                elif url and url.startswith("magnet:"):
                    download = aria2.add_magnet(url, options=options)
                elif mode == "leech_file":
                    download = aria2.add_torrent(url, options=options)
                else:
                    return "ERROR: Invalid Torrent Link"
            except Exception as e:
                return f"ERROR: Aria2 Add Failed: {e}"

            if download is None:
                return "ERROR: Failed to add torrent to aria2"

            await asyncio.sleep(2)
            try:
                download = aria2.get_download(download.gid)
            except Exception as e:
                return f"ERROR: {e}"

            meta_wait = 0
            while True:
                try:
                    download = aria2.get_download(download.gid)
                except Exception as e:
                    return f"ERROR: {e}"

                if message.id in abort_dict:
                    try:
                        aria2.remove([download.gid], force=True)
                    except:
                        pass
                    return "CANCELLED"

                if not download.is_metadata:
                    break

                if download.followed_by_ids:
                    try:
                        download = aria2.get_download(download.followed_by_ids[0])
                        break
                    except:
                        pass

                meta_wait += 2
                if meta_wait > 120:
                    return "ERROR: Metadata timeout"
                await asyncio.sleep(2)

            try:
                download = aria2.get_download(download.gid)
                if download.status in ["active", "waiting"]:
                    aria2.client.pause(download.gid)
                    await asyncio.sleep(1)
            except Exception:
                pass

            task_id = secrets.token_hex(4)
            try:
                file_list = [{"index": f.index, "name": os.path.basename(str(f.path)), "size": f.length}
                             for f in download.files]
            except Exception as e:
                return f"ERROR: Cannot read file list: {e}"

            pending_selections[task_id] = {
                "gid": download.gid,
                "files": file_list,
                "selected": None,
                "status": "waiting",
                "action": None
            }

            web_url = f"{BASE_URL}/?id={task_id}" if BASE_URL else f"http://YOUR_APP_URL/?id={task_id}"
            btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("🖥 Select Files (Web UI)", url=web_url)],
                [
                    InlineKeyboardButton("✅ Download All", callback_data=f"torrent_all_{task_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"torrent_cancel_{task_id}")
                ]
            ])
            await message.edit_text(
                f"⏸ <b>Torrent Paused!</b>\n"
                f"📂 <b>Files:</b> {len(file_list)}\n\n"
                f"Select files via Web UI, or choose below:",
                reply_markup=btn
            )

            timeout = 0
            while pending_selections[task_id]["status"] == "waiting":
                await asyncio.sleep(2)
                timeout += 2
                if message.id in abort_dict:
                    try:
                        aria2.client.remove(download.gid)
                    except:
                        pass
                    del pending_selections[task_id]
                    return "CANCELLED"
                if timeout > 600:
                    try:
                        aria2.client.remove(download.gid)
                    except:
                        pass
                    del pending_selections[task_id]
                    return "ERROR: Selection timeout"

            action = pending_selections[task_id].get("action")
            sel_idx = pending_selections[task_id].get("selected", [])
            del pending_selections[task_id]

            if action == "cancel":
                try:
                    aria2.client.remove(download.gid)
                except:
                    pass
                return "CANCELLED"

            try:
                current_download = aria2.get_download(download.gid)
                if action == "all" or not sel_idx:
                    all_indices = [str(f.index) for f in current_download.files]
                    try:
                        aria2.client.change_option(download.gid, {'select-file': ",".join(all_indices)})
                    except:
                        pass
                else:
                    try:
                        aria2.client.change_option(download.gid, {'select-file': ",".join(map(str, sel_idx))})
                    except Exception as e:
                        print(f"select-file warning: {e}")

                aria2.client.unpause(download.gid)
            except Exception as e:
                return f"ERROR: Resume failed: {e}"

            await message.edit_text(
                f"▶️ <b>Download Started!</b>\n"
                f"<b>Engine:</b> <code>aria2c {ARIA2C_VERSION}</code>"
            )

            gid = download.gid
            download_start_time = time.time()
            while True:
                if message.id in abort_dict:
                    try:
                        aria2.client.remove(gid)
                    except:
                        pass
                    return "CANCELLED"

                try:
                    status = aria2.get_download(gid)
                except Exception as e:
                    return f"ERROR: {e}"

                if status.status == "complete":
                    if seed:
                        seeding_gids[gid] = message
                        await message.edit_text(
                            f"✅ <b>Download Complete! Now Seeding... 🌱</b>\n"
                            f"<b>GID:</b> <code>{gid}</code>\n"
                            f"Use /stopseed {gid} to stop seeding."
                        )
                    selected_paths = []
                    for f in status.files:
                        try:
                            if f.selected and os.path.exists(str(f.path)):
                                selected_paths.append(str(f.path))
                        except:
                            pass
                    if not selected_paths:
                        for f in status.files:
                            try:
                                if os.path.exists(str(f.path)):
                                    selected_paths.append(str(f.path))
                            except:
                                pass

                    if len(selected_paths) > 1:
                        return selected_paths
                    elif len(selected_paths) == 1:
                        return str(selected_paths[0])
                    else:
                        return "ERROR: No downloaded files found"

                elif status.status == "error":
                    return f"ERROR: Aria2 Failed - {status.error_message}"

                try:
                    await update_progress_ui(
                        int(status.completed_length), int(status.total_length),
                        message, download_start_time, "🌀 Torrent Downloading...",
                        status.name, task_info
                    )
                except:
                    pass
                await asyncio.sleep(2)

        # ── YT-DLP (WZML-X style: all sites, not just YouTube) ──
        if mode == "ytdl" or (mode == "auto" and any(x in (url or "") for x in ["youtube.com","youtu.be","youtu","twitch","dailymotion.com","dmcdn.net","vimeo","hanime","hentaicity.com","pornhub.com","facebook","instagram","twitter","x.com"])):
            os.makedirs("downloads", exist_ok=True)
            loop = asyncio.get_running_loop()
            start_dl = time.time()

            def _hook(d):
                if d["status"] != "downloading":
                    return
                total   = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                current = d.get("downloaded_bytes") or 0
                fname   = os.path.basename(d.get("filename") or "Video")
                if current > 0:
                    asyncio.run_coroutine_threadsafe(
                        update_progress_ui(current, total, message, start_dl,
                                           "📥 Downloading...", fname, task_info,
                                           engine="ytdlp"),
                        loop
                    )

            uid2     = secrets.token_hex(4)
            dl_dir   = os.path.join("downloads", uid2)
            os.makedirs(dl_dir, exist_ok=True)
            out_tmpl = os.path.join(dl_dir, "%(title).150s.%(ext)s")
            fmt      = format_id if format_id else "bv*+ba/b"

            _dl_user_proxy = (await get_user_proxy(user_id)) or PROXY_URL
            def _do_dl():
                return _blocking_download(url, fmt, out_tmpl, _hook, False, _dl_user_proxy)

            try:
                result = await asyncio.to_thread(_do_dl)
                if not result:
                    shutil.rmtree(dl_dir, ignore_errors=True)
                    return "ERROR: yt-dlp download failed. Check URL/cookies."
                fp = result["filepath"]
                if rename and os.path.exists(fp):
                    ext      = os.path.splitext(fp)[1]
                    new_path = os.path.join(dl_dir, rename + ext)
                    with suppress(Exception):
                        os.rename(fp, new_path)
                        fp = new_path
                return str(fp)
            except Exception as e:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return f"ERROR: {e}"

        # ── Direct HTTP ──
        if url and "magnet:" not in url and ".torrent" not in url.lower():
            connector = aiohttp.TCPConnector(limit=20, force_close=False, enable_cleanup_closed=True, ttl_dns_cache=300)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return f"ERROR: HTTP {resp.status}"
                    total = int(resp.headers.get("content-length", 0))
                    name = None
                    if "Content-Disposition" in resp.headers:
                        cd = resp.headers["Content-Disposition"]
                        if 'filename="' in cd:
                            name = cd.split('filename="')[1].split('"')[0]
                    if not name:
                        name = os.path.basename(str(url)).split("?")[0]
                    name = urllib.parse.unquote(name)
                    if "." not in name:
                        name += ".mp4"

                    if rename:
                        ext = os.path.splitext(name)[1]
                        name = rename + ext

                    os.makedirs("downloads", exist_ok=True)
                    file_path = os.path.join("downloads", name)
                    async with aiofiles.open(file_path, mode='wb') as f:
                        dl_size = 0
                        start = time.time()
                        async for chunk in resp.content.iter_chunked(512 * 1024):
                            if message.id in abort_dict:
                                return "CANCELLED"
                            await f.write(chunk)
                            dl_size += len(chunk)
                            await update_progress_ui(dl_size, total, message, start, "☁️ Downloading...", name, task_info)
            return str(file_path)

        return str(file_path) if file_path else "ERROR: Nothing to download"
    except Exception as e:
        return f"ERROR: {e}"

# ─────────────────────────────────────────
# Main process_task
# ─────────────────────────────────────────
async def process_task(client, message, url, mode="auto", upload_target="tg",
                       task_info=None, format_id=None, status_msg=None, rename=None, seed=False,
                       user_id=None):
    try:
        if status_msg:
            msg = status_msg
        else:
            if not message.from_user:
                msg = await message.edit_text("☁️ <b>Starting...</b>")
            else:
                msg = await message.reply_text("☁️ <b>Initializing...</b>")
    except:
        return

    try:
        uid = user_id or (message.from_user.id if message.from_user else message.chat.id)

        if OWNER_ID and message.from_user and message.from_user.id != OWNER_ID:
            try:
                user = message.from_user
                user_info = f"👤 <b>New Task from:</b>\n" \
                            f"• Name: {clean_html(user.first_name or '')} {clean_html(user.last_name or '')}\n" \
                            f"• Username: @{user.username or 'N/A'}\n" \
                            f"• ID: <code>{user.id}</code>\n" \
                            f"• Mode: <code>{mode}</code>\n" \
                            f"• URL: <code>{clean_html(str(url or 'Reply/File'))[:200]}</code>"
                await client.send_message(OWNER_ID, user_info)
            except:
                pass

        # Determine upload destination
        if upload_target == "tg":
            active_dump = await get_active_dump(uid)
            target_chat = active_dump["id"] if active_dump else None
            # If no dump, will send to PM (message.chat.id)
        else:
            target_chat = None

        # Bunkr mode
        if mode == "bunkr":
            downloaded_files, err = await download_bunkr(url, msg, task_info)
            if err or not downloaded_files:
                await msg.edit_text(f"❌ <b>Bunkr Failed:</b>\n<code>{clean_html(str(err or 'No files'))}</code>")
                return
            overall_total_size = sum(os.path.getsize(f) for f in downloaded_files if os.path.exists(f))
            uploaded_so_far = 0
            task_start_time = time.time()
            batch_name = f"Bunkr Album ({len(downloaded_files)} files)"
            for index, f in enumerate(downloaded_files, 1):
                if message.id in abort_dict:
                    break
                if not os.path.exists(f):
                    continue
                item_size = os.path.getsize(f)
                t_info = f"File {index}/{len(downloaded_files)}"
                await upload_file(client, msg, f, message.chat.title or "User",
                                  t_info, batch_name, uploaded_so_far, overall_total_size, task_start_time,
                                  user_id=uid, target_chat=target_chat)
                uploaded_so_far += item_size
                try:
                    os.remove(f)
                except:
                    pass
            try:
                bunkr_dir = os.path.dirname(downloaded_files[0]) if downloaded_files else None
                if bunkr_dir and os.path.isdir(bunkr_dir):
                    shutil.rmtree(bunkr_dir, ignore_errors=True)
            except:
                pass
            await msg.edit_text("✅ <b>Bunkr Download Complete!</b>")
            return

        # Download from TG reply
        if not url and message.reply_to_message:
            media = (message.reply_to_message.document or message.reply_to_message.video or
                     message.reply_to_message.audio or message.reply_to_message.photo)
            if not media:
                await msg.edit_text("❌ <b>No Media!</b>")
                return
            fname = getattr(media, 'file_name', None) or f"tg_file_{message.reply_to_message.id}"
            if mode == "leech_file":
                if not fname.lower().endswith(".torrent"):
                    await msg.edit_text("❌ Not a .torrent file!")
                    return
                file_path = await message.reply_to_message.download()
                file_path = await download_logic(file_path, msg, uid, mode, task_info, format_id, rename, seed)
            else:
                file_path = await message.reply_to_message.download(
                    progress=update_progress_ui,
                    progress_args=(msg, time.time(), "📥 Downloading from TG...", fname, task_info)
                )
        else:
            file_path = await download_logic(url, msg, uid, mode, task_info, format_id, rename, seed)

        if not file_path or str(file_path).startswith("ERROR") or file_path == "CANCELLED":
            await msg.edit_text(f"❌ Failed: {clean_html(str(file_path))}")
            return

        if rename and isinstance(file_path, str) and os.path.exists(file_path):
            ext = os.path.splitext(file_path)[1]
            new_path = os.path.join(os.path.dirname(file_path), rename + ext)
            try:
                os.rename(file_path, new_path)
                file_path = new_path
            except:
                pass

        # Task pin
        if upload_target == "tg" and target_chat:
            if isinstance(file_path, list):
                try:
                    batch_name = os.path.basename(os.path.commonpath(file_path))
                except:
                    batch_name = "Batch_Task"
            else:
                batch_name = os.path.basename(str(file_path))
            pin_text = f"📌 <b>Batch Task:</b>\n<code>{clean_html(urllib.parse.unquote(batch_name))}</code>"
            try:
                info_msg = await client.send_message(chat_id=target_chat, text=pin_text)
                await info_msg.pin(disable_notification=True)
            except Exception as e:
                print(f"Pinning Error: {e}")

        final_files = []
        if isinstance(file_path, list):
            final_files = file_path
        elif os.path.isdir(str(file_path)):
            for root, dirs, files in os.walk(str(file_path)):
                for file in files:
                    full_p = os.path.join(root, file)
                    if os.path.getsize(full_p) > 0:
                        final_files.append(full_p)
            try:
                final_files.sort(key=natural_sort_key)
            except:
                final_files.sort()
        else:
            final_files = [str(file_path)]

        if len(final_files) == 0:
            await msg.edit_text("❌ <b>Error:</b> No files found to upload.")
            return

        if mode == "compress" and isinstance(file_path, str) and str(file_path).lower().endswith(('.mp4', '.mkv', '.webm', '.avi')):
            compressed_path, success = await compress_video(str(file_path), msg)
            if success:
                os.remove(file_path)
                final_files = [compressed_path]
        elif mode == "zip":
            await msg.edit_text("🤐 <b>Zipping...</b>")
            zip_path, success = create_archive(str(file_path))
            if success:
                os.remove(file_path)
                final_files = [zip_path]
        elif mode == "auto" and isinstance(file_path, str) and str(file_path).lower().endswith(('.zip', '.rar', '.7z', '.tar', '.gz')):
            await msg.edit_text("📦 <b>Extracting...</b>")
            extracted, temp_dir, err = extract_archive(file_path)
            if not err and extracted:
                final_files = extracted
                os.remove(file_path)

        overall_total_size = sum(os.path.getsize(f) for f in final_files)
        uploaded_so_far = 0
        task_start_time = time.time()
        batch_name = os.path.basename(str(file_path)) if not isinstance(file_path, list) else "Batch"

        for index, f in enumerate(final_files):
            upload_list = [f]
            if upload_target == "tg" and os.path.getsize(f) > 2000 * 1024 * 1024:
                await msg.edit_text(f"✂️ <b>Splitting...</b>\n{os.path.basename(f)}")
                parts, success = split_large_file(f)
                if success:
                    upload_list = parts
                    os.remove(f)

            for item in upload_list:
                item_size = os.path.getsize(item)
                up_name = rename if (rename and len(final_files) == 1 and len(upload_list) == 1) else None
                if upload_target == "rclone":
                    await rclone_upload_file(msg, item, task_info, batch_name)
                else:
                    await upload_file(client, msg, item, message.chat.title or "User",
                                      task_info, batch_name, uploaded_so_far, overall_total_size,
                                      task_start_time, custom_name=up_name, user_id=uid, target_chat=target_chat)
                uploaded_so_far += item_size

            if len(upload_list) > 1:
                shutil.rmtree(os.path.dirname(upload_list[0]), ignore_errors=True)

        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)
        if isinstance(file_path, list):
            try:
                base_dir = os.path.commonpath(file_path)
                if os.path.isdir(base_dir):
                    shutil.rmtree(base_dir, ignore_errors=True)
            except:
                pass
        elif os.path.exists(str(file_path)) and str(file_path) not in final_files:
            if os.path.isdir(str(file_path)):
                shutil.rmtree(str(file_path), ignore_errors=True)
            else:
                try:
                    os.remove(str(file_path))
                except:
                    pass
        for f in final_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass

        # Notify where file was sent
        if target_chat:
            dest_info = f"<b>Sent to:</b> dump channel"
        else:
            dest_info = f"<b>Sent to:</b> your PM (no dump set)"

        await msg.edit_text(
            f"✅ <b>Task Completed!</b>\n"
            f"{dest_info}\n"
            f"<b>Engine:</b> <code>aria2c {ARIA2C_VERSION}</code> | <code>pyrofork {PYROGRAM_VERSION}</code>"
        )
    except Exception as e:
        traceback.print_exc()
        await msg.edit_text(f"⚠️ <b>Error:</b> <code>{clean_html(str(e))}</code>")

# ─────────────────────────────────────────
# Command Handlers
# ─────────────────────────────────────────

@app.on_message(filters.command("setdump"))
async def set_dump_info(c, m):
    await m.reply_text("👋 <b>To Add a Dump:</b>\n1. Make me ADMIN in Channel.\n2. Forward a message from it here in PM.")

@app.on_message(filters.forwarded & filters.private)
async def dump_handler(c, m):
    if m.from_user and m.from_user.id in waiting_for_thumbnail:
        return
    if m.forward_from_chat:
        chat_id, title = m.forward_from_chat.id, m.forward_from_chat.title
        try:
            me = await c.get_chat_member(chat_id, "me")
            if me.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
                return await m.reply_text("❌ I am not Admin!")
        except:
            return await m.reply_text("❌ Cannot access channel!")
        await add_dump(m.chat.id, chat_id, title)
        await m.reply_text(f"✅ <b>Dump Added:</b> {title}")

@app.on_message(filters.command(["dumps", "settings"]))
async def list_dumps(c, m):
    dumps = await get_user_dumps(m.chat.id)
    if not dumps:
        return await m.reply_text("❌ No Dumps found! Forward a channel message to add one.")
    active = await get_active_dump(m.chat.id)
    active_id = active["id"] if active else None
    buttons = []
    for d in dumps:
        mark = "✅" if d["id"] == active_id else ""
        buttons.append([InlineKeyboardButton(f"{mark} {d['title']}", callback_data=f"setdump_{d['id']}")])
        buttons.append([InlineKeyboardButton("🗑 Delete", callback_data=f"deldump_{d['id']}")])
    await m.reply_text("⚙️ <b>Your Dumps</b>", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex(r"setdump_"))
async def set_active_cb(c, cb):
    chat_id = int(cb.data.split("_")[1])
    await set_active_dump(cb.message.chat.id, chat_id)
    await cb.answer("Active Dump Updated!")
    await list_dumps(c, cb.message)

@app.on_callback_query(filters.regex(r"deldump_"))
async def del_dump_cb(c, cb):
    chat_id = int(cb.data.split("_")[1])
    await delete_dump(cb.message.chat.id, chat_id)
    await cb.answer("Deleted!")
    await list_dumps(c, cb.message)

# ─────────────────────────────────────────
# /usersettings command
# ─────────────────────────────────────────
async def show_user_settings_menu(c, message, user_id, edit=False):
    settings = await get_user_settings(user_id)
    send_as = settings.get("send_as", "media")
    thumbnail = settings.get("thumbnail", None)
    user_proxy = await get_user_proxy(user_id)

    send_as_label = "📹 Media (Video/Doc)" if send_as == "media" else "📄 Document"
    send_as_toggle = "document" if send_as == "media" else "media"

    thumb_label = "🖼 Thumbnail: Set ✅" if thumbnail else "🖼 Thumbnail: Not Set ❌"
    proxy_label = "🌐 Proxy: Set ✅" if user_proxy else "🌐 Proxy: Not Set ❌"

    buttons = [
        [InlineKeyboardButton(f"Mode: {send_as_label}", callback_data=f"us_sendas_{send_as_toggle}_{user_id}")],
        [InlineKeyboardButton(thumb_label, callback_data=f"us_thumb_info_{user_id}")],
        [InlineKeyboardButton("📤 Set Thumbnail", callback_data=f"us_setthumb_{user_id}"),
         InlineKeyboardButton("🗑 Clear Thumbnail", callback_data=f"us_clrthumb_{user_id}")],
        [InlineKeyboardButton(proxy_label, callback_data=f"us_proxy_info_{user_id}")],
        [InlineKeyboardButton("📤 Set Proxy", callback_data=f"us_proxy_set_{user_id}"),
         InlineKeyboardButton("🗑 Clear Proxy", callback_data=f"us_proxy_clear_{user_id}")],
        [InlineKeyboardButton("🪞 Mirror Configs", callback_data=f"us_mirror_{user_id}")],
        [InlineKeyboardButton("❌ Close", callback_data=f"us_close_{user_id}")]
    ]

    text = (
        "⚙️ <b>User Settings</b>\n\n"
        f"<b>Send Mode:</b> {send_as_label}\n"
        f"<b>Thumbnail:</b> {'Custom thumbnail set ✅' if thumbnail else 'Not set (auto-generated for videos) ❌'}\n"
        f"<b>Proxy:</b> {'<code>' + clean_html(user_proxy) + '</code>' if user_proxy else 'Not set (uses bot default) ❌'}\n\n"
        "<i>• Set thumbnail once — it will auto-apply to all your /dl, /ytdl, /leech, /mdl downloads.\n"
        "• Proxy: <code>http://user:pass@host:port</code> — used for your yt-dlp / scraper downloads.\n"
        "• Use 🪞 Mirror Configs to upload your rclone.conf / Google Drive token.</i>"
    )

    markup = InlineKeyboardMarkup(buttons)
    if edit:
        try:
            await message.edit_text(text, reply_markup=markup)
        except:
            await message.reply_text(text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)

@app.on_message(filters.command("usersettings"))
async def user_settings_cmd(c, m):
    await show_user_settings_menu(c, m, m.from_user.id)

@app.on_callback_query(filters.regex(r"^us_sendas_"))
async def us_sendas_cb(c, cb):
    parts = cb.data.split("_")
    new_mode = parts[2]
    user_id = int(parts[3])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    await set_user_setting(user_id, "send_as", new_mode)
    mode_text = "Media (Video/Doc)" if new_mode == "media" else "Document"
    await cb.answer(f"✅ Mode set to: {mode_text}")
    await show_user_settings_menu(c, cb.message, user_id, edit=True)

@app.on_callback_query(filters.regex(r"^us_thumb_info_"))
async def us_thumb_info_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    settings = await get_user_settings(user_id)
    thumb = settings.get("thumbnail", None)
    if thumb:
        await cb.answer("Thumbnail is set. Use 'Clear Thumbnail' to remove it.", show_alert=True)
    else:
        await cb.answer("No thumbnail set. Send a photo to set one.", show_alert=True)

@app.on_callback_query(filters.regex(r"^us_setthumb_"))
async def us_setthumb_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    waiting_for_thumbnail[user_id] = True
    await cb.answer("📸 Send a photo now to set as thumbnail.", show_alert=True)
    try:
        await cb.message.edit_text(
            "📸 <b>Send a photo to set as your custom thumbnail.</b>\n\n"
            "This thumbnail will auto-apply to all your future downloads.\n"
            "Send /cancthumb to cancel."
        )
    except:
        pass

@app.on_callback_query(filters.regex(r"^us_clrthumb_"))
async def us_clrthumb_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    await clear_user_thumbnail(user_id)
    await cb.answer("🗑 Thumbnail cleared!")
    await show_user_settings_menu(c, cb.message, user_id, edit=True)

@app.on_callback_query(filters.regex(r"^us_close_"))
async def us_close_cb(c, cb):
    await cb.message.delete()
    await cb.answer()


# ─── Per-user proxy (Task 5) ───
@app.on_callback_query(filters.regex(r"^us_proxy_info_"))
async def us_proxy_info_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    p = await get_user_proxy(user_id)
    if p:
        await cb.answer(f"Proxy set: {p[:60]}", show_alert=True)
    else:
        await cb.answer("No proxy set. Tap 'Set Proxy' to add one.", show_alert=True)


@app.on_callback_query(filters.regex(r"^us_proxy_set_"))
async def us_proxy_set_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    waiting_for_proxy[user_id] = True
    await cb.answer("📩 Send your proxy URL now.", show_alert=True)
    with suppress(Exception):
        await cb.message.edit_text(
            "🌐 <b>Send your proxy URL now.</b>\n\n"
            "Format: <code>http://user:pass@host:port</code>\n"
            "or <code>socks5://user:pass@host:port</code>\n\n"
            "Send /cancproxy to cancel."
        )


@app.on_callback_query(filters.regex(r"^us_proxy_clear_"))
async def us_proxy_clear_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    await clear_user_proxy(user_id)
    await cb.answer("🗑 Proxy cleared!")
    await show_user_settings_menu(c, cb.message, user_id, edit=True)


@app.on_message(filters.command("cancproxy"))
async def cancproxy_cmd(c, m):
    if not m.from_user:
        return
    waiting_for_proxy.pop(m.from_user.id, None)
    await m.reply_text("✅ Proxy input cancelled.")


# ─── Mirror Configs submenu ───
def _mirror_configs_kb(user_id):
    has_rcl = _has_user_config(user_id, "rclone")
    has_gd  = _has_user_config(user_id, "gdrive")
    rcl_lbl = "🗂 rclone.conf: ✅" if has_rcl else "🗂 rclone.conf: ❌"
    gd_lbl  = "🔑 token.pickle: ✅" if has_gd  else "🔑 token.pickle: ❌"
    rows = [
        [InlineKeyboardButton(rcl_lbl, callback_data=f"us_mc_info_rclone_{user_id}")],
        [InlineKeyboardButton("📤 Upload rclone.conf", callback_data=f"us_mc_upload_rclone_{user_id}"),
         InlineKeyboardButton("🗑 Clear", callback_data=f"us_mc_clear_rclone_{user_id}")],
        [InlineKeyboardButton(gd_lbl, callback_data=f"us_mc_info_gdrive_{user_id}")],
        [InlineKeyboardButton("📤 Upload token.pickle", callback_data=f"us_mc_upload_gdrive_{user_id}"),
         InlineKeyboardButton("🗑 Clear", callback_data=f"us_mc_clear_gdrive_{user_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"us_mc_back_{user_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def _mirror_configs_text(user_id):
    has_rcl = _has_user_config(user_id, "rclone")
    has_gd  = _has_user_config(user_id, "gdrive")
    return (
        "🪞 <b>Mirror Configs</b>\n\n"
        f"<b>rclone.conf:</b> {'Set ✅' if has_rcl else 'Not set ❌'}\n"
        f"<b>token.pickle:</b> {'Set ✅' if has_gd else 'Not set ❌'}\n\n"
        "<i>Upload your config files here so /mirror -up rcl and "
        "Google Drive operations can use your own credentials.</i>"
    )


@app.on_callback_query(filters.regex(r"^us_mirror_"))
async def us_mirror_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    with suppress(Exception):
        await cb.message.edit_text(
            _mirror_configs_text(user_id),
            reply_markup=_mirror_configs_kb(user_id),
        )
    await cb.answer()


@app.on_callback_query(filters.regex(r"^us_mc_back_"))
async def us_mc_back_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    await cb.answer()
    await show_user_settings_menu(c, cb.message, user_id, edit=True)


@app.on_callback_query(filters.regex(r"^us_mc_info_"))
async def us_mc_info_cb(c, cb):
    parts = cb.data.split("_")
    kind = parts[3]               # 'rclone' or 'gdrive'
    user_id = int(parts[4])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    if _has_user_config(user_id, kind):
        await cb.answer(f"{kind} config is set. Use 🗑 Clear to remove.", show_alert=True)
    else:
        await cb.answer(f"No {kind} config uploaded yet.", show_alert=True)


@app.on_callback_query(filters.regex(r"^us_mc_upload_"))
async def us_mc_upload_cb(c, cb):
    parts = cb.data.split("_")
    kind = parts[3]               # 'rclone' or 'gdrive'
    user_id = int(parts[4])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    waiting_for_config_upload[user_id] = kind
    fname = "rclone.conf" if kind == "rclone" else "token.pickle"
    await cb.answer(f"📎 Send the {fname} file as a document.", show_alert=True)
    with suppress(Exception):
        await cb.message.edit_text(
            f"📎 <b>Send your <code>{fname}</code></b> as a document now.\n\n"
            f"Send /canccfg to cancel."
        )


@app.on_callback_query(filters.regex(r"^us_mc_clear_"))
async def us_mc_clear_cb(c, cb):
    parts = cb.data.split("_")
    kind = parts[3]
    user_id = int(parts[4])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    p = _user_config_path(user_id, kind)
    if os.path.exists(p):
        with suppress(Exception):
            os.remove(p)
        await cb.answer(f"🗑 {kind} config cleared!")
    else:
        await cb.answer("Nothing to clear.")
    with suppress(Exception):
        await cb.message.edit_text(
            _mirror_configs_text(user_id),
            reply_markup=_mirror_configs_kb(user_id),
        )


@app.on_message(filters.command("canccfg") & filters.private)
async def cancel_cfg_upload_cmd(c, m):
    if waiting_for_config_upload.pop(m.from_user.id, None):
        await m.reply_text("❌ Config upload cancelled.")
    else:
        await m.reply_text("Nothing to cancel.")


@app.on_message(filters.private & filters.document, group=-2)
async def config_doc_capture(c, m):
    """Capture a document upload only when the user is in mirror-config flow.
    Stays high-priority + non-stopping for everyone else."""
    if not m.from_user:
        return
    uid = m.from_user.id
    kind = waiting_for_config_upload.get(uid)
    if not kind:
        return  # not in our flow — let other handlers process
    waiting_for_config_upload.pop(uid, None)

    expected = "rclone.conf" if kind == "rclone" else "token.pickle"
    fname = (m.document.file_name or "").lower()
    if expected not in fname:
        # Soft-warn but still accept the file (user may rename).
        await m.reply_text(
            f"⚠️ File doesn't look like <code>{expected}</code>. Saving anyway."
        )

    dest = _user_config_path(uid, kind)
    try:
        await m.download(file_name=dest)
        await m.reply_text(
            f"✅ <b>{expected} saved!</b>\n\n"
            f"Stored at <code>{dest}</code>.\n"
            f"Open /usersettings → 🪞 Mirror Configs to manage it."
        )
    except Exception as e:
        await m.reply_text(f"❌ Failed to save: <code>{clean_html(str(e))}</code>")
    m.stop_propagation()


# Handle photo sent for thumbnail
@app.on_message(filters.private & filters.photo)
async def photo_thumbnail_handler(c, m):
    user_id = m.from_user.id
    if user_id not in waiting_for_thumbnail:
        return
    del waiting_for_thumbnail[user_id]
    photo = m.photo
    file_id = photo.file_id
    await set_user_thumbnail(user_id, file_id)
    await m.reply_text(
        "✅ <b>Thumbnail saved!</b>\n\n"
        "This will be automatically applied to all your future downloads (/dl, /ytdl, /leech, /mdl).\n"
        "You can change it anytime via /usersettings."
    )

@app.on_message(filters.command("cancthumb") & filters.private)
async def cancel_thumb_cmd(c, m):
    user_id = m.from_user.id
    if user_id in waiting_for_thumbnail:
        del waiting_for_thumbnail[user_id]
        await m.reply_text("❌ Thumbnail setting cancelled.")
    else:
        await m.reply_text("Nothing to cancel.")

# ─────────────────────────────────────────
# /login command (Mega)
# ─────────────────────────────────────────
@app.on_message(filters.command("login") & filters.private)
async def login_cmd(c, m):
    args = m.text.split(None, 2)
    if len(args) < 3:
        return await m.reply_text(
            "❌ <b>Usage:</b> <code>/login email password</code>\n\n"
            "This logs you into your Mega account via MegaCMD.\n"
            "Required for /mdl to download from your Mega."
        )
    email = args[1].strip()
    password = args[2].strip()

    # Delete the message for security
    try:
        await m.delete()
    except:
        pass

    msg = await c.send_message(m.chat.id, "🔐 <b>Logging into Mega...</b>")
    success, result = await mega_login(email, password)
    if success:
        await msg.edit_text(
            f"✅ <b>Logged into Mega!</b>\n"
            f"<code>{clean_html(result)}</code>\n\n"
            f"You can now use /mdl to download from Mega."
        )
    else:
        await msg.edit_text(
            f"❌ <b>Mega Login Failed!</b>\n"
            f"<code>{clean_html(result)}</code>"
        )

# ─────────────────────────────────────────
# Mega CLI helpers (logout / whoami)
# ─────────────────────────────────────────
async def mega_logout():
    try:
        subprocess.run(["mega-logout"], capture_output=True, timeout=30)
    except:
        pass

async def mega_whoami():
    try:
        r = subprocess.run(["mega-whoami"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except:
        return None

@app.on_message(filters.command("logout") & filters.private)
async def logout_cmd(c, m):
    msg = await m.reply_text("🔐 <b>Logging out from Mega...</b>")
    await mega_logout()
    await msg.edit_text("✅ <b>Logged out from Mega!</b>")

@app.on_message(filters.command("megainfo") & filters.private)
async def megainfo_cmd(c, m):
    whoami = await mega_whoami()
    if whoami:
        await m.reply_text(f"👤 <b>Mega Account:</b>\n<code>{clean_html(whoami)}</code>")
    else:
        await m.reply_text("❌ <b>Not logged into Mega.</b>\nUse /login email password to login.")

# ─────────────────────────────────────────
# /mdl command (Mega Download)
# ─────────────────────────────────────────
@app.on_message(filters.command("mdl"))
async def mega_dl_handler(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned from using this bot.")
    if not await _enforce_limits(m, "mdl"):
        return
    if len(m.command) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/mdl https://mega.nz/file/XXXXX</code>\n"
            "<code>/mdl https://mega.nz/folder/XXXXX</code>\n\n"
            "📌 <b>Note:</b> Login first with /login for private files."
        )

    url = m.text.split(None, 1)[1].strip()

    if not ("mega.nz" in url or "mega.co.nz" in url):
        return await m.reply_text("❌ <b>Invalid URL!</b> Only Mega links are supported.")

    uid = m.from_user.id
    # Seedha Initializing message (jaise /dl me hota hai)
    msg = await m.reply_text("☁️ <b>Initializing...</b>")

    async def _do_mega_download():
        try:
            download_dir = os.path.join("downloads", f"mega_{int(time.time())}")

            files, err = await mega_download(url, download_dir, msg)

            if err:
                await msg.edit_text(f"❌ <b>Mega Download Failed:</b>\n<code>{clean_html(err)}</code>")
                return
            if not files:
                await msg.edit_text("❌ <b>No files downloaded from Mega!</b>")
                return

            active_dump = await get_active_dump(uid)
            target_chat = active_dump["id"] if active_dump else None

            overall_total_size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
            uploaded_so_far = 0
            task_start_time = time.time()

            for index, f in enumerate(files, 1):
                if not os.path.exists(f):
                    continue
                item_size = os.path.getsize(f)

                upload_list = [f]
                if os.path.getsize(f) > 2000 * 1024 * 1024:
                    parts, success = split_large_file(f)
                    if success:
                        upload_list = parts
                        os.remove(f)

                for item in upload_list:
                    await upload_file(
                        c, msg, item, m.chat.title or "User",
                        f"File {index}/{len(files)}", None,
                        uploaded_so_far, overall_total_size, task_start_time,
                        user_id=uid, target_chat=target_chat
                    )
                    uploaded_so_far += os.path.getsize(item) if os.path.exists(item) else item_size

                try:
                    if os.path.exists(f):
                        os.remove(f)
                except:
                    pass

            # Cleanup
            shutil.rmtree(download_dir, ignore_errors=True)

            dest_info = "dump channel" if target_chat else "your PM"
            await msg.edit_text(
                f"✅ <b>Mega Download & Upload Complete!</b>\n"
                f"📤 <b>Sent to:</b> {dest_info}"
            )
        except Exception as e:
            traceback.print_exc()
            await msg.edit_text(f"⚠️ <b>Error:</b> <code>{clean_html(str(e))}</code>")

    asyncio.create_task(_do_mega_download())

# ═══════════════════════════════════════════
# yt-dlp WZML-X Options
# ═══════════════════════════════════════════
def _base_opts(user_proxy: str | None = None):
    o = {
        "usenetrc": True,
        "allow_multiple_video_streams": True,
        "allow_multiple_audio_streams": True,
        "noprogress": True,
        "overwrites": True,
        "writethumbnail": True,
        "trim_file_name": 200,
        "fragment_retries": 10,
        "retries": 10,
        "socket_timeout": 30,
        "nocheckcertificate": True,
        "retry_sleep_functions": {
            "http": lambda n: 3, "fragment": lambda n: 3,
            "file_access": lambda n: 3, "extractor": lambda n: 3,
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {"youtube": {"player_client": ["web","tv"], "skip": ["dash"]}},
        "remote_components": ["ejs:github"],
    }
    if COOKIES_FILE: o["cookiefile"] = COOKIES_FILE
    # User proxy (Task 5) takes precedence over env PROXY_URL
    _p = user_proxy or PROXY_URL
    if _p: o["proxy"] = _p
    return o

def _info_opts(user_proxy: str | None = None):
    o = _base_opts(user_proxy)
    o.update({"quiet": True, "no_warnings": True, "playlist_items": "0", "format": "bv*+ba/b"})
    return o

def _dl_opts(fmt, out_tmpl, progress_hook=None, is_pl=False, user_proxy: str | None = None):
    o = _base_opts(user_proxy)
    o["outtmpl"] = {"default": out_tmpl, "thumbnail": out_tmpl.replace(".%(ext)s","_t.%(ext)s")}
    o["postprocessors"] = [{"add_chapters":True,"add_infojson":"if_exists","add_metadata":True,"key":"FFmpegMetadata"}]
    if progress_hook: o["progress_hooks"] = [progress_hook]
    if is_pl:         o["ignoreerrors"]   = True
    is_audio = fmt.startswith("ba/b") or fmt in ("mp3",)
    if is_audio:
        parts = fmt.split("-") if "-" in fmt else ["ba/b","mp3","192"]
        afmt  = parts[1] if len(parts) > 1 else "mp3"
        arate = parts[2] if len(parts) > 2 else "192"
        o["format"] = "ba/b"
        o["postprocessors"].append({"key":"FFmpegExtractAudio","preferredcodec":afmt,"preferredquality":arate})
    else:
        o["format"] = fmt
    o["postprocessors"].append({"format":"jpg","key":"FFmpegThumbnailsConvertor","when":"before_dl"})
    ext = ".mp3" if is_audio else ".mp4"
    if ext in [".mp3",".mkv",".mka",".ogg",".opus",".flac",".m4a",".mp4",".mov",".m4v"]:
        o["postprocessors"].append({"already_have_thumbnail": True, "key": "EmbedThumbnail"})
    return o

# ═══════════════════════════════════════════
# WZML-X FORMAT PARSER (ALL sites)
# ═══════════════════════════════════════════
def parse_formats(result):
    """
    Parse yt-dlp formats for ANY site.
    Pre-muxed formats (e.g. Hanime) shown as video quality options, NOT forced to audio.
    """
    if "entries" in result:
        fmts = {}
        for h in ["144","240","360","480","720","1080","1440","2160"]:
            fmts[f"{h}|mp4"]  = f"bv*[height<=?{h}][ext=mp4]+ba[ext=m4a]/b[height<=?{h}]"
            fmts[f"{h}|webm"] = f"bv*[height<=?{h}][ext=webm]+ba/b[height<=?{h}]"
        return fmts, True

    fmts   = {}
    is_m4a = False
    for item in result.get("formats", []):
        fid    = item["format_id"]
        size   = item.get("filesize") or item.get("filesize_approx") or 0
        vcodec = (item.get("vcodec") or "none")
        acodec = (item.get("acodec") or "none")
        height = item.get("height")
        ext    = item.get("ext", "")
        # Pure audio stream
        if vcodec == "none" and acodec != "none":
            if item.get("audio_ext") == "m4a": is_m4a = True
            b_name = f"{item.get('acodec') or fid}-{ext}"
            tbr_val = item.get("tbr") or item.get("height") or item.get("format_id") or 0
            fmts.setdefault(b_name, {})[str(tbr_val)] = [size, fid]
        # Video stream (including pre-muxed)
        elif height:
            fps    = item.get("fps") or ""
            b_name = f"{height}p{fps}-{ext}"
            if acodec != "none":
                v_fmt = fid   # pre-muxed: use directly
            else:
                ba_ext = "[ext=m4a]" if is_m4a and ext == "mp4" else ""
                v_fmt  = f"{fid}+ba{ba_ext}/b[height=?{height}]"
            tbr_val = item.get("tbr") or item.get("height") or item.get("format_id") or 0
            fmts.setdefault(b_name, {})[str(tbr_val)] = [size, v_fmt]
    return fmts, False

# ═══════════════════════════════════════════
# WZML-X KEYBOARDS
# ═══════════════════════════════════════════
def _kb_main(fmts, uid, is_pl):
    btns = []; row = []
    if is_pl:
        for key in ["144|mp4","240|mp4","360|mp4","480|mp4","720|mp4","1080|mp4","1440|mp4","2160|mp4"]:
            row.append(InlineKeyboardButton(f"{key.split('|')[0]}p-mp4", callback_data=f"q|{uid}|fmt|{key}"))
            if len(row) == 3: btns.append(row); row = []
        if row: btns.append(row); row = []
        for key in ["144|webm","240|webm","360|webm","480|webm","720|webm","1080|webm","1440|webm","2160|webm"]:
            row.append(InlineKeyboardButton(f"{key.split('|')[0]}p-webm", callback_data=f"q|{uid}|fmt|{key}"))
            if len(row) == 3: btns.append(row); row = []
        if row: btns.append(row)
        label = "📋 <b>Playlist Quality</b>"
    else:
        for b_name, tbr_dict in fmts.items():
            # Skip pure audio formats from main video keyboard
            if not any(c.isdigit() for c in b_name.split("-")[0]): continue
            if len(tbr_dict) == 1:
                tbr, vl = next(iter(tbr_dict.items()))
                sz_str  = f" ({humanbytes(vl[0])})" if vl[0] else ""
                row.append(InlineKeyboardButton(f"{b_name}{sz_str}", callback_data=f"q|{uid}|sub|{b_name}|{tbr}"))
            else:
                row.append(InlineKeyboardButton(b_name, callback_data=f"q|{uid}|dict|{b_name}"))
            if len(row) == 2: btns.append(row); row = []
        if row: btns.append(row)
        label = "🎬 <b>Select Quality:</b>"
    btns.append([
        InlineKeyboardButton("🎵 MP3",           callback_data=f"q|{uid}|mp3|"),
        InlineKeyboardButton("🎧 Audio Formats",  callback_data=f"q|{uid}|audiofmt|"),
    ])
    btns.append([
        InlineKeyboardButton("⭐ Best Video", callback_data=f"q|{uid}|fmt|bv*+ba/b"),
        InlineKeyboardButton("🔊 Best Audio", callback_data=f"q|{uid}|fmt|ba/b"),
    ])
    btns.append([InlineKeyboardButton("❌ Cancel", callback_data=f"q|{uid}|cancel|")])
    return label, InlineKeyboardMarkup(btns)

def _kb_sub(b_name, tbr_dict, uid):
    btns = []; row = []
    for tbr, vl in tbr_dict.items():
        sz_str = f" ({humanbytes(vl[0])})" if vl[0] else ""
        row.append(InlineKeyboardButton(f"{tbr}K{sz_str}", callback_data=f"q|{uid}|sub|{b_name}|{tbr}"))
        if len(row) == 2: btns.append(row); row = []
    if row: btns.append(row)
    btns.append([InlineKeyboardButton("◀️ Back", callback_data=f"q|{uid}|back|"), InlineKeyboardButton("❌ Cancel", callback_data=f"q|{uid}|cancel|")])
    return f"🎚️ <b>{b_name}</b> — select bitrate:", InlineKeyboardMarkup(btns)

def _kb_mp3(uid):
    return ("🎵 <b>MP3 Bitrate:</b>", InlineKeyboardMarkup([[
        InlineKeyboardButton("64K",  callback_data=f"q|{uid}|fmt|ba/b-mp3-64"),
        InlineKeyboardButton("128K", callback_data=f"q|{uid}|fmt|ba/b-mp3-128"),
        InlineKeyboardButton("192K", callback_data=f"q|{uid}|fmt|ba/b-mp3-192"),
        InlineKeyboardButton("320K", callback_data=f"q|{uid}|fmt|ba/b-mp3-320"),
    ],[InlineKeyboardButton("◀️ Back", callback_data=f"q|{uid}|back|"), InlineKeyboardButton("❌ Cancel", callback_data=f"q|{uid}|cancel|")]]))

def _kb_audiofmt(uid):
    btns = []; row = []
    for f in ["aac","alac","flac","m4a","mp3","opus","vorbis","wav"]:
        row.append(InlineKeyboardButton(f, callback_data=f"q|{uid}|audioq|ba/b-{f}-"))
        if len(row) == 4: btns.append(row); row = []
    if row: btns.append(row)
    btns.append([InlineKeyboardButton("◀️ Back", callback_data=f"q|{uid}|back|"), InlineKeyboardButton("❌ Cancel", callback_data=f"q|{uid}|cancel|")])
    return "🎧 <b>Audio Format:</b>", InlineKeyboardMarkup(btns)

def _kb_audioq(prefix, uid):
    btns = []; row = []
    for q in range(11):
        row.append(InlineKeyboardButton(str(q), callback_data=f"q|{uid}|fmt|{prefix}{q}"))
        if len(row) == 4: btns.append(row); row = []
    if row: btns.append(row)
    btns.append([InlineKeyboardButton("◀️ Back", callback_data=f"q|{uid}|audiofmt|"), InlineKeyboardButton("❌ Cancel", callback_data=f"q|{uid}|cancel|")])
    return "🎚️ <b>Quality</b> (0=best, 10=worst):", InlineKeyboardMarkup(btns)

def _blocking_info(url, user_proxy=None):
    try:
        with yt_dlp.YoutubeDL(_info_opts(user_proxy)) as ydl:
            r = ydl.extract_info(url, download=False)
            if r is None: raise ValueError("Info result is None")
            return r
    except Exception as e:
        print(f"[ytdlp info] {e}")
        return None

def _blocking_download(url, fmt, out_tmpl, progress_hook=None, is_pl=False, user_proxy=None):
    try:
        opts = _dl_opts(fmt, out_tmpl, progress_hook, is_pl, user_proxy)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info   = ydl.extract_info(url, download=True)
            if not info: return None
            actual = ydl.prepare_filename(info)
            if fmt.startswith("ba/b") or fmt in ("mp3",):
                parts = fmt.split("-") if "-" in fmt else ["ba/b","mp3","192"]
                afmt  = parts[1] if len(parts) > 1 else "mp3"
                ext   = "ogg" if afmt == "vorbis" else "m4a" if afmt == "alac" else afmt
                actual = os.path.splitext(actual)[0] + f".{ext}"
            if not os.path.exists(actual):
                dl_dir = os.path.dirname(actual) or "."
                base   = os.path.splitext(os.path.basename(actual))[0][:30]
                for f in os.listdir(dl_dir):
                    if base in f and not f.endswith((".jpg",".jpeg",".png",".webp",".part")):
                        actual = os.path.join(dl_dir, f); break
            if not os.path.exists(actual) or os.path.getsize(actual) == 0:
                return None
            return {"filepath": actual, "info": info, "duration": info.get("duration",0), "title": info.get("title","Video")}
    except Exception as e:
        print(f"[ytdlp dl] {e}")
        return None

# ═══════════════════════════════════════════
# WZML-X Quality Picker
# ═══════════════════════════════════════════
async def show_quality_picker(url, smsg, user_id=None, rename=None):
    _qp_proxy = (await get_user_proxy(user_id)) or PROXY_URL
    # ── HentaiCity bypass (same logic as Pornhub) ──
    if "hentaicity.com" in url:
        await smsg.edit_text("🔍 <b>Fetching HentaiCity formats...</b>")
        hc_data = await asyncio.to_thread(get_hc_data, url)
        if not hc_data or not hc_data.get("formats"):
            await smsg.edit_text(
                "❌ <b>HentaiCity fetch failed!</b>\n\n"
                "• Check if the URL is correct\n"
                "• Site may have changed — update hc.py"
            )
            return
        info = hc_data["original_info"]
        # Build a direct-URL lookup keyed by format_id for the callback engine
        # so _start_ytdl receives the real CDN URL, not the page URL
        hc_direct_map = {
            fmt["format_id"]: fmt["url"]
            for fmt in hc_data["formats"]
            if fmt.get("format_id") and fmt.get("url")
        }
        fmts, is_pl = parse_formats(info)
        uid   = secrets.token_hex(5)
        title = clean_html((info.get("title") or hc_data.get("title", "HentaiCity Video"))[:60])
        upl   = clean_html(info.get("uploader") or info.get("channel") or "HentaiCity")
        dur   = time_formatter(info.get("duration", 0))
        udate = info.get("upload_date", "")
        if udate:
            try:
                udate = datetime.strptime(udate, "%Y%m%d").strftime("%d %b %Y")
            except: pass
        ytdl_session[uid] = {
            "url": url,
            "info": info,
            "fmts": fmts,
            "is_pl": is_pl,
            "user_id": user_id,
            "rename": rename,
            "created": time.time(),
            # CRITICAL: Store direct CDN map so _start_ytdl uses real URL
            "hc_direct_map": hc_direct_map,
        }
        label, kb = _kb_main(fmts, uid, is_pl)
        info_txt = f"🎌 <b>{title}</b>\n👤 <code>{upl}</code>\n⏱ {dur}  📅 {udate}\n\n{label}"
        thumb_url = get_best_thumbnail(info)
        try:
            if thumb_url:
                await smsg.reply_photo(photo=thumb_url, caption=info_txt, reply_markup=kb)
                with suppress(Exception): await smsg.delete()
            else:
                await smsg.edit_text(info_txt, reply_markup=kb)
        except Exception:
            with suppress(Exception): await smsg.edit_text(info_txt, reply_markup=kb)
        return

    # ── Pornhub bypass ──
    if "pornhub.com" in url:
        await smsg.edit_text("🔍 <b>Fetching Pornhub formats...</b>")
        phub_data = await asyncio.to_thread(get_direct_info, url)
        if not phub_data or not phub_data.get("formats"):
            await smsg.edit_text(
                "❌ <b>Pornhub fetch failed!</b>\n\n"
                "• Check if the URL is correct\n"
                "• Site may have changed — update phub.py"
            )
            return
        info = phub_data["original_info"]
        phub_direct_map = {
            fmt["format_id"]: fmt["url"]
            for fmt in phub_data["formats"]
            if fmt.get("format_id") and fmt.get("url")
        }
        fmts, is_pl = parse_formats(info)
        uid   = secrets.token_hex(5)
        title = clean_html((info.get("title") or phub_data.get("title", "Video"))[:60])
        upl   = clean_html(info.get("uploader") or info.get("channel") or "Pornhub")
        dur   = time_formatter(info.get("duration", 0))
        udate = info.get("upload_date", "")
        if udate:
            try:
                udate = datetime.strptime(udate, "%Y%m%d").strftime("%d %b %Y")
            except: pass
        ytdl_session[uid] = {
            "url": url,
            "info": info,
            "fmts": fmts,
            "is_pl": is_pl,
            "user_id": user_id,
            "rename": rename,
            "created": time.time(),
            # CRITICAL: Store direct CDN map so _start_ytdl uses real URL
            "phub_direct_map": phub_direct_map,
        }
        label, kb = _kb_main(fmts, uid, is_pl)
        info_txt = f"🎬 <b>{title}</b>\n👤 <code>{upl}</code>\n⏱ {dur}  📅 {udate}\n\n{label}"
        thumb_url = get_best_thumbnail(info)
        try:
            if thumb_url:
                await smsg.reply_photo(photo=thumb_url, caption=info_txt, reply_markup=kb)
                with suppress(Exception): await smsg.delete()
            else:
                await smsg.edit_text(info_txt, reply_markup=kb)
        except Exception:
            with suppress(Exception): await smsg.edit_text(info_txt, reply_markup=kb)
        return

    # ── WatchHentai bypass (reuses hc_direct_map key for existing bypass logic) ──
    if "watchhentai.net" in url:
        await smsg.edit_text("🔍 <b>Fetching WatchHentai formats...</b>")
        wh_data = await asyncio.to_thread(get_wh_data, url, _qp_proxy)
        if not wh_data or not wh_data.get("formats"):
            await smsg.edit_text(
                "❌ <b>WatchHentai fetch failed!</b>\n\n"
                "• Check if the URL is correct\n"
                "• Site may have changed — update wh.py"
            )
            return
        info = wh_data["original_info"]
        # Build direct-URL map; stored as hc_direct_map to reuse existing bypass
        # download logic in _start_ytdl without any changes there
        wh_direct_map = {
            fmt["format_id"]: fmt["url"]
            for fmt in wh_data["formats"]
            if fmt.get("format_id") and fmt.get("url")
        }
        fmts, is_pl = parse_formats(info)
        uid   = secrets.token_hex(5)
        title = clean_html((info.get("title") or wh_data.get("title", "WatchHentai Video"))[:60])
        upl   = clean_html(info.get("uploader") or info.get("channel") or "WatchHentai")
        dur   = time_formatter(info.get("duration", 0))
        udate = info.get("upload_date", "")
        if udate:
            try:
                udate = datetime.strptime(udate, "%Y%m%d").strftime("%d %b %Y")
            except: pass
        ytdl_session[uid] = {
            "url":     url,
            "info":    info,
            "fmts":    fmts,
            "is_pl":   is_pl,
            "user_id": user_id,
            "rename":  rename,
            "created": time.time(),
            # CRITICAL: Use hc_direct_map key so _start_ytdl bypass logic picks it up natively
            "hc_direct_map": wh_direct_map,
        }
        label, kb = _kb_main(fmts, uid, is_pl)
        info_txt = f"🎌 <b>{title}</b>\n👤 <code>{upl}</code>\n⏱ {dur}  📅 {udate}\n\n{label}"
        thumb_url = get_best_thumbnail(info)
        try:
            if thumb_url:
                await smsg.reply_photo(photo=thumb_url, caption=info_txt, reply_markup=kb)
                with suppress(Exception): await smsg.delete()
            else:
                await smsg.edit_text(info_txt, reply_markup=kb)
        except Exception:
            with suppress(Exception): await smsg.edit_text(info_txt, reply_markup=kb)
        return

    # ── Standard yt-dlp path ──
    info = await asyncio.to_thread(_blocking_info, url, _qp_proxy)
    if not info:
        await smsg.edit_text(
            "❌ <b>Video info not found!</b>\n\n"
            "• Private/age-restricted?\n"
            "• Refresh cookies.txt\n"
            "• URL correct?"
        )
        return
    fmts, is_pl = parse_formats(info)
    uid   = secrets.token_hex(5)
    title = clean_html((info.get("title") or "")[:60])
    upl   = clean_html(info.get("uploader") or info.get("channel") or "")
    dur   = time_formatter(info.get("duration", 0))
    udate = info.get("upload_date", "")
    if udate:
        try:
            udate = datetime.strptime(udate, "%Y%m%d").strftime("%d %b %Y")
        except: pass
    ytdl_session[uid] = {
        "url": url, "info": info, "fmts": fmts, "is_pl": is_pl,
        "user_id": user_id, "rename": rename, "created": time.time(),
    }
    label, kb = _kb_main(fmts, uid, is_pl)
    info_txt = f"🎬 <b>{title}</b>\n👤 <code>{upl}</code>\n⏱ {dur}  📅 {udate}\n\n{label}"
    thumb_url = get_best_thumbnail(info)
    try:
        if thumb_url:
            await smsg.reply_photo(photo=thumb_url, caption=info_txt, reply_markup=kb)
            with suppress(Exception): await smsg.delete()
        else:
            await smsg.edit_text(info_txt, reply_markup=kb)
    except Exception:
        with suppress(Exception): await smsg.edit_text(info_txt, reply_markup=kb)

# ═══════════════════════════════════════════
# WZML-X Callback
# ═══════════════════════════════════════════
@app.on_callback_query(filters.regex(r"^q\|"))
async def quality_cb(c, cb):
    parts  = cb.data.split("|")
    uid    = parts[1]
    action = parts[2]
    extra  = parts[3] if len(parts) > 3 else ""
    e = ytdl_session.get(uid)
    if not e:
        await cb.answer("❌ Session expired, send link again", show_alert=True); return
    if e.get("user_id") and cb.from_user.id != e["user_id"]:
        await cb.answer("❌ Not your session!", show_alert=True); return
    await cb.answer()
    fmts  = e["fmts"]
    is_pl = e["is_pl"]
    if action == "cancel":
        ytdl_session.pop(uid, None)
        with suppress(Exception): await cb.message.delete()
        return
    elif action == "back":
        label, kb = _kb_main(fmts, uid, is_pl)
        with suppress(Exception): await cb.message.edit_text(label, reply_markup=kb)
        return
    elif action == "mp3":
        txt, kb = _kb_mp3(uid)
        with suppress(Exception): await cb.message.edit_text(txt, reply_markup=kb)
        return
    elif action == "audiofmt":
        txt, kb = _kb_audiofmt(uid)
        with suppress(Exception): await cb.message.edit_text(txt, reply_markup=kb)
        return
    elif action == "audioq":
        txt, kb = _kb_audioq(extra, uid)
        with suppress(Exception): await cb.message.edit_text(txt, reply_markup=kb)
        return
    elif action == "dict":
        tbr_dict = fmts.get(extra, {})
        txt, kb  = _kb_sub(extra, tbr_dict, uid)
        with suppress(Exception): await cb.message.edit_text(txt, reply_markup=kb)
        return
    elif action == "sub":
        b_name   = extra
        tbr      = parts[4] if len(parts) > 4 else ""
        tbr_dict = fmts.get(b_name, {})
        qual     = tbr_dict.get(tbr, [None, f"bestvideo+bestaudio/best"])[1] if tbr in tbr_dict else f"bestvideo+bestaudio/best"
        await _start_ytdl(uid, qual, e, cb.message, c); return
    elif action == "fmt":
        qual = extra
        if "|" in qual:
            h, container = qual.split("|", 1)
            if container == "webm": qual = f"bv*[height<=?{h}][ext=webm]+ba/b[height<=?{h}]"
            else:                   qual = f"bv*[height<=?{h}][ext=mp4]+ba[ext=m4a]/b[height<=?{h}]"
        elif qual in fmts:
            qual = fmts[qual]
        await _start_ytdl(uid, qual, e, cb.message, c); return

async def _start_ytdl(uid, fmt, session, msg, client):
    ytdl_session.pop(uid, None)
    # 🚨 FIX: Remove any hidden newlines causing Header Injection
    original_page_url = session["url"].replace('\n', '').replace('\r', '').strip()
    rename   = session.get("rename")
    user_id  = session.get("user_id")
    _user_proxy = (await get_user_proxy(user_id)) or PROXY_URL

    hc_direct_map   = session.get("hc_direct_map", {})
    phub_direct_map = session.get("phub_direct_map", {})
    is_bypass = bool(hc_direct_map or phub_direct_map)
    direct_map = hc_direct_map if hc_direct_map else phub_direct_map

     # Default logic
    target_url = original_page_url
    target_fmt = fmt

    if is_bypass:
        raw_fmt_id = fmt.split("+")[0].split("/")[0].strip()

        if phub_direct_map:
            # Pornhub ko yt-dlp natively support karta hai, so original URL hi denge
            target_url = original_page_url
            target_fmt = raw_fmt_id

        elif hc_direct_map:
            # HentaiCity ke liye yt-dlp ko direct .m3u8 link chahiye
            direct_url = direct_map.get(raw_fmt_id) or direct_map.get(fmt)
            if not direct_url:
                direct_url = next(iter(direct_map.values()), None)

            if not direct_url:
                return await msg.edit_text("❌ <b>Could not resolve stream URL!</b>")

            target_url = direct_url.replace('\n', '').replace('\r', '').strip()
            target_fmt = "best" # Kyunki .m3u8 already quality-specific hai

    await msg.edit_text(f"⏳ <b>Starting Download...</b>\n<code>{target_fmt[:60]}</code>")

    async def _run():
        try:
            loop = asyncio.get_running_loop()
            start_dl = time.time()
            def _hook(d):
                if d["status"] != "downloading": return
                total   = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                current = d.get("downloaded_bytes") or 0
                fname   = os.path.basename(d.get("filename") or "Video")
                if current > 0:
                    asyncio.run_coroutine_threadsafe(
                        update_progress_ui(current, total, msg, start_dl,
                                           "📥 Downloading...", fname, engine="ytdlp"), loop)
            os.makedirs("downloads", exist_ok=True)
            dl_dir   = os.path.join("downloads", secrets.token_hex(4))
            os.makedirs(dl_dir, exist_ok=True)
            raw_title = session.get("info", {}).get("title", "Video")
            clean_title = re.sub(r'[<>:"/\\|?*]', "_", raw_title).strip()[:100]
            out_tmpl = os.path.join(dl_dir, f"{clean_title}.%(ext)s")

            # 🔥 YAHAN aiohttp ki jagah yt-dlp use hoga
            result = await asyncio.to_thread(_blocking_download, target_url, target_fmt, out_tmpl, _hook, False, _user_proxy)

            if not result:
                await msg.edit_text("❌ <b>Download failed!</b>\n\n• Check cookies.txt\n• Try another quality")
                shutil.rmtree(dl_dir, ignore_errors=True); return

            fp = result["filepath"]

            if rename:
                ext      = os.path.splitext(fp)[1]
                new_path = os.path.join(dl_dir, rename + ext)
                with suppress(Exception): os.rename(fp, new_path); fp = new_path

            # ── Task 2a: Download official web thumbnail for m3u8/stream files ──
            thumb_url = session.get("info", {}).get("thumbnail")
            if thumb_url:
                try:
                    import requests as _req
                    _thumb_headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Referer": original_page_url
                    }
                    def _dl_thumb():
                        r = _req.get(thumb_url, headers=_thumb_headers, timeout=10)
                        if r.status_code == 200 and len(r.content) > 0:
                            with open(f"{fp}_web.jpg", "wb") as _f:
                                _f.write(r.content)
                    await asyncio.to_thread(_dl_thumb)
                except Exception as _te:
                    print(f"[thumb_dl] {_te}")

            uid_for_dump = user_id or msg.chat.id
            active_dump  = await get_active_dump(uid_for_dump)
            target_chat  = active_dump["id"] if active_dump else None
            await handle_upload_split(client, msg, fp, msg.chat.title or "User",
                              user_id=uid_for_dump, target_chat=target_chat,
                              start_time=time.time())
            shutil.rmtree(dl_dir, ignore_errors=True)
            dest = "dump channel" if target_chat else "your PM"
            await msg.edit_text(
                f"✅ <b>Done!</b> Sent to {dest}\n"
                f"<b>Engine:</b> <code>yt-dlp Native</code>"
            )
        except Exception as ex:
            traceback.print_exc()
            await msg.edit_text(f"⚠️ <b>Error:</b> <code>{clean_html(str(ex))}</code>")

    asyncio.create_task(_run())

# ─────────────────────────────────────────
# /ytdl — WZML-X quality picker (ALL sites) + bulk -b
# ─────────────────────────────────────────
@app.on_message(filters.command("ytdl"))
async def ytdl_selector(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned from using this bot.")
    if not await _enforce_limits(m, "ytdl"):
        return
    raw_text = m.text
    rename   = None
    bulk     = False
    if " -b" in raw_text:
        raw_text = raw_text.replace(" -b","").strip(); bulk = True
    if " -n " in raw_text:
        parts    = raw_text.split(" -n ",1)
        raw_text = parts[0].strip()
        rename   = parts[1].strip().split()[0]
    # .txt file reply
    if m.reply_to_message and m.reply_to_message.document:
        doc = m.reply_to_message.document
        if doc.file_name and doc.file_name.endswith(".txt"):
            await _handle_bulk_txt(c, m, "ytdl", doc); return
    if len(m.command) < 2:
        return await m.reply_text("❌ Send Link! Usage: <code>/ytdl URL [-n name] [-b]</code>")
    body = raw_text.split(None,1)[1] if len(raw_text.split(None,1)) > 1 else ""
    if bulk:
        urls = [u.strip() for u in body.split("\n") if u.strip().startswith("http")]
        if not urls: return await m.reply_text("❌ No valid URLs after -b!")
        uid = m.from_user.id
        if uid not in user_queues: user_queues[uid] = []
        for u in urls: user_queues[uid].append((u, m, "ytdl", "tg", rename, False))
        await m.reply_text(f"📋 <b>Bulk:</b> {len(urls)} URLs queued!")
        asyncio.create_task(queue_manager(c, uid)); return
    url = body.strip().split()[0] if body.strip() else ""
    if not url: return await m.reply_text("❌ Send Link!")
    msg = await m.reply_text("🔍 <b>Fetching formats...</b>")
    await show_quality_picker(url, msg, user_id=m.from_user.id, rename=rename)

async def _handle_bulk_txt(c, m, cmd, doc):
    msg = await m.reply_text("📄 <b>Reading .txt file...</b>")
    try:
        path = await c.download_media(doc)
        async with aiofiles.open(path, "r") as f:
            lines = await f.readlines()
        with suppress(Exception): os.remove(path)
        urls = [l.strip() for l in lines if l.strip().startswith("http")]
        if not urls:
            await msg.edit_text("❌ No valid URLs in .txt file!"); return
        await msg.edit_text(f"📋 <b>Found {len(urls)} URLs!</b> Adding to queue...")
        uid = m.from_user.id
        if uid not in user_queues: user_queues[uid] = []
        for url in urls:
            mode = "ytdl" if cmd == "ytdl" else "auto"
            user_queues[uid].append((url, m, mode, "tg", None, False))
        asyncio.create_task(queue_manager(c, uid))
    except Exception as e:
        await msg.edit_text(f"❌ Error: <code>{clean_html(str(e))}</code>")

# ─────────────────────────────────────────
# /playlist support
# ─────────────────────────────────────────
@app.on_message(filters.command("playlist"))
async def playlist_handler(c, m):
    if len(m.command) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/playlist https://youtube.com/playlist?list=XXX</code>\n"
            "<code>/playlist url --quality 720</code>"
        )
    text = m.text.split(None, 1)[1]
    url = text.split()[0].strip()
    quality = "1080"
    rename_prefix = None

    if "--quality" in text:
        try:
            quality = text.split("--quality")[1].strip().split()[0]
        except:
            pass
    if " -n " in text:
        try:
            rename_prefix = text.split(" -n ", 1)[1].strip()
        except:
            pass

    msg = await m.reply_text("🔍 <b>Fetching Playlist Info...</b>")

    async def _process_playlist():
        try:
            ydl_opts_info = {
                'quiet': True,
                'extract_flat': True,
                'cookiefile': 'cookies.txt' if os.path.exists("cookies.txt") else None,
            }
            def _extract():
                with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                    return ydl.extract_info(url, download=False)

            info = await asyncio.to_thread(_extract)
            entries = info.get('entries', [])
            if not entries:
                await msg.edit_text("❌ No videos found in playlist!")
                return

            total = len(entries)
            await msg.edit_text(f"📋 <b>Playlist:</b> {clean_html(info.get('title', 'Unknown'))}\n"
                                 f"📹 <b>Total:</b> {total} videos\n\n"
                                 f"Starting download...")

            fmt = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"

            for i, entry in enumerate(entries, 1):
                if msg.id in abort_dict:
                    await msg.edit_text("🛑 Playlist cancelled.")
                    return
                video_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id', '')}"
                title = entry.get('title', f'video_{i}')
                rname = f"{rename_prefix}_{i:03d}" if rename_prefix else None
                task_info_str = f"Playlist {i}/{total}"
                await msg.edit_text(f"📥 <b>[{i}/{total}]</b> {clean_html(title[:50])}")
                await process_task(c, m, video_url, mode="ytdl", format_id=fmt,
                                   task_info=task_info_str, rename=rname, status_msg=msg,
                                   user_id=m.from_user.id)

            await msg.edit_text(f"✅ <b>Playlist Done!</b> {total} videos uploaded.")
        except Exception as e:
            await msg.edit_text(f"❌ Playlist Error: <code>{clean_html(str(e))}</code>")

    asyncio.create_task(_process_playlist())

# ─────────────────────────────────────────
# /leech /dl /rclone /queue /zip /compress
# ─────────────────────────────────────────
@app.on_message(filters.command(["leech", "dl", "rclone", "queue", "zip", "compress"]))
async def command_handler(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned from using this bot.")
    if not await _enforce_limits(m, "leech"):
        return
    raw_text = m.text
    rename   = None
    seed     = False
    bulk     = False

    if " -n " in raw_text:
        parts    = raw_text.split(" -n ", 1)
        raw_text = parts[0]
        rename   = parts[1].strip().split()[0]
    if " -s" in raw_text:
        raw_text = raw_text.replace(" -s", "").strip()
        seed = True
    if " -b" in raw_text:
        raw_text = raw_text.replace(" -b", "").strip()
        bulk = True

    uid = m.from_user.id
    # .txt file bulk
    is_txt_reply = (
        m.reply_to_message and m.reply_to_message.document and
        m.reply_to_message.document.file_name and
        m.reply_to_message.document.file_name.endswith(".txt")
    )
    if is_txt_reply and bulk:
        await _handle_bulk_txt(c, m, m.command[0], m.reply_to_message.document)
        return

    is_reply = m.reply_to_message and (
        m.reply_to_message.document or m.reply_to_message.video or
        m.reply_to_message.audio or m.reply_to_message.photo
    )
    url   = None
    links = []

    if is_reply:
        links = [None]
    elif len(m.command) > 1:
        body = raw_text.split(None, 1)[1] if len(raw_text.split(None, 1)) > 1 else ""
        if bulk:
            links = [u.strip() for u in body.split("\n") if u.strip().startswith("http")]
            if not links:
                links = [u.strip() for u in body.split() if u.strip().startswith("http")]
        else:
            links = body.split()
        if links: url = links[0]
    else:
        return await m.reply_text("❌ Send Link or Reply to File!")
    if not links:
        return await m.reply_text("❌ No valid links found!")

    # 🚨 YAHAN FIX KIYA HAI: cmd ko pehle hi define kar diya
    cmd = m.command[0]

    target = "rclone" if cmd == "rclone" else "tg"
    mode = "auto"

    if cmd == "leech":
        mode = "leech"
        if is_reply:
            doc = m.reply_to_message.document
            if not (doc and doc.file_name and doc.file_name.lower().endswith(".torrent")):
                return await m.reply_text("❌ <b>/leech</b> is only for .torrent files or magnet links!")
            mode = "leech_file"
        elif url and not _is_torrent_link(url):
            return await m.reply_text("❌ Use <b>/leech</b> for Torrents/Magnets only!\nUse /dl for direct links.")
    elif cmd == "dl":
        if url and _is_torrent_link(url):
            return await m.reply_text("❌ Use <b>/leech</b> for Torrents!")
        # ── GDrive links: route every drive.google.com link via gdown ──
        gdrive_links = [u for u in links if _is_gdrive_link(u)]
        if gdrive_links:
            for gl in gdrive_links:
                asyncio.create_task(
                    _run_gdown_dl(c, m, gl, uid, rename=rename)
                )
            # If only GDrive links were given, we're done.
            non_gd = [u for u in links if not _is_gdrive_link(u)]
            if not non_gd:
                return
            links = non_gd
            url = links[0] if links else None
    elif cmd == "zip":
        mode = "zip"
    elif cmd == "compress":
        mode = "compress"

    if bulk and len(links) > 1:
        if uid not in user_queues: user_queues[uid] = []
        for l in links: user_queues[uid].append((l, m, mode, target, rename, seed))
        await m.reply_text(f"📋 <b>Bulk:</b> {len(links)} URLs queued!")
        asyncio.create_task(queue_manager(c, uid)); return

    if cmd == "queue":
        if uid not in user_queues: user_queues[uid] = []
        for l in links: user_queues[uid].append((l, m, mode, target, rename, seed))
        await m.reply_text(f"✅ <b>Added {len(links)} Tasks to Queue!</b>")
        asyncio.create_task(queue_manager(c, uid))
    else:
        if is_reply:
            asyncio.create_task(process_task(c, m, None, mode, target, rename=rename, seed=seed, user_id=uid))
        else:
            for l in links:
                asyncio.create_task(process_task(c, m, l, mode, target, rename=rename, seed=seed, user_id=uid))

# ─────────────────────────────────────────
# /dl GDrive (gdown) and /mirror command
# ─────────────────────────────────────────
async def _run_gdown_dl(c, m, url, uid, rename=None):
    """Download a Google Drive link with gdown and upload to TG (or dump)."""
    msg = await m.reply_text(
        f"📥 <b>GDrive (gdown) download starting...</b>\n<code>{clean_html(url[:120])}</code>"
    )
    os.makedirs("downloads", exist_ok=True)
    dl_dir = os.path.join("downloads", secrets.token_hex(4))
    os.makedirs(dl_dir, exist_ok=True)
    try:
        try:
            fp = await asyncio.wait_for(
                asyncio.to_thread(_gdown_blocking, url, dl_dir),
                timeout=3600,
            )
        except asyncio.TimeoutError:
            shutil.rmtree(dl_dir, ignore_errors=True)
            return await msg.edit_text("❌ <b>gdown timeout</b> (1 hour). Try a smaller file.")
        except Exception as _e:
            shutil.rmtree(dl_dir, ignore_errors=True)
            return await msg.edit_text(f"❌ <b>gdown error:</b> <code>{clean_html(str(_e))}</code>")
        if not fp or not os.path.exists(fp):
            shutil.rmtree(dl_dir, ignore_errors=True)
            return await msg.edit_text("❌ gdown returned no file. Link may be private or invalid.")
        # Optional rename
        if rename:
            new_path = os.path.join(os.path.dirname(fp), rename)
            with suppress(Exception):
                os.rename(fp, new_path)
                fp = new_path

        active_dump = await get_active_dump(uid)
        target_chat = active_dump["id"] if active_dump else None
        await handle_upload_split(
            c, msg, fp, m.chat.title or "User",
            user_id=uid, target_chat=target_chat,
            start_time=time.time(),
        )
        shutil.rmtree(dl_dir, ignore_errors=True)
        dest = "dump channel" if target_chat else "your PM"
        await msg.edit_text(
            f"✅ <b>GDrive Download Complete!</b>\n"
            f"<b>Sent to:</b> {dest}\n"
            f"<b>Engine:</b> <code>gdown</code>"
        )
    except Exception as ex:
        shutil.rmtree(dl_dir, ignore_errors=True)
        traceback.print_exc()
        with suppress(Exception):
            await msg.edit_text(f"⚠️ <b>Error:</b> <code>{clean_html(str(ex))}</code>")


@app.on_message(filters.command("mirror"))
async def mirror_cmd(c, m):
    """Usage: /mirror -up <gdl|rcl> <link>
       gdl  → download via gdown (or HTTP) and upload to user's GDrive
              (requires uploaded token.pickle in 🪞 Mirror Configs).
       rcl  → download then upload via rclone using user's rclone.conf.
    """
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned from using this bot.")
    text = (m.text or "").strip()
    parts = text.split()
    # Expect: /mirror -up <gdl|rcl> <link>
    up_kind = None
    link    = None
    for i, tok in enumerate(parts):
        if tok == "-up" and i + 1 < len(parts):
            up_kind = parts[i + 1].lower()
        elif tok.startswith("http"):
            link = tok
            break
    if not up_kind or up_kind not in ("gdl", "rcl") or not link:
        return await m.reply_text(
            "❌ <b>Usage:</b> <code>/mirror -up gdl|rcl &lt;link&gt;</code>\n\n"
            "• <code>-up gdl</code> → upload to your Google Drive (token.pickle)\n"
            "• <code>-up rcl</code> → upload via rclone (rclone.conf)\n\n"
            "Configure both via /usersettings → 🪞 Mirror Configs."
        )

    uid = m.from_user.id
    # Validate config presence
    if up_kind == "gdl" and not _has_user_config(uid, "gdrive"):
        return await m.reply_text(
            "❌ No <code>token.pickle</code> uploaded.\n"
            "Open /usersettings → 🪞 Mirror Configs → Upload token.pickle."
        )
    if up_kind == "rcl" and not _has_user_config(uid, "rclone"):
        return await m.reply_text(
            "❌ No <code>rclone.conf</code> uploaded.\n"
            "Open /usersettings → 🪞 Mirror Configs → Upload rclone.conf."
        )

    msg = await m.reply_text(
        f"🪞 <b>Mirror starting...</b>\n"
        f"<b>Target:</b> <code>{up_kind}</code>\n"
        f"<code>{clean_html(link[:120])}</code>"
    )
    os.makedirs("downloads", exist_ok=True)
    dl_dir = os.path.join("downloads", secrets.token_hex(4))
    os.makedirs(dl_dir, exist_ok=True)

    try:
        # ── Download ──
        if _is_gdrive_link(link):
            await msg.edit_text("📥 <b>Downloading via gdown...</b>")
            try:
                fp = await asyncio.wait_for(
                    asyncio.to_thread(_gdown_blocking, link, dl_dir),
                    timeout=3600,
                )
            except Exception as _de:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return await msg.edit_text(
                    f"❌ <b>gdown error:</b> <code>{clean_html(str(_de))}</code>"
                )
        else:
            await msg.edit_text("📥 <b>Downloading via HTTP...</b>")
            fname = os.path.basename(urlparse(link).path) or f"file_{secrets.token_hex(3)}"
            fp = os.path.join(dl_dir, fname)
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(link, timeout=aiohttp.ClientTimeout(total=3600)) as resp:
                        if resp.status >= 400:
                            shutil.rmtree(dl_dir, ignore_errors=True)
                            return await msg.edit_text(
                                f"❌ <b>HTTP {resp.status}</b> from source."
                            )
                        async with aiofiles.open(fp, "wb") as f:
                            async for chunk in resp.content.iter_chunked(1 << 20):
                                await f.write(chunk)
            except Exception as _he:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return await msg.edit_text(
                    f"❌ <b>HTTP download error:</b> <code>{clean_html(str(_he))}</code>"
                )

        if not fp or not os.path.exists(fp):
            shutil.rmtree(dl_dir, ignore_errors=True)
            return await msg.edit_text("❌ Download produced no file.")

        # ── Upload ──
        if up_kind == "gdl":
            await msg.edit_text(
                f"📤 <b>Uploading to your Google Drive...</b>\n"
                f"<code>{clean_html(os.path.basename(fp))}</code>"
            )
            try:
                link_out = await _gdrive_upload_with_token(
                    fp, _user_config_path(uid, "gdrive")
                )
            except Exception as _ue:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return await msg.edit_text(
                    f"❌ <b>GDrive upload error:</b> <code>{clean_html(str(_ue))}</code>"
                )
            shutil.rmtree(dl_dir, ignore_errors=True)
            return await msg.edit_text(
                f"✅ <b>Mirror Complete (GDrive)!</b>\n"
                f"<b>Link:</b> {link_out}"
            )
        else:  # rcl
            await msg.edit_text(
                f"📤 <b>Uploading via rclone...</b>\n"
                f"<code>{clean_html(os.path.basename(fp))}</code>"
            )
            try:
                await _rclone_upload_with_conf(
                    fp, _user_config_path(uid, "rclone")
                )
            except Exception as _ue:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return await msg.edit_text(
                    f"❌ <b>rclone error:</b> <code>{clean_html(str(_ue))}</code>"
                )
            shutil.rmtree(dl_dir, ignore_errors=True)
            return await msg.edit_text(
                f"✅ <b>Mirror Complete (rclone)!</b>\n"
                f"<code>{clean_html(os.path.basename(fp))}</code>"
            )

    except Exception as ex:
        shutil.rmtree(dl_dir, ignore_errors=True)
        traceback.print_exc()
        with suppress(Exception):
            await msg.edit_text(f"⚠️ <b>Mirror failed:</b> <code>{clean_html(str(ex))}</code>")


# ─────────────────────────────────────────
# Torrent callbacks
# ─────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^torrent_all_"))
async def torrent_all_cb(c, cb):
    task_id = cb.data.replace("torrent_all_", "")
    if task_id in pending_selections:
        pending_selections[task_id]["action"] = "all"
        pending_selections[task_id]["status"] = "ready"
        await cb.answer("✅ Downloading all files!")
        try:
            await cb.message.edit_text("▶️ <b>Downloading all files...</b>")
        except:
            pass
    else:
        await cb.answer("❌ Session expired", show_alert=True)

@app.on_callback_query(filters.regex(r"^torrent_cancel_"))
async def torrent_cancel_cb(c, cb):
    task_id = cb.data.replace("torrent_cancel_", "")
    if task_id in pending_selections:
        pending_selections[task_id]["action"] = "cancel"
        pending_selections[task_id]["status"] = "ready"
        await cb.answer("🛑 Cancelled!")
        try:
            await cb.message.edit_text("🛑 <b>Torrent download cancelled.</b>")
        except:
            pass
    else:
        await cb.answer("❌ Session expired", show_alert=True)

# ─────────────────────────────────────────
# Stop Seeding
# ─────────────────────────────────────────
@app.on_message(filters.command("stopseed"))
async def stopseed_cmd(c, m):
    if len(m.command) < 2:
        if not seeding_gids:
            return await m.reply_text("ℹ️ No active seeding tasks.")
        gid_list = "\n".join([f"• <code>{g}</code>" for g in seeding_gids.keys()])
        return await m.reply_text(f"🌱 <b>Active Seeds:</b>\n{gid_list}\n\nUse: <code>/stopseed GID</code>")
    gid = m.command[1].strip()
    try:
        aria2.client.remove(gid)
        seeding_gids.pop(gid, None)
        await m.reply_text(f"✅ Seeding stopped for GID: <code>{gid}</code>")
    except Exception as e:
        await m.reply_text(f"❌ Error: <code>{clean_html(str(e))}</code>")

# ─────────────────────────────────────────
# /bdl Bunkr
# ─────────────────────────────────────────
@app.on_message(filters.command("bdl"))
async def bunkr_dl_handler(c, m):
    if not await _enforce_limits(m, "bdl"):
        return
    if len(m.command) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b> <code>/bdl https://bunkr.sk/a/albumname</code>\n\n"
            "✅ Single files aur Albums dono support hain!\n"
            "🔄 .wvm files auto-convert ho jayengi MP4 mein."
        )
    url = m.text.split(None, 1)[1].strip()
    if not (url.startswith("http") and "bunkr" in url):
        return await m.reply_text("❌ <b>Invalid URL!</b> Sirf Bunkr links supported hain.")
    asyncio.create_task(process_task(c, m, url, mode="bunkr", upload_target="tg", user_id=m.from_user.id))

# ─────────────────────────────────────────
# /scriptdl (Direct Bypass Script Downloader — with Quality Selection)
# ─────────────────────────────────────────

# scriptdl session store: msg_id -> {url, formats, uid}
scriptdl_session = {}

# PHub selective-mode session store
# msg_id -> {videos: [{url,title,thumb}], selected: set(int), page: int,
#            uid, chat_id, target_height, raw_fmt_id, target_fmt}
phub_select_sessions = {}
PHUB_SELECT_PAGE_SIZE = 8


def _scrape_phub_profile_videos(profile_url: str, proxy: str = None, max_pages: int = 30) -> list:
    """Scrape a PornHub profile/model/channel/playlist page for
    [{url, title, thumb}, ...]."""
    import requests
    from bs4 import BeautifulSoup as _BS
    from urllib.parse import urljoin as _join, urlparse as _urlp

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Cookie":     "age_verified=1; platform=pc; accessAgeDisclaimerPH=1",
        "Accept-Language": "en-US,en;q=0.9",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None

    base = profile_url.split("?")[0].rstrip("/")
    is_playlist = "/playlist/" in base
    if not is_playlist and not base.endswith("/videos"):
        videos_url = base + "/videos"
    else:
        videos_url = base

    seen = set()
    out  = []
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in videos_url else "?"
        page_url = videos_url if page == 1 else f"{videos_url}{sep}page={page}"
        try:
            r = requests.get(page_url, headers=headers, proxies=proxies, timeout=30)
        except Exception:
            break
        if r.status_code != 200:
            break

        soup = _BS(r.text, "html.parser")
        items = (soup.select("li.pcVideoListItem")
                 or soup.select("li.videoblock")
                 or soup.select("div.phimage"))
        if not items:
            break

        new_in_page = 0
        for li in items:
            a = li.find("a", href=True)
            img = li.find("img")
            if not a:
                continue
            href = _join("https://www.pornhub.com", a["href"])
            if "viewkey=" not in href:
                continue
            href = href.split("&")[0]
            if href in seen:
                continue
            seen.add(href)

            title = (a.get("title")
                     or (img.get("alt") if img else None)
                     or "Video").strip()
            thumb = ""
            if img is not None:
                thumb = (img.get("data-thumb_url")
                         or img.get("data-mediumthumb")
                         or img.get("data-image")
                         or img.get("data-src")
                         or img.get("src")
                         or "")
            out.append({"url": href, "title": title[:120], "thumb": thumb})
            new_in_page += 1

        if new_in_page == 0:
            break

    return out


def _phub_select_render(msg_id: int):
    """Build the selection text + InlineKeyboard for a given page."""
    s = phub_select_sessions.get(msg_id)
    if not s:
        return "❌ Session expired.", None
    videos   = s["videos"]
    selected = s["selected"]
    page     = s["page"]
    page_sz  = PHUB_SELECT_PAGE_SIZE
    total    = len(videos)
    pages    = max(1, (total + page_sz - 1) // page_sz)
    page     = max(0, min(page, pages - 1))
    s["page"] = page

    start = page * page_sz
    end   = min(start + page_sz, total)

    rows = []
    for i in range(start, end):
        v = videos[i]
        mark = "✅" if i in selected else "❌"
        title = (v.get("title") or "Video")[:48]
        label = f"{mark} {title}"
        rows.append([InlineKeyboardButton(
            label, callback_data=f"phsel|{msg_id}|t|{i}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "⬅️ Prev", callback_data=f"phsel|{msg_id}|p|{page-1}"
        ))
    nav.append(InlineKeyboardButton(
        f"📄 {page+1}/{pages}", callback_data=f"phsel|{msg_id}|noop|0"
    ))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(
            "Next ➡️", callback_data=f"phsel|{msg_id}|p|{page+1}"
        ))
    rows.append(nav)

    rows.append([
        InlineKeyboardButton("☑️ All",   callback_data=f"phsel|{msg_id}|all|0"),
        InlineKeyboardButton("🧹 Clear", callback_data=f"phsel|{msg_id}|clr|0"),
    ])
    rows.append([
        InlineKeyboardButton(
            f"🚀 Start Selected Downloads ({len(selected)})",
            callback_data=f"phsel|{msg_id}|go|0"
        ),
        InlineKeyboardButton("❌ Cancel", callback_data=f"phsel|{msg_id}|cancel|0"),
    ])

    text = (
        f"🎬 <b>PHub Selective Download</b>\n"
        f"📦 <b>Total:</b> {total}  •  ✅ <b>Selected:</b> {len(selected)}\n"
        f"📄 <b>Page {page+1}/{pages}</b>\n\n"
        f"Tap a video to toggle selection."
    )
    return text, InlineKeyboardMarkup(rows)


@app.on_callback_query(filters.regex(r"^phsel\|"))
async def phub_select_cb(c, cb):
    parts = cb.data.split("|")
    msg_id = int(parts[1])
    action = parts[2]
    arg    = parts[3] if len(parts) > 3 else "0"

    s = phub_select_sessions.get(msg_id)
    if not s:
        return await cb.answer("❌ Session expired.", show_alert=True)
    if cb.from_user.id != s["uid"]:
        return await cb.answer("❌ Not your session!", show_alert=True)

    if action == "noop":
        return await cb.answer()

    if action == "cancel":
        phub_select_sessions.pop(msg_id, None)
        await cb.answer("❌ Cancelled")
        with suppress(Exception):
            await cb.message.delete()
        return

    if action == "t":
        idx = int(arg)
        if idx in s["selected"]:
            s["selected"].discard(idx)
        else:
            s["selected"].add(idx)
        await cb.answer()

    elif action == "p":
        s["page"] = int(arg)
        await cb.answer()

    elif action == "all":
        s["selected"] = set(range(len(s["videos"])))
        await cb.answer("All selected")

    elif action == "clr":
        s["selected"].clear()
        await cb.answer("Cleared")

    elif action == "go":
        if not s["selected"]:
            return await cb.answer("Pick at least one video first.", show_alert=True)
        await cb.answer()
        await _phub_start_selected(c, cb.message, msg_id)
        return

    text, kb = _phub_select_render(msg_id)
    with suppress(Exception):
        await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)


# ── Unified PHub batch progress helpers ──
def _phub_progress_text(done, failed, total, label="PHub Batch"):
    processed = done + failed
    pct = int(processed / total * 100) if total else 0
    filled = pct // 5
    bar = "▓" * filled + "░" * (20 - filled)
    return (
        f"⏳ <b>{label} Progress:</b> {done}/{total}  |  ❌ <b>Failed:</b> {failed}\n"
        f"<code>[{bar}]</code> {pct}%"
    )


def _phub_final_text(done, failed, total, errors, label="PHub Batch"):
    head = (
        f"🎉 <b>{label} Complete!</b>\n\n"
        f"✅ <b>Done:</b> {done}\n"
        f"❌ <b>Failed:</b> {failed}\n"
        f"📦 <b>Total:</b> {total}\n"
    )
    if errors:
        body = "\n".join(
            f"• <code>{clean_html(e[:140])}</code>" for e in errors[:25]
        )
        head += f"\n<b>Failure reasons:</b>\n{body}"
        if len(errors) > 25:
            head += f"\n…and <b>{len(errors) - 25}</b> more"
    return head


def _batch_cancel_kb(uid: int) -> InlineKeyboardMarkup:
    """Cancel button attached to every batch progress message."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🛑 Cancel Batch", callback_data=f"cancel_scriptdl|{uid}"),
    ]])


async def _safe_status_edit(status_msg, text, reply_markup=None):
    with suppress(Exception):
        await status_msg.edit_text(text, reply_markup=reply_markup)


async def _phub_start_selected(c, anchor_msg, msg_id: int):
    """Run the parallel batch on the selected PHub videos with a unified
    persistent status message + per-video error tracking."""
    s = phub_select_sessions.pop(msg_id, None)
    if not s:
        return
    videos    = s["videos"]
    selected  = sorted(s["selected"])
    uid       = s["uid"]
    chat_id   = s["chat_id"]
    target_h  = s.get("target_height")  # may be None — first video defines it
    _user_proxy = (await get_user_proxy(uid)) or PROXY_URL

    chosen = [videos[i] for i in selected]
    total_v = len(chosen)
    if total_v == 0:
        return

    # Single persistent status message — never deleted/recreated per video.
    try:
        status_msg = await c.send_message(
            chat_id, _phub_progress_text(0, 0, total_v, label="PHub Selected")
        )
    except Exception:
        status_msg = anchor_msg
    with suppress(Exception):
        await anchor_msg.delete()

    counters = {"done": 0, "failed": 0}
    error_logs: list = []

    async def _silent_one(vid_url, fmt_id):
        """Returns None on success or an error-reason string on failure."""
        os.makedirs("downloads", exist_ok=True)
        dl_dir = os.path.join("downloads", secrets.token_hex(4))
        os.makedirs(dl_dir, exist_ok=True)
        out_tmpl = os.path.join(dl_dir, "%(title).100s.%(ext)s")
        try:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        _blocking_download, vid_url, fmt_id, out_tmpl, None, False, _user_proxy
                    ),
                    timeout=600,
                )
            except (asyncio.TimeoutError, TimeoutError):
                print(f"[phub_silent] Skipped due to timeout: {vid_url}")
                shutil.rmtree(dl_dir, ignore_errors=True)
                return f"Timeout: {vid_url}"
            except Exception as _de:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return f"Download error: {_de} ({vid_url})"
            if not result:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return f"Extraction Failed: {vid_url}"
            fp = result["filepath"]
            try:
                scratch = await c.send_message(chat_id, "📤 …")
            except Exception:
                scratch = status_msg
            try:
                active_dump = await get_active_dump(uid)
                target_chat = active_dump["id"] if active_dump else None
                ok = await handle_upload_split(
                    c, scratch, fp, "User",
                    user_id=uid, target_chat=target_chat,
                    start_time=time.time(),
                )
                if ok is False:
                    return f"Upload Failed: {os.path.basename(fp)}"
            finally:
                if scratch is not status_msg:
                    with suppress(Exception):
                        await scratch.delete()
                shutil.rmtree(dl_dir, ignore_errors=True)
            return None
        except Exception as _pe:
            shutil.rmtree(dl_dir, ignore_errors=True)
            print(f"[phsel_silent] {vid_url}: {_pe}")
            return f"Error: {_pe} ({vid_url})"

    async def _track(vid_url, fmt_id):
        err = await _silent_one(vid_url, fmt_id)
        if err is None:
            counters["done"] += 1
        else:
            counters["failed"] += 1
            error_logs.append(err)
        await _safe_status_edit(
            status_msg,
            _phub_progress_text(counters["done"], counters["failed"], total_v,
                                label="PHub Selected"),
        )

    async def _resolve_fmt(vid_url):
        per_fmt = "best"
        try:
            v_data = await asyncio.to_thread(get_direct_info, vid_url, _user_proxy)
            if v_data and v_data.get("formats"):
                v_fmts = v_data["formats"]
                picked = None
                if target_h:
                    for f in v_fmts:
                        if f.get("height") == target_h:
                            picked = f
                            break
                if not picked:
                    picked = v_fmts[-1]
                per_fmt = picked.get("format_id", "best")
        except Exception as _ie:
            print(f"[phsel] format resolve error: {_ie}")
        return per_fmt

    async def _runner():
        prev_task = None
        for i, v in enumerate(chosen, start=1):
            if uid in abort_dict:
                if prev_task is not None:
                    with suppress(Exception):
                        await prev_task
                break
            vid_url = v["url"]
            try:
                per_fmt = await _resolve_fmt(vid_url)
            except Exception as _re:
                counters["failed"] += 1
                error_logs.append(f"Resolve Failed: {_re} ({vid_url})")
                await _safe_status_edit(
                    status_msg,
                    _phub_progress_text(counters["done"], counters["failed"],
                                        total_v, label="PHub Selected"),
                )
                continue
            cur_task = asyncio.create_task(_track(vid_url, per_fmt))

            # Overlap the 120s anti-ban with the current download/upload.
            if i < total_v:
                await asyncio.sleep(120)

            if prev_task is not None:
                with suppress(Exception):
                    await prev_task
            prev_task = cur_task

        if prev_task is not None:
            with suppress(Exception):
                await prev_task

        await _safe_status_edit(
            status_msg,
            _phub_final_text(counters["done"], counters["failed"], total_v,
                             error_logs, label="PHub Selected"),
        )

    asyncio.create_task(_runner())


@app.on_message(filters.command("scriptdl"))
async def scriptdl_handler(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned from using this bot.")
    if len(m.command) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b> <code>/scriptdl URL [-s]</code>\n\n"
            "Supports: HentaiCity & PornHub\n"
            "Use <code>-s</code> with a PHub profile to pick which videos to download."
        )

    # Parse args & detect -s (selective) flag
    raw = m.text.split(None, 1)[1].strip()
    tokens = raw.split()
    selective = False
    url_tokens = []
    for tok in tokens:
        if tok == "-s":
            selective = True
        else:
            url_tokens.append(tok)
    url = " ".join(url_tokens).strip()
    uid = m.from_user.id
    msg = await m.reply_text("🔍 <b>Fetching formats via script...</b>")
    _user_proxy = (await get_user_proxy(uid)) or PROXY_URL

    is_phub_profile = any(x in url for x in [
        "pornhub.com/model/", "pornhub.com/pornstar/",
        "pornhub.com/channels/", "pornhub.com/playlist/"
    ])
    is_xh_profile = any(x in url for x in [
        "xhamster.com/users/", "xhamster.com/channels/",
        "xhamster.com/pornstars/",
    ])

    # ─── PHub Selective Mode (-s) ───
    if selective and is_phub_profile:
        await msg.edit_text("🛡️ <b>PHub Profile detected — scanning videos for selection...</b>")
        try:
            videos = await asyncio.to_thread(_scrape_phub_profile_videos, url, _user_proxy)
        except Exception as e:
            return await msg.edit_text(f"❌ <b>Profile scan failed:</b> <code>{clean_html(str(e))}</code>")
        if not videos:
            return await msg.edit_text("❌ <b>No videos found in this profile.</b>")

        phub_select_sessions[m.id] = {
            "videos":        videos,
            "selected":      set(),
            "page":          0,
            "uid":           uid,
            "chat_id":       m.chat.id,
            "target_height": None,
            "raw_fmt_id":    "best",
            "target_fmt":    "best",
        }
        text, kb = _phub_select_render(m.id)
        return await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)

    # ─── PHub Profile / Model / Channel / Playlist Batch Detection (auto) ───
    batch_urls = []
    if is_phub_profile:
        await msg.edit_text("🛡️ <b>PHub Profile detected — scanning all videos...</b>")
        try:
            batch_urls = await asyncio.to_thread(get_profile_videos, url, _user_proxy)
        except Exception as e:
            return await msg.edit_text(f"❌ <b>Profile scan failed:</b> <code>{clean_html(str(e))}</code>")
        if not batch_urls:
            return await msg.edit_text("❌ <b>No videos found in this profile.</b>")
        await msg.edit_text(
            f"✅ <b>Found {len(batch_urls)} videos.</b>\n"
            f"🔍 <b>Fetching formats from first video...</b>"
        )
        url = batch_urls[0]

    # ─── XHamster Profile / Channel Batch Detection (auto) ───────────────────
    xh_batch_urls = []
    if is_xh_profile:
        await msg.edit_text("🛡️ <b>XHamster Profile detected — scanning all videos...</b>")
        try:
            xh_batch_urls = await get_xh_profile_videos(url, proxy=_user_proxy)
        except Exception as e:
            return await msg.edit_text(f"❌ <b>XH Profile scan failed:</b> <code>{clean_html(str(e))}</code>")
        if not xh_batch_urls:
            return await msg.edit_text("❌ <b>No videos found in this XHamster profile.</b>")
        await msg.edit_text(
            f"✅ <b>Found {len(xh_batch_urls)} XHamster videos.</b>\n"
            f"🔍 <b>Fetching formats from first video...</b>"
        )
        url = xh_batch_urls[0]

    # Fetch data using the correct script
    if "hentaicity.com" in url:
        data = await asyncio.to_thread(get_hc_data, url, _user_proxy)
        site_label = "🎌 HentaiCity"
    elif "pornhub.com" in url:
        data = await asyncio.to_thread(get_direct_info, url, _user_proxy)
        site_label = "🎬 Pornhub"
    elif "watchhentai.net" in url:
        data = await asyncio.to_thread(get_wh_data, url, _user_proxy)
        site_label = "🎌 WatchHentai"
    elif "dailymotion.com" in url or "dmcdn.net" in url:
        data = await asyncio.to_thread(get_dm_data, url, _user_proxy)
        site_label = "🎬 Dailymotion"
    elif "xhamster.com" in url or "xhamster.desi" in url:
        raw_xh = await get_xh_data(url, proxy=_user_proxy)
        if not raw_xh:
            return await msg.edit_text("❌ <b>XHamster: failed to fetch video info.</b>")
        if raw_xh.get("fallback_ytdlp") or not raw_xh["formats"]:
            await msg.edit_text("⚙️ <b>XHamster: no direct streams found, using yt-dlp...</b>")
            data = {
                "formats":       [{"format_id": "best", "url": url, "height": None, "ext": "mp4"}],
                "title":         raw_xh.get("title", "XHamster Video"),
                "original_info": raw_xh,
            }
        else:
            data = {
                "formats":       raw_xh["formats"],
                "title":         raw_xh.get("title", "XHamster Video"),
                "original_info": raw_xh,
            }
        site_label = "🔞 XHamster"
    else:
        return await msg.edit_text(
            "❌ <b>URL not supported by ScriptDL.</b>\n"
            "Currently supports: HentaiCity, PornHub, WatchHentai, Dailymotion & XHamster.\n\n"
            "For other sites use <code>/ytdl</code>."
        )

    if not data or not data.get("formats"):
        return await msg.edit_text(
            f"❌ <b>ScriptDL: Could not fetch formats!</b>\n"
            f"• Check if URL is correct\n"
            f"• Try again later"
        )

    formats = data["formats"]
    info    = data.get("original_info", {})
    title   = clean_html((data.get("title") or info.get("title") or "Video")[:60])
    dur     = time_formatter(info.get("duration", 0))
    thumb   = get_best_thumbnail(info)

    # Build quality buttons from extracted formats
    buttons = []
    row = []
    for i, fmt in enumerate(formats):
        h       = fmt.get("height") or fmt.get("format_id") or f"Format {i+1}"
        ext     = fmt.get("ext", "mp4")
        fid     = fmt.get("format_id", str(i))
        label   = f"🎬 {h}p" if str(h).isdigit() else f"🎬 {h}"
        label  += f" ({ext})"
        row.append(InlineKeyboardButton(label, callback_data=f"sdl|{m.id}|{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"sdl|{m.id}|cancel")])

    # Store session keyed by original message id
    scriptdl_session[m.id] = {
        "url":        url,
        "formats":    formats,
        "uid":        uid,
        "title":      data.get("title") or info.get("title") or "video",
        "info":       info,
        "batch_urls": batch_urls or xh_batch_urls,  # phub or xhamster batch list
    }

    info_txt = (
        f"{site_label}\n"
        f"🎬 <b>{title}</b>\n"
        f"⏱ {dur}\n\n"
        f"<b>Select Quality:</b>"
    )

    try:
        if thumb:
            await msg.reply_photo(photo=thumb, caption=info_txt, reply_markup=InlineKeyboardMarkup(buttons))
            with suppress(Exception): await msg.delete()
        else:
            await msg.edit_text(info_txt, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        with suppress(Exception): await msg.edit_text(info_txt, reply_markup=InlineKeyboardMarkup(buttons))


@app.on_callback_query(filters.regex(r"^sdl\|"))
async def scriptdl_quality_cb(c, cb):
    parts   = cb.data.split("|")
    msg_id  = int(parts[1])
    choice  = parts[2]
    session = scriptdl_session.get(msg_id)
    if not session:
        return await cb.answer("❌ Session expired. Send the link again.", show_alert=True)
    if choice == "cancel":
        scriptdl_session.pop(msg_id, None)
        await cb.answer("❌ Cancelled")
        with suppress(Exception): await cb.message.delete()
        return
    await cb.answer()
    fmt_index = int(choice)
    formats   = session["formats"]

    if fmt_index >= len(formats):
        return await cb.message.edit_text("❌ Invalid format selected.")

    selected_fmt = formats[fmt_index]
    direct_url   = selected_fmt.get("url")
    raw_fmt_id   = selected_fmt.get("format_id", "best")
    original_url = session.get("url", "").replace('\n', '').replace('\r', '').strip()
    uid          = session["uid"]
    _user_proxy  = (await get_user_proxy(uid)) or PROXY_URL

    scriptdl_session.pop(msg_id, None)
    msg = cb.message

    if not direct_url:
        return await msg.edit_text("❌ No direct URL found.")

    # Decide what to pass to yt-dlp based on site
    if "pornhub.com" in original_url:
        # Pornhub: use original page URL + format_id
        target_url = original_url
        target_fmt = raw_fmt_id
    elif "dailymotion.com" in original_url or "dmcdn.net" in original_url:
        # Dailymotion: format_id IS the yt-dlp selector string (e.g. "bestvideo[height<=720]+bestaudio/best")
        # direct_url = original DM URL passed back by get_dm_data
        target_url = direct_url.replace("\n", "").replace("\r", "").strip()
        target_fmt = raw_fmt_id   # already a proper yt-dlp selector
    elif "xhamster.com" in original_url or "xhamster.desi" in original_url:
        # XHamster: direct MP4 CDN URL extracted by xh.py — no yt-dlp format id needed
        target_url = direct_url.replace("\n", "").replace("\r", "").strip()
        target_fmt = "best"
    else:
        # HentaiCity / WatchHentai: use pre-extracted direct CDN URL
        target_url = direct_url.replace("\n", "").replace("\r", "").strip()
        target_fmt = "best"

    # PHub batch list (empty if single video)
    batch_urls   = session.get("batch_urls") or []
    is_batch     = len(batch_urls) > 1
    target_height = selected_fmt.get("height")

    if is_batch:
        await msg.edit_text(
            f"⏳ <b>Starting PHub Batch Download</b>\n"
            f"📦 <b>Total Videos:</b> {len(batch_urls)}\n"
            f"🎯 <b>Quality:</b> <code>{target_height or target_fmt[:60]}</code>"
        )
    else:
        await msg.edit_text(f"⏳ <b>Starting ScriptDL...</b>\n<code>{target_fmt[:60]}</code>")

    # Batch tracking for unified progress (Task 3) — populated when is_batch.
    batch_counters = {"done": 0, "failed": 0}
    batch_errors: list = []

    async def _process_one(vid_url, fmt_id, idx=None, total=None):
        """Download + upload a single video. Returns True on success."""
        loop = asyncio.get_running_loop()
        start_dl = time.time()
        def _hook(d):
            if d["status"] != "downloading": return
            total_b = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            current = d.get("downloaded_bytes") or 0
            fname   = os.path.basename(d.get("filename") or "Video")
            if current > 0:
                prefix = f"📥 [{idx}/{total}] " if idx else "📥 "
                asyncio.run_coroutine_threadsafe(
                    update_progress_ui(current, total_b, msg, start_dl,
                                       f"{prefix}Downloading (ScriptDL)...", fname, engine="ytdlp"), loop)
        os.makedirs("downloads", exist_ok=True)
        dl_dir = os.path.join("downloads", secrets.token_hex(4))
        os.makedirs(dl_dir, exist_ok=True)
        out_tmpl = os.path.join(dl_dir, "%(title).100s.%(ext)s")

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_blocking_download, vid_url, fmt_id, out_tmpl, _hook, False, _user_proxy),
                timeout=600,
            )
        except (asyncio.TimeoutError, TimeoutError):
            print(f"[scriptdl] Skipped due to timeout: {vid_url}")
            shutil.rmtree(dl_dir, ignore_errors=True)
            return False
        except Exception as _de:
            print(f"[scriptdl] Skipped due to error ({_de}): {vid_url}")
            shutil.rmtree(dl_dir, ignore_errors=True)
            return False
        if not result:
            shutil.rmtree(dl_dir, ignore_errors=True)
            return False
        fp = result["filepath"]
        active_dump = await get_active_dump(uid)
        target_chat = active_dump["id"] if active_dump else None
        await handle_upload_split(c, msg, fp, msg.chat.title or "User",
                          user_id=uid, target_chat=target_chat,
                          start_time=time.time())
        shutil.rmtree(dl_dir, ignore_errors=True)
        return True

    async def _process_one_silent(vid_url, fmt_id, status_msg=None, total_v=0):
        """Silent batch download + upload.
        Returns None on success, or an error-reason string on failure."""
        os.makedirs("downloads", exist_ok=True)
        dl_dir = os.path.join("downloads", secrets.token_hex(4))
        os.makedirs(dl_dir, exist_ok=True)
        out_tmpl = os.path.join(dl_dir, "%(title).100s.%(ext)s")
        try:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        _blocking_download, vid_url, fmt_id, out_tmpl, None, False, _user_proxy
                    ),
                    timeout=600,
                )
            except (asyncio.TimeoutError, TimeoutError):
                print(f"[scriptdl_silent] Skipped due to timeout: {vid_url}")
                shutil.rmtree(dl_dir, ignore_errors=True)
                return f"Timeout: {vid_url}"
            except Exception as _de:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return f"Download error: {_de} ({vid_url})"
            if not result:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return f"Extraction Failed: {vid_url}"
            fp = result["filepath"]
            # Scratch message just for upload-progress UI (deleted after).
            try:
                scratch = await c.send_message(msg.chat.id, "📤 …")
            except Exception:
                scratch = status_msg or msg
            try:
                active_dump = await get_active_dump(uid)
                target_chat = active_dump["id"] if active_dump else None
                ok = await handle_upload_split(
                    c, scratch, fp, msg.chat.title or "User",
                    user_id=uid, target_chat=target_chat,
                    start_time=time.time(),
                )
                if ok is False:
                    return f"Upload Failed: {os.path.basename(fp)}"
            finally:
                if scratch is not (status_msg or msg):
                    with suppress(Exception):
                        await scratch.delete()
                shutil.rmtree(dl_dir, ignore_errors=True)
            return None
        except Exception as _pe:
            shutil.rmtree(dl_dir, ignore_errors=True)
            print(f"[scriptdl_silent] {vid_url}: {_pe}")
            return f"Error: {_pe} ({vid_url})"

    async def _track_silent(vid_url, fmt_id, status_msg, total_v,
                             max_retries: int = 3, retry_delay: int = 15):
        """Task 4: download + upload one video with up to 3 retries."""
        err = None
        for attempt in range(1, max_retries + 1):
            err = await _process_one_silent(vid_url, fmt_id, status_msg, total_v)
            if err is None:
                break  # success
            if attempt < max_retries:
                print(f"[batch] attempt {attempt}/{max_retries} failed: {err} — retrying in {retry_delay}s")
                await asyncio.sleep(retry_delay)
        if err is None:
            batch_counters["done"] += 1
        else:
            batch_counters["failed"] += 1
            batch_errors.append(f"[{vid_url[:55]}] {str(err)[:70]}")
        await _safe_status_edit(
            status_msg,
            _phub_progress_text(batch_counters["done"], batch_counters["failed"],
                                total_v, label="Batch DL"),
            reply_markup=_batch_cancel_kb(uid),
        )

    async def _resolve_phub_fmt(vid_url):
        """Pick a per-video format id matching the chosen target_height."""
        per_fmt = "best"
        try:
            v_data = await asyncio.to_thread(get_direct_info, vid_url, _user_proxy)
            if v_data and v_data.get("formats"):
                v_fmts = v_data["formats"]
                picked = None
                if target_height:
                    for f in v_fmts:
                        if f.get("height") == target_height:
                            picked = f
                            break
                if not picked:
                    picked = v_fmts[-1]
                per_fmt = picked.get("format_id", "best")
        except Exception as _ie:
            print(f"[batch] format resolve error: {_ie}")
        return per_fmt

    async def _run_scriptdl():
        try:
            if is_batch:
                # ─── Parallel batch with overlapping 120s anti-ban + unified progress ───
                total_v = len(batch_urls)
                with suppress(Exception):
                    await msg.edit_text(
                        _phub_progress_text(0, 0, total_v, label="Batch DL"),
                        reply_markup=_batch_cancel_kb(uid),   # Task 5: cancel button
                    )
                status_msg = msg
                prev_task = None

                for i, vid_url in enumerate(batch_urls, start=1):
                    if uid in abort_dict:
                        if prev_task is not None:
                            with suppress(Exception):
                                await prev_task
                        break

                    # 1. Extract (per-video format resolution).
                    try:
                        per_fmt = await _resolve_phub_fmt(vid_url)
                    except Exception as _re:
                        batch_counters["failed"] += 1
                        batch_errors.append(f"Resolve Failed: {_re} ({vid_url})")
                        await _safe_status_edit(
                            status_msg,
                            _phub_progress_text(batch_counters["done"],
                                                batch_counters["failed"],
                                                total_v, label="PHub Batch"),
                        )
                        continue

                    # 2. Kick off download+upload as a background task.
                    cur_task = asyncio.create_task(
                        _track_silent(vid_url, per_fmt, status_msg, total_v)
                    )

                    # 3. Sleep 120s in the main loop — this overlaps with the
                    #    download+upload of the current video. Skip on last.
                    if i < total_v and "pornhub" in vid_url:
                        await asyncio.sleep(120)
                    elif i < total_v:
                        await asyncio.sleep(5)

                    # 4. Ensure the previous video's task has finished before
                    #    we move on (kept ordered for predictable output).
                    if prev_task is not None:
                        with suppress(Exception):
                            await prev_task
                    prev_task = cur_task

                # Wait for the very last task to finish.
                if prev_task is not None:
                    with suppress(Exception):
                        await prev_task

                # Final summary — remove cancel button when done.
                await _safe_status_edit(
                    status_msg,
                    _phub_final_text(batch_counters["done"],
                                     batch_counters["failed"],
                                     total_v, batch_errors,
                                     label="Batch DL"),
                    reply_markup=None,
                )
                return

            # ─── Single video path (original behaviour preserved) ───
            loop = asyncio.get_running_loop()
            start_dl = time.time()
            def _hook(d):
                if d["status"] != "downloading": return
                total   = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                current = d.get("downloaded_bytes") or 0
                fname   = os.path.basename(d.get("filename") or "Video")
                spd     = d.get("speed") or None    # Task 3: yt-dlp native speed
                eta_sec = d.get("eta") or None      # Task 3: yt-dlp native ETA
                if current > 0:
                    asyncio.run_coroutine_threadsafe(
                        update_progress_ui(current, total, msg, start_dl,
                                           "📥 Downloading (ScriptDL)...", fname, engine="ytdlp",
                                           speed_override=spd, eta_override=eta_sec), loop)
            os.makedirs("downloads", exist_ok=True)
            dl_dir = os.path.join("downloads", secrets.token_hex(4))
            os.makedirs(dl_dir, exist_ok=True)
            raw_title = session.get("title", "Video")
            clean_title = re.sub(r'[<>:"/\\|?*]', "_", raw_title).strip()[:100]
            out_tmpl = os.path.join(dl_dir, f"{clean_title}.%(ext)s")

            # 🔥 YAHAN BHI aiohttp ki jagah yt-dlp use hoga
            result = await asyncio.to_thread(_blocking_download, target_url, target_fmt, out_tmpl, _hook, False, _user_proxy)
            if not result:
                await msg.edit_text("❌ <b>Download failed!</b>")
                shutil.rmtree(dl_dir, ignore_errors=True); return
            fp = result["filepath"]

            # ── Task 2b: Download official web thumbnail for m3u8/stream files ──
            thumb_url = session.get("original_info", {}).get("thumbnail")
            if thumb_url:
                try:
                    import requests as _req
                    _thumb_headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Referer": original_url
                    }
                    def _dl_thumb():
                        r = _req.get(thumb_url, headers=_thumb_headers, timeout=10)
                        if r.status_code == 200 and len(r.content) > 0:
                            with open(f"{fp}_web.jpg", "wb") as _f:
                                _f.write(r.content)
                    await asyncio.to_thread(_dl_thumb)
                except Exception as _te:
                    print(f"[thumb_dl_scriptdl] {_te}")

            active_dump = await get_active_dump(uid)
            target_chat = active_dump["id"] if active_dump else None
            await handle_upload_split(c, msg, fp, msg.chat.title or "User",
                              user_id=uid, target_chat=target_chat,
                              start_time=time.time())
            shutil.rmtree(dl_dir, ignore_errors=True)
            dest = "dump channel" if target_chat else "your PM"
            await msg.edit_text(
                f"✅ <b>ScriptDL Complete!</b>\n"
                f"<b>Sent to:</b> {dest}\n"
                f"<b>Engine:</b> <code>yt-dlp Native Bypass</code>"
            )
        except Exception as ex:
            traceback.print_exc()
            await msg.edit_text(f"⚠️ <b>Error:</b> <code>{clean_html(str(ex))}</code>")
    asyncio.create_task(_run_scriptdl())

# ─────────────────────────────────────────
# /teradl — TeraBox downloader via external API
# API: https://elated-geraldine-estbot-672c3126.koyeb.app/api?url=<terabox_url>
# ─────────────────────────────────────────
TERA_API_BASE = "https://elated-geraldine-estbot-672c3126.koyeb.app/api"

@app.on_message(filters.command("teradl"))
async def teradl_handler(c, m):
    if len(m.command) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b> <code>/teradl https://1024terabox.com/s/XXXXX</code>\n\n"
            "TeraBox / 1024TeraBox links supported.\n"
            "API se direct download link fetch karke upload karta hai."
        )

    url = m.text.split(None, 1)[1].strip()
    if not url.startswith("http"):
        return await m.reply_text("❌ Invalid URL!")

    uid = m.from_user.id
    msg = await m.reply_text("🔍 <b>Fetching TeraBox info via API...</b>")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                TERA_API_BASE,
                params={"url": url},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    return await msg.edit_text(
                        f"❌ <b>API Error:</b> HTTP {resp.status}\n"
                        f"TeraBox API unreachable. Try again later."
                    )
                api_data = await resp.json()
    except asyncio.TimeoutError:
        return await msg.edit_text("❌ <b>API Timeout!</b> TeraBox API took too long. Try again.")
    except Exception as e:
        return await msg.edit_text(f"❌ <b>API Request Failed:</b>\n<code>{clean_html(str(e))}</code>")

    if api_data.get("status") != "success":
        return await msg.edit_text(
            f"❌ <b>TeraBox API returned error.</b>\n"
            f"<code>{clean_html(str(api_data))}</code>"
        )

    # Parse response
    filename   = api_data.get("filename") or "TeraBox_File"
    file_size  = api_data.get("size") or "Unknown"
    thumbs     = api_data.get("thumbs", {})
    thumb_url  = thumbs.get("url3") or thumbs.get("url2") or thumbs.get("url1") or None
    # The API returns metadata but NOT a direct download URL in the shown response.
    # We need to fetch the actual download link — try common field names.
    direct_url = (
        api_data.get("direct_link") or
        api_data.get("download_url") or
        api_data.get("link") or
        api_data.get("url") or
        None
    )

    if not direct_url:
        # Show info to user and ask them to report if download link missing
        info_txt = (
            f"📦 <b>{clean_html(filename)}</b>\n"
            f"📏 Size: <code>{file_size}</code>\n\n"
            f"⚠️ <b>Direct download URL not in API response.</b>\n"
            f"API may need update. Raw response:\n"
            f"<code>{clean_html(str(api_data)[:300])}</code>"
        )
        return await msg.edit_text(info_txt)

    # Show info + start download
    info_txt = (
        f"📦 <b>{clean_html(filename)}</b>\n"
        f"📏 Size: <code>{file_size}</code>\n"
        f"🔗 Source: TeraBox"
    )

    try:
        if thumb_url:
            await msg.reply_photo(photo=thumb_url, caption=info_txt)
            with suppress(Exception): await msg.delete()
            # Get the new photo message for progress updates
            # We'll use a fresh status message
            msg = await c.send_message(m.chat.id, "⏳ <b>Starting TeraBox download...</b>")
        else:
            await msg.edit_text(f"{info_txt}\n\n⏳ <b>Starting download...</b>")
    except Exception:
        with suppress(Exception): await msg.edit_text(f"{info_txt}\n\n⏳ <b>Starting download...</b>")

    async def _run_tera():
        try:
            active_dump = await get_active_dump(uid)
            target_chat = active_dump["id"] if active_dump else None

            os.makedirs("downloads", exist_ok=True)
            dl_dir = os.path.join("downloads", f"tera_{secrets.token_hex(4)}")
            os.makedirs(dl_dir, exist_ok=True)
            fp = os.path.join(dl_dir, filename)

            headers_dl = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            }
            start_time = time.time()
            connector = aiohttp.TCPConnector(limit=10, force_close=False)
            async with aiohttp.ClientSession(connector=connector, headers=headers_dl) as dl_sess:
                async with dl_sess.get(direct_url) as resp:
                    if resp.status != 200:
                        await msg.edit_text(
                            f"❌ <b>Download failed!</b>\n"
                            f"HTTP {resp.status} — Direct link expired. Try again."
                        )
                        shutil.rmtree(dl_dir, ignore_errors=True)
                        return

                    total_size = int(resp.headers.get("content-length", 0))
                    dl_size    = 0

                    async with aiofiles.open(fp, "wb") as out_f:
                        async for chunk in resp.content.iter_chunked(512 * 1024):
                            await out_f.write(chunk)
                            dl_size += len(chunk)
                            await update_progress_ui(
                                dl_size, total_size, msg, start_time,
                                "📥 TeraBox Downloading...", filename,
                                engine="TeraBoxAPI"
                            )

            await handle_upload_split(
                client=c, message=msg, file_path=fp,
                user_mention=msg.chat.title or "User",
                task_info="TeraBox DL", batch_info=filename,
                start_time=time.time(), user_id=uid, target_chat=target_chat
            )
            shutil.rmtree(dl_dir, ignore_errors=True)
            dest = "dump channel" if target_chat else "your PM"
            await msg.edit_text(
                f"✅ <b>TeraBox Download Complete!</b>\n"
                f"<b>Sent to:</b> {dest}\n"
                f"<b>Engine:</b> <code>TeraBoxAPI</code>"
            )
        except Exception as ex:
            traceback.print_exc()
            await msg.edit_text(f"⚠️ <b>Error:</b> <code>{clean_html(str(ex))}</code>")

    asyncio.create_task(_run_tera())


# ─────────────────────────────────────────
# /renameall — Fast Mega Bulk Renamer
# Usage: /renameall <folder_link> | <pattern> | <replacement>
# ─────────────────────────────────────────
import posixpath
from concurrent.futures import ThreadPoolExecutor

_rename_executor = ThreadPoolExecutor(max_workers=10)

CMD_TIMEOUT_RA = 60   # per-file timeout
MAX_FILES_RA   = 10000

def _run_cmd_ra(args, timeout=CMD_TIMEOUT_RA):
    """Blocking subprocess helper for rename worker threads."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", f"Timed out after {timeout}s", 1
    except Exception as e:
        return "", str(e), 1

def _mega_ls_folder(link: str) -> list:
    """Fetch all file names inside a MEGA folder link via mega-ls -l.
    Filters out directories (lines whose flag column starts with 'd')."""
    out, err, code = _run_cmd_ra(["mega-ls", "-l", link], timeout=120)
    if code != 0:
        raise Exception(err or out or "mega-ls failed")

    files = []
    for raw in out.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        # Skip header line(s)
        upper = line.lstrip().upper()
        if upper.startswith("FLAGS") or upper.startswith("----"):
            continue

        parts = line.split(None, 4)
        if len(parts) < 2:
            # Likely a plain name without -l columns; treat as file
            name = line.strip()
            if name and not name.endswith("/"):
                files.append(name)
            continue

        flags = parts[0]
        # Directories: flag column begins with 'd' or 'D'
        if flags[:1] in ("d", "D"):
            continue
        # File line — name is the last column
        if len(parts) >= 5:
            name = parts[4].strip()
        else:
            name = parts[-1].strip()
        if not name or name.endswith("/"):
            continue
        files.append(name)

    return files[:MAX_FILES_RA]

def _build_new_name(old_name: str, pattern: str, replacement: str, index: int) -> str:
    """
    Patterns: prefix | suffix | replace (old|new) | regex (pat|repl) | number | channel
    """
    name, ext = posixpath.splitext(old_name)
    if pattern == "prefix":
        return f"{replacement}{old_name}"
    elif pattern == "suffix":
        return f"{name}{replacement}{ext}"
    elif pattern == "replace":
        parts = replacement.split("|", 1)
        if len(parts) == 2:
            return old_name.replace(parts[0], parts[1])
    elif pattern == "regex":
        parts = replacement.split("|", 1)
        if len(parts) == 2:
            try:
                return re.sub(parts[0], parts[1], old_name)
            except re.error:
                pass
    elif pattern == "number":
        return f"{str(index).zfill(5)}{ext}"
    elif pattern == "channel":
        ch = replacement.strip()
        if not ch.startswith("@"):
            ch = f"@{ch}"
        return f"{ch} ({index}){ext}"
    return old_name

def _rename_one_sync(file_obj, pattern: str, replacement: str, idx: int) -> bool:
    """
    Runs in thread pool.
    file_obj may be:
      • a (handle, node) tuple  — fast path, uses mega_client.rename(tuple, new_name)
      • a remote path string    — legacy / fallback, uses mega-mv CLI
    Returns True=renamed, False=skipped (same name), raises on error.
    """
    # ── Fast tuple path (mega.py API) ──
    if isinstance(file_obj, tuple) and len(file_obj) == 2:
        node = file_obj[1]
        try:
            old_name = node["a"]["n"]
        except Exception:
            return False
        new_name = _build_new_name(old_name, pattern, replacement, idx)
        if new_name == old_name:
            return False
        if mega_client is None:
            raise Exception("Mega client not available for tuple rename")
        mega_client.rename(file_obj, new_name)
        return True

    # ── Legacy/fallback string-path mode ──
    file_path = file_obj
    old_name = posixpath.basename(file_path)
    new_name = _build_new_name(old_name, pattern, replacement, idx)
    if new_name == old_name:
        return False   # nothing to do

    # Primary: Mega API client (fast)
    try:
        if mega_client is not None:
            mega_client.rename(file_path, new_name)
            return True
    except Exception:
        pass   # fall through to MegaCMD

    # Fallback: mega-mv CLI (reliable)
    parent   = posixpath.dirname(file_path)
    new_path = f"{parent}/{new_name}" if parent not in ("", "/") else f"/{new_name}"
    out, err, code = _run_cmd_ra(["mega-mv", file_path, new_path])
    if code != 0:
        raise Exception(err or out)
    return True


# Rename session store: msg_id -> {link, files, total, uid, pattern?, replacement?}
renameall_sessions = {}
# user_id -> msg_id (the renameall session awaiting a text reply)
waiting_for_renameall_text = {}

_RENAMEALL_OPTS = [
    ("prefix",  "✏️ Add Prefix"),
    ("suffix",  "✏️ Add Suffix"),
    ("replace", "🔁 Replace Text"),
    ("channel", "📣 Add Channel Name"),
]

_RENAMEALL_PROMPTS = {
    "prefix":  "📥 Send the text you want to add as <b>prefix</b>.",
    "suffix":  "📥 Send the text you want to add as <b>suffix</b> (before extension).",
    "replace": "📥 Send the replacement in <code>OldText|NewText</code> format.",
    "channel": "📥 Send the channel name (e.g. <code>@MyChannel</code>).",
}


def _renameall_options_kb(msg_id: int) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for pat, label in _RENAMEALL_OPTS:
        row.append(InlineKeyboardButton(label, callback_data=f"ra_opt|{msg_id}|{pat}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"ra_cancel|{msg_id}")])
    return InlineKeyboardMarkup(rows)


def _mega_find_all_files() -> list:
    """List every file across the entire logged-in MEGA account using
    `mega-find / --type=f`. Returns absolute remote paths."""
    out, err, code = _run_cmd_ra(["mega-find", "/", "--type=f"], timeout=180)
    if code != 0:
        raise Exception(err or out or "mega-find failed")
    files = [line.strip() for line in out.split("\n") if line.strip()]
    return files[:MAX_FILES_RA]


def _mega_get_all_file_nodes() -> list:
    """Walk the logged-in mega.py client's account tree and return a
    natural-sorted list of (handle, node) tuples for every file
    (node['t'] == 0). Capped at 10000.
    Falls back to an empty list if the client isn't ready."""
    try:
        if mega_client is None:
            return []
        all_nodes = mega_client.get_files()  # {handle: node}
        files = []
        for h, n in (all_nodes or {}).items():
            try:
                if n.get("t") == 0 and n.get("a") and n["a"].get("n"):
                    files.append((h, n))
            except Exception:
                continue
        files.sort(key=lambda kv: natural_sort_key(kv[1]["a"]["n"]))
        return files[:10000]
    except Exception as _e:
        print(f"[_mega_get_all_file_nodes] {_e}")
        return []


@app.on_message(filters.command("renameall"))
async def renameall_handler(c, m):
    """
    Usage: /renameall
    Renames files across the entire MEGA account (login required).
    Pick a rename option from the inline keyboard and send the value.
    """
    msg = await m.reply_text("🔍 <b>Scanning entire Mega account...</b>")

    # ── Fetch file list across the entire account ──
    # Prefer the fast in-memory tree from mega.py (tuples); fall back to
    # mega-find paths if the API client isn't ready or returns nothing.
    files = []
    try:
        if mega_client is not None:
            files = await asyncio.wait_for(
                asyncio.to_thread(_mega_get_all_file_nodes),
                timeout=180
            )
    except asyncio.TimeoutError:
        files = []
    except Exception as _e:
        print(f"[renameall_handler] tuple scan failed: {_e}")
        files = []

    if not files:
        try:
            files = await asyncio.wait_for(
                asyncio.to_thread(_mega_find_all_files),
                timeout=300
            )
        except asyncio.TimeoutError:
            return await msg.edit_text("❌ Account scan timeout. Please try again.")
        except Exception as e:
            return await msg.edit_text(f"❌ <b>Account scan failed:</b>\n<code>{clean_html(str(e))}</code>")

    total = len(files)
    if total == 0:
        return await msg.edit_text("📂 Mega account mein koi file nahi mili. Pehle /login karein.")

    # Store pending rename job keyed by original message id
    renameall_sessions[m.id] = {
        "files":       files,
        "total":       total,
        "uid":         m.from_user.id,
        "pattern":     None,
        "replacement": None,
    }

    first = files[0]
    if isinstance(first, tuple):
        sample_name = first[1]["a"]["n"]
    else:
        sample_name = posixpath.basename(first)
    sample = clean_html(sample_name[:60])
    await msg.edit_text(
        f"📂 <b>Files found: {total:,}</b> (entire account)\n\n"
        f"<b>Sample:</b> <code>{sample}</code>\n\n"
        f"<b>Choose a rename option:</b>",
        reply_markup=_renameall_options_kb(m.id)
    )


@app.on_callback_query(filters.regex(r"^ra_opt\|"))
async def ra_option_cb(c, cb):
    parts = cb.data.split("|")
    msg_id  = int(parts[1])
    pattern = parts[2]
    session = renameall_sessions.get(msg_id)
    if not session:
        return await cb.answer("❌ Session expired. Run /renameall again.", show_alert=True)
    if cb.from_user.id != session["uid"]:
        return await cb.answer("❌ Not your session!", show_alert=True)

    session["pattern"] = pattern
    waiting_for_renameall_text[cb.from_user.id] = msg_id
    await cb.answer()
    prompt = _RENAMEALL_PROMPTS.get(pattern, "Send the value:")
    with suppress(Exception):
        await cb.message.edit_text(
            f"{prompt}\n\nSend /cancrename to cancel."
        )


@app.on_message(filters.command("cancrename") & filters.private)
async def cancrename_cmd(c, m):
    uid = m.from_user.id
    msg_id = waiting_for_renameall_text.pop(uid, None)
    if msg_id is not None:
        renameall_sessions.pop(msg_id, None)
        await m.reply_text("❌ Rename input cancelled.")
    else:
        await m.reply_text("Nothing to cancel.")


# Capture user text replies for several input flows (proxy, bsettings, renameall).
# Runs in a high-priority group so it fires before generic command handlers.
@app.on_message(filters.text & ~filters.via_bot, group=-1)
async def renameall_text_capture(c, m):
    if not m.from_user:
        return
    uid = m.from_user.id
    text = (m.text or "").strip()

    # ─── Task 5: capture proxy URL input ───
    if uid in waiting_for_proxy:
        if text.startswith("/"):
            return
        waiting_for_proxy.pop(uid, None)
        if text.lower() in ("none", "off", "clear"):
            await clear_user_proxy(uid)
            await m.reply_text("🗑 Proxy cleared.")
        elif not (text.startswith("http://") or text.startswith("https://") or text.startswith("socks5://") or text.startswith("socks4://")):
            await m.reply_text("❌ Invalid proxy. Must start with http://, https://, socks5:// or socks4://")
        else:
            await set_user_proxy(uid, text)
            await m.reply_text(f"✅ Proxy saved:\n<code>{clean_html(text)}</code>")
        m.stop_propagation()
        return

    # ─── Task 4: capture /bsettings numeric input ───
    if uid in waiting_for_bsetting:
        if text.startswith("/"):
            return
        key = waiting_for_bsetting.pop(uid)
        try:
            if "size" in key:
                val = float(text)
            else:
                val = int(text)
            if val < 0:
                raise ValueError("must be ≥ 0")
        except Exception:
            return await m.reply_text("❌ Invalid number. Try again via /bsettings.")
        ok = await set_bsetting(key, val)
        await m.reply_text(("✅ Updated " if ok else "❌ Failed to update ") + f"<code>{key}</code> = <code>{val}</code>")
        m.stop_propagation()
        return

    if uid not in waiting_for_renameall_text:
        return
    # Renameall flow only in private (kept original behaviour)
    if m.chat.type != enums.ChatType.PRIVATE:
        return
    # Allow real commands to pass through to their own handlers
    if text.startswith("/"):
        return

    msg_id = waiting_for_renameall_text.pop(uid)
    session = renameall_sessions.get(msg_id)
    if not session:
        return await m.reply_text("❌ Session expired. Run /renameall again.")

    pattern = session.get("pattern")
    if not pattern:
        return await m.reply_text("❌ No rename option selected. Run /renameall again.")

    # Validate `replace` format
    if pattern == "replace" and "|" not in text:
        waiting_for_renameall_text[uid] = msg_id
        return await m.reply_text(
            "❌ Format must be <code>OldText|NewText</code>. Try again."
        )

    session["replacement"] = text

    files   = session["files"]
    total   = session["total"]

    def _basename_of(item):
        if isinstance(item, tuple):
            try:
                return item[1]["a"]["n"]
            except Exception:
                return ""
        return posixpath.basename(item)

    src1    = _basename_of(files[0])
    ex1     = _build_new_name(src1, pattern, text, 1)
    src2    = _basename_of(files[min(1, total-1)])
    ex2     = _build_new_name(src2, pattern, text, 2)

    await m.reply_text(
        f"📂 <b>Files: {total:,}</b>\n\n"
        f"👁 <b>Preview:</b>\n"
        f"• <code>{clean_html(src1[:50])}</code>\n"
        f"  → <code>{clean_html(ex1[:50])}</code>\n"
        f"• <code>{clean_html(src2[:50])}</code>\n"
        f"  → <code>{clean_html(ex2[:50])}</code>\n\n"
        f"<b>Pattern:</b> <code>{pattern}</code>\n"
        f"<b>Value:</b> <code>{clean_html(text)}</code>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🚀 Start Renaming!", callback_data=f"ra_confirm|{msg_id}"),
            InlineKeyboardButton("❌ Cancel",           callback_data=f"ra_cancel|{msg_id}"),
        ]])
    )
    m.stop_propagation()

@app.on_callback_query(filters.regex(r"^ra_cancel\|"))
async def ra_cancel_cb(c, cb):
    msg_id = int(cb.data.split("|")[1])
    renameall_sessions.pop(msg_id, None)
    await cb.answer("❌ Cancelled")
    with suppress(Exception): await cb.message.delete()


@app.on_callback_query(filters.regex(r"^ra_confirm\|"))
async def ra_confirm_cb(c, cb):
    msg_id  = int(cb.data.split("|")[1])
    session = renameall_sessions.pop(msg_id, None)
    if not session:
        await cb.answer("❌ Session expired. Run /renameall again.", show_alert=True)
        return
    await cb.answer()

    files       = session["files"]
    pattern     = session["pattern"]
    replacement = session["replacement"]
    total       = session["total"]
    status_msg  = cb.message

    # ── 2. Initial status message ──
    await status_msg.edit_text(
        f"🔄 <b>Renaming...</b>\n\n"
        f"<code>[░░░░░░░░░░░░░░░░░░░░]</code> 0%\n\n"
        f"📊 Total:   <code>{total:,}</code>\n"
        f"✅ Done:    <code>0</code>\n"
        f"❌ Failed:  <code>0</code>\n"
        f"⚡ Speed:   <code>—</code>\n"
        f"⏱ ETA:     <code>calculating...</code>"
    )

    # ── 3. Shared counters ──
    done    = 0
    failed  = 0
    loop    = asyncio.get_running_loop()
    sem     = asyncio.Semaphore(10)
    start_t = time.time()

    # ── 4. Single-file async wrapper ──
    async def rename_one(fp: str, idx: int):
        nonlocal done, failed
        async with sem:
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(_rename_executor, _rename_one_sync, fp, pattern, replacement, idx),
                    timeout=CMD_TIMEOUT_RA
                )
                if result:
                    done += 1
                # result=False → same name, skip silently
            except Exception as _e:
                failed += 1
                print(f"[renameall] [{idx}] {fp}: {_e}")

    # ── 5. Background progress ticker (every 5s) ──
    async def progress_ticker():
        while True:
            await asyncio.sleep(5)
            processed = done + failed
            elapsed   = max(time.time() - start_t, 0.1)
            speed     = processed / elapsed
            pct       = int(processed / total * 100) if total else 0
            filled    = pct // 5
            bar       = "▓" * filled + "░" * (20 - filled)
            eta       = int((total - processed) / speed) if speed > 0 and processed < total else 0
            eta_str   = f"{eta}s" if eta > 0 else "calculating..."
            with suppress(Exception):
                await status_msg.edit_text(
                    f"🔄 <b>Renaming...</b>\n\n"
                    f"<code>[{bar}]</code> {pct}%\n\n"
                    f"📊 Total:   <code>{total:,}</code>\n"
                    f"✅ Done:    <code>{done:,}</code>\n"
                    f"❌ Failed:  <code>{failed:,}</code>\n"
                    f"⚡ Speed:   <code>{speed:.1f} files/s</code>\n"
                    f"⏱ ETA:     <code>{eta_str}</code>"
                )

    # ── 6. Launch all tasks + ticker ──
    ticker   = asyncio.create_task(progress_ticker())
    all_jobs = [rename_one(fp, idx + 1) for idx, fp in enumerate(files)]
    await asyncio.gather(*all_jobs)
    ticker.cancel()

    # ── 7. Final summary ──
    elapsed  = max(time.time() - start_t, 0.1)
    avg_spd  = (done + failed) / elapsed
    skipped  = total - done - failed

    engine_used = "Mega API (fast)" if mega_client is not None else "MegaCMD (stable)"

    await status_msg.edit_text(
        f"🎉 <b>Rename Complete!</b>\n\n"
        f"<code>[{'▓' * 20}]</code> 100%\n\n"
        f"📊 Total:    <code>{total:,}</code>\n"
        f"✅ Renamed:  <code>{done:,}</code>\n"
        f"❌ Failed:   <code>{failed:,}</code>\n"
        f"⏭️ Skipped:  <code>{skipped:,}</code>\n\n"
        f"⚡ Avg Speed: <code>{avg_spd:.1f} files/s</code>\n"
        f"🕐 Time:     <code>{int(elapsed)}s</code>\n"
        f"🔧 Engine:   <code>{engine_used}</code>"
    )


# ─────────────────────────────────────────
# Queue manager
# ─────────────────────────────────────────
async def queue_manager(client, user_id):
    if is_processing.get(user_id, False):
        return
    is_processing[user_id] = True
    status_msg = await client.send_message(user_id, "⚙️ <b>Queue Started...</b>")
    processed = 0
    while user_queues.get(user_id):
        if status_msg.id in abort_dict:
            del abort_dict[status_msg.id]
        processed += 1
        current_queue_len = len(user_queues[user_id])
        task = user_queues[user_id].pop(0)
        task_info = f"Task {processed}/{processed + current_queue_len}"
        url_, msg_, mode_, target_, *extras = task
        rename_ = extras[0] if len(extras) > 0 else None
        seed_ = extras[1] if len(extras) > 1 else False
        await process_task(client, msg_, url_, mode_, target_, task_info,
                           status_msg=status_msg, rename=rename_, seed=seed_, user_id=user_id)
    is_processing[user_id] = False
    await client.send_message(user_id, "🏁 <b>All Queued Tasks Finished!</b>")

# ─────────────────────────────────────────
# /status — WZML-X global progress (Task 3)
# ─────────────────────────────────────────
@app.on_message(filters.command("status"))
async def status_cmd(c, m):
    chat_id = m.chat.id
    text = _format_global_status()
    sent = await m.reply_text(text, disable_web_page_preview=True)
    # Replace any previous global status message bookkeeping for this chat
    prev = GLOBAL_STATUS.get(chat_id)
    if prev:
        with suppress(Exception):
            prev.get("task") and prev["task"].cancel()
        with suppress(Exception):
            await prev["message"].delete()
    GLOBAL_STATUS[chat_id] = {"message": sent, "task": None}
    GLOBAL_STATUS[chat_id]["task"] = asyncio.create_task(_global_status_loop(chat_id))


# ─────────────────────────────────────────
# /bsettings — admin-only limits (Task 4)
# ─────────────────────────────────────────
def _bsettings_render(s):
    return (
        "🛠️ <b>Bot Settings (admin)</b>\n\n"
        f"• Max tasks/user: <code>{s['max_tasks_per_user']}</code>\n"
        f"• Max GB ytdl:    <code>{s['max_size_gb_ytdl']}</code>\n"
        f"• Max GB mdl:     <code>{s['max_size_gb_mdl']}</code>\n"
        f"• Max GB bdl:     <code>{s['max_size_gb_bdl']}</code>\n"
        f"• Max GB leech:   <code>{s['max_size_gb_leech']}</code>\n\n"
        "Tap a row to change. Then send the new value as a plain number.\n"
        "Use <code>/cancbs</code> to cancel input."
    )

def _bsettings_kb():
    rows = [
        [InlineKeyboardButton("Max tasks/user", callback_data="bs|max_tasks_per_user")],
        [InlineKeyboardButton("Max GB ytdl",    callback_data="bs|max_size_gb_ytdl")],
        [InlineKeyboardButton("Max GB mdl",     callback_data="bs|max_size_gb_mdl")],
        [InlineKeyboardButton("Max GB bdl",     callback_data="bs|max_size_gb_bdl")],
        [InlineKeyboardButton("Max GB leech",   callback_data="bs|max_size_gb_leech")],
        [InlineKeyboardButton("✖ Close",        callback_data="bs|close")],
    ]
    return InlineKeyboardMarkup(rows)

@app.on_message(filters.command("bsettings"))
async def bsettings_cmd(c, m):
    if m.from_user.id != OWNER_ID:
        return await m.reply_text("❌ Owner only.")
    s = await get_bsettings()
    await m.reply_text(_bsettings_render(s), reply_markup=_bsettings_kb())

@app.on_message(filters.command("cancbs"))
async def cancbs_cmd(c, m):
    waiting_for_bsetting.pop(m.from_user.id, None)
    await m.reply_text("✅ <b>BSetting input cancelled.</b>")

@app.on_callback_query(filters.regex(r"^bs\|"))
async def bs_cb(c, cb):
    if cb.from_user.id != OWNER_ID:
        return await cb.answer("❌ Owner only.", show_alert=True)
    key = cb.data.split("|", 1)[1]
    if key == "close":
        waiting_for_bsetting.pop(cb.from_user.id, None)
        with suppress(Exception): await cb.message.delete()
        return await cb.answer("Closed")
    if key not in DEFAULT_BSETTINGS:
        return await cb.answer("❌ Unknown setting", show_alert=True)
    waiting_for_bsetting[cb.from_user.id] = key
    await cb.answer(f"Send new value for {key}")
    with suppress(Exception):
        await cb.message.reply_text(
            f"✏️ Send the new value for <code>{key}</code> as a plain number.\n"
            f"Use /cancbs to cancel."
        )


# ─────────────────────────────────────────
# /tgdl — Telegram restricted-channel downloader (Task 6)
# Usage:
#   /tgdl <link>                    — single message
#   /tgdl <link> <count>            — range starting at link, downloading <count> msgs
#   /tgdl <from_link> <to_link>     — explicit range
# ─────────────────────────────────────────
_TGLINK_RE = re.compile(
    r"^https?://t\.me/(?:c/(?P<cid>\d+)|(?P<uname>[^/]+))/(?P<mid>\d+)/?",
    re.IGNORECASE,
)

def _parse_tg_link(link: str):
    m = _TGLINK_RE.match(link.strip())
    if not m:
        return None
    cid = m.group("cid")
    uname = m.group("uname")
    mid = int(m.group("mid"))
    if cid:
        return (int(f"-100{cid}"), mid)
    return (uname, mid)

@app.on_message(filters.command("tgdl"))
async def tgdl_cmd(c, m):
    if user_app is None:
        return await m.reply_text(
            "❌ <b>Restricted-channel downloader is disabled.</b>\n"
            "Set <code>USER_SESSION_STRING</code> env to enable <code>/tgdl</code>."
        )

    # ── Argument parsing ──────────────────────────────────────────────────────
    # Supported forms:
    #   /tgdl <link>                   → single message
    #   /tgdl <link> <count>           → N messages from link
    #   /tgdl <from_link> <to_link>    → explicit range
    #   /tgdl <link> -all              → from link to latest message
    raw_text = m.text or ""
    parts = raw_text.split(maxsplit=2)
    if len(parts) < 2:
        return await m.reply_text(
            "ℹ️ <b>Usage:</b>\n"
            "<code>/tgdl &lt;link&gt;</code>\n"
            "<code>/tgdl &lt;link&gt; &lt;count&gt;</code>\n"
            "<code>/tgdl &lt;from_link&gt; &lt;to_link&gt;</code>\n"
            "<code>/tgdl &lt;link&gt; -all</code>  — from link to latest"
        )

    a = _parse_tg_link(parts[1])
    if not a:
        return await m.reply_text("❌ <b>Invalid Telegram link.</b>")
    chat_a, mid_a = a
    mid_b = mid_a
    use_all = False  # -all flag

    if len(parts) >= 3:
        tok = parts[2].strip()
        if tok.lower() == "-all":
            use_all = True
        else:
            b = _parse_tg_link(tok) if tok.lower().startswith("http") else None
            if b:
                chat_b, mid_b2 = b
                if chat_b != chat_a:
                    return await m.reply_text("❌ <b>Range must be in same chat.</b>")
                mid_b = mid_b2
            else:
                try:
                    cnt = max(1, min(200, int(tok)))
                    mid_b = mid_a + cnt - 1
                except Exception:
                    return await m.reply_text("❌ <b>Bad count or to-link.</b>")

    if not use_all and mid_b < mid_a:
        mid_a, mid_b = mid_b, mid_a

    uid = m.from_user.id

    # ── Fix 1: Dump channel routing ───────────────────────────────────────────
    active_dump = await get_active_dump(uid)
    target_chat = active_dump["id"] if active_dump else m.chat.id
    dump_label  = active_dump["title"] if active_dump else "PM"

    total_count = 0 if use_all else (mid_b - mid_a + 1)
    if use_all:
        range_label = f"<code>{mid_a}</code> → Latest"
    else:
        range_label = f"<code>{mid_a}–{mid_b}</code>"

    status = await m.reply_text(
        f"📥 <b>TGDL</b>\n"
        f"Chat: <code>{chat_a}</code>\n"
        f"Range: {range_label}\n"
        f"Dump: <b>{clean_html(dump_label)}</b>\n\n"
        f"⚙️ <b>Initializing...</b>"
    )

    # Register in ACTIVE_TASKS so /status shows this job with a Cancel button
    ACTIVE_TASKS[status.id] = {
        "user_id":    uid,
        "user_name":  m.from_user.first_name or "user",
        "name":       f"TGDL {chat_a}",
        "action":     "📥 Downloading (TGDL)...",
        "current":    0, "total": 0, "speed": 0, "eta": "0s",
        "start_time": time.time(),
        "engine":     "PyroTgfork",
    }

    started = False
    try:
        if not user_app.is_connected:
            await user_app.start()
            started = True
    except Exception as _e:
        ACTIVE_TASKS.pop(status.id, None)
        return await status.edit_text(f"❌ User client start failed: <code>{clean_html(str(_e))}</code>")

    # ── Helper: best-effort filename ──────────────────────────────────────────
    def _fname_from_msg(msg, fallback="file"):
        for attr in ("video", "audio", "document", "animation", "voice", "video_note"):
            obj = getattr(msg, attr, None)
            if obj:
                return getattr(obj, "file_name", None) or f"{attr}_{msg.id}"
        if msg.photo:
            return f"photo_{msg.id}.jpg"
        return fallback

    # ── Fix 2: Rate-limited progress factory (2-second throttle per callback) ─
    def _make_progress(fname_display: str, file_idx: int, phase: str):
        """
        Returns a Pyrogram-compatible async progress callback.
        - Own 2-second rate-limit (faster than update_progress_ui's 5s default).
        - Clears progress_status before calling so the internal 5s throttle is
          bypassed and our 2s window is authoritative.
        - Passes engine='PyroTgfork' for a consistent progress bar look.
        """
        start_ts = time.time()
        last_fired = [0.0]
        label = f"File {file_idx}/{total_count}" if total_count > 0 else f"File {file_idx}"

        async def _cb(current, total):
            if total == 0 or status.id in abort_dict:
                return
            now = time.time()
            # Allow final 100% update through even if <2s since last
            if now - last_fired[0] < 2.0 and current < total:
                return
            last_fired[0] = now
            # Bypass internal 5s throttle so our 2s window controls rate
            progress_status.pop(status.id, None)
            await update_progress_ui(
                current, total, status, start_ts,
                phase, fname_display,
                task_info=label,
                engine="PyroTgfork",
            )
        return _cb

    # ── Helper: download + upload one message to target_chat ─────────────────
    ok, fail = 0, 0

    async def _process_one(src, file_idx: int):
        """Download and upload a single non-group message. Returns True on success."""
        nonlocal ok, fail
        # Plain text messages
        if src.text and not src.media:
            try:
                await c.send_message(target_chat, src.text.html or str(src.text))
                ok += 1
                return True
            except Exception:
                fail += 1
                return False
        # No media and no text
        if not src.media:
            return False
        fp = None
        try:
            fname_d = _fname_from_msg(src, f"file_{src.id}")
            dl_cb = _make_progress(fname_d, file_idx, "📥 Downloading...")
            fp = await user_app.download_media(src, in_memory=False, progress=dl_cb)
            if not fp:
                fail += 1
                return False
            cap = src.caption or ""
            ul_cb = _make_progress(fname_d, file_idx, "📤 Uploading...")
            if src.photo:
                await c.send_photo(target_chat, fp, caption=cap, progress=ul_cb)
            elif src.video:
                await c.send_video(target_chat, fp, caption=cap, progress=ul_cb)
            elif src.audio:
                await c.send_audio(target_chat, fp, caption=cap, progress=ul_cb)
            elif src.voice:
                await c.send_voice(target_chat, fp, caption=cap, progress=ul_cb)
            elif src.animation:
                await c.send_animation(target_chat, fp, caption=cap, progress=ul_cb)
            else:
                await c.send_document(target_chat, fp, caption=cap, progress=ul_cb)
            ok += 1
            return True
        except Exception as _se:
            fail += 1
            print(f"[tgdl] send err: {_se}")
            return False
        finally:
            if fp:
                with suppress(Exception): os.remove(fp)

    async def _process_group(src, file_idx: int):
        """Download + upload a full media group."""
        nonlocal ok, fail
        try:
            group = await user_app.get_media_group(chat_a, src.id)
        except Exception:
            group = [src]
        files = []
        for g in group:
            fname_d = _fname_from_msg(g, f"file_{g.id}")
            dl_cb = _make_progress(fname_d, file_idx, "📥 Downloading (group)...")
            try:
                fp = await user_app.download_media(g, in_memory=False, progress=dl_cb)
                if fp:
                    files.append((g, fp))
            except Exception as _ge:
                print(f"[tgdl] grp dl err: {_ge}")
        if not files:
            fail += 1
            return
        # Media groups: no per-file upload progress, show brief status
        progress_status.pop(status.id, None)
        with suppress(Exception):
            lbl = f"File {file_idx}/{total_count}" if total_count > 0 else f"File {file_idx}"
            await status.edit_text(
                f"📤 <b>Uploading group</b> ({len(files)} files)…\n"
                f"<b>{lbl}</b> → <b>{clean_html(dump_label)}</b>"
            )
        from pyrogram.types import (
            InputMediaPhoto, InputMediaVideo,
            InputMediaDocument, InputMediaAudio,
        )
        media = []
        for g, fp in files:
            cap = (g.caption or "") if g else ""
            if g.photo:    media.append(InputMediaPhoto(fp, caption=cap))
            elif g.video:  media.append(InputMediaVideo(fp, caption=cap))
            elif g.audio:  media.append(InputMediaAudio(fp, caption=cap))
            else:          media.append(InputMediaDocument(fp, caption=cap))
        try:
            await c.send_media_group(target_chat, media)
            ok += 1
        except Exception as _se:
            fail += 1
            print(f"[tgdl] send group err: {_se}")
        finally:
            for _g, fp in files:
                with suppress(Exception): os.remove(fp)

    # ── Main download loop ────────────────────────────────────────────────────
    seen_groups: set = set()
    try:
        if use_all:
            # ── Fix 3: -all mode — iterate from mid_a to newest ───────────────
            file_idx = 0
            async for src in user_app.get_chat_history(
                chat_a, offset_id=mid_a - 1, reverse=True, limit=0
            ):
                if status.id in abort_dict:
                    break
                if not src or getattr(src, "empty", False):
                    continue
                file_idx += 1
                try:
                    gid = getattr(src, "media_group_id", None)
                    if gid:
                        if gid in seen_groups:
                            continue
                        seen_groups.add(gid)
                        await _process_group(src, file_idx)
                    else:
                        await _process_one(src, file_idx)
                except Exception as _le:
                    fail += 1
                    print(f"[tgdl -all] loop err mid={src.id}: {_le}")
        else:
            # ── Range / single mode ───────────────────────────────────────────
            for file_idx, mid in enumerate(range(mid_a, mid_b + 1), start=1):
                if status.id in abort_dict:
                    break
                try:
                    src = await user_app.get_messages(chat_a, mid)
                    if not src or getattr(src, "empty", False):
                        continue
                    gid = getattr(src, "media_group_id", None)
                    if gid:
                        if gid in seen_groups:
                            continue
                        seen_groups.add(gid)
                        await _process_group(src, file_idx)
                    else:
                        await _process_one(src, file_idx)
                except Exception as _le:
                    fail += 1
                    print(f"[tgdl] loop err mid={mid}: {_le}")
    finally:
        if started:
            with suppress(Exception): await user_app.stop()

    # Clear tracking and show final summary
    progress_status.pop(status.id, None)
    ACTIVE_TASKS.pop(status.id, None)
    cancelled = status.id in abort_dict
    abort_dict.pop(status.id, None)
    suffix = " (cancelled)" if cancelled else ""
    await status.edit_text(
        f"🏁 <b>TGDL done{suffix}</b>\n"
        f"✅ {ok}  •  ❌ {fail}\n"
        f"Dump: <b>{clean_html(dump_label)}</b>"
    )


# ─────────────────────────────────────────
# Start / Ping / Restart
# ─────────────────────────────────────────
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(c, m):
    uid = m.from_user.id

    # ─── User Tracking ───
    try:
        user = await users_col.find_one({"_id": uid})
        if not user:
            await users_col.insert_one({
                "_id": uid,
                "dumps": [],
                "active_dump": None,
                "warns": 0,
                "is_banned": False,
            })
    except Exception as _te:
        print(f"[user_track] {_te}")

    if await is_user_banned(uid):
        return await m.reply_text("❌ You are banned from using this bot.")

    # ─── Force-PM check: bot needs to be able to DM user ───
    if m.chat.type != enums.ChatType.PRIVATE:
        # Group /start: verify bot can PM user
        try:
            from pyrogram.errors import PeerIdInvalid
            await c.send_chat_action(uid, enums.ChatAction.TYPING)
        except Exception:
            try:
                bot_me = await c.get_me()
                btn = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Start me in PM",
                                         url=f"https://t.me/{bot_me.username}?start=1")
                ]])
                await m.reply_text(
                    f"⚠️ {m.from_user.mention}, please start me in PM first "
                    f"so I can send you files!",
                    reply_markup=btn
                )
            except Exception:
                pass
            return

    # ─── Auth check: only AUTH_GROUP members (if configured) ───
    if AUTH_GROUP and AUTH_GROUP != 0 and m.chat.type == enums.ChatType.PRIVATE:
        try:
            member = await c.get_chat_member(AUTH_GROUP, uid)
            from pyrogram.enums import ChatMemberStatus
            if member.status in (ChatMemberStatus.BANNED, ChatMemberStatus.LEFT):
                raise Exception("Not a member")
        except Exception:
            if JOIN_LINK:
                btn = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔗 Join Group to Use Bot", url=JOIN_LINK)
                ]])
                return await m.reply_text(
                    f"⚠️ <b>Access Restricted!</b>\n\n"
                    f"You must be a member of the authorized group to use this bot.\n"
                    f"Managed by <b>{DEV_NAME}</b>.",
                    reply_markup=btn
                )

    welcome_text = (
        f"👋 <b>Hello {clean_html(m.from_user.first_name)}!</b>\n\n"
        "🤖 <b>Advanced All-in-One Leech & Mega Bot</b>\n\n"
        "This bot can:\n"
        "📥 Download from <b>YouTube, Direct Links, Torrents, Mega, "
        "Bunkr, TeraBox, HentaiCity, PornHub, WatchHentai, Dailymotion</b>\n"
        "✏️ Bulk rename thousands of Mega files in seconds (<b>/renameall</b>)\n"
        "📤 Upload to Telegram as Video or Document with auto-thumbnail\n"
        "⚙️ Customise send mode, custom thumbnails, dump channels & more\n\n"
        "👉 Use /help to see all commands & usage.\n\n"
        f"⚙️ <b>Engine:</b> aria2c <code>{ARIA2C_VERSION}</code> | "
        f"yt-dlp <code>{YTDLP_VERSION}</code> | pyrofork <code>{PYROGRAM_VERSION}</code>"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("👨‍💻 Developer", url="tg://user?id=8493596199"),
        InlineKeyboardButton("📖 Help",      callback_data="open_help"),
    ]])
    await m.reply_text(welcome_text, reply_markup=keyboard)


@app.on_callback_query(filters.regex(r"^open_help$"))
async def open_help_cb(c, cb):
    await cb.answer()
    await _send_help(cb.message.reply_text)


@app.on_message(filters.command("help"))
async def help_cmd(c, m):
    await _send_help(m.reply_text)


async def _send_help(reply_fn):
    help_text = (
        "📖 <b>Command Reference</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "📥 <b>Download Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <code>/dl &lt;url&gt;</code>\n"
        "  Direct HTTP download. Add <code>-n name</code> to rename.\n\n"

        "• <code>/ytdl &lt;url&gt;</code>\n"
        "  YouTube / any site via yt-dlp. Shows quality picker.\n"
        "  Flags: <code>-n name</code> rename · <code>-b</code> bulk mode\n\n"

        "• <code>/leech &lt;url or magnet&gt;</code>\n"
        "  Torrent / magnet download via aria2c.\n"
        "  Flag: <code>-s</code> to keep seeding after download\n\n"

        "• <code>/mdl &lt;mega_url&gt;</code>\n"
        "  Download from Mega.nz (public file/folder).\n\n"

        "• <code>/scriptdl &lt;url&gt;</code>\n"
        "  Bypass downloader for HentaiCity, PornHub, WatchHentai.\n"
        "  Shows quality selection buttons.\n\n"

        "• <code>/teradl &lt;url&gt;</code>\n"
        "  TeraBox / 1024TeraBox download via API.\n\n"

        "• <code>/bdl &lt;url&gt;</code>\n"
        "  Bunkr single file or full album download.\n\n"

        "• <code>/playlist &lt;url&gt; [--quality 720]</code>\n"
        "  Full YouTube playlist download. Default quality: 1080p.\n\n"

        "• <code>/queue &lt;url&gt;</code>\n"
        "  Add URL to sequential download queue.\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "✏️ <b>Mega Renamer</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <code>/renameall &lt;folder_link&gt; | &lt;pattern&gt; | &lt;replacement&gt;</code>\n"
        "  Bulk rename all files in a Mega folder.\n"
        "  Patterns:\n"
        "  — <code>prefix</code>  → Add text before filename\n"
        "  — <code>suffix</code>  → Add text after filename (before ext)\n"
        "  — <code>replace</code> → <code>old|new</code> text replacement\n"
        "  — <code>regex</code>   → <code>pattern|replacement</code> regex sub\n"
        "  — <code>number</code>  → Sequential: 00001.ext, 00002.ext ...\n"
        "  — <code>channel</code> → @ch (1).ext, @ch (2).ext ...\n"
        "  Example: <code>/renameall mega.nz/folder/xx | prefix | @MyChannel - </code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "☁️ <b>Mega Account</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <code>/login email password</code> — MegaCMD login (required for /renameall)\n"
        "• <code>/logout</code>               — Logout from Mega\n"
        "• <code>/megainfo</code>             — Show account info\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <code>/usersettings</code>   — Toggle send mode (Media/Document) + set custom thumbnail\n"
        "• <code>/setdump</code>        — Set upload destination channel (forward any channel message)\n"
        "• <code>/dumps</code>          — Manage / switch dump channels\n"
        "• <code>/stopseed &lt;GID&gt;</code> — Stop a seeding torrent by its GID\n"
        "• <code>/ping</code>           — Check bot status & uptime\n"
        "• <code>/restart</code>        — Restart bot\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>Tips</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• No dump channel set? Files go to your PM automatically.\n"
        "• Set a thumbnail once via /usersettings — auto-applied to every upload.\n"
        "• /ytdl supports bulk mode: send multiple links separated by newlines with <code>-b</code>.\n"
        "• Reply to a .txt file + <code>/dl -b</code> to bulk download all URLs in the file.\n"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("👨‍💻 Developer: @ayuprime", url="tg://user?id=8493596199"),
    ]])
    await reply_fn(help_text, reply_markup=keyboard)

@app.on_message(filters.command("ping"))
async def ping_cmd(c, m):
    uptime = get_readable_time(int(time.time() - bot_start_time))
    await m.reply_text(
        f"🏓 <b>Bot is Alive!</b>\n"
        f"⏱ <b>Uptime:</b> <code>{uptime}</code>\n"
        f"⚙️ <b>aria2c:</b> <code>{ARIA2C_VERSION}</code>\n"
        f"🎬 <b>yt-dlp:</b> <code>{YTDLP_VERSION}</code>\n"
        f"📡 <b>pyrofork:</b> <code>{PYROGRAM_VERSION}</code>"
    )

# ─────────────────────────────────────────
# Admin: /broadcast /msg /ban /unban /warn   (OWNER_ID only)
# ─────────────────────────────────────────
@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_cmd(c, m):
    if len(m.command) < 2 and not m.reply_to_message:
        return await m.reply_text("❌ <b>Usage:</b> <code>/broadcast your message</code>")
    text = m.text.split(None, 1)[1] if len(m.command) >= 2 else (m.reply_to_message.text or "")
    if not text:
        return await m.reply_text("❌ Empty broadcast message.")
    status = await m.reply_text("📣 <b>Broadcasting...</b>")
    sent, failed = 0, 0
    async for u in users_col.find({}, {"_id": 1}):
        try:
            await c.send_message(u["_id"], text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.1)
    await status.edit_text(f"✅ <b>Broadcast Done</b>\n📨 Sent: {sent}\n❌ Failed: {failed}")


@app.on_message(filters.command("msg") & filters.user(OWNER_ID))
async def admin_msg_cmd(c, m):
    if len(m.command) < 3:
        return await m.reply_text("❌ <b>Usage:</b> <code>/msg user_id your message</code>")
    try:
        target_id = int(m.command[1])
    except ValueError:
        return await m.reply_text("❌ Invalid user_id.")
    text = m.text.split(None, 2)[2]
    try:
        await c.send_message(target_id, text)
        await m.reply_text(f"✅ Sent to <code>{target_id}</code>")
    except Exception as e:
        await m.reply_text(f"❌ Failed: <code>{clean_html(str(e))}</code>")


@app.on_message(filters.command("ban") & filters.user(OWNER_ID))
async def ban_cmd(c, m):
    if len(m.command) < 2:
        return await m.reply_text("❌ <b>Usage:</b> <code>/ban user_id</code>")
    try:
        target_id = int(m.command[1])
    except ValueError:
        return await m.reply_text("❌ Invalid user_id.")
    await users_col.update_one(
        {"_id": target_id},
        {"$set": {"is_banned": True}},
        upsert=True,
    )
    await m.reply_text(f"🚫 <b>Banned</b> <code>{target_id}</code>")
    try:
        await c.send_message(target_id, "🚫 You have been <b>banned</b> from using this bot.")
    except Exception:
        pass


@app.on_message(filters.command("unban") & filters.user(OWNER_ID))
async def unban_cmd(c, m):
    if len(m.command) < 2:
        return await m.reply_text("❌ <b>Usage:</b> <code>/unban user_id</code>")
    try:
        target_id = int(m.command[1])
    except ValueError:
        return await m.reply_text("❌ Invalid user_id.")
    await users_col.update_one(
        {"_id": target_id},
        {"$set": {"is_banned": False, "warns": 0}},
        upsert=True,
    )
    await m.reply_text(f"✅ <b>Unbanned</b> <code>{target_id}</code>")
    try:
        await c.send_message(target_id, "✅ You have been <b>unbanned</b>. Warns reset.")
    except Exception:
        pass


@app.on_message(filters.command("warn") & filters.user(OWNER_ID))
async def warn_cmd(c, m):
    if len(m.command) < 2:
        return await m.reply_text("❌ <b>Usage:</b> <code>/warn user_id</code>")
    try:
        target_id = int(m.command[1])
    except ValueError:
        return await m.reply_text("❌ Invalid user_id.")
    res = await users_col.find_one_and_update(
        {"_id": target_id},
        {"$inc": {"warns": 1}},
        upsert=True,
        return_document=True,
    )
    # Re-read to get authoritative count (works on older motor too)
    doc = await users_col.find_one({"_id": target_id}) or {}
    warns = int(doc.get("warns", 0))
    if warns >= 2:
        await users_col.update_one({"_id": target_id}, {"$set": {"is_banned": True}})
        await m.reply_text(f"⛔ <code>{target_id}</code> reached {warns} warns — <b>auto-banned</b>.")
        try:
            await c.send_message(
                target_id,
                f"⛔ You received warn #{warns} and have been <b>banned</b> from using this bot."
            )
        except Exception:
            pass
    else:
        await m.reply_text(f"⚠️ <code>{target_id}</code> warned ({warns}/2).")
        try:
            await c.send_message(
                target_id,
                f"⚠️ You have been warned by an admin ({warns}/2). Next warn = ban."
            )
        except Exception:
            pass


@app.on_message(filters.command("restart"))
async def restart_cmd(c, m):
    await m.reply_text("🔄 <b>Restarting Bot...</b>")
    os.execl(sys.executable, sys.executable, *sys.argv)

@app.on_callback_query(filters.regex(r"^cancel_scriptdl\|"))
async def cancel_scriptdl_cb(c, cb):
    """Task 5: cancel a running batch scriptdl task."""
    try:
        target_uid = int(cb.data.split("|")[1])
    except (IndexError, ValueError):
        return await cb.answer("❌ Bad data", show_alert=True)
    if cb.from_user.id != target_uid:
        return await cb.answer("❌ Not your task!", show_alert=True)
    abort_dict[target_uid] = True
    await cb.answer("🛑 Batch cancel requested — finishing current video then stopping.", show_alert=True)
    with suppress(Exception):
        await cb.message.edit_reply_markup(reply_markup=None)


@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_cb(c, cb):
    msg_id = int(cb.data.split("_")[1])
    abort_dict[msg_id] = True
    ACTIVE_TASKS.pop(msg_id, None)
    await cb.answer("🛑 Task stopped", show_alert=True)
    try:
        await cb.message.edit_text("🛑 <b>Task stopped by user</b>")
    except:
        pass

# ─────────────────────────────────────────
# Web UI
# ─────────────────────────────────────────
async def web_index(request):
    try:
        with open("index.html", "r") as f:
            html_content = f.read()
        return web.Response(text=html_content, content_type='text/html')
    except Exception as e:
        return web.Response(text=f"Error loading UI. Create index.html! Error: {e}", status=500)

async def web_api_get_files(request):
    task_id = request.query.get("id")
    if task_id in pending_selections:
        return web.json_response({"success": True, "files": pending_selections[task_id]["files"]})
    return web.json_response({"success": False, "error": "Invalid or Expired Link."})

async def web_api_submit(request):
    try:
        data = await request.json()
        task_id = data.get("id")
        selected_indexes = data.get("selected_indexes", [])
        if task_id in pending_selections:
            pending_selections[task_id]["selected"] = selected_indexes
            pending_selections[task_id]["action"] = "select"
            pending_selections[task_id]["status"] = "ready"
            return web.json_response({"success": True})
        return web.json_response({"success": False, "error": "Invalid Task ID"})
    except:
        return web.json_response({"success": False, "error": "Bad Request"})

# ─────────────────────────────────────────
# Main
# ─────────────────────────────────────────
async def main():
    await init_db()

    if shutil.which("aria2c"):
        subprocess.Popen([
            'aria2c',
            '--enable-rpc',
            '--rpc-listen-all=true',
            '--rpc-listen-port=6800',
            '--daemon',
            '--allow-overwrite=true',
            '--auto-file-renaming=false',
            '--bt-stop-timeout=0',
            '--seed-time=0',
            '--max-connection-per-server=16',
            '--split=16',
            '--min-split-size=10M',
            '--max-concurrent-downloads=5',
            '--follow-torrent=mem',
        ])
        await asyncio.sleep(3)
        global aria2
        try:
            aria2 = aria2p.API(aria2p.Client(host="http://localhost", port=6800, secret=""))
            print(f"✅ aria2c {ARIA2C_VERSION} connected")
        except Exception as e:
            print(f"❌ aria2 connect failed: {e}")

    app_web = web.Application()
    app_web.router.add_get("/", web_index)
    app_web.router.add_get("/api/files", web_api_get_files)
    app_web.router.add_post("/api/submit", web_api_submit)

    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await app.start()
    # Start the optional user client used by /tgdl
    if user_app is not None:
        try:
            await user_app.start()
            print("👤 User client started (TGDL ready)")
        except Exception as _ue:
            print(f"⚠️ user_app.start failed: {_ue}")
    print(f"🤖 Bot Started | aria2c {ARIA2C_VERSION} | pyrofork {PYROGRAM_VERSION}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
