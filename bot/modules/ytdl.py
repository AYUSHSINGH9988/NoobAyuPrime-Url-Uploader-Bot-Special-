"""
/ytdl — YouTube / any yt-dlp supported site with quality picker.
/playlist — full playlist download.
"""

import asyncio
import os
import shutil
import time
from contextlib import suppress

import yt_dlp
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.config import PROXY_URL, COOKIES_FILE
from bot.core.database.mongo import is_user_banned, get_active_dump, get_user_proxy, get_bsettings
from bot.helper.download_utils import _blocking_download
from bot.helper.progress import update_progress_ui, YTDLP_VERSION
from bot.helper.task_manager import abort_dict, ACTIVE_TASKS, ytdl_session
from bot.helper.time_format import clean_html, humanbytes
from bot.helper.uploader import handle_upload_split


async def _get_formats_info(url, proxy=None):
    opts = {"quiet": True, "no_warnings": True}
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    if proxy:
        opts["proxy"] = proxy

    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        return await asyncio.to_thread(_extract)
    except Exception as e:
        return None


@app.on_message(filters.command("ytdl"))
async def ytdl_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")

    raw    = m.text
    rename = None
    bulk   = " -b" in raw

    for f in (" -b",):
        raw = raw.replace(f, "")
    if " -n " in raw:
        parts  = raw.split(" -n ", 1)
        raw    = parts[0]
        rename = parts[1].strip().split()[0]

    args = raw.split(None, 1)
    if len(args) < 2:
        return await m.reply_text("❌ Usage: /ytdl URL [-n rename] [-b bulk]")

    uid    = m.from_user.id
    body   = args[1].strip()
    proxy  = (await get_user_proxy(uid)) or PROXY_URL

    if bulk:
        urls = [u.strip() for u in body.split("\n") if u.strip().startswith("http")]
        if not urls:
            return await m.reply_text("❌ No valid URLs found.")
        msg = await m.reply_text(f"📋 <b>Bulk mode: {len(urls)} URLs queued.</b>")
        for i, url in enumerate(urls, 1):
            await _ytdl_download_and_upload(c, m, url, "bv*+ba/b", uid, rename, f"URL {i}/{len(urls)}")
        return

    url = body.split()[0]
    msg = await m.reply_text(f"🔍 <b>Fetching formats...</b>\n<code>{clean_html(url[:80])}</code>")
    info = await _get_formats_info(url, proxy)

    if not info:
        return await msg.edit_text("❌ Could not fetch formats. Check URL or try again.")

    # Build quality picker
    formats = [
        f for f in info.get("formats", [])
        if f.get("vcodec") != "none" and f.get("height")
    ]
    formats.sort(key=lambda x: x.get("height", 0), reverse=True)
    seen_h, rows = set(), []
    for f in formats[:10]:
        h = f.get("height", 0)
        if h in seen_h:
            continue
        seen_h.add(h)
        fid   = f.get("format_id", "best")
        ext   = f.get("ext", "mp4")
        label = f"{'🎬' if h >= 720 else '📺'} {h}p [{ext}]"
        rows.append([InlineKeyboardButton(label, callback_data=f"ytq|{msg.id}|{fid}|{uid}")])

    if not rows:
        rows.append([InlineKeyboardButton("🎬 Best Quality", callback_data=f"ytq|{msg.id}|bv*+ba/b|{uid}")])
    rows.append([InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data=f"ytq|{msg.id}|audio|{uid}")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"ytq|{msg.id}|cancel|{uid}")])

    ytdl_session[msg.id] = {
        "url":    url,
        "uid":    uid,
        "rename": rename,
        "title":  info.get("title", "video"),
    }

    await msg.edit_text(
        f"🎬 <b>Select Quality</b>\n"
        f"<b>{clean_html(info.get('title', url)[:60])}</b>",
        reply_markup=InlineKeyboardMarkup(rows),
    )


@app.on_callback_query(filters.regex(r"^ytq\|"))
async def ytq_cb(c, cb):
    parts  = cb.data.split("|")
    msg_id = int(parts[1])
    fmt_id = parts[2]
    uid    = int(parts[3])

    if cb.from_user.id != uid:
        return await cb.answer("❌ Not yours!", show_alert=True)

    if fmt_id == "cancel":
        ytdl_session.pop(msg_id, None)
        await cb.answer("❌ Cancelled")
        with suppress(Exception):
            await cb.message.delete()
        return

    sess = ytdl_session.pop(msg_id, None)
    if not sess:
        return await cb.answer("⚠️ Session expired.", show_alert=True)

    await cb.answer("⬇️ Starting...")
    is_audio = fmt_id == "audio"
    fmt = "ba/b" if is_audio else fmt_id

    await cb.message.edit_text(
        f"⬇️ <b>Downloading...</b>\n"
        f"<b>{clean_html(sess.get('title', '')[:60])}</b>"
    )
    await _ytdl_download_and_upload(
        c, cb.message, sess["url"], fmt, uid, sess.get("rename"),
        is_audio=is_audio,
    )


async def _ytdl_download_and_upload(c, msg, url, fmt, uid, rename=None, task_info=None, is_audio=False):
    proxy  = (await get_user_proxy(uid)) or PROXY_URL
    dl_dir = f"downloads/ytdl_{int(time.time())}_{uid}"
    os.makedirs(dl_dir, exist_ok=True)
    out_tmpl = os.path.join(dl_dir, "%(title).150s.%(ext)s")
    loop     = asyncio.get_running_loop()
    start    = time.time()

    def _hook(d):
        if d["status"] != "downloading":
            return
        total   = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        current = d.get("downloaded_bytes") or 0
        fname   = os.path.basename(d.get("filename") or "video")
        if current > 0:
            asyncio.run_coroutine_threadsafe(
                update_progress_ui(current, total, msg, start, "📥 Downloading...",
                                   fname, task_info, engine="ytdlp"),
                loop,
            )

    try:
        result = await asyncio.to_thread(_blocking_download, url, fmt, out_tmpl, _hook, is_audio, proxy)
        if not result:
            return await msg.edit_text("❌ Download failed. Check URL or format.")

        fp = result["filepath"]
        if rename and os.path.exists(fp):
            ext   = os.path.splitext(fp)[1]
            new_p = os.path.join(dl_dir, rename + ext)
            with suppress(Exception):
                os.rename(fp, new_p)
                fp = new_p

        active_dump = await get_active_dump(uid)
        target_chat = active_dump["id"] if active_dump else None
        await handle_upload_split(
            c, msg, fp, "User",
            user_id=uid, target_chat=target_chat, start_time=start,
        )
        dest = "dump" if target_chat else "PM"
        await msg.edit_text(f"✅ <b>Upload complete → {dest}!</b>")
    except Exception as e:
        with suppress(Exception):
            await msg.edit_text(f"❌ <code>{clean_html(str(e))}</code>")
    finally:
        shutil.rmtree(dl_dir, ignore_errors=True)


@app.on_message(filters.command("playlist"))
async def playlist_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")
    args = m.text.split()
    if len(args) < 2:
        return await m.reply_text("❌ Usage: /playlist URL [--quality 720]")

    url     = args[1]
    quality = "720"
    for i, a in enumerate(args):
        if a == "--quality" and i + 1 < len(args):
            quality = args[i + 1]

    uid = m.from_user.id
    msg = await m.reply_text(f"📋 <b>Fetching playlist info...</b>")

    proxy = (await get_user_proxy(uid)) or PROXY_URL
    fmt   = f"bv*[height<={quality}]+ba/b[height<={quality}]/bv*+ba/b"

    asyncio.create_task(_do_playlist(c, msg, url, fmt, uid, quality, proxy))


async def _do_playlist(c, msg, url, fmt, uid, quality, proxy):
    dl_dir   = f"downloads/playlist_{int(time.time())}_{uid}"
    os.makedirs(dl_dir, exist_ok=True)
    out_tmpl = os.path.join(dl_dir, "%(playlist_index)s - %(title).100s.%(ext)s")
    loop     = asyncio.get_running_loop()
    start    = time.time()
    idx      = {"n": 0}

    def _hook(d):
        if d["status"] == "downloading":
            total   = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            current = d.get("downloaded_bytes") or 0
            fname   = os.path.basename(d.get("filename") or "video")
            asyncio.run_coroutine_threadsafe(
                update_progress_ui(current, total, msg, start, f"📥 Video {idx['n']}...",
                                   fname, engine="ytdlp"),
                loop,
            )
        elif d["status"] == "finished":
            idx["n"] += 1

    try:
        result = await asyncio.to_thread(_blocking_download, url, fmt, out_tmpl, _hook, False, proxy)
        if not result:
            return await msg.edit_text("❌ Playlist download failed.")

        files = sorted(
            [os.path.join(dl_dir, f) for f in os.listdir(dl_dir)],
        )
        active_dump = await get_active_dump(uid)
        target_chat = active_dump["id"] if active_dump else None

        for i, fp in enumerate(files, 1):
            await handle_upload_split(
                c, msg, fp, "User",
                task_info=f"Video {i}/{len(files)}",
                user_id=uid, target_chat=target_chat, start_time=start,
            )
        await msg.edit_text(f"✅ <b>Playlist done! {len(files)} videos uploaded.</b>")
    except Exception as e:
        with suppress(Exception):
            await msg.edit_text(f"❌ <code>{clean_html(str(e))}</code>")
    finally:
        shutil.rmtree(dl_dir, ignore_errors=True)
