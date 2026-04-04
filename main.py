import os
import time
import asyncio
import aiohttp
import aiofiles
import yt_dlp
import aria2p
import subprocess
import shutil
import traceback
import re
import urllib.parse
import mimetypes
import secrets
import sys
import json
from math import floor
from base64 import b64decode
from bs4 import BeautifulSoup
from urllib.parse import urlparse, unquote

bot_start_time = time.time()

def get_readable_time(seconds: int) -> str:
    count = 0
    ping_time = ""
    time_list = []
    time_suffix_list = ["s", "m", "h", "days"]
    while count < 4:
        count += 1
        remainder, result = divmod(seconds, 60) if count < 3 else divmod(seconds, 24)
        if seconds == 0 and remainder == 0: break
        time_list.append(int(result))
        seconds = int(remainder)
    for x in range(len(time_list)):
        time_list[x] = str(time_list[x]) + time_suffix_list[x]
    if len(time_list) == 4: ping_time += time_list.pop() + ", "
    time_list.reverse()
    ping_time += ":".join(time_list)
    return ping_time

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")
RCLONE_PATH = os.environ.get("RCLONE_PATH", "remote:")
PORT = int(os.environ.get("PORT", 8080))
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

if not MONGO_URL:
    print("Error: MONGO_URL is missing!")
    exit(1)

# ✅ SPEED OPTIMIZATION: workers aur connections badhaaye
app = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML,
    workers=16,               # Default 4 tha, ab 16
    max_concurrent_transmissions=5  # Parallel uploads ke liye
)

mongo_client, db, users_col = None, None, None

async def init_db():
    global mongo_client, db, users_col
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URL)
        db = mongo_client["URL_Uploader_Bot"]
        users_col = db["users"]
        print("MongoDB Connected!")
    except Exception as e:
        print(f"MongoDB Failed: {e}")

async def add_dump(user_id, chat_id, chat_title):
    user = await users_col.find_one({"_id": user_id})
    new_dump = {"id": chat_id, "title": chat_title}
    if not user: await users_col.insert_one({"_id": user_id, "dumps": [new_dump], "active_dump": chat_id})
    else:
        dumps = user.get("dumps", [])
        if not any(d["id"] == chat_id for d in dumps):
            dumps.append(new_dump)
            await users_col.update_one({"_id": user_id}, {"$set": {"dumps": dumps}})
            if not user.get("active_dump"): await users_col.update_one({"_id": user_id}, {"$set": {"active_dump": chat_id}})

async def get_user_dumps(user_id):
    user = await users_col.find_one({"_id": user_id})
    return user.get("dumps", []) if user else []

async def set_active_dump(user_id, chat_id):
    await users_col.update_one({"_id": user_id}, {"$set": {"active_dump": chat_id}})

async def get_active_dump(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user: return None
    active_id = user.get("active_dump")
    dumps = user.get("dumps", [])
    for d in dumps:
        if d["id"] == active_id: return d
    if dumps:
        await set_active_dump(user_id, dumps[0]["id"])
        return dumps[0]
    return None

async def delete_dump(user_id, chat_id):
    user = await users_col.find_one({"_id": user_id})
    if not user: return
    dumps = user.get("dumps", [])
    new_dumps = [d for d in dumps if d["id"] != chat_id]
    update = {"dumps": new_dumps}
    if user.get("active_dump") == chat_id: update["active_dump"] = new_dumps[0]["id"] if new_dumps else None
    await users_col.update_one({"_id": user_id}, {"$set": update})

abort_dict = {}
user_queues = {}
is_processing = {}
progress_status = {}
ytdl_session = {}
aria2 = None
pending_selections = {}

def humanbytes(size):
    if not size: return "0B"
    for unit in ['B','KB','MB','GB','TB']:
        if size < 1024: return f"{round(size, 2)} {unit}"
        size /= 1024
    return f"{round(size, 2)} PB"

def time_formatter(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return "{:02d}:{:02d}:{:02d}".format(int(hours), int(minutes), int(seconds))

def clean_html(text): return str(text).replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('(\d+)', s)]

async def take_screenshot(video_path):
    try:
        thumb_path = f"{video_path}.jpg"
        cmd = ["ffmpeg", "-ss", "00:00:01", "-i", video_path, "-vframes", "1", "-q:v", "2", thumb_path, "-y"]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await process.wait()
        if os.path.exists(thumb_path): return thumb_path
    except: pass
    return None

async def get_video_duration(video_path):
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        return int(float(stdout.decode().strip()))
    except:
        return 0

# ==========================================
#   ✅ NEW: WVM → MP4 CONVERTER
# ==========================================
async def convert_wvm_to_mp4(file_path, message):
    """
    .wvm (Widevine encrypted / proprietary format) ko ffmpeg se MP4 mein convert karta hai.
    Agar ffmpeg directly convert kar sake to karta hai, warna as-is return karta hai.
    """
    if not file_path.lower().endswith('.wvm'):
        return file_path, False

    output_path = os.path.splitext(file_path)[0] + "_converted.mp4"
    try:
        await message.edit_text(f"🔄 <b>Converting .wvm → .mp4...</b>\n<code>{clean_html(os.path.basename(file_path))}</code>")
        cmd = [
            "ffmpeg", "-y",
            "-i", file_path,
            "-c:v", "copy",      # Video stream copy (re-encode nahi, fast hai)
            "-c:a", "copy",      # Audio stream copy
            output_path
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            os.remove(file_path)
            return output_path, True
        else:
            # ✅ Fallback: agar copy kaam na kare, re-encode try karo
            await message.edit_text(f"🔄 <b>Re-encoding .wvm → .mp4 (fallback)...</b>")
            cmd2 = [
                "ffmpeg", "-y",
                "-i", file_path,
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "aac", "-b:a", "128k",
                output_path
            ]
            process2 = await asyncio.create_subprocess_exec(
                *cmd2,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process2.communicate()
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                os.remove(file_path)
                return output_path, True

    except Exception as e:
        print(f"WVM conversion error: {e}")

    return file_path, False  # Conversion fail - original return

# ==========================================
#   ✅ NEW: BUNKR ALBUM DOWNLOADER
# ==========================================
BUNKR_VS_API_URL = "https://bunkr.cr/api/vs"
SECRET_KEY_BASE = "SECRET_KEY_"

BUNKR_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
    'Referer': 'https://bunkr.sk/',
}

def bunkr_remove_illegal_chars(string):
    return re.sub(r'[<>:"/\\|?*\']|[\0-\31]', "-", string).strip()

async def bunkr_get_encryption_data(session, slug):
    try:
        async with session.post(BUNKR_VS_API_URL, json={'slug': slug}, headers=BUNKR_HEADERS) as r:
            if r.status != 200:
                return None
            return await r.json()
    except Exception as e:
        print(f"Bunkr encryption data error: {e}")
        return None

def bunkr_decrypt_url(encryption_data):
    try:
        secret_key = f"{SECRET_KEY_BASE}{floor(encryption_data['timestamp'] / 3600)}"
        encrypted_bytes = list(b64decode(encryption_data['url']))
        key_bytes = list(secret_key.encode('utf-8'))
        return "".join(chr(encrypted_bytes[i] ^ key_bytes[i % len(key_bytes)]) for i in range(len(encrypted_bytes)))
    except Exception as e:
        print(f"Bunkr decrypt error: {e}")
        return None

async def bunkr_get_real_url(session, url, item_name=None):
    """Single file ka real download URL nikalta hai."""
    try:
        full_url = url if 'https' in url else f'https://bunkr.sk{url}'
        async with session.get(full_url, headers=BUNKR_HEADERS) as r:
            if r.status != 200:
                return None
            slug_match = re.search(r'\/f\/(.*?)$', full_url)
            if not slug_match:
                return None
            slug = unquote(slug_match.group(1))
        
        enc_data = await bunkr_get_encryption_data(session, slug)
        if not enc_data:
            return None
        
        real_url = bunkr_decrypt_url(enc_data)
        if not real_url:
            return None
        
        return {'url': real_url, 'name': item_name or os.path.basename(real_url)}
    except Exception as e:
        print(f"Bunkr get_real_url error: {e}")
        return None

async def bunkr_get_album_items(session, url):
    """
    Bunkr album/folder ke saare items fetch karta hai.
    Returns: list of {'url': real_download_url, 'name': filename}
    """
    items_result = []
    try:
        async with session.get(url, headers=BUNKR_HEADERS) as r:
            if r.status != 200:
                return [], f"HTTP {r.status}"
            html = await r.text()

        soup = BeautifulSoup(html, 'html.parser')
        title_tag = soup.find('title')
        if not title_tag or "| Bunkr" not in title_tag.text:
            return [], "Not a Bunkr page"

        # ✅ Single file check
        is_single = soup.find('span', {'class': 'ic-videos'}) is not None or \
                    soup.find('div', {'class': 'lightgallery'}) is not None

        if is_single:
            # Single file URL hai
            item = await bunkr_get_real_url(session, url)
            if item:
                items_result.append(item)
        else:
            # Album hai - saare items fetch karo
            album_name_tag = soup.find('h1', {'class': 'truncate'})
            album_name = bunkr_remove_illegal_chars(album_name_tag.text) if album_name_tag else "BunkrAlbum"

            the_items = soup.find_all('div', {'class': 'theItem'})
            for the_item in the_items:
                box = the_item.find('a', {'class': 'after:absolute'})
                name_tag = the_item.find('p')
                if not box:
                    continue
                item_url = box['href']
                item_name = name_tag.text.strip() if name_tag else None
                real_item = await bunkr_get_real_url(session, item_url, item_name)
                if real_item:
                    items_result.append(real_item)

            # ✅ Pagination support
            pagination = soup.find('nav', {'class': 'pagination'})
            if pagination:
                try:
                    current_page = int(pagination.find('span', {'class': 'active'}).text)
                    page_links = [a for a in pagination.find_all('a') if a.text.strip().isdigit()]
                    last_page = int(page_links[-1].text) if page_links else current_page

                    for page_num in range(current_page + 1, last_page + 1):
                        if re.search(r'([?&])page=\d+', url):
                            next_url = re.sub(r'([?&])page=\d+', f'\\1page={page_num}', url)
                        else:
                            next_url = f"{url}{'&' if '?' in url else '?'}page={page_num}"
                        
                        next_items, _ = await bunkr_get_album_items(session, next_url)
                        items_result.extend(next_items)
                except Exception as e:
                    print(f"Bunkr pagination error: {e}")

        return items_result, None

    except Exception as e:
        return [], str(e)

async def bunkr_download_file(session, item, download_dir, message, index, total, overall_start):
    """Ek bunkr file download karta hai aiohttp se, progress ke saath."""
    real_url = item['url']
    file_name = item.get('name') or os.path.basename(urlparse(real_url).path)
    file_name = unquote(file_name)
    if not file_name or '.' not in file_name:
        file_name = f"bunkr_file_{index}.mp4"

    file_path = os.path.join(download_dir, file_name)

    try:
        async with session.get(real_url, headers=BUNKR_HEADERS) as r:
            if r.status != 200:
                print(f"Bunkr download error {r.status} for {real_url}")
                return None

            if r.url.path == "/maintenance.mp4":
                print(f"Bunkr maintenance for {file_name}")
                return None

            total_size = int(r.headers.get('content-length', 0))
            dl_size = 0
            start_time = time.time()

            # ✅ SPEED OPTIMIZATION: 512KB chunks (8KB ki jagah)
            async with aiofiles.open(file_path, 'wb') as f:
                async for chunk in r.content.iter_chunked(512 * 1024):
                    if message.id in abort_dict:
                        return None
                    await f.write(chunk)
                    dl_size += len(chunk)
                    await update_progress_ui(
                        dl_size, total_size, message, start_time,
                        f"📥 Downloading [{index}/{total}]", file_name
                    )

        # ✅ Maintenance file check (size-based)
        if total_size > 0 and os.path.getsize(file_path) != total_size:
            print(f"Size mismatch for {file_name}")
            os.remove(file_path)
            return None

        return file_path

    except Exception as e:
        print(f"Bunkr download exception: {e}")
        return None

async def download_bunkr(url, message, task_info=None):
    """
    Main bunkr handler: single file ya album dono handle karta hai.
    Returns: list of downloaded file paths
    """
    connector = aiohttp.TCPConnector(
        limit=10,
        force_close=False,
        enable_cleanup_closed=True,
        ttl_dns_cache=300
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        await message.edit_text("🔍 <b>Fetching Bunkr items...</b>")
        items, err = await bunkr_get_album_items(session, url)

        if err:
            return [], f"Bunkr Error: {err}"
        if not items:
            return [], "No downloadable items found!"

        total = len(items)
        await message.edit_text(f"📥 <b>Found {total} file(s). Downloading...</b>")

        download_dir = os.path.join("downloads", f"bunkr_{int(time.time())}")
        os.makedirs(download_dir, exist_ok=True)

        downloaded_files = []
        overall_start = time.time()

        for i, item in enumerate(items, 1):
            if message.id in abort_dict:
                break
            try:
                await message.edit_text(
                    f"📥 <b>Bunkr Download [{i}/{total}]</b>\n"
                    f"<code>{clean_html(item.get('name', 'Unknown'))}</code>"
                )
                fp = await bunkr_download_file(session, item, download_dir, message, i, total, overall_start)
                if fp:
                    downloaded_files.append(fp)
            except Exception as e:
                print(f"Item {i} failed: {e}")
                continue

        return downloaded_files, None

# ==========================================
#           CORE LOGIC & UPLOADERS
# ==========================================
async def update_progress_ui(current, total, message, start_time, action, filename="Processing...", task_info=None, batch_info=None):
    if message.id in abort_dict: return
    now = time.time()
    if (now - progress_status.get(message.id, 0) < 5) and (current != total): return
    progress_status[message.id] = now

    perc = current * 100 / total if total > 0 else 0
    speed = current / (now - start_time) if (now - start_time) > 0 else 0
    eta = time_formatter((total - current) / speed) if speed > 0 else "0s"

    completed = int(perc // 8.33)
    bar = '⬢' * completed + '⬡' * (12 - completed)

    display_name = batch_info if batch_info else filename
    text = f"1. <b>{clean_html(urllib.parse.unquote(display_name))}</b>\n"
    if task_info: text += f"🔢 <b>{task_info}</b>\n"
    text += f"<b>{action}</b>\n"
    text += f"<code>[{bar}]</code>\n"
    text += f"<b>Progress:</b> {round(perc, 2)}%\n"
    text += f"<b>Processed:</b> {humanbytes(current)}\n"
    text += f"<b>Total Size:</b> {humanbytes(total)}\n"
    text += f"<b>Speed:</b> {humanbytes(speed)}/s\n"
    text += f"<b>ETA:</b> {eta}"

    try: await message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{message.id}")]]))
    except: pass

def extract_archive(file_path):
    output_dir = f"extracted_{int(time.time())}"
    os.makedirs(output_dir, exist_ok=True)
    if not shutil.which("7z"): return [], None, "7z missing!"
    cmd = ["7z", "x", str(file_path), f"-o{output_dir}", "-y"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files_list = []
    for root, _, files in os.walk(output_dir):
        for file in files: files_list.append(os.path.join(root, file))
    files_list.sort(key=natural_sort_key)
    return files_list, output_dir, None

def create_archive(file_path):
    if not shutil.which("7z"): return file_path, False
    zip_path = file_path + ".zip"
    cmd = ["7z", "a", zip_path, file_path, "-mx1"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return zip_path, True

async def compress_video(file_path, message):
    if not shutil.which("ffmpeg"): return file_path, False
    output_path = f"{os.path.splitext(file_path)[0]}_480p.mp4"
    cmd = ["ffmpeg", "-i", file_path, "-vf", "scale=-2:480", "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "64k", output_path, "-y"]
    await message.edit_text(f"📉 <b>Compressing to 480p...</b>\nThis may take time.")
    process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await process.wait()
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0: return output_path, True
    return file_path, False

def split_large_file(file_path):
    limit = 2000 * 1024 * 1024
    if os.path.getsize(file_path) <= limit: return [file_path], False
    out_dir = f"split_{int(time.time())}"
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["7z", "a", f"-v{2000}m", os.path.join(out_dir, os.path.basename(file_path) + ".7z"), file_path, "-mx0"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL)
    parts = [os.path.join(out_dir, f) for f in os.listdir(out_dir)]
    parts.sort(key=natural_sort_key)
    return parts, True

# ==========================================
#           UPLOADERS
# ==========================================
async def upload_file(client, message, file_path, user_mention, task_info=None, batch_info=None, overall_current=0, overall_total=0, start_time=None):
    try:
        if message.id in abort_dict: return False
        file_path = str(file_path)
        file_name = os.path.basename(file_path)

        # ✅ WVM → MP4 auto-conversion (upload se pehle)
        if file_name.lower().endswith('.wvm'):
            converted_path, success = await convert_wvm_to_mp4(file_path, message)
            if success:
                file_path = converted_path
                file_name = os.path.basename(file_path)
            # Agar conversion fail, as-is continue karein

        thumb_path = None
        duration = 0
        VIDEO_EXTS = ('.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.m4v')
        is_video = file_name.lower().endswith(VIDEO_EXTS)

        if is_video:
            thumb_path = await take_screenshot(file_path)
            duration = await get_video_duration(file_path)

        caption = (
            f"☁️ <b>File:</b> {clean_html(file_name)}\n"
            f"📦 <b>Size:</b> {humanbytes(os.path.getsize(file_path))}\n"
            f"👤 <b>User:</b> {user_mention}"
        )

        active_dump = await get_active_dump(message.chat.id)
        if active_dump:
            target_chat = active_dump["id"]
            current_total = overall_total if overall_total > 0 else os.path.getsize(file_path)
            file_size = os.path.getsize(file_path)

            # ✅ SPEED OPTIMIZATION: Progress sirf bade files ke liye (10MB+)
            async def progress_func(current, total):
                if file_size > 10 * 1024 * 1024:
                    actual_current = overall_current + current
                    await update_progress_ui(
                        actual_current, current_total, message, start_time,
                        "📤 Uploading...", filename=file_name,
                        task_info=task_info, batch_info=batch_info
                    )

            try:
                if is_video:
                    await client.send_video(
                        chat_id=target_chat,
                        video=file_path,
                        caption=caption,
                        thumb=thumb_path,
                        duration=duration,
                        supports_streaming=True,
                        progress=progress_func if file_size > 10 * 1024 * 1024 else None
                    )
                else:
                    await client.send_document(
                        chat_id=target_chat,
                        document=file_path,
                        caption=caption,
                        thumb=thumb_path,
                        progress=progress_func if file_size > 10 * 1024 * 1024 else None
                    )
            except Exception as e:
                try: await message.reply_text(f"❌ <b>Upload Error for {clean_html(file_name)}:</b>\n<code>{clean_html(str(e))}</code>")
                except: pass
                return False
        else:
            await message.edit_text("❌ <b>No Dump Selected!</b>")
            return False

        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        return True
    except Exception as e:
        try: await message.reply_text(f"❌ <b>Upload Error:</b>\n<code>{clean_html(str(e))}</code>")
        except: pass
        return False

async def rclone_upload_file(message, file_path, task_info=None, batch_info=None):
    if message.id in abort_dict: return False
    if not os.path.exists("rclone.conf"): return await message.edit_text("❌ rclone.conf missing!")
    file_name = os.path.basename(file_path)
    cmd = ["rclone", "copy", file_path, RCLONE_PATH, "--config", "rclone.conf", "-P"]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    last_update = 0
    while True:
        if message.id in abort_dict: process.kill(); return await message.edit_text("❌ Cancelled")
        line = await process.stdout.readline()
        if not line: break
        decoded = line.decode().strip()
        now = time.time()
        if "%" in decoded and (now - last_update) > 5:
            match = re.search(r"(\d+)%", decoded)
            if match:
                try: await message.edit_text(f"☁️ <b>Cloud Upload</b>\n📂 {file_name}\n📊 {match.group(1)}% Done")
                except: pass
                last_update = now
    await process.wait()
    return True

# ==========================================
#           DOWNLOAD LOGIC
# ==========================================
async def download_logic(url, message, user_id, mode, task_info=None, format_id=None):
    try:
        file_path = None
        # ✅ SPEED OPTIMIZATION: Bigger headers + connection pool
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive"
        }

        if mode == "leech" or (url and ("magnet:" in url or ".torrent" in url.lower())):
            if not aria2: return "ERROR: Aria2 Not Connected. Please restart bot."

            tracker_list = ["http://tracker.opentrackr.org:1337/announce", "udp://tracker.opentrackr.org:1337/announce"]
            options = {'bt-tracker': ",".join(tracker_list)}

            try:
                if url.startswith("http"):
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, headers=headers) as resp:
                            if resp.status == 200:
                                with open("task.torrent", "wb") as f: f.write(await resp.read())
                                download = aria2.add_torrent("task.torrent", options=options)
                            else: return f"ERROR: HTTP {resp.status}"
                elif url.startswith("magnet:"):
                    download = aria2.add_magnet(url, options=options)
                else:
                    return "ERROR: Invalid Torrent Link"
            except Exception as e: return f"ERROR: Aria2 Add Failed: {e}"

            while download.is_metadata:
                if message.id in abort_dict:
                    try: aria2.remove([download.gid], force=True)
                    except: pass
                    return "CANCELLED"
                await asyncio.sleep(2)
                download.update()
                if download.followed_by_ids:
                    download = aria2.get_download(download.followed_by_ids[0])

            if message.id in abort_dict:
                try: aria2.remove([download.gid], force=True)
                except: pass
                return "CANCELLED"

            try:
                download.update()
                if download.status in ["active", "waiting"]:
                    download.pause()
            except Exception:
                pass

            task_id = secrets.token_hex(4)
            file_list = [{"index": f.index, "name": os.path.basename(f.path), "size": f.length} for f in download.files]

            pending_selections[task_id] = {"gid": download.gid, "files": file_list, "selected": [], "status": "waiting"}

            web_url = f"{BASE_URL}/?id={task_id}" if BASE_URL else f"http://YOUR_APP_URL/?id={task_id}"
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("🖥 Select Files (Web UI)", url=web_url)]])
            await message.edit_text(f"⏸ <b>Torrent Paused!</b>\nSelect files you want to download:", reply_markup=btn)

            while pending_selections[task_id]["status"] == "waiting":
                await asyncio.sleep(2)
                if message.id in abort_dict:
                    try: aria2.remove([download.gid], force=True)
                    except: pass
                    return "CANCELLED"

            sel_idx = pending_selections[task_id]["selected"]
            if not sel_idx:
                try: aria2.remove([download.gid], force=True)
                except: pass
                return "CANCELLED"

            aria2.client.change_option(download.gid, {'select-file': ",".join(map(str, sel_idx))})
            download.resume()
            await message.edit_text("▶️ <b>Download Resumed!</b>")

            gid = download.gid
            download_start_time = time.time()
            while True:
                if message.id in abort_dict: aria2.remove([gid]); return "CANCELLED"
                status = aria2.get_download(gid)

                if status.status == "complete":
                    selected_paths = []
                    for f in status.files:
                        if f.selected and os.path.exists(str(f.path)):
                            selected_paths.append(str(f.path))

                    if len(selected_paths) > 1:
                        return selected_paths
                    elif len(selected_paths) == 1:
                        return str(selected_paths[0])
                    else:
                        return "ERROR: No downloaded files found"

                elif status.status == "error": return "ERROR: Aria2 Failed"

                await update_progress_ui(int(status.completed_length), int(status.total_length), message, download_start_time, "☁️ Torrent Downloading...", status.name, task_info)
                await asyncio.sleep(2)

        # --- 2. YT-DLP ---
        if mode == "ytdl" or (mode == "auto" and ("youtube.com" in url or "youtu.be" in url)):
            start_time = time.time()
            loop = asyncio.get_event_loop()

            def ytdl_progress(d):
                if d['status'] == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    current = d.get('downloaded_bytes', 0)
                    filename = os.path.basename(d.get('filename', 'Video'))
                    if current > 0: asyncio.run_coroutine_threadsafe(update_progress_ui(current, total, message, start_time, "📥 Downloading Video...", filename, task_info), loop)

            ydl_opts = {
                'format': format_id if format_id else 'bestvideo+bestaudio/best',
                'outtmpl': '%(title)s.%(ext)s',
                'quiet': True,
                'nocheckcertificate': True,
                'cookiefile': 'cookies.txt' if os.path.exists("cookies.txt") else None,
                'noplaylist': True,
                'progress_hooks': [ytdl_progress]
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return str(ydl.prepare_filename(info))

        # --- 3. DIRECT HTTP ---
        if "magnet:" not in url and ".torrent" not in url.lower():
            # ✅ SPEED OPTIMIZATION: TCPConnector with connection pooling
            connector = aiohttp.TCPConnector(
                limit=20,
                force_close=False,
                enable_cleanup_closed=True,
                ttl_dns_cache=300
            )
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200: return f"ERROR: HTTP {resp.status}"
                    total = int(resp.headers.get("content-length", 0))
                    name = None
                    if "Content-Disposition" in resp.headers:
                        cd = resp.headers["Content-Disposition"]
                        if 'filename="' in cd: name = cd.split('filename="')[1].split('"')[0]
                    if not name: name = os.path.basename(str(url)).split("?")[0]
                    name = urllib.parse.unquote(name)
                    if "." not in name: name += ".mp4"
                    file_path = os.path.join("downloads", name) if "downloads" not in name else name
                    os.makedirs("downloads", exist_ok=True)
                    f = await aiofiles.open(file_path, mode='wb')
                    dl_size = 0; start = time.time()
                    # ✅ SPEED OPTIMIZATION: 512KB chunks
                    async for chunk in resp.content.iter_chunked(512 * 1024):
                        if message.id in abort_dict: await f.close(); return "CANCELLED"
                        await f.write(chunk); dl_size += len(chunk)
                        await update_progress_ui(dl_size, total, message, start, "☁️ Downloading...", name, task_info)
                    await f.close()
            return str(file_path)

        return str(file_path)
    except Exception as e: return f"ERROR: {e}"

# ==========================================
#           PROCESSOR
# ==========================================
async def process_task(client, message, url, mode="auto", upload_target="tg", task_info=None, format_id=None, status_msg=None):
    try:
        if status_msg:
            msg = status_msg
        else:
            if not message.from_user: msg = await message.edit_text("☁️ <b>Starting...</b>")
            else: msg = await message.reply_text("☁️ <b>Initializing...</b>")
    except: return

    try:
        if upload_target == "tg":
            active_dump = await get_active_dump(message.chat.id)
            if not active_dump:
                await msg.edit_text("❌ <b>No Dump Selected!</b>\nUse /setdump to add a channel.")
                return

        # ✅ Bunkr mode alag handle hoga
        if mode == "bunkr":
            downloaded_files, err = await download_bunkr(url, msg, task_info)
            if err or not downloaded_files:
                await msg.edit_text(f"❌ <b>Bunkr Failed:</b>\n<code>{clean_html(str(err or 'No files'))}</code>")
                return

            overall_total_size = sum(os.path.getsize(f) for f in downloaded_files if os.path.exists(f))
            uploaded_so_far = 0
            task_start_time = time.time()
            batch_name = f"Bunkr Album ({len(downloaded_files)} files)"

            for index, f in enumerate(downloaded_files, 1):
                if message.id in abort_dict:
                    break
                if not os.path.exists(f):
                    continue
                item_size = os.path.getsize(f)
                t_info = f"File {index}/{len(downloaded_files)}"
                await upload_file(
                    client, msg, f,
                    message.chat.title or "User",
                    t_info, batch_name,
                    uploaded_so_far, overall_total_size,
                    task_start_time
                )
                uploaded_so_far += item_size
                try: os.remove(f)
                except: pass

            # Cleanup bunkr dir
            try:
                bunkr_dir = os.path.dirname(downloaded_files[0]) if downloaded_files else None
                if bunkr_dir and os.path.isdir(bunkr_dir):
                    shutil.rmtree(bunkr_dir, ignore_errors=True)
            except: pass

            await msg.edit_text("✅ <b>Bunkr Download Complete!</b>")
            return

        # 1. Downloading
        if not url and message.reply_to_message:
            media = message.reply_to_message.document or message.reply_to_message.video or message.reply_to_message.audio or message.reply_to_message.photo
            if not media: await msg.edit_text("❌ <b>No Media!</b>"); return
            fname = getattr(media, 'file_name', None) or f"tg_file_{message.reply_to_message.id}"

            if mode == "leech_file":
                if not fname.lower().endswith(".torrent"): await msg.edit_text("❌ Not a .torrent file!"); return
                file_path = await message.reply_to_message.download()
                file_path = await download_logic(file_path, msg, message.chat.id, mode, task_info, format_id)
            else:
                file_path = await message.reply_to_message.download(progress=update_progress_ui, progress_args=(msg, time.time(), "📥 Downloading from TG...", fname, task_info))
        else:
            file_path = await download_logic(url, msg, message.chat.id, mode, task_info, format_id)

        if not file_path or str(file_path).startswith("ERROR") or file_path == "CANCELLED":
            await msg.edit_text(f"❌ Failed: {file_path}")
            return

        # TASK PIN LOGIC
        if upload_target == "tg":
            active_dump = await get_active_dump(message.chat.id)
            if active_dump:
                if isinstance(file_path, list):
                    try: batch_name = os.path.basename(os.path.commonpath(file_path))
                    except: batch_name = "Batch_Task"
                else:
                    batch_name = os.path.basename(str(file_path))

                if mode == "zip": batch_name += " (Zipping...)"
                elif mode == "compress": batch_name += " (Compressing...)"
                elif mode == "auto" and batch_name.lower().endswith(('.zip','.rar','.7z','.tar','.gz')):
                    batch_name += " (Extracting...)"

                pin_text = f"📌 <b>Batch Task:</b>\n<code>{clean_html(urllib.parse.unquote(batch_name))}</code>"
                try:
                    info_msg = await client.send_message(chat_id=active_dump["id"], text=pin_text)
                    await info_msg.pin(disable_notification=True)
                except Exception as e: print(f"Pinning Error: {e}")

        final_files = []
        if isinstance(file_path, list):
            final_files = file_path
        elif os.path.isdir(str(file_path)):
            for root, dirs, files in os.walk(str(file_path)):
                for file in files:
                    full_p = os.path.join(root, file)
                    if os.path.getsize(full_p) > 0:
                        final_files.append(full_p)
            try: final_files.sort(key=natural_sort_key)
            except: final_files.sort()
        else:
            final_files = [str(file_path)]

        if len(final_files) == 0:
            await msg.edit_text("❌ <b>Error:</b> No files found to upload (or 0 bytes).")
            return

        # 2. Operations (Compress/Zip/Extract)
        if mode == "compress" and (isinstance(file_path, str) and str(file_path).lower().endswith(('.mp4', '.mkv', '.webm', '.avi'))):
            compressed_path, success = await compress_video(str(file_path), msg)
            if success: os.remove(file_path); final_files = [compressed_path]
        elif mode == "zip":
            await msg.edit_text("🤐 <b>Zipping...</b>")
            zip_path, success = create_archive(str(file_path))
            if success: os.remove(file_path); final_files = [zip_path]
        elif mode == "auto" and (isinstance(file_path, str) and str(file_path).lower().endswith(('.zip','.rar','.7z','.tar','.gz'))):
            await msg.edit_text("📦 <b>Extracting...</b>")
            extracted, temp_dir, err = extract_archive(file_path)
            if not err and extracted: final_files = extracted; os.remove(file_path)

        # 3. Upload Loop
        overall_total_size = sum(os.path.getsize(f) for f in final_files)
        uploaded_so_far = 0
        task_start_time = time.time()

        for index, f in enumerate(final_files):
            upload_list = [f]
            if upload_target == "tg" and os.path.getsize(f) > 2000*1024*1024:
                await msg.edit_text(f"✂️ <b>Splitting...</b>\n{os.path.basename(f)}")
                parts, success = split_large_file(f)
                if success: upload_list = parts; os.remove(f)

            for item in upload_list:
                item_size = os.path.getsize(item)
                if upload_target == "rclone":
                    await rclone_upload_file(msg, item, task_info, batch_name)
                else:
                    await upload_file(client, msg, item, message.chat.title or "User", task_info, batch_name, uploaded_so_far, overall_total_size, task_start_time)
                uploaded_so_far += item_size

            if len(upload_list) > 1: shutil.rmtree(os.path.dirname(upload_list[0]), ignore_errors=True)

        # 4. Cleanup
        if 'temp_dir' in locals(): shutil.rmtree(temp_dir, ignore_errors=True)

        if isinstance(file_path, list):
            try:
                base_dir = os.path.commonpath(file_path)
                if os.path.isdir(base_dir): shutil.rmtree(base_dir, ignore_errors=True)
            except: pass
        elif os.path.exists(str(file_path)) and str(file_path) not in final_files:
            if os.path.isdir(str(file_path)): shutil.rmtree(str(file_path), ignore_errors=True)
            else: os.remove(str(file_path))

        for f in final_files:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

        await msg.edit_text("✅ <b>Task Completed!</b>")
    except Exception as e:
        traceback.print_exc()
        await msg.edit_text(f"⚠️ <b>Error:</b> <code>{clean_html(str(e))}</code>")

# ==========================================
#           COMMAND HANDLERS
# ==========================================
@app.on_message(filters.command("setdump"))
async def set_dump_info(c, m):
    await m.reply_text("👋 <b>To Add a Dump:</b>\n1. Make me ADMIN in Channel.\n2. Forward a message from it.")

@app.on_message(filters.forwarded & filters.private)
async def dump_handler(c, m):
    if m.forward_from_chat:
        chat_id, title = m.forward_from_chat.id, m.forward_from_chat.title
        try:
            me = await c.get_chat_member(chat_id, "me")
            if me.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]: return await m.reply_text("❌ I am not Admin!")
        except: return await m.reply_text("❌ Cannot access channel!")
        await add_dump(m.chat.id, chat_id, title)
        await m.reply_text(f"✅ <b>Dump Added:</b> {title}")

@app.on_message(filters.command(["dumps", "settings"]))
async def list_dumps(c, m):
    dumps = await get_user_dumps(m.chat.id)
    if not dumps: return await m.reply_text("❌ No Dumps found!")
    active = await get_active_dump(m.chat.id)
    active_id = active["id"] if active else None
    buttons = []
    for d in dumps:
        mark = "✅" if d["id"] == active_id else ""
        buttons.append([InlineKeyboardButton(f"{mark} {d['title']}", callback_data=f"setdump_{d['id']}")])
        buttons.append([InlineKeyboardButton(f"🗑 Delete", callback_data=f"deldump_{d['id']}")])
    await m.reply_text("⚙️ <b>Your Dumps</b>", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex(r"setdump_"))
async def set_active_cb(c, cb):
    chat_id = int(cb.data.split("_")[1])
    await set_active_dump(cb.message.chat.id, chat_id)
    await cb.answer("Active Dump Updated!")
    await list_dumps(c, cb.message)

@app.on_callback_query(filters.regex(r"deldump_"))
async def del_dump_cb(c, cb):
    chat_id = int(cb.data.split("_")[1])
    await delete_dump(cb.message.chat.id, chat_id)
    await cb.answer("Deleted!")
    await list_dumps(c, cb.message)

@app.on_message(filters.command("ytdl"))
async def ytdl_selector(c, m):
    if len(m.command) < 2: return await m.reply_text("❌ Send Link!")
    url = m.text.split(None, 1)[1]
    msg = await m.reply_text("🔍 <b>Fetching...</b>")
    try:
        with yt_dlp.YoutubeDL({'listformats': True, 'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])
        buttons = []
        seen = set()
        for f in formats:
            h = f.get('height')
            if h and h not in seen and f.get('ext') == 'mp4':
                buttons.append([InlineKeyboardButton(f"🎬 {h}p", callback_data=f"yt_vid|{h}|{m.id}")])
                seen.add(h)
        buttons.append([InlineKeyboardButton("🎵 MP3", callback_data=f"yt_aud|mp3|{m.id}")])
        buttons.append([InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{m.id}")])
        ytdl_session[m.id] = {"url": url, "user": m.from_user.id}
        await msg.edit_text(f"Select Quality:", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await msg.edit_text(f"❌ <b>Error Fetching Info:</b>\n<code>{clean_html(str(e))}</code>")

@app.on_callback_query(filters.regex(r"^yt_"))
async def ytdl_cb(c, cb):
    data = cb.data.split("|")
    mode, quality, msg_id = data[0], data[1], int(data[2])
    session = ytdl_session.get(msg_id)
    if not session: return await cb.answer("❌ Expired", show_alert=True)

    await cb.message.edit_text(f"⏳ <b>Starting Download: {quality}p...</b>")

    if mode == "yt_vid":
        f_id = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
    else:
        f_id = "bestaudio/best"

    asyncio.create_task(process_task(c, cb.message, session['url'], mode="ytdl", format_id=f_id))

# ==========================================
#   ✅ NEW: /bdl - BUNKR DOWNLOADER (Album + Single)
# ==========================================
@app.on_message(filters.command("bdl"))
async def bunkr_dl_handler(c, m):
    """
    /bdl <bunkr_url>
    Single file aur full album/folder dono support karta hai.
    .wvm files automatically MP4 mein convert hongi.
    """
    if len(m.command) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b> <code>/bdl https://bunkr.sk/a/albumname</code>\n\n"
            "✅ Single files aur Albums dono support hain!\n"
            "🔄 .wvm files auto-convert ho jayengi MP4 mein."
        )

    url = m.text.split(None, 1)[1].strip()

    # Basic URL validation
    if not (url.startswith("http") and ("bunkr" in url)):
        return await m.reply_text("❌ <b>Invalid URL!</b> Sirf Bunkr links supported hain.")

    active_dump = await get_active_dump(m.chat.id)
    if not active_dump:
        return await m.reply_text("❌ <b>No Dump Selected!</b>\nUse /setdump to add a channel first.")

    asyncio.create_task(process_task(c, m, url, mode="bunkr", upload_target="tg"))

@app.on_message(filters.command(["leech", "dl", "rclone", "queue", "zip", "compress"]))
async def command_handler(c, m):
    is_reply = m.reply_to_message and (m.reply_to_message.document or m.reply_to_message.video or m.reply_to_message.audio or m.reply_to_message.photo)
    url = None
    links = []

    if is_reply:
        links = [None]
    elif len(m.command) > 1:
        text = m.text.split(None, 1)[1]
        links = text.split()
        url = links[0]
    else:
        return await m.reply_text("❌ Send Link or Reply to File!")

    cmd = m.command[0]
    target = "rclone" if cmd == "rclone" else "tg"
    mode = "auto"

    if cmd == "leech":
        mode = "leech"
        if is_reply:
            doc = m.reply_to_message.document
            if not (doc and doc.file_name and doc.file_name.lower().endswith(".torrent")):
                return await m.reply_text("❌ <b>/leech</b> is only for .torrent files!")
            mode = "leech_file"
    elif cmd == "dl":
        if url and ("magnet:" in url or ".torrent" in url.lower()):
            return await m.reply_text("❌ Use <b>/leech</b> for Torrents!")
    elif cmd == "zip": mode = "zip"
    elif cmd == "compress": mode = "compress"
    elif cmd == "ytdl": mode = "ytdl"

    if cmd == "queue":
        if m.from_user.id not in user_queues: user_queues[m.from_user.id] = []
        for l in links: user_queues[m.from_user.id].append((l, m, mode, target))
        await m.reply_text(f"✅ <b>Added {len(links)} Tasks!</b>")
        asyncio.create_task(queue_manager(c, m.from_user.id))
    else:
        if is_reply:
            asyncio.create_task(process_task(c, m, None, mode, target))
        else:
            for l in links: asyncio.create_task(process_task(c, m, l, mode, target))

async def queue_manager(client, user_id):
    if is_processing.get(user_id, False): return
    is_processing[user_id] = True

    status_msg = await client.send_message(user_id, "⚙️ <b>Queue Started...</b>")
    processed = 0

    while user_queues.get(user_id):
        if status_msg.id in abort_dict:
            del abort_dict[status_msg.id]

        processed += 1
        current_queue_len = len(user_queues[user_id])
        task = user_queues[user_id].pop(0)
        task_info = f"Task {processed}/{processed + current_queue_len}"

        await process_task(client, task[1], task[0], task[2], task[3], task_info, status_msg=status_msg)

    is_processing[user_id] = False
    await client.send_message(user_id, "🏁 <b>All Queued Tasks Finished!</b>")

# ==========================================
#             START COMMAND
# ==========================================
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(c, m):
    welcome_text = (
        f"👋 <b>Hello {clean_html(m.from_user.first_name)}!</b>\n\n"
        "🤖 <b>I am an Advanced URL & Torrent Uploader Bot.</b>\n\n"
        "🔗 Send me any direct link, YouTube link, or .torrent file!\n\n"
        "<b>Main Commands:</b>\n"
        "• <code>/leech</code> - Torrents ke liye\n"
        "• <code>/dl</code> - Direct Links ke liye\n"
        "• <code>/ytdl</code> - YouTube Videos ke liye\n"
        "• <code>/bdl</code> - Bunkr (Single + Album) ke liye 🆕\n"
        "• <code>/queue</code> - Multiple links add karo\n"
        "• <code>/setdump</code> - Upload channel set karo\n\n"
        "💡 <b>Tip:</b> .wvm files auto-convert ho jaati hain MP4 mein!"
    )
    await m.reply_text(welcome_text)

# ==========================================
#          UPTIME & RESTART COMMANDS
# ==========================================
@app.on_message(filters.command("ping"))
async def ping_cmd(c, m):
    uptime = get_readable_time(time.time() - bot_start_time)
    await m.reply_text(f"🏓 <b>Bot is Alive!</b>\n⏱ <b>Uptime:</b> <code>{uptime}</code>")

@app.on_message(filters.command("restart"))
async def restart_cmd(c, m):
    await m.reply_text("🔄 <b>Restarting Bot... Please wait 10-15 seconds.</b>")
    os.execl(sys.executable, sys.executable, *sys.argv)

@app.on_callback_query(filters.regex(r"cancel_"))
async def cancel(c, cb):
    abort_dict[cb.message.id] = True
    await cb.answer("🛑 Task stopped by user", show_alert=True)
    try:
        await cb.message.edit_text("🛑 <b>Task stopped by user</b>")
    except:
        pass

# ==========================================
#           WEB UI ROUTES
# ==========================================
async def web_index(request):
    try:
        with open("index.html", "r") as f:
            html_content = f.read()
        return web.Response(text=html_content, content_type='text/html')
    except Exception as e:
        return web.Response(text=f"Error loading UI. Create index.html file! Error: {e}", status=500)

async def web_api_get_files(request):
    task_id = request.query.get("id")
    if task_id in pending_selections:
        return web.json_response({"success": True, "files": pending_selections[task_id]["files"]})
    return web.json_response({"success": False, "error": "Invalid or Expired Link. Please try again from Telegram."})

async def web_api_submit(request):
    try:
        data = await request.json()
        task_id = data.get("id")
        selected_indexes = data.get("selected_indexes", [])

        if task_id in pending_selections:
            pending_selections[task_id]["selected"] = selected_indexes
            pending_selections[task_id]["status"] = "ready"
            return web.json_response({"success": True})
        return web.json_response({"success": False, "error": "Invalid Task ID"})
    except:
        return web.json_response({"success": False, "error": "Bad Request"})

# ==========================================
#                 MAIN
# ==========================================
async def main():
    await init_db()
    if shutil.which("aria2c"):
        subprocess.Popen(['aria2c', '--enable-rpc', '--rpc-listen-port=6800', '--daemon', '--seed-time=0', '--allow-overwrite=true'])
        await asyncio.sleep(3)
        global aria2
        aria2 = aria2p.API(aria2p.Client(host="http://localhost", port=6800, secret=""))

    app_web = web.Application()
    app_web.router.add_get("/", web_index)
    app_web.router.add_get("/api/files", web_api_get_files)
    app_web.router.add_post("/api/submit", web_api_submit)

    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await app.start()
    print("🤖 Bot Started — Bunkr Album + WVM Convert + Speed Optimized")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
