"""
/setdump, /dumps — manage upload destination channels.
"""

from contextlib import suppress

from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.database.mongo import (
    add_dump, get_user_dumps, set_active_dump,
    get_active_dump, delete_dump,
)
from bot.helper.time_format import clean_html


@app.on_message(filters.command("setdump"))
async def setdump_cmd(c, m):
    await m.reply_text(
        "📤 <b>Forward any message from the channel you want to use as dump.</b>\n\n"
        "The bot will save that channel and upload all files there by default."
    )


@app.on_message(filters.forwarded & filters.private)
async def dump_handler(c, m):
    if not m.forward_from_chat:
        return
    chat    = m.forward_from_chat
    chat_id = chat.id
    title   = chat.title or str(chat_id)
    uid     = m.from_user.id
    await add_dump(uid, chat_id, title)
    await m.reply_text(
        f"✅ <b>Dump set:</b> <code>{clean_html(title)}</code>\n"
        f"<b>ID:</b> <code>{chat_id}</code>\n\n"
        "All your files will now be uploaded here."
    )


@app.on_message(filters.command(["dumps", "listdumps"]))
async def list_dumps_cmd(c, m):
    uid   = m.from_user.id
    dumps = await get_user_dumps(uid)
    active = await get_active_dump(uid)
    active_id = active["id"] if active else None

    if not dumps:
        return await m.reply_text(
            "❌ <b>No dump channels set.</b>\n\n"
            "Forward any channel message to set one.\n"
            "Use /setdump for instructions."
        )

    rows = []
    for d in dumps:
        label = f"{'✅ ' if d['id'] == active_id else ''}{clean_html(d['title'])}"
        rows.append([
            InlineKeyboardButton(label, callback_data=f"setdump_{d['id']}_{uid}"),
            InlineKeyboardButton("🗑", callback_data=f"deldump_{d['id']}_{uid}"),
        ])

    await m.reply_text(
        "📋 <b>Your Dump Channels</b>\n\n"
        "Tap a channel to make it active, or 🗑 to remove it.",
        reply_markup=InlineKeyboardMarkup(rows),
    )


@app.on_callback_query(filters.regex(r"^setdump_"))
async def setdump_active_cb(c, cb):
    parts   = cb.data.split("_")
    chat_id = int(parts[1])
    user_id = int(parts[2])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not yours!", show_alert=True)
    await set_active_dump(user_id, chat_id)
    await cb.answer("✅ Active dump updated!")
    # Refresh list
    dumps    = await get_user_dumps(user_id)
    active   = await get_active_dump(user_id)
    active_id = active["id"] if active else None
    rows = []
    for d in dumps:
        label = f"{'✅ ' if d['id'] == active_id else ''}{clean_html(d['title'])}"
        rows.append([
            InlineKeyboardButton(label, callback_data=f"setdump_{d['id']}_{user_id}"),
            InlineKeyboardButton("🗑",  callback_data=f"deldump_{d['id']}_{user_id}"),
        ])
    with suppress(Exception):
        await cb.message.edit_reply_markup(InlineKeyboardMarkup(rows))


@app.on_callback_query(filters.regex(r"^deldump_"))
async def deldump_cb(c, cb):
    parts   = cb.data.split("_")
    chat_id = int(parts[1])
    user_id = int(parts[2])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not yours!", show_alert=True)
    await delete_dump(user_id, chat_id)
    await cb.answer("🗑 Dump removed!")
    dumps    = await get_user_dumps(user_id)
    active   = await get_active_dump(user_id)
    active_id = active["id"] if active else None
    if not dumps:
        with suppress(Exception):
            await cb.message.edit_text("❌ <b>No dump channels left.</b> Forward a channel message to add one.")
        return
    rows = []
    for d in dumps:
        label = f"{'✅ ' if d['id'] == active_id else ''}{clean_html(d['title'])}"
        rows.append([
            InlineKeyboardButton(label, callback_data=f"setdump_{d['id']}_{user_id}"),
            InlineKeyboardButton("🗑",  callback_data=f"deldump_{d['id']}_{user_id}"),
        ])
    with suppress(Exception):
        await cb.message.edit_reply_markup(InlineKeyboardMarkup(rows))
