"""
/scriptdl — bypass downloader for HentaiCity, PornHub, WatchHentai, XHamster, Dailymotion.
/scriptdl /status — show failed links for the user.
Phub profile -s flag → phub_select_sessions → phsel| callback → batch download.
"""

import asyncio
import os
import secrets
import shutil
import time
from contextlib import suppress

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.config import PROXY_URL
from bot.core.database.mongo import is_user_banned, get_active_dump, get_user_proxy
from bot.helper.task_manager import abort_dict, failed_scriptdl, phub_select_sessions
from bot.helper.time_format import clean_html, humanbytes
from bot.helper.progress import update_progress_ui
from bot.helper.uploader import handle_upload_split
from bot.helper.download_utils import _blocking_download

PHUB_SELECT_PAGE_SIZE = 8


# ── Site detection ─────────────────────────────────────────────────────────────

def _detect_site(url: str) -> str:
    u = url.lower()
    if "hentaicity" in u:
        return "hc"
    if "pornhub.com" in u:
        return "phub"
    if "watchhentai" in u:
        return "wh"
    if "xhamster" in u:
        return "xh"
    if "dailymotion" in u or "dmcdn" in u:
        return "dm"
    return "unknown"


def _is_phub_profile(url: str) -> bool:
    u = url.lower()
    return "pornhub.com" in u and (
        "/model/" in u or "/channels/" in u or "/pornstar/" in u
        or "/users/" in u or "/playlist/" in u
    )


# ── Format fetchers ────────────────────────────────────────────────────────────

async def _get_formats(url: str, site: str, proxy=None):
    try:
        if site == "hc":
            from bot.extractors.hc import get_hc_data
            data = await asyncio.to_thread(get_hc_data, url, proxy)
            if not data:
                return None, "No formats found."
            return data["formats"], None

        elif site == "phub":
            from bot.extractors.phub import get_direct_info
            data = await asyncio.to_thread(get_direct_info, url, proxy)
            if not data:
                return None, "PHub extraction failed."
            return data.get("formats", []), None

        elif site == "wh":
            from bot.extractors.wh import get_wh_data
            data = await asyncio.to_thread(get_wh_data, url, proxy)
            if not data:
                return None, "WH extraction failed."
            return data.get("formats", []), None

        elif site == "xh":
            from bot.extractors.xh import get_xh_data
            data = await asyncio.to_thread(get_xh_data, url, proxy)
            if not data:
                return None, "XH extraction failed."
            return data.get("formats", []), None

        elif site == "dm":
            from bot.extractors.dm import get_dm_data
            data = await asyncio.to_thread(get_dm_data, url, proxy)
            if not data:
                return None, "DM extraction failed."
            return data.get("formats", []), None

        return None, "Unsupported site."
    except Exception as e:
        return None, str(e)


# ── Download + upload single video ─────────────────────────────────────────────

def _blocking_ytdlp(url, opts):
    import yt_dlp
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            return None
        fp = ydl.prepare_filename(info)
        if not os.path.exists(fp):
            mp4 = os.path.splitext(fp)[0] + ".mp4"
            if os.path.exists(mp4):
                return mp4
        return fp if os.path.exists(fp) else None


async def _download_and_upload(c, msg, url, fmt, uid, title="video"):
    dl_dir   = f"downloads/sdl_{int(time.time())}_{uid}"
    os.makedirs(dl_dir, exist_ok=True)
    out_tmpl = os.path.join(dl_dir, f"{title[:60]}.%(ext)s")
    loop     = asyncio.get_running_loop()
    start    = time.time()

    def _hook(d):
        if d["status"] != "downloading":
            return
        total   = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        current = d.get("downloaded_bytes") or 0
        asyncio.run_coroutine_threadsafe(
            update_progress_ui(current, total, msg, start, "📥 ScriptDL...",
                               title[:50], engine="ScriptDL"),
            loop,
        )

    opts = {
        "format":              fmt or "best",
        "outtmpl":             out_tmpl,
        "progress_hooks":      [_hook],
        "merge_output_format": "mp4",
    }
    proxy = (await get_user_proxy(uid)) or PROXY_URL
    if proxy:
        opts["proxy"] = proxy

    try:
        fp = await asyncio.to_thread(_blocking_ytdlp, url, opts)
        if not fp:
            return False, "Download failed."
        active_dump = await get_active_dump(uid)
        target_chat = active_dump["id"] if active_dump else None
        await handle_upload_split(
            c, msg, fp, "User",
            user_id=uid, target_chat=target_chat, start_time=start,
        )
        return True, None
    except Exception as e:
        return False, str(e)
    finally:
        shutil.rmtree(dl_dir, ignore_errors=True)


# ── PHub batch helpers ─────────────────────────────────────────────────────────

def _phub_progress_text(done, failed, total, label="PHub Batch"):
    processed = done + failed
    pct    = int(processed / total * 100) if total else 0
    filled = pct // 5
    bar    = "▓" * filled + "░" * (20 - filled)
    return (
        f"⏳ <b>{label} Progress:</b> {done}/{total}  |  ❌ <b>Failed:</b> {failed}\n"
        f"<code>[{bar}]</code> {pct}%"
    )


def _phub_final_text(done, failed, total, errors, label="PHub Batch"):
    head = (
        f"🎉 <b>{label} Complete!</b>\n\n"
        f"✅ <b>Done:</b> {done}\n"
        f"❌ <b>Failed:</b> {failed}\n"
        f"📦 <b>Total:</b> {total}\n"
    )
    if errors:
        body = "\n".join(
            f"• <code>{clean_html(e[:140])}</code>" for e in errors[:25]
        )
        head += f"\n<b>Failure reasons:</b>\n{body}"
        if len(errors) > 25:
            head += f"\n…and <b>{len(errors) - 25}</b> more"
    return head


def _batch_cancel_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🛑 Cancel Batch", callback_data=f"cancel_scriptdl|{uid}"),
    ]])


async def _safe_status_edit(status_msg, text, reply_markup=None):
    with suppress(Exception):
        await status_msg.edit_text(text, reply_markup=reply_markup)


# ── PHub selective pagination UI ───────────────────────────────────────────────

def _phub_select_render(msg_id: int):
    s = phub_select_sessions.get(msg_id)
    if not s:
        return "❌ Session expired.", None
    videos   = s["videos"]
    selected = s["selected"]
    page     = s["page"]
    page_sz  = PHUB_SELECT_PAGE_SIZE
    total    = len(videos)
    pages    = max(1, (total + page_sz - 1) // page_sz)
    page     = max(0, min(page, pages - 1))
    s["page"] = page

    start = page * page_sz
    end   = min(start + page_sz, total)

    rows = []
    for i in range(start, end):
        v     = videos[i]
        mark  = "✅" if i in selected else "❌"
        title = (v.get("title") or "Video")[:48]
        rows.append([InlineKeyboardButton(
            f"{mark} {title}", callback_data=f"phsel|{msg_id}|t|{i}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"phsel|{msg_id}|p|{page-1}"))
    nav.append(InlineKeyboardButton(f"📄 {page+1}/{pages}", callback_data=f"phsel|{msg_id}|noop|0"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"phsel|{msg_id}|p|{page+1}"))
    rows.append(nav)

    rows.append([
        InlineKeyboardButton("☑️ All",   callback_data=f"phsel|{msg_id}|all|0"),
        InlineKeyboardButton("🧹 Clear", callback_data=f"phsel|{msg_id}|clr|0"),
    ])
    rows.append([
        InlineKeyboardButton(
            f"🚀 Start Selected ({len(selected)})",
            callback_data=f"phsel|{msg_id}|go|0"
        ),
        InlineKeyboardButton("❌ Cancel", callback_data=f"phsel|{msg_id}|cancel|0"),
    ])

    text = (
        f"🎬 <b>PHub Selective Download</b>\n"
        f"📦 <b>Total:</b> {total}  •  ✅ <b>Selected:</b> {len(selected)}\n"
        f"📄 <b>Page {page+1}/{pages}</b>\n\n"
        f"Tap a video to toggle selection."
    )
    return text, InlineKeyboardMarkup(rows)


async def _phub_start_selected(c, anchor_msg, msg_id: int):
    s = phub_select_sessions.pop(msg_id, None)
    if not s:
        return
    videos     = s["videos"]
    selected   = sorted(s["selected"])
    uid        = s["uid"]
    chat_id    = s["chat_id"]
    _user_proxy = (await get_user_proxy(uid)) or PROXY_URL

    chosen  = [videos[i] for i in selected]
    total_v = len(chosen)
    if total_v == 0:
        return

    try:
        status_msg = await c.send_message(
            chat_id, _phub_progress_text(0, 0, total_v, label="PHub Selected")
        )
    except Exception:
        status_msg = anchor_msg
    with suppress(Exception):
        await anchor_msg.delete()

    counters   = {"done": 0, "failed": 0}
    error_logs = []

    async def _silent_one(vid_url, fmt_id):
        os.makedirs("downloads", exist_ok=True)
        dl_dir   = os.path.join("downloads", secrets.token_hex(4))
        os.makedirs(dl_dir, exist_ok=True)
        out_tmpl = os.path.join(dl_dir, "%(title).100s.%(ext)s")
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_blocking_download, vid_url, fmt_id, out_tmpl, None, False, _user_proxy),
                timeout=600,
            )
        except (asyncio.TimeoutError, TimeoutError):
            shutil.rmtree(dl_dir, ignore_errors=True)
            return f"Timeout: {vid_url}"
        except Exception as _de:
            shutil.rmtree(dl_dir, ignore_errors=True)
            return f"Download error: {_de} ({vid_url})"

        if not result:
            shutil.rmtree(dl_dir, ignore_errors=True)
            return f"Extraction Failed: {vid_url}"

        fp = result["filepath"]
        try:
            scratch = await c.send_message(chat_id, "📤 …")
        except Exception:
            scratch = status_msg
        try:
            active_dump = await get_active_dump(uid)
            target_chat = active_dump["id"] if active_dump else None
            ok = await handle_upload_split(
                c, scratch, fp, "User",
                user_id=uid, target_chat=target_chat,
                start_time=time.time(),
            )
            if ok is False:
                return f"Upload Failed: {os.path.basename(fp)}"
        finally:
            if scratch is not status_msg:
                with suppress(Exception):
                    await scratch.delete()
            shutil.rmtree(dl_dir, ignore_errors=True)
        return None

    async def _track(vid):
        if abort_dict.get(uid):
            return
        fmt_id = "best"
        try:
            from bot.extractors.phub import get_direct_info
            v_data = await asyncio.to_thread(get_direct_info, vid.get("url", ""), _user_proxy)
            if v_data and v_data.get("formats"):
                fmts = sorted(v_data["formats"], key=lambda x: x.get("height", 0), reverse=True)
                if fmts:
                    fmt_id = fmts[0].get("format_id", "best")
        except Exception:
            pass

        err = await _silent_one(vid.get("url", ""), fmt_id)
        if err is None:
            counters["done"] += 1
        else:
            counters["failed"] += 1
            error_logs.append(err)
        await _safe_status_edit(
            status_msg,
            _phub_progress_text(counters["done"], counters["failed"], total_v, label="PHub Selected"),
            reply_markup=_batch_cancel_kb(uid),
        )

    await asyncio.gather(*[_track(v) for v in chosen])
    abort_dict.pop(uid, None)

    with suppress(Exception):
        await status_msg.edit_text(
            _phub_final_text(counters["done"], counters["failed"], total_v, error_logs, label="PHub Selected"),
            reply_markup=None,
        )


# ── /scriptdl command ──────────────────────────────────────────────────────────

@app.on_message(filters.command("scriptdl"))
async def scriptdl_cmd(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")

    args_raw = m.text.split(None, 1)
    if len(args_raw) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b> <code>/scriptdl URL</code>\n\n"
            "Supports: HentaiCity, PornHub, WatchHentai, XHamster, Dailymotion\n"
            "PHub profile: <code>/scriptdl -s URL</code> — selective batch\n\n"
            "Use <code>/scriptdl /status</code> to see failed links."
        )

    arg = args_raw[1].strip()

    # Show failed links status
    if arg in ("/status", "status"):
        uid    = m.from_user.id
        failed = failed_scriptdl.get(uid, [])
        if not failed:
            return await m.reply_text("✅ <b>No failed scriptdl links!</b>")
        text = f"❌ <b>Failed ScriptDL links ({len(failed)}):</b>\n\n"
        for i, link in enumerate(failed[-20:], 1):
            text += f"{i}. <code>{clean_html(link)}</code>\n"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Clear Failed", callback_data=f"sdl_clearfail_{uid}")
        ]])
        return await m.reply_text(text, reply_markup=kb)

    uid   = m.from_user.id
    proxy = (await get_user_proxy(uid)) or PROXY_URL

    # PHub profile selective mode: /scriptdl -s URL
    selective = False
    if arg.startswith("-s "):
        selective = True
        arg = arg[3:].strip()

    # Batch URLs (one per line)
    lines = [l.strip() for l in arg.split("\n") if l.strip().startswith("http")]
    if not lines:
        lines = [arg] if arg.startswith("http") else []
    if not lines:
        return await m.reply_text("❌ No valid URL given.")

    for url in lines:
        site = _detect_site(url)

        # PHub profile selective batch
        if site == "phub" and (selective or _is_phub_profile(url)):
            msg = await m.reply_text(f"🔍 <b>Scraping PHub profile...</b>\n<code>{clean_html(url[:80])}</code>")
            try:
                from bot.extractors.phub import scrape_phub_profile_videos
                videos = await asyncio.wait_for(
                    asyncio.to_thread(scrape_phub_profile_videos, url, proxy),
                    timeout=60,
                )
            except Exception as e:
                failed_scriptdl.setdefault(uid, []).append(url)
                await msg.edit_text(f"❌ Scrape failed: <code>{clean_html(str(e))}</code>")
                continue

            if not videos:
                await msg.edit_text("❌ No videos found on that profile.")
                continue

            phub_select_sessions[msg.id] = {
                "videos":   videos,
                "selected": set(),
                "page":     0,
                "uid":      uid,
                "chat_id":  m.chat.id,
            }
            text, kb = _phub_select_render(msg.id)
            await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
            continue

        # Single video quality picker
        msg = await m.reply_text(f"🔍 <b>Fetching formats...</b>\n<code>{clean_html(url[:80])}</code>")
        formats, err = await _get_formats(url, site, proxy)

        if err or not formats:
            failed_scriptdl.setdefault(uid, []).append(url)
            with suppress(Exception):
                await msg.edit_text(
                    f"❌ <b>Extraction failed:</b> <code>{clean_html(err or 'No formats')}</code>\n"
                    f"Added to failed list. Use <code>/scriptdl /status</code> to view."
                )
            continue

        rows = []
        for fmt in sorted(formats, key=lambda x: x.get("height", 0), reverse=True)[:8]:
            h   = fmt.get("height", 0)
            fid = fmt.get("format_id", "best")
            label = f"{'🎬' if h >= 720 else '📺'} {h}p" if h else f"📺 {fid}"
            rows.append([InlineKeyboardButton(label, callback_data=f"sdl|{msg.id}|{fid}|{uid}")])
        rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"sdl|{msg.id}|cancel|{uid}")])

        from bot.helper.task_manager import ytdl_session
        ytdl_session[msg.id] = {
            "url":   url,
            "uid":   uid,
            "title": f"video_{int(time.time())}",
        }

        await msg.edit_text(
            f"🎬 <b>Select Quality</b>\n<code>{clean_html(url[:70])}</code>",
            reply_markup=InlineKeyboardMarkup(rows),
        )


# ── sdl| callback — quality picked ────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^sdl\|"))
async def sdl_cb(c, cb):
    parts  = cb.data.split("|")
    msg_id = int(parts[1])
    fmt_id = parts[2]
    uid    = int(parts[3])

    if cb.from_user.id != uid:
        return await cb.answer("❌ Not yours!", show_alert=True)

    if fmt_id == "cancel":
        from bot.helper.task_manager import ytdl_session
        ytdl_session.pop(msg_id, None)
        await cb.answer("❌ Cancelled")
        with suppress(Exception):
            await cb.message.delete()
        return

    from bot.helper.task_manager import ytdl_session
    sess = ytdl_session.pop(msg_id, None)
    if not sess:
        return await cb.answer("Session expired.", show_alert=True)

    await cb.answer("⬇️ Starting download...")
    await cb.message.edit_text(
        f"⬇️ <b>Downloading {fmt_id}...</b>\n<code>{clean_html(sess['url'][:70])}</code>"
    )

    ok, err = await _download_and_upload(c, cb.message, sess["url"], fmt_id, uid, sess.get("title", "video"))
    if not ok:
        failed_scriptdl.setdefault(uid, []).append(sess["url"])
        with suppress(Exception):
            await cb.message.edit_text(
                f"❌ <b>Download failed:</b> <code>{clean_html(err or 'Unknown')}</code>\n"
                f"Added to failed list. Use <code>/scriptdl /status</code> to view."
            )
    else:
        with suppress(Exception):
            await cb.message.edit_text("✅ <b>Upload complete!</b>")


# ── phsel| callback — phub selective mode ─────────────────────────────────────

@app.on_callback_query(filters.regex(r"^phsel\|"))
async def phub_select_cb(c, cb):
    parts  = cb.data.split("|")
    msg_id = int(parts[1])
    action = parts[2]
    arg    = parts[3] if len(parts) > 3 else "0"

    s = phub_select_sessions.get(msg_id)
    if not s:
        return await cb.answer("❌ Session expired.", show_alert=True)
    if cb.from_user.id != s["uid"]:
        return await cb.answer("❌ Not your session!", show_alert=True)

    if action == "noop":
        return await cb.answer()

    if action == "cancel":
        phub_select_sessions.pop(msg_id, None)
        await cb.answer("❌ Cancelled")
        with suppress(Exception):
            await cb.message.delete()
        return

    if action == "t":
        idx = int(arg)
        if idx in s["selected"]:
            s["selected"].discard(idx)
        else:
            s["selected"].add(idx)
        await cb.answer()

    elif action == "p":
        s["page"] = int(arg)
        await cb.answer()

    elif action == "all":
        s["selected"] = set(range(len(s["videos"])))
        await cb.answer("All selected")

    elif action == "clr":
        s["selected"].clear()
        await cb.answer("Cleared")

    elif action == "go":
        if not s["selected"]:
            return await cb.answer("Pick at least one video first.", show_alert=True)
        await cb.answer()
        await _phub_start_selected(c, cb.message, msg_id)
        return

    text, kb = _phub_select_render(msg_id)
    with suppress(Exception):
        await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)


# ── cancel_scriptdl| callback — abort running batch ───────────────────────────

@app.on_callback_query(filters.regex(r"^cancel_scriptdl\|"))
async def cancel_scriptdl_cb(c, cb):
    try:
        target_uid = int(cb.data.split("|")[1])
    except (IndexError, ValueError):
        return await cb.answer("❌ Bad data", show_alert=True)
    if cb.from_user.id != target_uid:
        return await cb.answer("❌ Not your task!", show_alert=True)
    abort_dict[target_uid] = True
    await cb.answer("🛑 Batch cancel requested — finishing current video then stopping.", show_alert=True)
    with suppress(Exception):
        await cb.message.edit_reply_markup(reply_markup=None)


# ── Clear failed links ─────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^sdl_clearfail_"))
async def sdl_clearfail_cb(c, cb):
    uid = int(cb.data.split("_")[-1])
    if cb.from_user.id != uid:
        return await cb.answer("❌ Not yours!", show_alert=True)
    failed_scriptdl.pop(uid, None)
    await cb.answer("🗑 Cleared!")
    with suppress(Exception):
        await cb.message.edit_text("✅ <b>Failed links cleared.</b>")
