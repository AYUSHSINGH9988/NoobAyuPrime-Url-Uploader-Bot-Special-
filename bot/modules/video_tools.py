"""
/vt — Video Tools command

Usage (direct):
  /vt               (reply to video) — show interactive tool menu
  /vt -mp3          (reply to video) — extract audio as MP3
  /vt -merge        (reply to video, quote audio)   — merge audio into video
  /vt -subextract [n] (reply to video)              — extract subtitle stream n
  /vt -submerge     (reply to video, quote .srt)    — add subtitle to video

Usage (via -vt flag from any download command):
  /dl URL -vt        → download then show VT menu
  /ytdl URL -vt      → download then show VT menu
  /mdl URL -vt       → download then show VT menu
  /leech URL -vt     → download then show VT menu
"""

import asyncio
import os
import shutil
import time
from contextlib import suppress

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.database.mongo import is_user_banned, get_active_dump
from bot.helper.ffmpeg.video_tools import (
    extract_mp3, merge_audio_into_video,
    extract_subtitles, merge_subtitle_into_video,
)
from bot.helper.time_format import clean_html, humanbytes
from bot.helper.uploader import handle_upload_split
from bot.helper.task_manager import (
    vt_sessions, vt_download_sessions, waiting_for_vt_merge,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared: show VT menu after a file has already been downloaded (-vt flag)
# ─────────────────────────────────────────────────────────────────────────────

async def show_vt_menu(msg, fp: str, dl_dir: str, uid: int, target_chat, user_name: str):
    """
    Called by other modules when -vt flag is used.
    Stores session and shows interactive InlineKeyboard.
    Only the user who initiated (uid) can interact with the buttons.
    """
    fname = os.path.basename(fp)
    vt_download_sessions[msg.id] = {
        "uid":         uid,
        "fp":          fp,
        "dl_dir":      dl_dir,
        "target_chat": target_chat,
        "user_name":   user_name,
    }
    await msg.edit_text(
        f"🎬 <b>Video Tools</b>\n"
        f"<code>{clean_html(fname[:60])}</code>\n\n"
        f"Choose an action:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎵 Extract Audio (MP3)",      callback_data=f"vtd|{msg.id}|mp3|{uid}")],
            [InlineKeyboardButton("🔤 Extract Subtitles",        callback_data=f"vtd|{msg.id}|subextract|{uid}")],
            [InlineKeyboardButton("🔀 Merge Audio into Video",   callback_data=f"vtd|{msg.id}|merge|{uid}")],
            [InlineKeyboardButton("📝 Merge Subtitle into Video",callback_data=f"vtd|{msg.id}|submerge|{uid}")],
            [InlineKeyboardButton("📤 Upload As-Is",             callback_data=f"vtd|{msg.id}|upload|{uid}")],
            [InlineKeyboardButton("❌ Cancel & Delete",          callback_data=f"vtd|{msg.id}|cancel|{uid}")],
        ]),
    )


# ─────────────────────────────────────────────────────────────────────────────
# vtd| callback — post-download VT actions (triggered by -vt flag)
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^vtd\|"))
async def vtd_action_cb(c, cb):
    parts   = cb.data.split("|")
    msg_id  = int(parts[1])
    action  = parts[2]
    uid     = int(parts[3])

    if cb.from_user.id != uid:
        return await cb.answer("❌ Not your menu!", show_alert=True)

    sess = vt_download_sessions.get(msg_id)
    if not sess:
        return await cb.answer("❌ Session expired.", show_alert=True)

    if action == "cancel":
        vt_download_sessions.pop(msg_id, None)
        shutil.rmtree(sess["dl_dir"], ignore_errors=True)
        await cb.answer("❌ Cancelled")
        with suppress(Exception):
            await cb.message.edit_text("❌ <b>Cancelled & file deleted.</b>", reply_markup=None)
        return

    # Consume session — we handle it now
    vt_download_sessions.pop(msg_id, None)
    msg        = cb.message
    fp         = sess["fp"]
    dl_dir     = sess["dl_dir"]
    target_chat = sess["target_chat"]
    user_name  = sess["user_name"]

    # Upload as-is
    if action == "upload":
        await cb.answer("📤 Uploading...")
        await msg.edit_text("📤 <b>Uploading file...</b>", reply_markup=None)
        try:
            await handle_upload_split(
                c, msg, fp, user_name,
                user_id=uid, target_chat=target_chat, start_time=time.time(),
            )
            dest = "dump" if target_chat else "PM"
            await msg.edit_text(f"✅ <b>Uploaded → {dest}!</b>")
        finally:
            shutil.rmtree(dl_dir, ignore_errors=True)
        return

    # Merge actions — download video is done; need second file
    if action in ("merge", "submerge"):
        await cb.answer("✅ Video ready. Send the second file.")
        waiting_for_vt_merge[uid] = {
            "video_fp":    fp,
            "dl_dir":      dl_dir,
            "msg":         msg,
            "action":      action,
            "target_chat": target_chat,
            "user_name":   user_name,
        }
        prompt = (
            "✅ <b>Video is ready!</b>\n\n"
            "Now <b>send the audio file</b> (document or audio message)\n"
            "in this chat — the bot will merge it automatically."
            if action == "merge" else
            "✅ <b>Video is ready!</b>\n\n"
            "Now <b>send the subtitle file</b> (.srt / .ass / .vtt) as a document\n"
            "in this chat — the bot will mux it automatically."
        )
        await msg.edit_text(prompt, reply_markup=None)
        return

    # Extract actions — handle immediately
    await cb.answer("⏳ Processing...")
    await msg.edit_text("⏳ <b>Processing...</b>", reply_markup=None)
    try:
        await _run_extract_action(c, msg, action, [], fp, dl_dir, uid, target_chat, user_name)
    except Exception as e:
        with suppress(Exception):
            await msg.edit_text(f"❌ <b>Error:</b> <code>{clean_html(str(e))}</code>")
    finally:
        shutil.rmtree(dl_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# /vt command — interactive menu (file not yet downloaded)
# ─────────────────────────────────────────────────────────────────────────────

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
    uid = m.from_user.id

    # No flags → show interactive menu
    if not flags:
        rep = m.reply_to_message
        if not rep or not (rep.document or rep.video or rep.audio):
            return await m.reply_text(
                "🎬 <b>Video Tools — /vt</b>\n\n"
                "Reply to a video/audio file and use:\n"
                "• <code>/vt</code>                   — Interactive menu\n"
                "• <code>/vt -mp3</code>               — Extract audio as MP3\n"
                "• <code>/vt -merge</code>             — Merge audio into video\n"
                "• <code>/vt -subextract [n]</code>    — Extract subtitle stream\n"
                "• <code>/vt -submerge</code>          — Add subtitle file\n\n"
                "<b>Or add <code>-vt</code> to any download command:</b>\n"
                "<code>/dl URL -vt</code>   <code>/ytdl URL -vt</code>   "
                "<code>/mdl URL -vt</code>   <code>/leech URL -vt</code>"
            )

        menu_msg = await m.reply_text(
            "🎬 <b>Video Tools</b>\nSelect what to do with this file:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎵 Extract Audio (MP3)",      callback_data=f"vt|{m.id}|mp3|{uid}")],
                [InlineKeyboardButton("🔤 Extract Subtitles",        callback_data=f"vt|{m.id}|subextract|{uid}")],
                [InlineKeyboardButton("🔀 Merge Audio into Video",   callback_data=f"vt|{m.id}|merge|{uid}")],
                [InlineKeyboardButton("📝 Merge Subtitle into Video",callback_data=f"vt|{m.id}|submerge|{uid}")],
                [InlineKeyboardButton("❌ Cancel",                   callback_data=f"vt|{m.id}|cancel|{uid}")],
            ]),
        )
        vt_sessions[menu_msg.id] = {
            "uid":        uid,
            "chat_id":    m.chat.id,
            "rep_msg_id": rep.id,
        }
        return

    # Flags provided — download file then process
    rep = m.reply_to_message
    if not rep or not (rep.document or rep.video or rep.audio):
        return await m.reply_text("❌ Reply to a file first.")

    msg = await m.reply_text("⬇️ <b>Downloading file...</b>")
    dl_dir = f"downloads/vt_{int(time.time())}"
    os.makedirs(dl_dir, exist_ok=True)

    try:
        fp = await c.download_media(rep, file_name=dl_dir + "/")
        if not fp:
            return await msg.edit_text("❌ Download failed.")

        active_dump = await get_active_dump(uid)
        target_chat = active_dump["id"] if active_dump else None

        await _run_vt_flags_action(c, msg, flags, extras, fp, dl_dir, uid, target_chat, m.from_user.first_name)

    except Exception as e:
        with suppress(Exception):
            await msg.edit_text(f"❌ <b>Error:</b> <code>{clean_html(str(e))}</code>")
    finally:
        shutil.rmtree(dl_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# vt| callback — /vt command interactive menu (needs to download first)
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^vt\|"))
async def vt_action_cb(c, cb):
    parts       = cb.data.split("|")
    orig_msg_id = int(parts[1])
    action      = parts[2]
    uid         = int(parts[3])

    if cb.from_user.id != uid:
        return await cb.answer("❌ Not your menu!", show_alert=True)

    if action == "cancel":
        vt_sessions.pop(cb.message.id, None)
        await cb.answer("❌ Cancelled")
        with suppress(Exception):
            await cb.message.delete()
        return

    sess = vt_sessions.pop(cb.message.id, None)
    if not sess:
        return await cb.answer("❌ Session expired.", show_alert=True)

    msg = cb.message

    # For merge actions: download video, then wait for second file
    if action in ("merge", "submerge"):
        await cb.answer("⬇️ Downloading video...")
        await msg.edit_text("⬇️ <b>Downloading video...</b>", reply_markup=None)
        dl_dir = f"downloads/vt_{int(time.time())}"
        os.makedirs(dl_dir, exist_ok=True)

        try:
            rep = await c.get_messages(sess["chat_id"], sess["rep_msg_id"])
        except Exception as e:
            shutil.rmtree(dl_dir, ignore_errors=True)
            return await msg.edit_text(f"❌ Cannot fetch file: <code>{clean_html(str(e))}</code>")

        fp = await c.download_media(rep, file_name=dl_dir + "/")
        if not fp:
            shutil.rmtree(dl_dir, ignore_errors=True)
            return await msg.edit_text("❌ Download failed.")

        active_dump = await get_active_dump(uid)
        target_chat = active_dump["id"] if active_dump else None
        waiting_for_vt_merge[uid] = {
            "video_fp":    fp,
            "dl_dir":      dl_dir,
            "msg":         msg,
            "action":      action,
            "target_chat": target_chat,
            "user_name":   cb.from_user.first_name,
        }
        prompt = (
            "✅ <b>Video downloaded!</b>\n\n"
            "Now <b>send the audio file</b> (document or audio) in this chat."
            if action == "merge" else
            "✅ <b>Video downloaded!</b>\n\n"
            "Now <b>send the subtitle file</b> (.srt/.ass/.vtt) in this chat."
        )
        await msg.edit_text(prompt)
        return

    # Extract actions — download then process immediately
    await cb.answer("⬇️ Downloading...")
    await msg.edit_text("⬇️ <b>Downloading file...</b>", reply_markup=None)
    dl_dir = f"downloads/vt_{int(time.time())}"
    os.makedirs(dl_dir, exist_ok=True)

    try:
        rep = await c.get_messages(sess["chat_id"], sess["rep_msg_id"])
    except Exception as e:
        shutil.rmtree(dl_dir, ignore_errors=True)
        return await msg.edit_text(f"❌ Cannot fetch file: <code>{clean_html(str(e))}</code>")

    fp = await c.download_media(rep, file_name=dl_dir + "/")
    if not fp:
        shutil.rmtree(dl_dir, ignore_errors=True)
        return await msg.edit_text("❌ Download failed.")

    active_dump = await get_active_dump(uid)
    target_chat = active_dump["id"] if active_dump else None

    try:
        await _run_extract_action(c, msg, action, [], fp, dl_dir, uid, target_chat, cb.from_user.first_name)
    except Exception as e:
        with suppress(Exception):
            await msg.edit_text(f"❌ <b>Error:</b> <code>{clean_html(str(e))}</code>")
    finally:
        shutil.rmtree(dl_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Handler: catch second file for merge operations
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message((filters.private | filters.group) & (filters.document | filters.audio))
async def vt_merge_file_handler(c, m):
    """Catches the audio/sub file sent by user after clicking Merge in /vt menu."""
    if not m.from_user:
        return
    uid  = m.from_user.id
    sess = waiting_for_vt_merge.get(uid)
    if not sess:
        return

    # Consume session
    waiting_for_vt_merge.pop(uid, None)

    msg        = sess["msg"]
    video_fp   = sess["video_fp"]
    dl_dir     = sess["dl_dir"]
    action     = sess["action"]
    target_chat = sess["target_chat"]
    user_name  = sess["user_name"]

    try:
        await msg.edit_text("⬇️ <b>Downloading second file...</b>")
        second_fp = await c.download_media(m, file_name=dl_dir + "/second_")
        if not second_fp:
            shutil.rmtree(dl_dir, ignore_errors=True)
            return await msg.edit_text("❌ Second file download failed.")

        if action == "merge":
            await msg.edit_text("🔀 <b>Merging audio into video...</b>")
            out, err = await merge_audio_into_video(video_fp, second_fp, dl_dir)
            if err:
                return await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")
            await msg.edit_text("📤 <b>Uploading merged video...</b>")
            await handle_upload_split(
                c, msg, out, user_name,
                user_id=uid, target_chat=target_chat, start_time=time.time(),
            )
            await msg.edit_text("✅ <b>Audio merged & uploaded!</b>")

        elif action == "submerge":
            await msg.edit_text("📝 <b>Muxing subtitle into video...</b>")
            out, err = await merge_subtitle_into_video(video_fp, second_fp, dl_dir)
            if err:
                return await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")
            await msg.edit_text("📤 <b>Uploading video with subtitle...</b>")
            await handle_upload_split(
                c, msg, out, user_name,
                user_id=uid, target_chat=target_chat, start_time=time.time(),
            )
            await msg.edit_text("✅ <b>Subtitle muxed & uploaded!</b>")

    except Exception as e:
        with suppress(Exception):
            await msg.edit_text(f"❌ <b>Error:</b> <code>{clean_html(str(e))}</code>")
    finally:
        shutil.rmtree(dl_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Shared action runners
# ─────────────────────────────────────────────────────────────────────────────

async def _run_extract_action(c, msg, action: str, extras: list, fp: str,
                               dl_dir: str, uid: int, target_chat, user_name: str):
    """Run an extract action (mp3 or subextract) on an already-downloaded file."""
    if action == "mp3":
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
            c, msg, out, user_name,
            user_id=uid, target_chat=target_chat, start_time=time.time(),
        )
        await msg.edit_text("✅ <b>MP3 extracted & uploaded!</b>")

    elif action == "subextract":
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
            c, msg, out, user_name,
            user_id=uid, target_chat=target_chat, start_time=time.time(),
        )
        await msg.edit_text("✅ <b>Subtitle extracted & uploaded!</b>")


async def _run_vt_flags_action(c, msg, flags: set, extras: list, fp: str,
                                dl_dir: str, uid: int, target_chat, user_name: str):
    """Run a VT action triggered directly by /vt flags (not interactive menu)."""
    if "-mp3" in flags:
        await _run_extract_action(c, msg, "mp3", extras, fp, dl_dir, uid, target_chat, user_name)

    elif "-subextract" in flags:
        await _run_extract_action(c, msg, "subextract", extras, fp, dl_dir, uid, target_chat, user_name)

    elif "-merge" in flags:
        # Need the replied-to message's reply (quoted audio)
        audio_rep = None
        if hasattr(msg, "reply_to_message") and msg.reply_to_message:
            r = msg.reply_to_message
            if r and r.reply_to_message and (r.reply_to_message.audio or r.reply_to_message.document):
                audio_rep = r.reply_to_message
        if not audio_rep:
            return await msg.edit_text(
                "❌ <b>Reply to the video AND quote the audio message.</b>\n\n"
                "Or use <code>/vt -merge</code> — reply to video with quoted audio."
            )
        await msg.edit_text("⬇️ <b>Downloading audio...</b>")
        audio_fp = await c.download_media(audio_rep, file_name=dl_dir + "/audio_")
        if not audio_fp:
            return await msg.edit_text("❌ Audio download failed.")
        await msg.edit_text(f"🔀 <b>Merging audio into video...</b>")
        out, err = await merge_audio_into_video(fp, audio_fp, dl_dir)
        if err:
            return await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")
        await msg.edit_text("📤 <b>Uploading merged video...</b>")
        await handle_upload_split(
            c, msg, out, user_name,
            user_id=uid, target_chat=target_chat, start_time=time.time(),
        )
        await msg.edit_text("✅ <b>Audio merged & uploaded!</b>")

    elif "-submerge" in flags:
        sub_rep = None
        if hasattr(msg, "reply_to_message") and msg.reply_to_message:
            r = msg.reply_to_message
            if r and r.reply_to_message and r.reply_to_message.document:
                sub_rep = r.reply_to_message
        if not sub_rep:
            return await msg.edit_text(
                "❌ Reply to the video AND quote the subtitle (.srt/.ass) file."
            )
        await msg.edit_text("⬇️ <b>Downloading subtitle...</b>")
        sub_fp = await c.download_media(sub_rep, file_name=dl_dir + "/sub_")
        if not sub_fp:
            return await msg.edit_text("❌ Subtitle download failed.")
        await msg.edit_text("📝 <b>Muxing subtitle into video...</b>")
        out, err = await merge_subtitle_into_video(fp, sub_fp, dl_dir)
        if err:
            return await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")
        await msg.edit_text("📤 <b>Uploading video with subtitle...</b>")
        await handle_upload_split(
            c, msg, out, user_name,
            user_id=uid, target_chat=target_chat, start_time=time.time(),
        )
        await msg.edit_text("✅ <b>Subtitle muxed & uploaded!</b>")
