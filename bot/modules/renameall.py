"""
/renameall — bulk rename ALL files in the logged-in Mega account.
Flow: /renameall → scans account → keyboard for pattern → user sends text → preview → confirm.
/cancrename — cancel pending rename input.
"""

import asyncio
import posixpath
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.client import app
from bot.core.database.mongo import is_user_banned
from bot.helper.task_manager import (
    renameall_sessions, waiting_for_renameall_text, abort_dict
)
from bot.helper.time_format import clean_html

# ── Constants ──────────────────────────────────────────────────────────────────
CMD_TIMEOUT_RA  = 60      # per-file rename timeout (seconds)
MAX_FILES_RA    = 10000
_rename_executor = ThreadPoolExecutor(max_workers=10)

_RENAMEALL_OPTS = [
    ("prefix",  "✏️ Add Prefix"),
    ("suffix",  "✏️ Add Suffix"),
    ("replace", "🔁 Replace Text"),
    ("channel", "📣 Add Channel Name"),
]

_RENAMEALL_PROMPTS = {
    "prefix":  "📥 Send the text you want to add as <b>prefix</b>.",
    "suffix":  "📥 Send the text you want to add as <b>suffix</b> (before extension).",
    "replace": "📥 Send the replacement in <code>OldText|NewText</code> format.",
    "channel": "📥 Send the channel name (e.g. <code>@MyChannel</code>).",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _renameall_options_kb(msg_id: int) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for pat, label in _RENAMEALL_OPTS:
        row.append(InlineKeyboardButton(label, callback_data=f"ra_opt|{msg_id}|{pat}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"ra_cancel|{msg_id}")])
    return InlineKeyboardMarkup(rows)


def _run_cmd_ra(args, timeout=CMD_TIMEOUT_RA):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", f"Timed out after {timeout}s", 1
    except Exception as e:
        return "", str(e), 1


def _mega_find_all_files() -> list:
    out, err, code = _run_cmd_ra(["mega-find", "/", "--type=f"], timeout=180)
    if code != 0:
        raise Exception(err or out or "mega-find failed")
    files = [line.strip() for line in out.split("\n") if line.strip()]
    return files[:MAX_FILES_RA]


def _mega_get_all_file_nodes() -> list:
    try:
        from bot.extractors.mega_utils import mega_client as _mc
        if _mc is None:
            return []
        all_nodes = _mc.get_files()
        files = []
        for h, n in (all_nodes or {}).items():
            try:
                if n.get("t") == 0 and n.get("a") and n["a"].get("n"):
                    files.append((h, n))
            except Exception:
                continue
        files.sort(key=lambda kv: kv[1]["a"]["n"].lower())
        return files[:10000]
    except Exception as _e:
        print(f"[_mega_get_all_file_nodes] {_e}")
        return []


def _build_new_name(old_name: str, pattern: str, replacement: str, index: int) -> str:
    name, ext = posixpath.splitext(old_name)
    if pattern == "prefix":
        return f"{replacement}{old_name}"
    elif pattern == "suffix":
        return f"{name}{replacement}{ext}"
    elif pattern == "replace":
        parts = replacement.split("|", 1)
        if len(parts) == 2:
            return old_name.replace(parts[0], parts[1])
    elif pattern == "regex":
        parts = replacement.split("|", 1)
        if len(parts) == 2:
            try:
                return re.sub(parts[0], parts[1], old_name)
            except re.error:
                pass
    elif pattern == "number":
        return f"{str(index).zfill(5)}{ext}"
    elif pattern == "channel":
        ch = replacement.strip()
        if not ch.startswith("@"):
            ch = f"@{ch}"
        return f"{ch} ({index}){ext}"
    return old_name


def _rename_one_sync(file_obj, pattern: str, replacement: str, idx: int) -> bool:
    try:
        from bot.extractors.mega_utils import mega_client as _mc
    except Exception:
        _mc = None

    if isinstance(file_obj, tuple) and len(file_obj) == 2:
        node = file_obj[1]
        try:
            old_name = node["a"]["n"]
        except Exception:
            return False
        new_name = _build_new_name(old_name, pattern, replacement, idx)
        if new_name == old_name:
            return False
        if _mc is None:
            raise Exception("Mega client not available for tuple rename")
        _mc.rename(file_obj, new_name)
        return True

    file_path = file_obj
    old_name  = posixpath.basename(file_path)
    new_name  = _build_new_name(old_name, pattern, replacement, idx)
    if new_name == old_name:
        return False

    try:
        if _mc is not None:
            _mc.rename(file_path, new_name)
            return True
    except Exception:
        pass

    parent  = posixpath.dirname(file_path)
    new_path = posixpath.join(parent, new_name)
    _, _, code = _run_cmd_ra(["mega-mv", file_path, new_path])
    if code != 0:
        raise Exception(f"mega-mv failed for {old_name}")
    return True


def _basename_of(item):
    if isinstance(item, tuple):
        try:
            return item[1]["a"]["n"]
        except Exception:
            return ""
    return posixpath.basename(item)


# ── /renameall command ─────────────────────────────────────────────────────────

@app.on_message(filters.command("renameall"))
async def renameall_handler(c, m):
    if await is_user_banned(m.from_user.id):
        return await m.reply_text("❌ You are banned.")

    msg = await m.reply_text("🔍 <b>Scanning entire Mega account...</b>")

    files = []
    try:
        files = await asyncio.wait_for(
            asyncio.to_thread(_mega_get_all_file_nodes),
            timeout=180
        )
    except asyncio.TimeoutError:
        files = []
    except Exception as _e:
        print(f"[renameall_handler] tuple scan failed: {_e}")
        files = []

    if not files:
        try:
            files = await asyncio.wait_for(
                asyncio.to_thread(_mega_find_all_files),
                timeout=300
            )
        except asyncio.TimeoutError:
            return await msg.edit_text("❌ Account scan timeout. Please try again.")
        except Exception as e:
            return await msg.edit_text(f"❌ <b>Account scan failed:</b>\n<code>{clean_html(str(e))}</code>")

    total = len(files)
    if total == 0:
        return await msg.edit_text("📂 Mega account mein koi file nahi mili. Pehle /login karein.")

    renameall_sessions[m.id] = {
        "files":       files,
        "total":       total,
        "uid":         m.from_user.id,
        "pattern":     None,
        "replacement": None,
    }

    sample_name = _basename_of(files[0])
    sample = clean_html(sample_name[:60])
    await msg.edit_text(
        f"📂 <b>Files found: {total:,}</b> (entire account)\n\n"
        f"<b>Sample:</b> <code>{sample}</code>\n\n"
        f"<b>Choose a rename option:</b>",
        reply_markup=_renameall_options_kb(m.id)
    )


# ── ra_opt| callback — pattern selected ───────────────────────────────────────

@app.on_callback_query(filters.regex(r"^ra_opt\|"))
async def ra_option_cb(c, cb):
    parts   = cb.data.split("|")
    msg_id  = int(parts[1])
    pattern = parts[2]

    session = renameall_sessions.get(msg_id)
    if not session:
        return await cb.answer("❌ Session expired. Run /renameall again.", show_alert=True)
    if cb.from_user.id != session["uid"]:
        return await cb.answer("❌ Not your session!", show_alert=True)

    session["pattern"] = pattern
    waiting_for_renameall_text[cb.from_user.id] = msg_id
    await cb.answer()
    prompt = _RENAMEALL_PROMPTS.get(pattern, "Send the value:")
    with suppress(Exception):
        await cb.message.edit_text(
            f"{prompt}\n\nSend /cancrename to cancel."
        )


# ── /cancrename — cancel pending input ────────────────────────────────────────

@app.on_message(filters.command("cancrename") & filters.private)
async def cancrename_cmd(c, m):
    uid    = m.from_user.id
    msg_id = waiting_for_renameall_text.pop(uid, None)
    if msg_id is not None:
        renameall_sessions.pop(msg_id, None)
        await m.reply_text("❌ Rename input cancelled.")
    else:
        await m.reply_text("Nothing to cancel.")


# ── ra_cancel| callback ────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^ra_cancel\|"))
async def ra_cancel_cb(c, cb):
    msg_id = int(cb.data.split("|")[1])
    session = renameall_sessions.get(msg_id)
    if session and cb.from_user.id != session["uid"]:
        return await cb.answer("❌ Not yours!", show_alert=True)
    renameall_sessions.pop(msg_id, None)
    await cb.answer("❌ Cancelled")
    with suppress(Exception):
        await cb.message.delete()


# ── ra_confirm| callback — start the actual rename ────────────────────────────

@app.on_callback_query(filters.regex(r"^ra_confirm\|"))
async def ra_confirm_cb(c, cb):
    msg_id  = int(cb.data.split("|")[1])
    session = renameall_sessions.pop(msg_id, None)
    if not session:
        return await cb.answer("❌ Session expired. Run /renameall again.", show_alert=True)
    await cb.answer()

    files       = session["files"]
    pattern     = session["pattern"]
    replacement = session["replacement"]
    total       = session["total"]
    status_msg  = cb.message

    await status_msg.edit_text(
        f"🔄 <b>Renaming...</b>\n\n"
        f"<code>[░░░░░░░░░░░░░░░░░░░░]</code> 0%\n\n"
        f"📊 Total:   <code>{total:,}</code>\n"
        f"✅ Done:    <code>0</code>\n"
        f"❌ Failed:  <code>0</code>\n"
        f"⏱ ETA:     <code>calculating...</code>"
    )

    done    = 0
    failed  = 0
    loop    = asyncio.get_running_loop()
    sem     = asyncio.Semaphore(10)
    start_t = time.time()

    async def rename_one(fp, idx):
        nonlocal done, failed
        async with sem:
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(_rename_executor, _rename_one_sync, fp, pattern, replacement, idx),
                    timeout=CMD_TIMEOUT_RA
                )
                if result:
                    done += 1
            except Exception as _e:
                failed += 1
                print(f"[renameall] [{idx}] {_e}")

    async def progress_ticker():
        while True:
            await asyncio.sleep(5)
            processed = done + failed
            elapsed   = max(time.time() - start_t, 0.1)
            speed     = processed / elapsed
            pct       = int(processed / total * 100) if total else 0
            filled    = pct // 5
            bar       = "▓" * filled + "░" * (20 - filled)
            eta       = int((total - processed) / speed) if speed > 0 and processed < total else 0
            eta_str   = f"{eta}s" if eta > 0 else "calculating..."
            with suppress(Exception):
                await status_msg.edit_text(
                    f"🔄 <b>Renaming...</b>\n\n"
                    f"<code>[{bar}]</code> {pct}%\n\n"
                    f"📊 Total:   <code>{total:,}</code>\n"
                    f"✅ Done:    <code>{done:,}</code>\n"
                    f"❌ Failed:  <code>{failed:,}</code>\n"
                    f"⚡ Speed:   <code>{speed:.1f} files/s</code>\n"
                    f"⏱ ETA:     <code>{eta_str}</code>"
                )

    ticker   = asyncio.create_task(progress_ticker())
    all_jobs = [rename_one(fp, idx + 1) for idx, fp in enumerate(files)]
    await asyncio.gather(*all_jobs)
    ticker.cancel()

    elapsed  = max(time.time() - start_t, 0.1)
    avg_spd  = (done + failed) / elapsed
    skipped  = total - done - failed

    try:
        from bot.extractors.mega_utils import mega_client as _mc
        engine_used = "Mega API (fast)" if _mc is not None else "MegaCMD (stable)"
    except Exception:
        engine_used = "MegaCMD (stable)"

    with suppress(Exception):
        await status_msg.edit_text(
            f"🎉 <b>Rename Complete!</b>\n\n"
            f"<code>[{'▓' * 20}]</code> 100%\n\n"
            f"📊 Total:    <code>{total:,}</code>\n"
            f"✅ Renamed:  <code>{done:,}</code>\n"
            f"❌ Failed:   <code>{failed:,}</code>\n"
            f"⏭️ Skipped:  <code>{skipped:,}</code>\n\n"
            f"⚡ Avg Speed: <code>{avg_spd:.1f} files/s</code>\n"
            f"🕐 Time:     <code>{int(elapsed)}s</code>\n"
            f"🔧 Engine:   <code>{engine_used}</code>"
        )
