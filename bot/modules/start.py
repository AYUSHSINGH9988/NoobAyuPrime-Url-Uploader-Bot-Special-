"""
/start, /help, /ping, /restart
"""

import sys
import time

from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.config import AUTH_GROUP, JOIN_LINK, DEV_NAME, OWNER_ID
from bot.core.database.mongo import is_user_banned, ensure_user
from bot.helper.time_format import get_readable_time, clean_html
from bot.helper import progress as _prog

bot_start_time = time.time()


HELP_TEXT = (
    "📖 <b>Command Reference</b>\n\n"

    "━━━ 📥 Downloads ━━━\n"
    "• /dl &lt;url&gt; [-n name] [-z|-zip] [-e|-extract]\n"
    "• /ytdl &lt;url&gt; [-n name] [-b bulk]\n"
    "• /leech &lt;magnet/url&gt; [-s seed]\n"
    "• /mdl &lt;mega_url&gt;\n"
    "• /scriptdl &lt;url&gt; — HC / PHub / WH / XH\n"
    "• /teradl &lt;url&gt; — TeraBox\n"
    "• /bdl &lt;url&gt; — Bunkr\n"
    "• /playlist &lt;url&gt; [--quality 720]\n"
    "• /tgdl — TG channel dump\n"
    "• /queue &lt;url&gt; — Add to queue\n\n"

    "━━━ 📦 Files ━━━\n"
    "• /cmd &lt;reply/link&gt; -z  — Zip\n"
    "• /cmd &lt;reply&gt; -e       — Extract\n\n"

    "━━━ 🎬 Video Tools ━━━\n"
    "• /vt &lt;reply&gt; -mp3          — Extract audio as MP3\n"
    "• /vt &lt;reply&gt; -merge &lt;reply audio&gt; — Merge audio into video\n"
    "• /vt &lt;reply&gt; -subextract [n]  — Extract subtitle stream #n\n"
    "• /vt &lt;reply&gt; -submerge &lt;reply sub&gt; — Add subtitle to video\n\n"

    "━━━ ☁️ Mega ━━━\n"
    "• /login email pass\n"
    "• /logout | /megainfo\n"
    "• /renameall &lt;folder_link&gt; | &lt;pattern&gt; | &lt;replacement&gt;\n\n"

    "━━━ ⚙️ Settings ━━━\n"
    "• /usersettings — compact settings panel\n"
    "• /setdump | /dumps — manage upload channels\n"
    "• /status — all tasks live status\n"
    "• /scriptdl /status — view failed links\n"
    "• /mirror -up gdl|rcl &lt;url&gt;\n"
    "• /stopseed &lt;gid&gt;\n"
    "• /ping | /restart\n"
)


@app.on_message(filters.command("start") & filters.private)
async def start_cmd(c, m):
    uid = m.from_user.id
    try:
        await ensure_user(uid)
    except Exception:
        pass

    if await is_user_banned(uid):
        return await m.reply_text("❌ You are banned from using this bot.")

    if AUTH_GROUP and AUTH_GROUP != 0:
        try:
            member = await c.get_chat_member(AUTH_GROUP, uid)
            from pyrogram.enums import ChatMemberStatus
            if member.status in (ChatMemberStatus.BANNED, ChatMemberStatus.LEFT):
                raise Exception("Not a member")
        except Exception:
            if JOIN_LINK:
                btn = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔗 Join to Use Bot", url=JOIN_LINK)
                ]])
                return await m.reply_text(
                    f"⚠️ <b>Access Restricted</b>\n\n"
                    f"You must join the authorized group.\n"
                    f"Managed by <b>{clean_html(DEV_NAME)}</b>.",
                    reply_markup=btn,
                )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👨‍💻 Developer", url="tg://user?id=8493596199"),
        InlineKeyboardButton("📖 Help",      callback_data="open_help"),
    ]])
    await m.reply_text(
        f"👋 <b>Hello {clean_html(m.from_user.first_name)}!</b>\n\n"
        "🤖 <b>Advanced Leech & Uploader Bot</b>\n\n"
        "Download from YouTube, Direct Links, Torrents, Mega, Bunkr, "
        "TeraBox, HentaiCity, PornHub, WatchHentai, Dailymotion & more.\n\n"
        "📤 Upload to Telegram with auto-thumbnail, custom dump channels, "
        "video tools, archive ops and more.\n\n"
        f"⚙️ <b>Engine:</b> aria2c <code>{_prog.ARIA2C_VERSION}</code> | "
        f"yt-dlp <code>{_prog.YTDLP_VERSION}</code>",
        reply_markup=kb,
    )


@app.on_callback_query(filters.regex(r"^open_help$"))
async def open_help_cb(c, cb):
    await cb.answer()
    await cb.message.reply_text(HELP_TEXT, reply_markup=InlineKeyboardMarkup([[
        InlineKeyboardButton("👨‍💻 Developer", url="tg://user?id=8493596199"),
    ]]))


@app.on_message(filters.command("help"))
async def help_cmd(c, m):
    await m.reply_text(HELP_TEXT, reply_markup=InlineKeyboardMarkup([[
        InlineKeyboardButton("👨‍💻 Developer", url="tg://user?id=8493596199"),
    ]]))


@app.on_message(filters.command("ping"))
async def ping_cmd(c, m):
    uptime = get_readable_time(int(time.time() - bot_start_time))
    await m.reply_text(
        f"🏓 <b>Bot is Alive!</b>\n"
        f"⏱ <b>Uptime:</b> <code>{uptime}</code>\n"
        f"⚙️ <b>aria2c:</b> <code>{_prog.ARIA2C_VERSION}</code>\n"
        f"🎬 <b>yt-dlp:</b> <code>{_prog.YTDLP_VERSION}</code>\n"
        f"📡 <b>pyrogram:</b> <code>{_prog.PYROGRAM_VERSION}</code>"
    )


@app.on_message(filters.command("restart"))
async def restart_cmd(c, m):
    if m.from_user.id != OWNER_ID:
        return await m.reply_text("❌ Owner only.")
    await m.reply_text("🔄 Restarting...")
    sys.exit(0)
