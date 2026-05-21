"""
/status — wzml-x style global progress bar for all users' tasks.
Shows all active tasks in a single auto-refreshing message.
"""

import asyncio
from contextlib import suppress

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.helper.progress import format_global_status, global_status_loop
from bot.helper.task_manager import GLOBAL_STATUS, abort_dict, ACTIVE_TASKS


@app.on_message(filters.command("status"))
async def status_cmd(c, m):
    chat_id = m.chat.id
    text    = format_global_status()

    kb   = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data="status_refresh"),
        InlineKeyboardButton("❌ Close",   callback_data="status_close"),
    ]])
    sent = await m.reply_text(text, reply_markup=kb, disable_web_page_preview=True)

    # Cancel any previous status watcher in this chat
    prev = GLOBAL_STATUS.get(chat_id)
    if prev:
        with suppress(Exception):
            prev.get("task") and prev["task"].cancel()
        with suppress(Exception):
            await prev["message"].delete()

    GLOBAL_STATUS[chat_id] = {"message": sent, "task": None}
    GLOBAL_STATUS[chat_id]["task"] = asyncio.create_task(global_status_loop(chat_id))


@app.on_callback_query(filters.regex(r"^status_refresh$"))
async def status_refresh_cb(c, cb):
    await cb.answer("Refreshed!")
    text = format_global_status()
    kb   = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data="status_refresh"),
        InlineKeyboardButton("❌ Close",   callback_data="status_close"),
    ]])
    with suppress(Exception):
        await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)


@app.on_callback_query(filters.regex(r"^status_close$"))
async def status_close_cb(c, cb):
    await cb.answer()
    chat_id = cb.message.chat.id
    prev = GLOBAL_STATUS.pop(chat_id, None)
    if prev:
        with suppress(Exception):
            prev.get("task") and prev["task"].cancel()
    with suppress(Exception):
        await cb.message.delete()


@app.on_callback_query(filters.regex(r"^cancel_(\d+)$"))
async def cancel_task_cb(c, cb):
    msg_id = int(cb.data.split("_")[1])
    abort_dict[msg_id] = True
    ACTIVE_TASKS.pop(msg_id, None)
    await cb.answer("⛔ Cancelling task...", show_alert=True)
    with suppress(Exception):
        await cb.message.edit_text("⛔ <b>Task cancelled by user.</b>")
