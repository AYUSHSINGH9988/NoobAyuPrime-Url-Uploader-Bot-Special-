"""
/tgdl — Dump all media from a Telegram channel to your dump.
Requires USER_SESSION_STRING for reading private channels.
Usage: /tgdl [channel_id_or_link] [start_msg_id]
"""

import asyncio
import os
import time
from contextlib import suppress

from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app, user_app
from bot.core.config import OWNER_ID
from bot.core.database.mongo import get_active_dump
from bot.helper.task_manager import abort_dict
from bot.helper.time_format import clean_html, humanbytes
from bot.helper.uploader import upload_file

_tgdl_active: dict[int, bool] = {}


@app.on_message(filters.command("tgdl"))
async def tgdl_cmd(c, m):
    if m.from_user.id != OWNER_ID:
        return await m.reply_text("❌ Owner only command.")

    if not user_app:
        return await m.reply_text(
            "❌ <b>USER_SESSION_STRING not set.</b>\n"
            "Set it as environment variable to dump from private channels."
        )

    args = m.command
    if len(args) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b> <code>/tgdl channel_id [start_msg_id]</code>\n\n"
            "Examples:\n"
            "• <code>/tgdl -1001234567890</code>\n"
            "• <code>/tgdl @channelname 500</code>\n"
            "• <code>/tgdl -1001234567890 250</code>"
        )

    chat_arg    = args[1]
    start_id    = int(args[2]) if len(args) > 2 else 1
    uid         = m.from_user.id

    if _tgdl_active.get(uid):
        return await m.reply_text("❌ You already have a /tgdl running! Use /tgdlstop to stop it.")

    try:
        chat_id = int(chat_arg)
    except ValueError:
        chat_id = chat_arg

    active_dump = await get_active_dump(uid)
    target_chat = active_dump["id"] if active_dump else None
    if not target_chat:
        return await m.reply_text("❌ No active dump set. Use /setdump first.")

    msg = await m.reply_text(
        f"📥 <b>TG Dump Starting...</b>\n"
        f"From: <code>{clean_html(str(chat_id))}</code>\n"
        f"→ Dump: <code>{target_chat}</code>"
    )

    _tgdl_active[uid] = True
    asyncio.create_task(_do_tgdl(c, msg, chat_id, start_id, target_chat, uid))


@app.on_message(filters.command("tgdlstop"))
async def tgdlstop_cmd(c, m):
    uid = m.from_user.id
    if _tgdl_active.pop(uid, False):
        await m.reply_text("⛔ <b>TG Dump stopped!</b>")
    else:
        await m.reply_text("❌ No active /tgdl to stop.")


async def _do_tgdl(c, msg, chat_id, start_id, target_chat, uid):
    MEDIA_TYPES = (
        enums.MessageMediaType.DOCUMENT,
        enums.MessageMediaType.VIDEO,
        enums.MessageMediaType.AUDIO,
        enums.MessageMediaType.PHOTO,
    )
    total = ok = fail = 0
    start = time.time()
    last_upd = 0.0

    try:
        client = user_app if user_app else c
        async for message in client.get_chat_history(chat_id):
            if not _tgdl_active.get(uid):
                break
            if message.id < start_id:
                break
            if not message.media:
                continue
            if message.media not in MEDIA_TYPES:
                continue
            total += 1

            try:
                dl_dir = f"downloads/tgdl_{uid}_{message.id}"
                os.makedirs(dl_dir, exist_ok=True)
                fp = await client.download_media(message, file_name=dl_dir + "/")
                if fp:
                    await upload_file(
                        c, msg, fp, "TGDump",
                        user_id=uid, target_chat=target_chat,
                    )
                    ok += 1
                    with suppress(Exception):
                        os.remove(fp)
                else:
                    fail += 1
                import shutil
                shutil.rmtree(dl_dir, ignore_errors=True)
            except Exception:
                fail += 1

            now = time.time()
            if now - last_upd > 10:
                elapsed = int(now - start)
                with suppress(Exception):
                    await msg.edit_text(
                        f"📥 <b>TG Dump in progress...</b>\n"
                        f"✅ {ok}  ❌ {fail}  📦 Total: {total}\n"
                        f"⏱ {elapsed}s  |  /tgdlstop to stop"
                    )
                last_upd = now

        elapsed = int(time.time() - start)
        with suppress(Exception):
            await msg.edit_text(
                f"✅ <b>TG Dump Complete!</b>\n"
                f"✅ {ok} files  ❌ {fail} failed\n"
                f"⏱ Total: {elapsed}s"
            )
    except Exception as e:
        with suppress(Exception):
            await msg.edit_text(f"❌ TG Dump error:\n<code>{clean_html(str(e))}</code>")
    finally:
        _tgdl_active.pop(uid, None)
