"""
Core download engine: download_logic + process_task + aria2 init.
"""

import asyncio
import aiohttp
import aiofiles
import os
import secrets
import shutil
import subprocess
import time
import urllib.parse
import traceback
from contextlib import suppress

import yt_dlp

from bot.core.config import (
    PROXY_URL, BASE_URL, COOKIES_FILE,
)
from bot.core.database.mongo import (
    get_user_settings, get_active_dump, get_user_proxy, is_user_banned,
    get_bsettings,
)
from bot.helper.progress import update_progress_ui, ARIA2C_VERSION, YTDLP_VERSION
from bot.helper.task_manager import (
    abort_dict, ACTIVE_TASKS, seeding_gids, pending_selections,
    user_queues, is_processing,
)
from bot.helper.time_format import clean_html, humanbytes
from bot.helper.archive import create_zip, extract_archive
from bot.helper.uploader import upload_file, handle_upload_split, rclone_upload_file


aria2 = None


def init_aria2():
    global aria2
    try:
        import aria2p
        aria2 = aria2p.API(
            aria2p.Client(host="http://localhost", port=6800, secret="")
        )
        print(f"[aria2] Connected — aria2c {ARIA2C_VERSION}")
    except Exception as e:
        print(f"[aria2] Not connected: {e}")


def _is_torrent_link(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return (
        u.startswith("magnet:")
        or ".torrent" in u
        or "torrents.php?action=download" in u
        or ("action=download" in u and any(k in u for k in ("authkey=", "torrent_pass=", "passkey=")))
    )


def _is_gdrive_link(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return "drive.google.com" in u or "docs.google.com" in u or "usercontent.google.com" in u


def _blocking_download(url, fmt, out_tmpl, hook, is_audio, proxy=None):
    opts: dict = {
        "format":          fmt,
        "outtmpl":         out_tmpl,
        "progress_hooks":  [hook],
        "merge_output_format": "mp4",
        "writethumbnail":  True,
        "convert_thumbnails": "jpg",
        "postprocessors":  [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}],
        "concurrent_fragment_downloads": 4,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    }
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    if proxy:
        opts["proxy"] = proxy
    if is_audio:
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            return None
        fp = ydl.prepare_filename(info)
        # Handle merged mp4
        if not os.path.exists(fp):
            mp4 = os.path.splitext(fp)[0] + ".mp4"
            if os.path.exists(mp4):
                fp = mp4
        return {"filepath": fp, "title": info.get("title", "Video"), "info": info}


async def download_logic(
    url, message, user_id, mode,
    task_info=None, format_id=None, rename=None, seed=False,
):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
        }

        # ── Torrent / Leech ───────────────────────────────────────────────────
        if mode in ("leech", "leech_file") or _is_torrent_link(url or ""):
            if not aria2:
                return "ERROR: Aria2 not connected. Restart the bot."

            tracker_list = [
                "http://tracker.opentrackr.org:1337/announce",
                "udp://tracker.opentrackr.org:1337/announce",
                "udp://open.tracker.cl:1337/announce",
                "udp://exodus.desync.com:6969/announce",
            ]
            options = {
                "bt-tracker": ",".join(tracker_list),
                "seed-time":  "525600" if seed else "0",
            }

            try:
                download = None
                if url and url.startswith("http"):
                    async with aiohttp.ClientSession() as sess:
                        async with sess.get(url, headers=headers) as resp:
                            if resp.status == 200:
                                tb = await resp.read()
                                with open("task.torrent", "wb") as f:
                                    f.write(tb)
                                download = aria2.add_torrent("task.torrent", options=options)
                            else:
                                return f"ERROR: HTTP {resp.status}"
                elif url and url.startswith("magnet:"):
                    download = aria2.add_magnet(url, options=options)
                elif mode == "leech_file":
                    download = aria2.add_torrent(url, options=options)
                else:
                    return "ERROR: Invalid torrent/magnet link"
            except Exception as e:
                return f"ERROR: aria2 add failed: {e}"

            if download is None:
                return "ERROR: Failed to add to aria2"

            await asyncio.sleep(2)
            try:
                download = aria2.get_download(download.gid)
            except Exception as e:
                return f"ERROR: {e}"

            # Wait for metadata
            meta_wait = 0
            while True:
                try:
                    download = aria2.get_download(download.gid)
                except Exception as e:
                    return f"ERROR: {e}"
                if message.id in abort_dict:
                    with suppress(Exception):
                        aria2.remove([download.gid], force=True)
                    return "CANCELLED"
                if not download.is_metadata:
                    break
                if download.followed_by_ids:
                    with suppress(Exception):
                        download = aria2.get_download(download.followed_by_ids[0])
                    break
                meta_wait += 2
                if meta_wait > 120:
                    return "ERROR: Metadata timeout"
                await asyncio.sleep(2)

            # WITHOUT -s flag: skip selection UI, download ALL files directly
            if not seed:
                # Unpause and start immediately
                with suppress(Exception):
                    aria2.client.unpause(download.gid)
                await message.edit_text(
                    f"▶️ <b>Torrent Downloading (All Files)...</b>\n"
                    f"<b>Engine:</b> <code>aria2c {ARIA2C_VERSION}</code>"
                )
            else:
                # WITH -s flag: show file selection UI
                with suppress(Exception):
                    aria2.client.pause(download.gid)
                    await asyncio.sleep(1)

                task_id = secrets.token_hex(4)
                try:
                    file_list = [
                        {"index": f.index, "name": os.path.basename(str(f.path)), "size": f.length}
                        for f in download.files
                    ]
                except Exception as e:
                    return f"ERROR: Cannot read file list: {e}"

                pending_selections[task_id] = {
                    "gid": download.gid, "files": file_list,
                    "selected": None, "status": "waiting", "action": None,
                }

                from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                web_url = f"{BASE_URL}/?id={task_id}" if BASE_URL else f"http://your-app/?id={task_id}"
                btn = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🖥 Select Files (Web UI)", url=web_url)],
                    [
                        InlineKeyboardButton("✅ All", callback_data=f"torrent_all_{task_id}"),
                        InlineKeyboardButton("❌ Cancel", callback_data=f"torrent_cancel_{task_id}"),
                    ],
                ])
                await message.edit_text(
                    f"⏸ <b>Torrent Paused — Select Files</b>\n"
                    f"📂 <b>Files:</b> {len(file_list)}\n"
                    f"Choose via Web UI or tap below:",
                    reply_markup=btn,
                )

                timeout = 0
                while pending_selections[task_id]["status"] == "waiting":
                    await asyncio.sleep(2)
                    timeout += 2
                    if message.id in abort_dict:
                        with suppress(Exception):
                            aria2.client.remove(download.gid)
                        pending_selections.pop(task_id, None)
                        return "CANCELLED"
                    if timeout > 600:
                        with suppress(Exception):
                            aria2.client.remove(download.gid)
                        pending_selections.pop(task_id, None)
                        return "ERROR: Selection timeout"

                action  = pending_selections[task_id].get("action")
                sel_idx = pending_selections[task_id].get("selected", [])
                pending_selections.pop(task_id, None)

                if action == "cancel":
                    with suppress(Exception):
                        aria2.client.remove(download.gid)
                    return "CANCELLED"

                with suppress(Exception):
                    cur = aria2.get_download(download.gid)
                    if action == "all" or not sel_idx:
                        all_idx = [str(f.index) for f in cur.files]
                        aria2.client.change_option(download.gid, {"select-file": ",".join(all_idx)})
                    else:
                        aria2.client.change_option(download.gid, {"select-file": ",".join(map(str, sel_idx))})
                    aria2.client.unpause(download.gid)

                await message.edit_text(
                    f"▶️ <b>Download Started!</b>\n"
                    f"<b>Engine:</b> <code>aria2c {ARIA2C_VERSION}</code>"
                )

            # Monitor download
            gid   = download.gid
            dl_st = time.time()
            while True:
                if message.id in abort_dict:
                    with suppress(Exception):
                        aria2.client.remove(gid)
                    return "CANCELLED"
                try:
                    status = aria2.get_download(gid)
                except Exception as e:
                    return f"ERROR: {e}"

                if status.status == "complete":
                    if seed:
                        seeding_gids[gid] = message
                        await message.edit_text(
                            f"✅ <b>Done! Now Seeding 🌱</b>\n"
                            f"GID: <code>{gid}</code>\n"
                            f"/stopseed {gid} to stop."
                        )
                    paths = []
                    for f in status.files:
                        with suppress(Exception):
                            if f.selected and os.path.exists(str(f.path)):
                                paths.append(str(f.path))
                    if not paths:
                        for f in status.files:
                            with suppress(Exception):
                                if os.path.exists(str(f.path)):
                                    paths.append(str(f.path))
                    if len(paths) > 1:
                        return paths
                    elif paths:
                        return paths[0]
                    return "ERROR: No downloaded files found"

                elif status.status == "error":
                    return f"ERROR: {status.error_message}"

                with suppress(Exception):
                    await update_progress_ui(
                        int(status.completed_length), int(status.total_length),
                        message, dl_st, "🌀 Torrent Downloading...",
                        status.name, task_info,
                    )
                await asyncio.sleep(2)

        # ── YT-DLP ───────────────────────────────────────────────────────────
        if mode == "ytdl" or mode == "auto":
            os.makedirs("downloads", exist_ok=True)
            loop    = asyncio.get_running_loop()
            dl_st   = time.time()

            def _hook(d):
                if d["status"] != "downloading":
                    return
                total   = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                current = d.get("downloaded_bytes") or 0
                fname   = os.path.basename(d.get("filename") or "video")
                if current > 0:
                    asyncio.run_coroutine_threadsafe(
                        update_progress_ui(
                            current, total, message, dl_st,
                            "📥 Downloading...", fname, task_info, engine="ytdlp",
                        ),
                        loop,
                    )

            uid2    = secrets.token_hex(4)
            dl_dir  = os.path.join("downloads", uid2)
            os.makedirs(dl_dir, exist_ok=True)
            out_tmpl = os.path.join(dl_dir, "%(title).150s.%(ext)s")
            fmt     = format_id or "bv*+ba/b"

            user_proxy = (await get_user_proxy(user_id)) or PROXY_URL

            def _do_dl():
                return _blocking_download(url, fmt, out_tmpl, _hook, False, user_proxy)

            try:
                result = await asyncio.to_thread(_do_dl)
                if not result:
                    shutil.rmtree(dl_dir, ignore_errors=True)
                    return "ERROR: yt-dlp returned nothing."
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

        # ── Direct HTTP ───────────────────────────────────────────────────────
        if url:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return f"ERROR: HTTP {resp.status}"
                    total = int(resp.headers.get("content-length", 0))
                    name  = None
                    cd    = resp.headers.get("Content-Disposition", "")
                    if 'filename="' in cd:
                        name = cd.split('filename="')[1].split('"')[0]
                    if not name:
                        name = os.path.basename(str(url)).split("?")[0]
                    name = urllib.parse.unquote(name) or "download.bin"
                    if rename:
                        name = rename + os.path.splitext(name)[1]

                    os.makedirs("downloads", exist_ok=True)
                    file_path = os.path.join("downloads", name)
                    dl_sz = 0
                    dl_st = time.time()
                    async with aiofiles.open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(512 * 1024):
                            if message.id in abort_dict:
                                return "CANCELLED"
                            await f.write(chunk)
                            dl_sz += len(chunk)
                            await update_progress_ui(
                                dl_sz, total, message, dl_st,
                                "⬇️ Downloading...", name, task_info,
                                engine="DirectHTTP",
                            )
            return str(file_path)

        return "ERROR: Nothing to download"

    except Exception as e:
        traceback.print_exc()
        return f"ERROR: {e}"


async def process_task(
    client, message, url, mode="auto", upload_target="tg",
    rename=None, seed=False, task_info=None, format_id=None, user_id=None,
    do_vt=False,
):
    uid = user_id or (message.from_user.id if message.from_user else None)
    msg = await message.reply_text(
        f"⬇️ <b>Starting{'...' if not url else ': ' + clean_html(url[:80])}</b>"
    )

    try:
        ACTIVE_TASKS[msg.id] = {
            "user_id":   uid,
            "user_name": getattr(message.from_user, "first_name", "user") if message.from_user else "user",
            "name":      url or "file",
            "action":    "Queued",
            "current":   0, "total": 0, "speed": 0, "eta": "—",
            "start_time": time.time(), "engine": "",
        }

        # Handle Telegram file reply (leech_file or zip/extract)
        if url is None and message.reply_to_message:
            rep = message.reply_to_message
            if rep.document or rep.video or rep.audio:
                dl_dir = f"downloads/reply_{int(time.time())}"
                os.makedirs(dl_dir, exist_ok=True)
                rep_fname = (
                    getattr(rep.document, "file_name", None)
                    or getattr(getattr(rep.video, "file_name", None), "__str__", lambda: None)()
                    or "file"
                )
                await msg.edit_text("⬇️ <b>Downloading replied file...</b>")
                dl_start = time.time()

                async def _reply_dl_progress(current, total):
                    if total and total > 0:
                        await update_progress_ui(
                            current, total, msg, dl_start,
                            "⬇️ Downloading...", rep_fname,
                            engine="PyroTgfork",
                        )

                fp = await client.download_media(
                    rep, file_name=dl_dir + "/", progress=_reply_dl_progress
                )
                if not fp:
                    return await msg.edit_text("❌ Download failed.")
                if mode == "zip":
                    out, ok = create_zip(fp)
                    fp = out if ok else fp
                elif mode == "zip_extract":
                    files, out_dir, err = extract_archive(fp)
                    if err:
                        return await msg.edit_text(f"❌ {clean_html(err)}")
                    active_dump = await get_active_dump(uid)
                    target_chat = active_dump["id"] if active_dump else None
                    for i, f in enumerate(files, 1):
                        await handle_upload_split(
                            client, msg, f, "User",
                            task_info=f"File {i}/{len(files)}",
                            user_id=uid, target_chat=target_chat,
                        )
                    shutil.rmtree(out_dir, ignore_errors=True)
                    return await msg.edit_text(f"✅ Extracted & uploaded {len(files)} file(s)!")
                active_dump = await get_active_dump(uid)
                target_chat = active_dump["id"] if active_dump else None
                if do_vt:
                    from bot.modules.video_tools import show_vt_menu
                    user_name = getattr(message.from_user, "first_name", "User") if message.from_user else "User"
                    await show_vt_menu(msg, fp, dl_dir, uid, target_chat, user_name)
                    return  # dl_dir kept alive — vtd| callback cleans up
                await handle_upload_split(
                    client, msg, fp, "User",
                    user_id=uid, target_chat=target_chat, start_time=time.time(),
                )
                shutil.rmtree(dl_dir, ignore_errors=True)
                return await msg.edit_text("✅ <b>Done!</b>")

        result = await download_logic(
            url, msg, uid, mode,
            task_info=task_info, format_id=format_id,
            rename=rename, seed=seed,
        )

        ACTIVE_TASKS.pop(msg.id, None)

        if isinstance(result, str) and result.startswith("ERROR:"):
            return await msg.edit_text(f"❌ <b>{clean_html(result)}</b>")
        if result == "CANCELLED":
            return await msg.edit_text("⛔ <b>Cancelled.</b>")

        # Zip / extract post-processing
        if mode == "zip":
            await msg.edit_text("🗜️ <b>Zipping...</b>")
            out, ok = create_zip(str(result))
            result = out if ok else result
        elif mode == "zip_extract":
            await msg.edit_text("📦 <b>Extracting archive...</b>")
            files, out_dir, err = extract_archive(str(result))
            if err:
                return await msg.edit_text(f"❌ {clean_html(err)}")
            active_dump = await get_active_dump(uid)
            target_chat = active_dump["id"] if active_dump else None
            for i, f in enumerate(files, 1):
                await handle_upload_split(
                    client, msg, f, "User",
                    task_info=f"File {i}/{len(files)}",
                    user_id=uid, target_chat=target_chat,
                )
            shutil.rmtree(out_dir, ignore_errors=True)
            return await msg.edit_text(f"✅ Extracted & uploaded {len(files)} file(s)!")

        active_dump = await get_active_dump(uid)
        target_chat = active_dump["id"] if active_dump else None

        # -vt flag: show Video Tools menu instead of uploading
        if do_vt and not isinstance(result, list):
            fp = str(result)
            fp_dir = os.path.dirname(fp)
            from bot.modules.video_tools import show_vt_menu
            user_name = getattr(message.from_user, "first_name", "User") if message.from_user else "User"
            await show_vt_menu(msg, fp, fp_dir, uid, target_chat, user_name)
            return  # Keep files — vtd| callback cleans up

        if upload_target == "rclone":
            await rclone_upload_file(msg, str(result), task_info=task_info)
        else:
            if isinstance(result, list):
                for i, fp in enumerate(result, 1):
                    await handle_upload_split(
                        client, msg, fp, "User",
                        task_info=f"File {i}/{len(result)}",
                        user_id=uid, target_chat=target_chat,
                        start_time=time.time(),
                    )
                    with suppress(Exception):
                        os.remove(fp)
                dest = f"<b>dump</b> ({target_chat})" if target_chat else "<b>PM</b>"
                await msg.edit_text(f"✅ <b>{len(result)} file(s) uploaded to {dest}!</b>")
            else:
                fp = str(result)
                await handle_upload_split(
                    client, msg, fp, "User",
                    user_id=uid, target_chat=target_chat,
                    start_time=time.time(),
                )
                dest = f"dump" if target_chat else "PM"
                await msg.edit_text(f"✅ <b>Upload complete → {dest}!</b>")
                with suppress(Exception):
                    os.remove(fp)

    except Exception as e:
        traceback.print_exc()
        ACTIVE_TASKS.pop(msg.id, None)
        with suppress(Exception):
            await msg.edit_text(f"❌ <b>Error:</b> <code>{clean_html(str(e))}</code>")
    finally:
        abort_dict.pop(msg.id, None)
