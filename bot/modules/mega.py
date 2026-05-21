"""
/mdl, /login, /logout, /megainfo — Mega download & account management.
"""

import asyncio
import os
import re
import shutil
import subprocess
import time
from contextlib import suppress

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.database.mongo import is_user_banned, get_active_dump
from bot.helper.progress import update_progress_ui
from bot.helper.task_manager import abort_dict
from bot.helper.time_format import clean_html, humanbytes
from bot.helper.uploader import handle_upload_split
from bot.extractors.mega_utils import megacmd_login, megacmd_download


_MEGA_PROG_RE = re.compile(
    r"\(\s*([\d.]+)\s*/\s*([\d.]+)\s*([KMGT]?i?B)\s*:\s*([\d.]+)\s*%?\s*\)",
    re.IGNORECASE,
)


def _parse_bytes(num, unit):
    try:
        n = float(num)
    except Exception:
        return 0
    u = (unit or "").strip().upper()
    mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4,
            "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}.get(u, 1)
    return int(n * mult)


async def _mega_download_with_progress(url, out_dir, msg):
    os.makedirs(out_dir, exist_ok=True)
    start = time.time()
    proc  = await asyncio.create_subprocess_exec(
        "mega-get", url, out_dir,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    err_acc = []
    buf = bytearray()
    last_ui = 0.0

    assert proc.stdout is not None
    while True:
        chunk = await proc.stdout.read(256)
        if not chunk:
            break
        buf.extend(chunk)
        while True:
            nl = -1
            for i, b in enumerate(buf):
                if b in (0x0A, 0x0D):
                    nl = i
                    break
            if nl < 0:
                break
            line = bytes(buf[:nl]).decode(errors="ignore").strip()
            del buf[:nl + 1]
            if not line:
                continue
            err_acc.append(line)
            if msg.id in abort_dict:
                with suppress(Exception):
                    proc.terminate()
                return [], "CANCELLED"
            m = _MEGA_PROG_RE.search(line)
            if not m:
                continue
            cur_b = _parse_bytes(m.group(1), m.group(3))
            tot_b = _parse_bytes(m.group(2), m.group(3))
            now   = time.time()
            if now - last_ui < 2 or tot_b <= 0:
                continue
            last_ui = now
            with suppress(Exception):
                await update_progress_ui(cur_b, tot_b, msg, start, "📥 Mega DL...", engine="MegaAPI")

    await proc.wait()
    if msg.id in abort_dict:
        return [], "CANCELLED"
    if proc.returncode != 0:
        tail = " | ".join(err_acc[-6:]) if err_acc else "mega-get failed"
        return [], f"MegaCMD Error: {tail}"

    files = []
    for root, _, fnames in os.walk(out_dir):
        for fname in fnames:
            files.append(os.path.join(root, fname))
    return files, None


@app.on_message(filters.command("mdl"))
async def mega_dl_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")
    args = m.command
    if len(args) < 2:
        return await m.reply_text("❌ Usage: /mdl mega.nz/...")

    url = args[1]
    uid = m.from_user.id
    msg = await m.reply_text(f"📥 <b>Mega DL starting...</b>\n<code>{clean_html(url[:80])}</code>")

    out_dir = f"downloads/mega_{int(time.time())}"
    files, err = await _mega_download_with_progress(url, out_dir, msg)

    if err:
        return await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")

    if not files:
        return await msg.edit_text("❌ No files downloaded.")

    active_dump = await get_active_dump(uid)
    target_chat = active_dump["id"] if active_dump else None

    for i, fp in enumerate(files, 1):
        await handle_upload_split(
            c, msg, fp, "User",
            task_info=f"File {i}/{len(files)}",
            user_id=uid, target_chat=target_chat, start_time=time.time(),
        )
    shutil.rmtree(out_dir, ignore_errors=True)
    dest = "dump" if target_chat else "PM"
    await msg.edit_text(f"✅ <b>Mega download done! {len(files)} file(s) → {dest}.</b>")


@app.on_message(filters.command("login") & filters.private)
async def login_cmd(c, m):
    args = m.command
    if len(args) < 3:
        return await m.reply_text("❌ Usage: /login email password")
    email, password = args[1], args[2]
    msg = await m.reply_text("🔐 <b>Logging in to Mega...</b>")
    try:
        ok, err = await asyncio.to_thread(megacmd_login, email, password)
    except Exception as e:
        return await msg.edit_text(f"❌ Login error: <code>{clean_html(str(e))}</code>")
    if ok:
        await msg.edit_text(f"✅ <b>Logged in!</b> <code>{clean_html(email)}</code>")
    else:
        await msg.edit_text(f"❌ <b>Login failed:</b> <code>{clean_html(err or 'unknown')}</code>")


@app.on_message(filters.command("logout") & filters.private)
async def logout_cmd(c, m):
    try:
        subprocess.run(["mega-logout"], capture_output=True, timeout=15)
        await m.reply_text("✅ <b>Logged out from Mega.</b>")
    except Exception as e:
        await m.reply_text(f"❌ <code>{clean_html(str(e))}</code>")


@app.on_message(filters.command("megainfo") & filters.private)
async def megainfo_cmd(c, m):
    try:
        r = subprocess.run(["mega-whoami", "-l"], capture_output=True, text=True, timeout=15)
        out = (r.stdout or r.stderr or "No info").strip()
        await m.reply_text(f"☁️ <b>Mega Account Info:</b>\n<pre>{clean_html(out[:1000])}</pre>")
    except Exception as e:
        await m.reply_text(f"❌ <code>{clean_html(str(e))}</code>")
