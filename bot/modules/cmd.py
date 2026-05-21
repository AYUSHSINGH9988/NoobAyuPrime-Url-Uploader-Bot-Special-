"""
/cmd — file operations:
  reply to file + /cmd -z  or  /cmd link -z  → zip the file
  reply to file + /cmd -e  or  /cmd -extract → extract the archive

Also handles direct-link -z/-e flags from /dl command.
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
from bot.helper.archive import create_zip, extract_archive
from bot.helper.time_format import clean_html, humanbytes
from bot.helper.uploader import handle_upload_split


@app.on_message(filters.command("cmd"))
async def cmd_handler(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")

    args = m.text.split()[1:]
    do_zip     = any(a in ("-z", "-zip")     for a in args)
    do_extract = any(a in ("-e", "-extract") for a in args)

    if not do_zip and not do_extract:
        return await m.reply_text(
            "❌ <b>Usage:</b>\n"
            "• <code>/cmd -z</code>  (reply to file) — zip it\n"
            "• <code>/cmd -e</code>  (reply to file) — extract it\n"
            "• <code>/cmd link -z</code>              — download & zip"
        )

    # Need a replied-to file
    rep = m.reply_to_message
    if not rep or not (rep.document or rep.video or rep.audio):
        return await m.reply_text("❌ Reply to a file first.")

    uid  = m.from_user.id
    msg  = await m.reply_text("⬇️ <b>Downloading file...</b>")
    dl_dir = f"downloads/cmd_{int(time.time())}"
    os.makedirs(dl_dir, exist_ok=True)

    try:
        fp = await c.download_media(rep, file_name=dl_dir + "/")
        if not fp:
            return await msg.edit_text("❌ Download failed.")

        if do_zip:
            await msg.edit_text(
                f"🗜️ <b>Zipping...</b>\n<code>{clean_html(os.path.basename(fp))}</code>"
            )
            out, ok = create_zip(fp)
            if not ok:
                return await msg.edit_text("❌ 7z not found. Install p7zip.")
            await msg.edit_text(
                f"📤 <b>Uploading zip...</b>\n"
                f"<code>{clean_html(os.path.basename(out))}</code>"
            )
            active_dump = await get_active_dump(uid)
            target_chat = active_dump["id"] if active_dump else None
            await handle_upload_split(
                c, msg, out, m.from_user.first_name,
                user_id=uid, target_chat=target_chat, start_time=time.time(),
            )
            with suppress(Exception): os.remove(fp)
            with suppress(Exception): os.remove(out)
            await msg.edit_text("✅ <b>Done!</b> Zip uploaded.")

        elif do_extract:
            await msg.edit_text(
                f"📂 <b>Extracting...</b>\n<code>{clean_html(os.path.basename(fp))}</code>"
            )
            files, out_dir, err = extract_archive(fp)
            if err:
                return await msg.edit_text(f"❌ Extract error: <code>{clean_html(err)}</code>")
            total = len(files)
            await msg.edit_text(f"📤 <b>Uploading {total} file(s)...</b>")
            active_dump = await get_active_dump(uid)
            target_chat = active_dump["id"] if active_dump else None
            for i, f in enumerate(files, 1):
                await handle_upload_split(
                    c, msg, f, m.from_user.first_name,
                    task_info=f"File {i}/{total}",
                    user_id=uid, target_chat=target_chat, start_time=time.time(),
                )
            with suppress(Exception): os.remove(fp)
            with suppress(Exception): shutil.rmtree(out_dir, ignore_errors=True)
            await msg.edit_text(f"✅ <b>Extracted & uploaded {total} file(s)!</b>")

    except Exception as e:
        with suppress(Exception):
            await msg.edit_text(f"❌ <b>Error:</b> <code>{clean_html(str(e))}</code>")
    finally:
        shutil.rmtree(dl_dir, ignore_errors=True)
