"""
/vt — Video Tools command
Flags:
  -mp3                     → Extract audio as MP3
  -merge   (reply audio)   → Merge replied audio into replied video
  -subextract [n=0]        → Extract subtitle stream n from video
  -submerge   (reply sub)  → Merge replied subtitle file into video
"""

import asyncio
import os
import shutil
import time
from contextlib import suppress

from pyrogram import filters

from bot.core.client import app
from bot.core.database.mongo import is_user_banned, get_active_dump
from bot.helper.ffmpeg.video_tools import (
    extract_mp3, merge_audio_into_video,
    extract_subtitles, merge_subtitle_into_video,
)
from bot.helper.time_format import clean_html, humanbytes
from bot.helper.uploader import handle_upload_split


def _parse_vt_args(text: str):
    tokens = text.split()[1:]
    flags  = {"-mp3", "-merge", "-subextract", "-submerge"}
    found  = set()
    extras = []
    for t in tokens:
        if t in flags:
            found.add(t)
        else:
            extras.append(t)
    return found, extras


@app.on_message(filters.command("vt"))
async def vt_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")

    flags, extras = _parse_vt_args(m.text)

    if not flags:
        return await m.reply_text(
            "🎬 <b>Video Tools — /vt</b>\n\n"
            "• <code>/vt -mp3</code>  (reply to video)           — Extract audio as MP3\n"
            "• <code>/vt -merge</code>  (reply to video, + quote audio) — Merge audio into video\n"
            "• <code>/vt -subextract [0]</code>  (reply to video) — Extract subtitle stream\n"
            "• <code>/vt -submerge</code>  (reply to video, + quote sub) — Add subtitle to video"
        )

    rep = m.reply_to_message
    if not rep or not (rep.document or rep.video or rep.audio):
        return await m.reply_text("❌ Reply to a file first.")

    uid = m.from_user.id
    msg = await m.reply_text("⬇️ <b>Downloading file...</b>")
    dl_dir = f"downloads/vt_{int(time.time())}"
    os.makedirs(dl_dir, exist_ok=True)

    try:
        fp = await c.download_media(rep, file_name=dl_dir + "/")
        if not fp:
            return await msg.edit_text("❌ Download failed.")

        active_dump = await get_active_dump(uid)
        target_chat = active_dump["id"] if active_dump else None

        # ── MP3 Extract ──────────────────────────────────────────────────────
        if "-mp3" in flags:
            await msg.edit_text(
                f"🎵 <b>Extracting MP3...</b>\n<code>{clean_html(os.path.basename(fp))}</code>"
            )
            out, err = await extract_mp3(fp, dl_dir)
            if err:
                return await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")
            await msg.edit_text(
                f"📤 <b>Uploading MP3...</b>\n"
                f"<code>{clean_html(os.path.basename(out))}</code>\n"
                f"Size: {humanbytes(os.path.getsize(out))}"
            )
            await handle_upload_split(
                c, msg, out, m.from_user.first_name,
                user_id=uid, target_chat=target_chat, start_time=time.time(),
            )
            await msg.edit_text("✅ <b>MP3 extracted & uploaded!</b>")

        # ── Audio Merge ──────────────────────────────────────────────────────
        elif "-merge" in flags:
            # Need a second file (audio) — look for quoted message or another reply
            audio_rep = None
            if rep.reply_to_message and (rep.reply_to_message.audio or rep.reply_to_message.document):
                audio_rep = rep.reply_to_message
            if not audio_rep:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return await msg.edit_text(
                    "❌ <b>Reply to the video AND quote the audio message.</b>\n\n"
                    "How: Reply to the audio → select 'Reply' on the video message too."
                )
            await msg.edit_text("⬇️ <b>Downloading audio...</b>")
            audio_fp = await c.download_media(audio_rep, file_name=dl_dir + "/audio_")
            if not audio_fp:
                return await msg.edit_text("❌ Audio download failed.")
            await msg.edit_text(
                f"🔀 <b>Merging audio into video...</b>\n"
                f"Video: <code>{clean_html(os.path.basename(fp))}</code>"
            )
            out, err = await merge_audio_into_video(fp, audio_fp, dl_dir)
            if err:
                return await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")
            await msg.edit_text(f"📤 <b>Uploading merged video...</b>")
            await handle_upload_split(
                c, msg, out, m.from_user.first_name,
                user_id=uid, target_chat=target_chat, start_time=time.time(),
            )
            await msg.edit_text("✅ <b>Audio merged & uploaded!</b>")

        # ── Subtitle Extract ─────────────────────────────────────────────────
        elif "-subextract" in flags:
            idx = 0
            if extras:
                try:
                    idx = int(extras[0])
                except ValueError:
                    pass
            await msg.edit_text(
                f"🔤 <b>Extracting subtitle stream #{idx}...</b>\n"
                f"<code>{clean_html(os.path.basename(fp))}</code>"
            )
            out, err = await extract_subtitles(fp, stream_index=idx, output_dir=dl_dir)
            if err:
                return await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")
            await msg.edit_text(
                f"📤 <b>Uploading subtitle...</b>\n"
                f"<code>{clean_html(os.path.basename(out))}</code>"
            )
            await handle_upload_split(
                c, msg, out, m.from_user.first_name,
                user_id=uid, target_chat=target_chat, start_time=time.time(),
            )
            await msg.edit_text("✅ <b>Subtitle extracted & uploaded!</b>")

        # ── Subtitle Merge ───────────────────────────────────────────────────
        elif "-submerge" in flags:
            sub_rep = None
            if rep.reply_to_message and rep.reply_to_message.document:
                sub_rep = rep.reply_to_message
            if not sub_rep:
                shutil.rmtree(dl_dir, ignore_errors=True)
                return await msg.edit_text(
                    "❌ Reply to the video AND quote the subtitle (.srt/.ass) file."
                )
            await msg.edit_text("⬇️ <b>Downloading subtitle...</b>")
            sub_fp = await c.download_media(sub_rep, file_name=dl_dir + "/sub_")
            if not sub_fp:
                return await msg.edit_text("❌ Subtitle download failed.")
            await msg.edit_text("🔤 <b>Muxing subtitle into video...</b>")
            out, err = await merge_subtitle_into_video(fp, sub_fp, dl_dir)
            if err:
                return await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")
            await msg.edit_text(f"📤 <b>Uploading video with subtitle...</b>")
            await handle_upload_split(
                c, msg, out, m.from_user.first_name,
                user_id=uid, target_chat=target_chat, start_time=time.time(),
            )
            await msg.edit_text("✅ <b>Subtitle muxed & uploaded!</b>")

    except Exception as e:
        with suppress(Exception):
            await msg.edit_text(f"❌ <b>Error:</b> <code>{clean_html(str(e))}</code>")
    finally:
        shutil.rmtree(dl_dir, ignore_errors=True)
