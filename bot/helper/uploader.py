"""
Upload helpers: upload_file, handle_upload_split, rclone_upload_file.
"""

import asyncio
import os
import re
import time
from contextlib import suppress

from bot.core.config import AUTO_DELETE_SECONDS, RCLONE_PATH
from bot.helper.archive import split_large_file
from bot.helper.progress import update_progress_ui
from bot.helper.task_manager import abort_dict
from bot.helper.time_format import clean_html
from bot.helper.video import get_video_duration, take_screenshot, convert_to_streamable
from bot.core.database.mongo import get_user_settings, get_active_dump


async def _download_thumb(client, file_id, user_id):
    try:
        thumb_dir = "thumbnails"
        os.makedirs(thumb_dir, exist_ok=True)
        path = os.path.join(thumb_dir, f"thumb_{user_id}.jpg")
        await client.download_media(file_id, file_name=path)
        return path
    except Exception:
        return None


async def upload_file(
    client, message, file_path, user_mention,
    task_info=None, batch_info=None,
    overall_current=0, overall_total=0,
    start_time=None, custom_name=None,
    user_id=None, target_chat=None,
):
    try:
        if message.id in abort_dict:
            return False
        file_path  = str(file_path)
        file_name  = custom_name or os.path.basename(file_path)

        CONVERT_EXTS = (".wvm", ".wmv", ".m4v", ".avi", ".f4v")
        if os.path.splitext(file_name)[1].lower() in CONVERT_EXTS:
            converted, ok = await convert_to_streamable(file_path, message)
            if ok:
                stem = os.path.splitext(file_name)[0]
                named = os.path.join(os.path.dirname(converted), stem + ".mp4")
                with suppress(Exception):
                    os.rename(converted, named)
                    converted = named
                file_path = converted
                file_name = os.path.basename(file_path)

        uid      = user_id or message.chat.id
        settings = await get_user_settings(uid)
        send_as  = settings.get("send_as", "media")
        thumb_id = settings.get("thumbnail", None)

        VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m4v")
        AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav")
        is_video = file_name.lower().endswith(VIDEO_EXTS)
        is_audio = file_name.lower().endswith(AUDIO_EXTS)

        duration   = 0
        thumb_path = None

        if is_video or is_audio:
            duration = await get_video_duration(file_path)

        base_no_ext    = os.path.splitext(file_path)[0]
        ytdl_thumb     = f"{base_no_ext}_t.jpg"
        ytdl_thumb_alt = f"{base_no_ext}.jpg"
        web_thumb      = f"{file_path}_web.jpg"

        if is_video or is_audio:
            if os.path.exists(ytdl_thumb) and os.path.getsize(ytdl_thumb) > 0:
                thumb_path = ytdl_thumb
            elif os.path.exists(ytdl_thumb_alt) and os.path.getsize(ytdl_thumb_alt) > 0:
                thumb_path = ytdl_thumb_alt
            elif os.path.exists(web_thumb) and os.path.getsize(web_thumb) > 0:
                thumb_path = web_thumb
            elif thumb_id:
                thumb_path = await _download_thumb(client, thumb_id, uid)
            elif is_video:
                thumb_path = await take_screenshot(file_path, duration)
        elif thumb_id:
            thumb_path = await _download_thumb(client, thumb_id, uid)

        caption   = clean_html(file_name)
        dest_chat = target_chat if target_chat is not None else message.chat.id

        cur_total = overall_total if overall_total > 0 else os.path.getsize(file_path)
        file_size = os.path.getsize(file_path)
        if start_time is None:
            start_time = time.time()

        async def _progress(current, total):
            if file_size > 10 * 1024 * 1024:
                await update_progress_ui(
                    overall_current + current, cur_total,
                    message, start_time, "📤 Uploading...",
                    filename=file_name,
                    task_info=task_info, batch_info=batch_info,
                )

        sent_msg = None
        try:
            if send_as == "document":
                sent_msg = await client.send_document(
                    dest_chat, file_path, caption=caption, thumb=thumb_path,
                    progress=_progress if file_size > 10 * 1024 * 1024 else None,
                )
            elif is_video:
                sent_msg = await client.send_video(
                    dest_chat, file_path, caption=caption, thumb=thumb_path,
                    duration=duration, supports_streaming=True,
                    progress=_progress if file_size > 10 * 1024 * 1024 else None,
                )
            else:
                sent_msg = await client.send_document(
                    dest_chat, file_path, caption=caption, thumb=thumb_path,
                    progress=_progress if file_size > 10 * 1024 * 1024 else None,
                )
        except Exception as e:
            with suppress(Exception):
                await message.reply_text(
                    f"❌ <b>Upload Error:</b> <code>{clean_html(str(e))}</code>"
                )
            return False

        # Cleanup sidecar thumbs
        for _t in (ytdl_thumb, ytdl_thumb_alt, web_thumb):
            if _t and os.path.exists(_t) and _t != thumb_path:
                with suppress(Exception):
                    os.remove(_t)
        if thumb_path and not thumb_id and os.path.exists(thumb_path):
            with suppress(Exception):
                os.remove(thumb_path)

        # Auto-delete in source chat only
        if sent_msg and AUTO_DELETE_SECONDS > 0 and dest_chat == message.chat.id:
            async def _del(_m):
                await asyncio.sleep(AUTO_DELETE_SECONDS)
                with suppress(Exception):
                    await _m.delete()
            asyncio.create_task(_del(sent_msg))

        return True
    except Exception as e:
        with suppress(Exception):
            await message.reply_text(
                f"❌ <b>Upload Error:</b> <code>{clean_html(str(e))}</code>"
            )
        return False


async def handle_upload_split(
    client, message, file_path, user_mention,
    task_info=None, batch_info=None,
    start_time=None, user_id=None, target_chat=None,
):
    if not os.path.exists(file_path):
        return False

    upload_list = [file_path]
    if os.path.getsize(file_path) > 2000 * 1024 * 1024:
        with suppress(Exception):
            await message.edit_text(
                f"✂️ <b>Splitting large file...</b>\n"
                f"<code>{clean_html(os.path.basename(file_path))}</code>"
            )
        parts, ok = split_large_file(file_path)
        if ok and parts:
            upload_list = parts
            with suppress(Exception):
                os.remove(file_path)

    overall_total = sum(os.path.getsize(f) for f in upload_list if os.path.exists(f))
    if start_time is None:
        start_time = time.time()

    uploaded = 0
    all_ok   = True
    for item in upload_list:
        if not os.path.exists(item):
            all_ok = False
            continue
        size = os.path.getsize(item)
        ok = await upload_file(
            client, message, item, user_mention,
            task_info=task_info, batch_info=batch_info,
            overall_current=uploaded, overall_total=overall_total,
            start_time=start_time, user_id=user_id, target_chat=target_chat,
        )
        if not ok:
            all_ok = False
        uploaded += size
    return all_ok


async def rclone_upload_file(message, file_path, task_info=None, batch_info=None):
    if message.id in abort_dict:
        return False
    if not os.path.exists("rclone.conf"):
        return await message.edit_text("❌ rclone.conf missing!")
    file_name  = os.path.basename(file_path)
    cmd        = ["rclone", "copy", file_path, RCLONE_PATH, "--config", "rclone.conf", "-P"]
    process    = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    last_up = 0
    while True:
        if message.id in abort_dict:
            process.kill()
            return await message.edit_text("❌ Cancelled")
        line = await process.stdout.readline()
        if not line:
            break
        decoded = line.decode().strip()
        now = time.time()
        if "%" in decoded and (now - last_up) > 5:
            m = re.search(r"(\d+)%", decoded)
            if m:
                with suppress(Exception):
                    await message.edit_text(
                        f"☁️ <b>Cloud Upload</b>\n"
                        f"📂 {clean_html(file_name)}\n"
                        f"📊 {m.group(1)}% Done"
                    )
                last_up = now
    await process.wait()
    return True
