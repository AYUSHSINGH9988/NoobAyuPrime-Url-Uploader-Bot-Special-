"""
/usersettings — wzml-x compact style.
All settings visible in one tight inline keyboard, no extra menus.
"""

import os
from contextlib import suppress

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.config import USER_CONFIG_DIR
from bot.core.database.mongo import (
    get_user_settings, set_user_setting,
    get_user_thumbnail, set_user_thumbnail, clear_user_thumbnail,
    get_user_proxy, set_user_proxy, clear_user_proxy,
)
from bot.helper.task_manager import (
    waiting_for_thumbnail, waiting_for_config_upload, waiting_for_proxy,
)
from bot.helper.time_format import clean_html


def _cfg_path(user_id, kind):
    base = os.path.join(USER_CONFIG_DIR, str(user_id))
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "rclone.conf" if kind == "rclone" else "token.pickle")


def _has_cfg(user_id, kind):
    p = _cfg_path(user_id, kind)
    return os.path.exists(p) and os.path.getsize(p) > 0


# ── Compact settings menu (wzml-x style) ─────────────────────────────────────

async def _build_settings_menu(user_id):
    settings   = await get_user_settings(user_id)
    send_as    = settings.get("send_as", "media")
    has_thumb  = bool(settings.get("thumbnail"))
    proxy      = await get_user_proxy(user_id)
    has_rcl    = _has_cfg(user_id, "rclone")
    has_gd     = _has_cfg(user_id, "gdrive")

    sa_icon  = "📹" if send_as == "media" else "📄"
    sa_next  = "document" if send_as == "media" else "media"
    sa_label = "Media" if send_as == "media" else "Doc"

    text = (
        "⚙️ <b>User Settings</b>\n"
        "─────────────────────\n"
        f"<b>Mode:</b>      {sa_icon} {sa_label}\n"
        f"<b>Thumb:</b>     {'✅ Set' if has_thumb else '❌ Not set'}\n"
        f"<b>Proxy:</b>     {'✅ ' + clean_html((proxy or '')[:30]) if proxy else '❌ Not set'}\n"
        f"<b>rclone:</b>    {'✅' if has_rcl else '❌'}\n"
        f"<b>GDrive:</b>    {'✅' if has_gd else '❌'}\n"
        "─────────────────────\n"
        "<i>Tap a button below to change.</i>"
    )

    thumb_btn = "🗑 Thumb" if has_thumb else "🖼 Thumb"
    proxy_btn = "🗑 Proxy" if proxy else "🌐 Proxy"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{sa_icon} Mode: {sa_label}", callback_data=f"us2_mode_{sa_next}_{user_id}"),
        ],
        [
            InlineKeyboardButton("📷 Set Thumb",   callback_data=f"us2_setthumb_{user_id}"),
            InlineKeyboardButton("🗑 Clear Thumb", callback_data=f"us2_clrthumb_{user_id}"),
        ],
        [
            InlineKeyboardButton("🌐 Set Proxy",   callback_data=f"us2_setproxy_{user_id}"),
            InlineKeyboardButton("🗑 Clear Proxy",  callback_data=f"us2_clrproxy_{user_id}"),
        ],
        [
            InlineKeyboardButton("📤 rclone.conf",   callback_data=f"us2_upload_rclone_{user_id}"),
            InlineKeyboardButton("🗑",               callback_data=f"us2_clear_rclone_{user_id}"),
            InlineKeyboardButton("📤 token.pickle",  callback_data=f"us2_upload_gdrive_{user_id}"),
            InlineKeyboardButton("🗑",               callback_data=f"us2_clear_gdrive_{user_id}"),
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data=f"us2_close_{user_id}"),
        ],
    ])
    return text, kb


@app.on_message(filters.command("usersettings"))
async def usersettings_cmd(c, m):
    uid  = m.from_user.id
    text, kb = await _build_settings_menu(uid)
    await m.reply_text(text, reply_markup=kb)


async def _refresh_settings(cb, user_id):
    text, kb = await _build_settings_menu(user_id)
    with suppress(Exception):
        await cb.message.edit_text(text, reply_markup=kb)


# ── Send mode ─────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^us2_mode_"))
async def us2_mode_cb(c, cb):
    parts   = cb.data.split("_")
    new_mode = parts[2]
    user_id  = int(parts[3])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    await set_user_setting(user_id, "send_as", new_mode)
    await cb.answer(f"✅ Mode → {'Media' if new_mode == 'media' else 'Document'}")
    await _refresh_settings(cb, user_id)


# ── Thumbnail ─────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^us2_setthumb_"))
async def us2_setthumb_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    waiting_for_thumbnail[user_id] = True
    await cb.answer("📸 Send a photo now.", show_alert=True)
    with suppress(Exception):
        await cb.message.edit_text(
            "📸 <b>Send a photo to set as thumbnail.</b>\n"
            "Use /cancthumb to cancel."
        )


@app.on_callback_query(filters.regex(r"^us2_clrthumb_"))
async def us2_clrthumb_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    await clear_user_thumbnail(user_id)
    await cb.answer("🗑 Thumbnail cleared!")
    await _refresh_settings(cb, user_id)


@app.on_message(filters.private & filters.photo)
async def photo_thumb_handler(c, m):
    uid = m.from_user.id
    if not waiting_for_thumbnail.pop(uid, False):
        return
    file_id = m.photo.file_id
    await set_user_thumbnail(uid, file_id)
    await m.reply_text("✅ <b>Thumbnail saved!</b> It will be applied to all future uploads.")


@app.on_message(filters.command("cancthumb") & filters.private)
async def cancthumb_cmd(c, m):
    waiting_for_thumbnail.pop(m.from_user.id, None)
    await m.reply_text("✅ Thumbnail input cancelled.")


# ── Proxy ─────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^us2_setproxy_"))
async def us2_setproxy_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    waiting_for_proxy[user_id] = True
    await cb.answer("📩 Send proxy URL.", show_alert=True)
    with suppress(Exception):
        await cb.message.edit_text(
            "🌐 <b>Send your proxy URL:</b>\n"
            "<code>http://user:pass@host:port</code>\n"
            "Use /cancproxy to cancel."
        )


@app.on_callback_query(filters.regex(r"^us2_clrproxy_"))
async def us2_clrproxy_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    await clear_user_proxy(user_id)
    await cb.answer("🗑 Proxy cleared!")
    await _refresh_settings(cb, user_id)


@app.on_message(filters.command("cancproxy") & filters.private)
async def cancproxy_cmd(c, m):
    waiting_for_proxy.pop(m.from_user.id, None)
    await m.reply_text("✅ Proxy input cancelled.")


# ── rclone / GDrive config upload ────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^us2_upload_(rclone|gdrive)_"))
async def us2_upload_cfg_cb(c, cb):
    parts   = cb.data.split("_")
    kind    = parts[2]
    user_id = int(parts[3])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    waiting_for_config_upload[user_id] = kind
    fname = "rclone.conf" if kind == "rclone" else "token.pickle"
    await cb.answer(f"📎 Send {fname} now.", show_alert=True)
    with suppress(Exception):
        await cb.message.edit_text(
            f"📎 <b>Send your <code>{fname}</code> file now.</b>\n"
            f"Use /canccfg to cancel."
        )


@app.on_callback_query(filters.regex(r"^us2_clear_(rclone|gdrive)_"))
async def us2_clear_cfg_cb(c, cb):
    parts   = cb.data.split("_")
    kind    = parts[2]
    user_id = int(parts[3])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    p = _cfg_path(user_id, kind)
    if os.path.exists(p):
        os.remove(p)
    fname = "rclone.conf" if kind == "rclone" else "token.pickle"
    await cb.answer(f"🗑 {fname} cleared!")
    await _refresh_settings(cb, user_id)


@app.on_message(filters.private & filters.document, group=-2)
async def config_doc_capture(c, m):
    uid = m.from_user.id
    kind = waiting_for_config_upload.get(uid)
    if not kind:
        return
    fname = "rclone.conf" if kind == "rclone" else "token.pickle"
    doc   = m.document
    if not doc or not doc.file_name:
        return
    if doc.file_name not in ("rclone.conf", "token.pickle"):
        with suppress(Exception):
            await m.reply_text(
                f"❌ Expected <code>{fname}</code> — got <code>{clean_html(doc.file_name)}</code>."
            )
        return
    waiting_for_config_upload.pop(uid, None)
    dest = _cfg_path(uid, kind)
    await c.download_media(m, file_name=dest)
    await m.reply_text(f"✅ <b><code>{fname}</code> saved!</b>")


@app.on_message(filters.command("canccfg") & filters.private)
async def canccfg_cmd(c, m):
    waiting_for_config_upload.pop(m.from_user.id, None)
    await m.reply_text("✅ Config upload cancelled.")


# ── Close ─────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^us2_close_"))
async def us2_close_cb(c, cb):
    await cb.answer()
    with suppress(Exception):
        await cb.message.delete()
