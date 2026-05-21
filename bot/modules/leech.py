"""
/leech, /dl, /queue, /stopseed
  - /leech link    → direct torrent/magnet download (no selection UI)
  - /leech link -s → show file selection UI before download
  - /dl link       → direct download (HTTP / yt-dlp / GDrive)
  - /dl reply -z   → zip replied file
  - /dl reply -e   → extract replied archive
"""

import asyncio
import os
import shutil
import time
from contextlib import suppress

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.database.mongo import (
    is_user_banned, get_active_dump, get_bsettings, get_user_proxy,
)
from bot.core.config import PROXY_URL
from bot.helper.download_utils import (
    process_task, _is_torrent_link, _is_gdrive_link,
    download_logic, init_aria2, aria2,
)
from bot.helper.task_manager import (
    abort_dict, ACTIVE_TASKS, seeding_gids,
    pending_selections, user_queues, is_processing,
)
from bot.helper.time_format import clean_html
from bot.helper.uploader import handle_upload_split
from bot.helper import download_utils as _du


async def _enforce_limits(message) -> bool:
    uid = message.from_user.id if message.from_user else None
    s   = await get_bsettings()
    max_t = int(s.get("max_tasks_per_user") or 0)
    if max_t > 0:
        active = sum(1 for t in ACTIVE_TASKS.values() if t.get("user_id") == uid)
        if active >= max_t:
            await message.reply_text(
                f"❌ Task limit reached ({active}/{max_t}).\n"
                f"Wait for current tasks or /status to cancel."
            )
            return False
    return True


async def _queue_manager(client, user_id):
    if is_processing.get(user_id):
        return
    is_processing[user_id] = True
    try:
        while user_queues.get(user_id):
            url, msg, mode, target, rename, seed = user_queues[user_id].pop(0)
            await process_task(client, msg, url, mode, target, rename=rename, seed=seed)
    finally:
        is_processing.pop(user_id, None)


# ── /leech ────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("leech"))
async def leech_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")
    if not await _enforce_limits(m):
        return

    raw  = m.text
    seed = " -s" in raw
    raw  = raw.replace(" -s", "").strip()

    # Rename flag
    rename = None
    if " -n " in raw:
        parts  = raw.split(" -n ", 1)
        raw    = parts[0]
        rename = parts[1].strip().split()[0]

    # Extract URL
    args = raw.split(None, 1)
    url  = args[1].strip() if len(args) > 1 else None

    # Replied .torrent file
    is_reply = m.reply_to_message and m.reply_to_message.document and \
               (m.reply_to_message.document.file_name or "").lower().endswith(".torrent")

    if is_reply:
        rep = m.reply_to_message
        dl_msg = await m.reply_text("⬇️ <b>Downloading .torrent file...</b>")
        fp = await c.download_media(rep, file_name="task.torrent")
        asyncio.create_task(
            process_task(c, m, fp, "leech_file", seed=seed, user_id=m.from_user.id)
        )
        return

    if not url:
        return await m.reply_text(
            "❌ <b>Usage:</b>\n"
            "• <code>/leech magnet:?xt=urn:...</code>\n"
            "• <code>/leech https://...torrent</code>\n"
            "• <code>/leech ... -s</code>  — show file selector\n"
            "Reply to a .torrent file too."
        )

    if not _is_torrent_link(url):
        return await m.reply_text("❌ /leech is for torrents/magnets only.\nUse /dl for direct links.")

    asyncio.create_task(
        process_task(c, m, url, "leech", seed=seed, rename=rename, user_id=m.from_user.id)
    )


# ── /dl ───────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("dl"))
async def dl_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")
    if not await _enforce_limits(m):
        return

    raw  = m.text
    rename = None
    bulk   = " -b" in raw
    do_zip = any(f in raw.split() for f in ("-z", "-zip"))
    do_ext = any(f in raw.split() for f in ("-e", "-extract"))

    for f in (" -b", " -z", " -zip", " -e", " -extract"):
        raw = raw.replace(f, "")
    if " -n " in raw:
        parts  = raw.split(" -n ", 1)
        raw    = parts[0]
        rename = parts[1].strip().split()[0]
    raw = raw.strip()

    uid  = m.from_user.id
    mode = "zip" if do_zip else ("compress" if do_ext else "auto")

    # Replied file
    if m.reply_to_message and (m.reply_to_message.document or m.reply_to_message.video or m.reply_to_message.audio):
        asyncio.create_task(
            process_task(c, m, None, mode, user_id=uid)
        )
        return

    args = raw.split(None, 1)
    if len(args) < 2:
        return await m.reply_text("❌ Send a URL or reply to a file.")

    body  = args[1].strip()
    links = [u.strip() for u in (body.split("\n") if bulk else [body.split()[0]]) if u.strip().startswith("http")]
    if not links:
        return await m.reply_text("❌ No valid links found.")

    if _is_torrent_link(links[0]):
        return await m.reply_text("❌ Use /leech for torrents/magnets.")

    if bulk and len(links) > 1:
        user_queues.setdefault(uid, [])
        for l in links:
            user_queues[uid].append((l, m, mode, "tg", rename, False))
        await m.reply_text(f"📋 {len(links)} links queued!")
        asyncio.create_task(_queue_manager(c, uid))
        return

    for l in links:
        asyncio.create_task(
            process_task(c, m, l, mode, rename=rename, user_id=uid)
        )


# ── /queue ────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("queue"))
async def queue_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")
    args = m.text.split(None, 1)
    if len(args) < 2:
        return await m.reply_text("❌ Usage: /queue URL")
    uid  = m.from_user.id
    urls = [u.strip() for u in args[1].split("\n") if u.strip().startswith("http")]
    if not urls:
        return await m.reply_text("❌ No valid links found.")
    user_queues.setdefault(uid, [])
    for u in urls:
        user_queues[uid].append((u, m, "auto", "tg", None, False))
    await m.reply_text(f"✅ <b>{len(urls)} task(s) added to queue.</b>")
    asyncio.create_task(_queue_manager(c, uid))


# ── /stopseed ─────────────────────────────────────────────────────────────────

@app.on_message(filters.command("stopseed"))
async def stopseed_cmd(c, m):
    from bot.helper.download_utils import aria2 as _aria2
    args = m.command
    if len(args) < 2:
        return await m.reply_text("❌ Usage: /stopseed GID")
    gid = args[1]
    try:
        _aria2.client.remove(gid)
        seeding_gids.pop(gid, None)
        await m.reply_text(f"✅ <b>Stopped seeding:</b> <code>{gid}</code>")
    except Exception as e:
        await m.reply_text(f"❌ Error: <code>{clean_html(str(e))}</code>")


# ── Torrent callback queries ──────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^torrent_all_"))
async def torrent_all_cb(c, cb):
    task_id = cb.data.split("_")[2]
    if task_id in pending_selections:
        pending_selections[task_id]["action"] = "all"
        pending_selections[task_id]["status"] = "done"
    await cb.answer("✅ Downloading all files!")
    with suppress(Exception):
        await cb.message.edit_text("▶️ <b>Downloading all files...</b>")


@app.on_callback_query(filters.regex(r"^torrent_cancel_"))
async def torrent_cancel_cb(c, cb):
    task_id = cb.data.split("_")[2]
    if task_id in pending_selections:
        pending_selections[task_id]["action"] = "cancel"
        pending_selections[task_id]["status"] = "done"
    await cb.answer("❌ Cancelled!")
    with suppress(Exception):
        await cb.message.edit_text("⛔ <b>Torrent cancelled.</b>")
