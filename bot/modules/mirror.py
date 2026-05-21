"""
/mirror -up gdl|rcl URL — download and re-upload to GDrive or rclone.
"""

import asyncio
import os
import shutil
import time
from contextlib import suppress

from pyrogram import filters

from bot.core.client import app
from bot.core.config import PROXY_URL, USER_CONFIG_DIR
from bot.core.database.mongo import is_user_banned, get_user_proxy
from bot.helper.download_utils import download_logic
from bot.helper.time_format import clean_html


def _cfg_path(user_id, kind):
    base = os.path.join(USER_CONFIG_DIR, str(user_id))
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "rclone.conf" if kind == "rclone" else "token.pickle")


@app.on_message(filters.command("mirror"))
async def mirror_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")

    args = m.text.split()
    if len(args) < 4 or args[1] != "-up" or args[2] not in ("gdl", "rcl"):
        return await m.reply_text(
            "❌ <b>Usage:</b>\n"
            "• <code>/mirror -up gdl URL</code> — upload to your GDrive\n"
            "• <code>/mirror -up rcl URL</code> — upload via your rclone config\n\n"
            "Upload your configs in /usersettings → rclone.conf / token.pickle"
        )

    kind = args[2]   # gdl or rcl
    url  = args[3]
    uid  = m.from_user.id
    msg  = await m.reply_text(f"⬇️ <b>Downloading for mirror...</b>\n<code>{clean_html(url[:80])}</code>")

    result = await download_logic(url, msg, uid, "auto")

    if isinstance(result, str) and result.startswith("ERROR:"):
        return await msg.edit_text(f"❌ <code>{clean_html(result)}</code>")
    if result == "CANCELLED":
        return await msg.edit_text("⛔ Cancelled.")

    fp = str(result) if isinstance(result, str) else str(result[0])

    if kind == "gdl":
        token_path = _cfg_path(uid, "gdrive")
        if not os.path.exists(token_path):
            return await msg.edit_text(
                "❌ No <b>token.pickle</b> found.\n"
                "Upload it via /usersettings → 📤 token.pickle"
            )
        await msg.edit_text("☁️ <b>Uploading to Google Drive...</b>")
        try:
            from bot.extractors.gdrive import gdrive_upload_with_token
            link = await gdrive_upload_with_token(fp, token_path)
            await msg.edit_text(f"✅ <b>Uploaded to GDrive!</b>\n🔗 {link}")
        except Exception as e:
            await msg.edit_text(f"❌ GDrive upload failed:\n<code>{clean_html(str(e))}</code>")

    elif kind == "rcl":
        conf_path = _cfg_path(uid, "rclone")
        if not os.path.exists(conf_path):
            return await msg.edit_text(
                "❌ No <b>rclone.conf</b> found.\n"
                "Upload it via /usersettings → 📤 rclone.conf"
            )
        await msg.edit_text("☁️ <b>Uploading via rclone...</b>")
        try:
            # Read first remote name from conf
            remote = None
            with open(conf_path, "r", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("[") and line.endswith("]"):
                        remote = line[1:-1]
                        break
            if not remote:
                return await msg.edit_text("❌ No [remote] section found in rclone.conf")
            proc = await asyncio.create_subprocess_exec(
                "rclone", "copy", fp, f"{remote}:", "--config", conf_path, "-v",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode != 0:
                return await msg.edit_text(
                    f"❌ rclone failed:\n<code>{clean_html(err.decode(errors='ignore')[:300])}</code>"
                )
            await msg.edit_text(f"✅ <b>Uploaded via rclone to {remote}!</b>")
        except Exception as e:
            await msg.edit_text(f"❌ rclone error:\n<code>{clean_html(str(e))}</code>")

    with suppress(Exception):
        os.remove(fp)
