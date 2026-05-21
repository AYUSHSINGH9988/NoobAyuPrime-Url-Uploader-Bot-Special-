"""
/teradl — TeraBox download via API.
"""

import asyncio
import os
import shutil
import time
from contextlib import suppress

import aiohttp
from pyrogram import filters

from bot.core.client import app
from bot.core.config import PROXY_URL
from bot.core.database.mongo import is_user_banned, get_active_dump, get_user_proxy
from bot.helper.progress import update_progress_ui
from bot.helper.task_manager import abort_dict
from bot.helper.time_format import clean_html, humanbytes
from bot.helper.uploader import handle_upload_split

_TERA_API = "https://terabox.udayscript.tech/api"
_HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json",
}


async def _terabox_info(url: str, proxy=None) -> dict | None:
    try:
        params = {"url": url}
        async with aiohttp.ClientSession() as sess:
            async with sess.get(_TERA_API, params=params, headers=_HEADERS,
                                proxy=proxy, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data if data.get("status") == "success" else None
    except Exception:
        return None


async def _stream_down(url, out_path, msg, start, name, proxy=None):
    connector = aiohttp.TCPConnector(limit=4, ssl=False)
    timeout   = aiohttp.ClientTimeout(total=7200)
    dl = 0
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as sess:
            async with sess.get(url, headers=_HEADERS, proxy=proxy) as resp:
                if resp.status not in (200, 206):
                    return False, f"HTTP {resp.status}"
                total = int(resp.headers.get("content-length", 0))
                with open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(512 * 1024):
                        if msg.id in abort_dict:
                            return False, "CANCELLED"
                        f.write(chunk)
                        dl += len(chunk)
                        await update_progress_ui(dl, total, msg, start, "📥 TeraBox DL...", name, engine="TeraBoxAPI")
        return True, None
    except Exception as e:
        return False, str(e)


@app.on_message(filters.command("teradl"))
async def teradl_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")

    args = m.text.split(None, 1)
    if len(args) < 2:
        return await m.reply_text("❌ Usage: /teradl https://1024terabox.com/...")

    url   = args[1].strip()
    uid   = m.from_user.id
    proxy = (await get_user_proxy(uid)) or PROXY_URL
    msg   = await m.reply_text(f"🔍 <b>Fetching TeraBox info...</b>\n<code>{clean_html(url[:80])}</code>")

    info = await _terabox_info(url, proxy)
    if not info:
        return await msg.edit_text(
            "❌ Could not fetch TeraBox info.\n"
            "The file may be private, expired or the API is down."
        )

    files = info.get("files", [])
    if not files:
        # Single file
        dl_url = info.get("download_url") or info.get("url")
        name   = info.get("file_name") or f"terabox_{int(time.time())}.mp4"
        files  = [{"file_name": name, "download_url": dl_url}]

    dl_dir = f"downloads/tera_{int(time.time())}_{uid}"
    os.makedirs(dl_dir, exist_ok=True)

    active_dump = await get_active_dump(uid)
    target_chat = active_dump["id"] if active_dump else None

    for i, file_info in enumerate(files, 1):
        task_info = f"File {i}/{len(files)}" if len(files) > 1 else None
        name      = file_info.get("file_name") or f"tera_{i}.mp4"
        dl_url    = file_info.get("download_url") or file_info.get("url") or ""

        if not dl_url:
            await msg.edit_text(f"❌ No download URL for: <code>{clean_html(name)}</code>")
            continue

        out_path = os.path.join(dl_dir, name)
        await msg.edit_text(
            f"⬇️ <b>Downloading...</b>\n<code>{clean_html(name[:60])}</code>"
            + (f"\n{task_info}" if task_info else "")
        )

        ok, err = await _stream_down(dl_url, out_path, msg, time.time(), name, proxy)
        if not ok:
            with suppress(Exception):
                await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")
            continue

        await handle_upload_split(
            c, msg, out_path, "User",
            task_info=task_info,
            user_id=uid, target_chat=target_chat, start_time=time.time(),
        )

    shutil.rmtree(dl_dir, ignore_errors=True)
    dest = "dump" if target_chat else "PM"
    with suppress(Exception):
        await msg.edit_text(f"✅ <b>TeraBox done! {len(files)} file(s) → {dest}!</b>")
