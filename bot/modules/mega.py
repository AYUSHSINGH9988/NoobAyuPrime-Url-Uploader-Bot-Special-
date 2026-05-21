"""
/mdl, /login, /logout, /megainfo — Mega download & account management.

Flags:
  -z / -zip      → zip files before upload
  -e / -extract  → extract downloaded archive(s)
  -vt            → show Video Tools menu after download
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


def _parse_mdl_args(text: str):
    """Return (url, do_zip, do_ext, do_vt) from /mdl command text."""
    tokens = text.split()
    do_zip = any(t in ("-z", "-zip")     for t in tokens)
    do_ext = any(t in ("-e", "-extract") for t in tokens)
    do_vt  = "-vt" in tokens
    skip   = {"/mdl", "-z", "-zip", "-e", "-extract", "-vt"}
    url_tokens = [t for t in tokens if t not in skip]
    url = url_tokens[0] if url_tokens else None
    return url, do_zip, do_ext, do_vt


async def _mega_download_with_progress(url, out_dir, msg):
    os.makedirs(out_dir, exist_ok=True)
    start = time.time()
    proc  = await asyncio.create_subprocess_exec(
        "mega-get", url, out_dir,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    err_acc = []
    buf     = bytearray()
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
                await update_progress_ui(
                    cur_b, tot_b, msg, start, "📥 Mega DL...", engine="MegaAPI"
                )

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

    url, do_zip, do_ext, do_vt = _parse_mdl_args(m.text)

    if not url:
        return await m.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/mdl https://mega.nz/file/XXXXX</code>\n"
            "<code>/mdl https://mega.nz/folder/XXXXX</code>\n\n"
            "Flags:\n"
            "• <code>-z</code> / <code>-zip</code>       — zip files before upload\n"
            "• <code>-e</code> / <code>-extract</code>   — extract archive after download\n"
            "• <code>-vt</code>                           — open Video Tools after download\n\n"
            "📌 Login first with /login for private files."
        )

    if not ("mega.nz" in url or "mega.co.nz" in url):
        return await m.reply_text("❌ <b>Invalid URL!</b> Only Mega links are supported.")

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

    # ── Post-download processing ────────────────────────────────────────────
    if do_zip:
        from bot.helper.archive import create_zip
        await msg.edit_text("🗜️ <b>Zipping downloaded files...</b>")
        zip_path, ok = create_zip(out_dir if len(files) > 1 else files[0])
        if ok:
            files = [zip_path]
        else:
            with suppress(Exception):
                await msg.reply_text("⚠️ Zip failed — uploading files as-is.")

    elif do_ext:
        from bot.helper.archive import extract_archive
        extracted_all = []
        await msg.edit_text("📦 <b>Extracting archive(s)...</b>")
        for fp in files:
            exts, ex_dir, ex_err = extract_archive(fp)
            if ex_err:
                with suppress(Exception):
                    await msg.reply_text(
                        f"⚠️ Extract failed for <code>{clean_html(os.path.basename(fp))}</code>: "
                        f"{clean_html(ex_err)}"
                    )
            else:
                extracted_all.extend(exts)
        if extracted_all:
            files = extracted_all

    # ── -vt: show Video Tools menu on first file ────────────────────────────
    if do_vt and files:
        fp = files[0]
        if not os.path.exists(fp):
            return await msg.edit_text("❌ Downloaded file not found.")
        from bot.modules.video_tools import show_vt_menu
        await show_vt_menu(msg, fp, out_dir, uid, target_chat, m.from_user.first_name)
        if len(files) > 1:
            await msg.reply_text(
                f"ℹ️ -vt applied to first file. Uploading remaining {len(files)-1} file(s)..."
            )
            for fp2 in files[1:]:
                await handle_upload_split(
                    c, msg, fp2, "User",
                    user_id=uid, target_chat=target_chat, start_time=time.time(),
                )
        return  # Do NOT rmtree — vt_download_sessions holds the dir

    # ── Normal upload ────────────────────────────────────────────────────────
    for i, fp in enumerate(files, 1):
        if not os.path.exists(fp):
            continue
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
