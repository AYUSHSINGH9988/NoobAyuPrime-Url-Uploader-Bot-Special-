"""
High-priority text message handler (group=-1).
Captures user text replies for:
  - Proxy URL input (waiting_for_proxy)
  - Admin bsettings numeric input (waiting_for_bsetting)
  - Renameall replacement text (waiting_for_renameall_text)
Runs BEFORE all other handlers due to group=-1.
"""

from contextlib import suppress

from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.database.mongo import (
    set_user_proxy, clear_user_proxy, set_bsetting,
)
from bot.helper.task_manager import (
    waiting_for_proxy, waiting_for_bsetting,
    waiting_for_renameall_text, renameall_sessions,
)
from bot.helper.time_format import clean_html


@app.on_message(filters.text & ~filters.via_bot, group=-1)
async def text_capture_handler(c, m):
    if not m.from_user:
        return

    uid  = m.from_user.id
    text = (m.text or "").strip()

    # ── 1. Proxy URL input ────────────────────────────────────────────────────
    if uid in waiting_for_proxy:
        if text.startswith("/"):
            return
        waiting_for_proxy.pop(uid, None)
        if text.lower() in ("none", "off", "clear"):
            await clear_user_proxy(uid)
            await m.reply_text("🗑 Proxy cleared.")
        elif not any(text.startswith(p) for p in ("http://", "https://", "socks5://", "socks4://")):
            await m.reply_text(
                "❌ Invalid proxy. Must start with http://, https://, socks5:// or socks4://"
            )
        else:
            await set_user_proxy(uid, text)
            await m.reply_text(f"✅ Proxy saved:\n<code>{clean_html(text)}</code>")
        m.stop_propagation()
        return

    # ── 2. Admin bsettings numeric input ──────────────────────────────────────
    if uid in waiting_for_bsetting:
        if text.startswith("/"):
            return
        key = waiting_for_bsetting.pop(uid)
        try:
            if "size" in key:
                val = float(text)
            else:
                val = int(text)
            if val < 0:
                raise ValueError("must be ≥ 0")
        except Exception:
            return await m.reply_text("❌ Invalid number. Try again via /bsettings.")
        ok = await set_bsetting(key, val)
        await m.reply_text(
            ("✅ Updated " if ok else "❌ Failed to update ")
            + f"<code>{key}</code> = <code>{val}</code>"
        )
        m.stop_propagation()
        return

    # ── 3. Renameall text input ───────────────────────────────────────────────
    if uid not in waiting_for_renameall_text:
        return

    if m.chat.type != enums.ChatType.PRIVATE:
        return

    if text.startswith("/"):
        return

    msg_id  = waiting_for_renameall_text.pop(uid)
    session = renameall_sessions.get(msg_id)
    if not session:
        return await m.reply_text("❌ Session expired. Run /renameall again.")

    pattern = session.get("pattern")
    if not pattern:
        return await m.reply_text("❌ No rename option selected. Run /renameall again.")

    if pattern == "replace" and "|" not in text:
        waiting_for_renameall_text[uid] = msg_id
        return await m.reply_text(
            "❌ Format must be <code>OldText|NewText</code>. Try again."
        )

    session["replacement"] = text

    import posixpath

    def _basename_of(item):
        if isinstance(item, tuple):
            try:
                return item[1]["a"]["n"]
            except Exception:
                return ""
        return posixpath.basename(item)

    from bot.modules.renameall import _build_new_name

    files = session["files"]
    total = session["total"]
    src1  = _basename_of(files[0])
    ex1   = _build_new_name(src1, pattern, text, 1)
    src2  = _basename_of(files[min(1, total - 1)])
    ex2   = _build_new_name(src2, pattern, text, 2)

    await m.reply_text(
        f"📂 <b>Files: {total:,}</b>\n\n"
        f"👁 <b>Preview:</b>\n"
        f"• <code>{clean_html(src1[:50])}</code>\n"
        f"  → <code>{clean_html(ex1[:50])}</code>\n"
        f"• <code>{clean_html(src2[:50])}</code>\n"
        f"  → <code>{clean_html(ex2[:50])}</code>\n\n"
        f"<b>Pattern:</b> <code>{pattern}</code>\n"
        f"<b>Value:</b> <code>{clean_html(text)}</code>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🚀 Start Renaming!", callback_data=f"ra_confirm|{msg_id}"),
            InlineKeyboardButton("❌ Cancel",           callback_data=f"ra_cancel|{msg_id}"),
        ]])
    )
    m.stop_propagation()
