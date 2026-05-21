"""
Strict group lock — silently ignore messages from unauthorized groups.
Must be registered with group=-3 so it runs before all other handlers.
"""

from contextlib import suppress
from pyrogram import filters, enums
from pyrogram.types import Message, CallbackQuery

from bot.core.client import app
from bot.core.config import ALLOWED_GROUP_ID


@app.on_message(group=-3)
async def _strict_group_lock(_, m: Message):
    try:
        ct = getattr(m.chat, "type", None)
        if ct == enums.ChatType.PRIVATE:
            return
        if ALLOWED_GROUP_ID and m.chat.id == ALLOWED_GROUP_ID:
            return
        m.stop_propagation()
    except Exception:
        pass


@app.on_callback_query(group=-3)
async def _strict_group_lock_cb(_, cb: CallbackQuery):
    try:
        msg = cb.message
        ct  = getattr(msg.chat, "type", None) if msg else None
        if ct == enums.ChatType.PRIVATE:
            return
        if ALLOWED_GROUP_ID and msg and msg.chat.id == ALLOWED_GROUP_ID:
            return
        with suppress(Exception):
            await cb.answer()
        cb.stop_propagation()
    except Exception:
        pass
