"""
/bdl — Download from Bunkr.
"""

import asyncio
import os
import re
import shutil
import time
from contextlib import suppress

import aiohttp
import yt_dlp
from bs4 import BeautifulSoup
from pyrogram import filters

from bot.core.client import app
from bot.core.config import PROXY_URL
from bot.core.database.mongo import is_user_banned, get_active_dump, get_user_proxy
from bot.helper.progress import update_progress_ui
from bot.helper.task_manager import abort_dict
from bot.helper.time_format import clean_html, humanbytes
from bot.helper.uploader import handle_upload_split

_BUNKR_HEADERS = {
    "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":     "https://bunkr.site/",
    "Accept":      "*/*",
}
_BUNKR_SITES = r"bunkr\.(site|is|ru|la|to|ph|su|black|cat|li|sk|fi|media)"


def _is_bunkr(url: str) -> bool:
    return bool(re.search(_BUNKR_SITES, url, re.I))


def _bunkr_direct(url: str) -> str | None:
    """Try to scrape direct download link from a Bunkr page."""
    try:
        import requests
        r = requests.get(url, headers=_BUNKR_HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        # Look for direct media src or og:video
        for attr in ("og:video", "og:video:url", "og:url"):
            tag = soup.find("meta", property=attr)
            if tag and tag.get("content"):
                c = tag["content"]
                if any(c.lower().endswith(e) for e in (".mp4", ".mkv", ".m3u8", ".webm", ".mov")):
                    return c
        # Also check <video> and <source>
        for tag in soup.select("source[src], video[src]"):
            src = tag.get("src") or tag.get("data-src")
            if src and src.startswith("http"):
                return src
        # Check download link
        dl_link = soup.select_one('a[href*="/d/"], a.btn-download, a[download]')
        if dl_link and dl_link.get("href"):
            return "https://bunkr.site" + dl_link["href"] if dl_link["href"].startswith("/") else dl_link["href"]
    except Exception:
        pass
    return None


async def _download_stream(url, out_path, msg, uid, proxy=None):
    """Stream-download with progress."""
    _proxy = proxy or PROXY_URL
    connector = aiohttp.TCPConnector(limit=4, ssl=False)
    timeout   = aiohttp.ClientTimeout(total=7200)
    start = time.time()
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as sess:
            async with sess.get(url, headers=_BUNKR_HEADERS, proxy=_proxy) as resp:
                if resp.status not in (200, 206):
                    return False, f"HTTP {resp.status}"
                total = int(resp.headers.get("content-length", 0))
                name  = os.path.basename(out_path)
                dl    = 0
                with open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(512 * 1024):
                        if msg.id in abort_dict:
                            return False, "CANCELLED"
                        f.write(chunk)
                        dl += len(chunk)
                        await update_progress_ui(dl, total, msg, start, "📥 Bunkr DL...", name, engine="BunkrScript")
        return True, None
    except Exception as e:
        return False, str(e)


@app.on_message(filters.command("bdl"))
async def bdl_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")

    args = m.text.split(None, 1)
    if len(args) < 2:
        return await m.reply_text("❌ Usage: /bdl bunkr.site/v/...")

    uid   = m.from_user.id
    urls  = [u.strip() for u in args[1].split("\n") if u.strip().startswith("http")]
    if not urls:
        urls = [args[1].strip()] if args[1].strip().startswith("http") else []
    if not urls:
        return await m.reply_text("❌ Invalid URL.")

    proxy = (await get_user_proxy(uid)) or PROXY_URL

    for i, url in enumerate(urls, 1):
        task_info = f"File {i}/{len(urls)}" if len(urls) > 1 else None
        msg = await m.reply_text(
            f"🔍 <b>Bunkr scrape...</b>\n<code>{clean_html(url[:80])}</code>"
        )

        direct = _bunkr_direct(url)

        if not direct:
            # Fallback: yt-dlp
            try:
                import yt_dlp as _ydl
                def _ydl_probe():
                    with _ydl.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                        info = ydl.extract_info(url, download=False)
                        return info.get("url") if info else None
                direct = await asyncio.to_thread(_ydl_probe)
            except Exception:
                pass

        if not direct:
            await msg.edit_text(f"❌ Could not get direct link for:\n<code>{clean_html(url)}</code>")
            continue

        fname   = os.path.basename(direct.split("?")[0]) or f"bunkr_{int(time.time())}.mp4"
        dl_dir  = f"downloads/bunkr_{int(time.time())}_{uid}"
        os.makedirs(dl_dir, exist_ok=True)
        out_path = os.path.join(dl_dir, fname)

        await msg.edit_text(
            f"⬇️ <b>Downloading...</b>\n<code>{clean_html(fname[:60])}</code>"
        )
        ok, err = await _download_stream(direct, out_path, msg, uid, proxy)

        if not ok:
            with suppress(Exception):
                await msg.edit_text(f"❌ <code>{clean_html(err)}</code>")
            shutil.rmtree(dl_dir, ignore_errors=True)
            continue

        active_dump = await get_active_dump(uid)
        target_chat = active_dump["id"] if active_dump else None

        await handle_upload_split(
            c, msg, out_path, "User",
            task_info=task_info,
            user_id=uid, target_chat=target_chat, start_time=time.time(),
        )
        shutil.rmtree(dl_dir, ignore_errors=True)
        dest = "dump" if target_chat else "PM"
        with suppress(Exception):
            await msg.edit_text(f"✅ <b>Done → {dest}!</b>")
