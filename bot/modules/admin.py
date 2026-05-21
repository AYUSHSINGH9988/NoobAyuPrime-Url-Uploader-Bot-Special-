"""
Admin commands: /broadcast /msg /ban /unban /warn /bsettings
"""

from contextlib import suppress

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.config import OWNER_ID
from bot.core.database.mongo import (
    users_col, get_bsettings, set_bsetting, DEFAULT_BSETTINGS,
)
from bot.helper.task_manager import waiting_for_bsetting
from bot.helper.time_format import clean_html


# ── /broadcast ────────────────────────────────────────────────────────────────

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_cmd(c, m):
    if len(m.command) < 2 and not m.reply_to_message:
        return await m.reply_text("❌ <b>Usage:</b> /broadcast your message")
    text   = (m.reply_to_message.text or m.reply_to_message.caption) \
             if m.reply_to_message else " ".join(m.command[1:])
    cursor = users_col.find({}, {"_id": 1})
    ok, fail = 0, 0
    async for user in cursor:
        try:
            await c.send_message(user["_id"], text)
            ok += 1
        except Exception:
            fail += 1
    await m.reply_text(f"📢 <b>Broadcast done.</b> ✅ {ok}  ❌ {fail}")


# ── /msg ──────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("msg") & filters.user(OWNER_ID))
async def admin_msg_cmd(c, m):
    args = m.text.split(None, 2)
    if len(args) < 3:
        return await m.reply_text("❌ Usage: /msg USER_ID message")
    try:
        target = int(args[1])
    except ValueError:
        return await m.reply_text("❌ Invalid user ID.")
    await c.send_message(target, args[2])
    await m.reply_text("✅ Sent.")


# ── /ban /unban ───────────────────────────────────────────────────────────────

@app.on_message(filters.command("ban") & filters.user(OWNER_ID))
async def ban_cmd(c, m):
    target = await _resolve_target(m)
    if not target:
        return await m.reply_text("❌ Provide a user ID or reply.")
    await users_col.update_one({"_id": target}, {"$set": {"is_banned": True}}, upsert=True)
    await m.reply_text(f"🔨 <b>User <code>{target}</code> banned.</b>")


@app.on_message(filters.command("unban") & filters.user(OWNER_ID))
async def unban_cmd(c, m):
    target = await _resolve_target(m)
    if not target:
        return await m.reply_text("❌ Provide a user ID or reply.")
    await users_col.update_one({"_id": target}, {"$set": {"is_banned": False}})
    await m.reply_text(f"✅ <b>User <code>{target}</code> unbanned.</b>")


@app.on_message(filters.command("warn") & filters.user(OWNER_ID))
async def warn_cmd(c, m):
    target = await _resolve_target(m)
    if not target:
        return await m.reply_text("❌ Provide a user ID or reply.")
    doc = await users_col.find_one({"_id": target}) or {}
    warns = doc.get("warns", 0) + 1
    ban   = warns >= 3
    await users_col.update_one(
        {"_id": target},
        {"$set": {"warns": warns, "is_banned": ban}},
        upsert=True,
    )
    msg = f"⚠️ Warn #{warns}/3 sent to <code>{target}</code>."
    if ban:
        msg += "\n🔨 <b>Auto-banned at 3 warns.</b>"
    await m.reply_text(msg)


async def _resolve_target(m):
    if len(m.command) > 1:
        try:
            return int(m.command[1])
        except ValueError:
            pass
    if m.reply_to_message and m.reply_to_message.from_user:
        return m.reply_to_message.from_user.id
    return None


# ── /bsettings ────────────────────────────────────────────────────────────────

def _bs_text(s):
    return (
        "🛠️ <b>Bot Settings</b>\n\n"
        f"• Max tasks/user:  <code>{s['max_tasks_per_user']}</code>\n"
        f"• Max GB ytdl:     <code>{s['max_size_gb_ytdl']}</code>\n"
        f"• Max GB mdl:      <code>{s['max_size_gb_mdl']}</code>\n"
        f"• Max GB bdl:      <code>{s['max_size_gb_bdl']}</code>\n"
        f"• Max GB leech:    <code>{s['max_size_gb_leech']}</code>\n\n"
        "<i>Tap a row to edit. Send new value. /cancbs to cancel.</i>"
    )


def _bs_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Max tasks/user", callback_data="bs|max_tasks_per_user")],
        [InlineKeyboardButton("Max GB ytdl",    callback_data="bs|max_size_gb_ytdl")],
        [InlineKeyboardButton("Max GB mdl",     callback_data="bs|max_size_gb_mdl")],
        [InlineKeyboardButton("Max GB bdl",     callback_data="bs|max_size_gb_bdl")],
        [InlineKeyboardButton("Max GB leech",   callback_data="bs|max_size_gb_leech")],
        [InlineKeyboardButton("✖ Close",        callback_data="bs|close")],
    ])


@app.on_message(filters.command("bsettings"))
async def bsettings_cmd(c, m):
    if m.from_user.id != OWNER_ID:
        return await m.reply_text("❌ Owner only.")
    s = await get_bsettings()
    await m.reply_text(_bs_text(s), reply_markup=_bs_kb())


@app.on_message(filters.command("cancbs"))
async def cancbs_cmd(c, m):
    waiting_for_bsetting.pop(m.from_user.id, None)
    await m.reply_text("✅ Cancelled.")


@app.on_callback_query(filters.regex(r"^bs\|"))
async def bs_cb(c, cb):
    if cb.from_user.id != OWNER_ID:
        return await cb.answer("❌ Owner only!", show_alert=True)
    key = cb.data.split("|")[1]
    if key == "close":
        await cb.answer()
        with suppress(Exception):
            await cb.message.delete()
        return
    waiting_for_bsetting[cb.from_user.id] = key
    await cb.answer(f"Send new value for: {key}")
    with suppress(Exception):
        await cb.message.edit_text(
            f"⌨️ <b>Send new value for:</b> <code>{key}</code>\n"
            f"Use /cancbs to cancel."
        )
