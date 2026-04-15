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
from mega import Mega
from math import floor
from base64 import b64decode
from bs4 import BeautifulSoup
from urllib.parse import urlparse, unquote

bot_start_time = time.time()

def get_aria2c_version():
    try:
        result = subprocess.run(["aria2c", "--version"], capture_output=True, text=True)
        match = re.search(r"aria2 version (\S+)", result.stdout)
        return match.group(1) if match else "unknown"
    except:
        return "N/A"

def get_pyrogram_version():
    try:
        import pyrogram
        return getattr(pyrogram, "__version__", "unknown")
    except:
        return "N/A"

ARIA2C_VERSION = get_aria2c_version()
PYROGRAM_VERSION = get_pyrogram_version()

def get_readable_time(seconds: int) -> str:
    count = 0
    ping_time = ""
    time_list = []
    time_suffix_list = ["s", "m", "h", "days"]
    while count < 4:
        count += 1
        remainder, result = divmod(seconds, 60) if count < 3 else divmod(seconds, 24)
        if seconds == 0 and remainder == 0:
            break
        time_list.append(int(result))
        seconds = int(remainder)
    for x in range(len(time_list)):
        time_list[x] = str(time_list[x]) + time_suffix_list[x]
    if len(time_list) == 4:
        ping_time += time_list.pop() + ", "
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
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

if not MONGO_URL:
    print("Error: MONGO_URL is missing!")
    exit(1)

app = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML,
    workers=16,
    max_concurrent_transmissions=5
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

# ─────────────────────────────────────────
# Dump helpers
# ─────────────────────────────────────────
async def add_dump(user_id, chat_id, chat_title):
    user = await users_col.find_one({"_id": user_id})
    new_dump = {"id": chat_id, "title": chat_title}
    if not user:
        await users_col.insert_one({"_id": user_id, "dumps": [new_dump], "active_dump": chat_id})
    else:
        dumps = user.get("dumps", [])
        if not any(d["id"] == chat_id for d in dumps):
            dumps.append(new_dump)
            await users_col.update_one({"_id": user_id}, {"$set": {"dumps": dumps}})
            if not user.get("active_dump"):
                await users_col.update_one({"_id": user_id}, {"$set": {"active_dump": chat_id}})

async def get_user_dumps(user_id):
    user = await users_col.find_one({"_id": user_id})
    return user.get("dumps", []) if user else []

async def set_active_dump(user_id, chat_id):
    await users_col.update_one({"_id": user_id}, {"$set": {"active_dump": chat_id}})

async def get_active_dump(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return None
    active_id = user.get("active_dump")
    dumps = user.get("dumps", [])
    for d in dumps:
        if d["id"] == active_id:
            return d
    if dumps:
        await set_active_dump(user_id, dumps[0]["id"])
        return dumps[0]
    return None

async def delete_dump(user_id, chat_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return
    dumps = user.get("dumps", [])
    new_dumps = [d for d in dumps if d["id"] != chat_id]
    update = {"dumps": new_dumps}
    if user.get("active_dump") == chat_id:
        update["active_dump"] = new_dumps[0]["id"] if new_dumps else None
    await users_col.update_one({"_id": user_id}, {"$set": update})

# ─────────────────────────────────────────
# User Settings helpers
# ─────────────────────────────────────────
async def get_user_settings(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return {"send_as": "media", "thumbnail": None}
    return {
        "send_as": user.get("send_as", "media"),
        "thumbnail": user.get("thumbnail", None),
    }

async def set_user_setting(user_id, key, value):
    await users_col.update_one({"_id": user_id}, {"$set": {key: value}}, upsert=True)

async def get_user_thumbnail(user_id):
    user = await users_col.find_one({"_id": user_id})
    return user.get("thumbnail", None) if user else None

async def set_user_thumbnail(user_id, file_id):
    await users_col.update_one({"_id": user_id}, {"$set": {"thumbnail": file_id}}, upsert=True)

async def clear_user_thumbnail(user_id):
    await users_col.update_one({"_id": user_id}, {"$unset": {"thumbnail": ""}})

# ─────────────────────────────────────────
# Mega login helpers (MegaAPI Engine)
# ─────────────────────────────────────────
mega_api = Mega()
mega_client = mega_api.login() # Default anonymous
mega_creds = {"email": None}

async def mega_login(email, password):
    global mega_client, mega_creds
    try:
        def _login():
            return mega_api.login(email, password)
        mega_client = await asyncio.to_thread(_login)
        mega_creds["email"] = email
        return True, "Login successful via MegaAPI!"
    except Exception as e:
        return False, str(e)

async def mega_logout():
    global mega_client, mega_creds
    mega_client = mega_api.login() # Reset to anonymous
    mega_creds["email"] = None

async def mega_whoami():
    return mega_creds["email"]

async def mega_download(url_or_path, download_dir, message):
    os.makedirs(download_dir, exist_ok=True)
    global mega_client
    try:
        await message.edit_text(
            "⏳ <b>Downloading from Mega via MegaAPI...</b>\n"
            "<i>(MegaAPI direct download kar raha hai, please wait...)</i>"
        )
        
        def _dl():
            return mega_client.download_url(url_or_path, dest_path=download_dir)
            
        await asyncio.to_thread(_dl)
        
        downloaded_files = []
        for root, dirs, files in os.walk(download_dir):
            for file in files:
                downloaded_files.append(os.path.join(root, file))
                
        if not downloaded_files:
            return [], "MegaAPI: No files downloaded."
            
        return downloaded_files, None
    except Exception as e:
        return [], f"MegaAPI Error: {str(e)}"

 
# ─────────────────────────────────────────
# State dicts
# ─────────────────────────────────────────
abort_dict = {}
user_queues = {}
is_processing = {}
progress_status = {}
ytdl_session = {}
aria2 = None
pending_selections = {}
seeding_gids = {}
waiting_for_thumbnail = {}  # user_id -> True

def humanbytes(size):
    if not size:
        return "0B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{round(size, 2)} {unit}"
        size /= 1024
    return f"{round(size, 2)} PB"

def time_formatter(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return "{:02d}:{:02d}:{:02d}".format(int(hours), int(minutes), int(seconds))

def clean_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

async def take_screenshot(video_path):
    try:
        thumb_path = f"{video_path}.jpg"
        cmd = ["ffmpeg", "-ss", "00:00:01", "-i", video_path, "-vframes", "1", "-q:v", "2", thumb_path, "-y"]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await process.wait()
        if os.path.exists(thumb_path):
            return thumb_path
    except:
        pass
    return None

async def get_video_duration(video_path):
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", video_path]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        return int(float(stdout.decode().strip()))
    except:
        return 0

async def convert_to_streamable(file_path, message):
    ext = os.path.splitext(file_path)[1].lower()
    output_path = os.path.splitext(file_path)[0] + "_conv.mp4"
    try:
        await message.edit_text(
            f"🔄 <b>Converting {ext} → .mp4...</b>\n<code>{clean_html(os.path.basename(file_path))}</code>"
        )
        cmd = ["ffmpeg", "-y", "-i", file_path,
               "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", output_path]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            os.remove(file_path)
            return output_path, True
        await message.edit_text(f"🔄 <b>Re-encoding {ext} → .mp4...</b>")
        cmd2 = ["ffmpeg", "-y", "-i", file_path,
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "aac", "-b:a", "128k", output_path]
        proc2 = await asyncio.create_subprocess_exec(
            *cmd2, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc2.communicate()
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            os.remove(file_path)
            return output_path, True
    except Exception as e:
        print(f"convert_to_streamable error ({ext}): {e}")
    return file_path, False

# ─────────────────────────────────────────
# Bunkr helpers
# ─────────────────────────────────────────
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
        is_single = soup.find('span', {'class': 'ic-videos'}) is not None or \
                    soup.find('div', {'class': 'lightgallery'}) is not None
        if is_single:
            item = await bunkr_get_real_url(session, url)
            if item:
                items_result.append(item)
        else:
            album_name_tag = soup.find('h1', {'class': 'truncate'})
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
    real_url = item['url']
    file_name = item.get('name') or os.path.basename(urlparse(real_url).path)
    file_name = unquote(file_name)
    if not file_name or '.' not in file_name:
        file_name = f"bunkr_file_{index}.mp4"
    file_path = os.path.join(download_dir, file_name)
    try:
        async with session.get(real_url, headers=BUNKR_HEADERS) as r:
            if r.status != 200:
                return None
            if r.url.path == "/maintenance.mp4":
                return None
            total_size = int(r.headers.get('content-length', 0))
            dl_size = 0
            start_time = time.time()
            async with aiofiles.open(file_path, 'wb') as f:
                async for chunk in r.content.iter_chunked(512 * 1024):
                    if message.id in abort_dict:
                        return None
                    await f.write(chunk)
                    dl_size += len(chunk)
                    await update_progress_ui(dl_size, total_size, message, start_time, f"📥 Downloading [{index}/{total}]", file_name)
        if total_size > 0 and os.path.getsize(file_path) != total_size:
            os.remove(file_path)
            return None
        return file_path
    except Exception as e:
        print(f"Bunkr download exception: {e}")
        return None

async def download_bunkr(url, message, task_info=None):
    connector = aiohttp.TCPConnector(limit=10, force_close=False, enable_cleanup_closed=True, ttl_dns_cache=300)
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
                await message.edit_text(f"📥 <b>Bunkr Download [{i}/{total}]</b>\n<code>{clean_html(item.get('name', 'Unknown'))}</code>")
                fp = await bunkr_download_file(session, item, download_dir, message, i, total, overall_start)
                if fp:
                    downloaded_files.append(fp)
            except Exception as e:
                print(f"Item {i} failed: {e}")
                continue
        return downloaded_files, None

# ─────────────────────────────────────────
# Progress UI
# ─────────────────────────────────────────
async def update_progress_ui(current, total, message, start_time, action, filename="Processing...", task_info=None, batch_info=None):
    if message.id in abort_dict:
        return
    now = time.time()
    if (now - progress_status.get(message.id, 0) < 5) and (current != total):
        return
    progress_status[message.id] = now
    perc = current * 100 / total if total > 0 else 0
    speed = current / (now - start_time) if (now - start_time) > 0 else 0
    eta = time_formatter((total - current) / speed) if speed > 0 else "0s"
    completed = int(perc // 8.33)
    bar = '⬢' * completed + '⬡' * (12 - completed)
    display_name = batch_info if batch_info else filename
    text = f"1. <b>{clean_html(urllib.parse.unquote(display_name))}</b>\n"
    if task_info:
        text += f"🔢 <b>{task_info}</b>\n"
    text += f"<b>{action}</b>\n"
    text += f"<code>[{bar}]</code>\n"
    text += f"<b>Progress:</b> {round(perc, 2)}%\n"
    text += f"<b>Processed:</b> {humanbytes(current)}\n"
    text += f"<b>Total Size:</b> {humanbytes(total)}\n"
    text += f"<b>Speed:</b> {humanbytes(speed)}/s\n"
    text += f"<b>ETA:</b> {eta}\n"
    text += f"<b>Engine:</b> aria2c {ARIA2C_VERSION} | pyrofork {PYROGRAM_VERSION}"
    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{message.id}")]]))
    except:
        pass

# ─────────────────────────────────────────
# Archive / Compress / Split helpers
# ─────────────────────────────────────────
def extract_archive(file_path):
    output_dir = f"extracted_{int(time.time())}"
    os.makedirs(output_dir, exist_ok=True)
    if not shutil.which("7z"):
        return [], None, "7z missing!"
    cmd = ["7z", "x", str(file_path), f"-o{output_dir}", "-y"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files_list = []
    for root, _, files in os.walk(output_dir):
        for file in files:
            files_list.append(os.path.join(root, file))
    files_list.sort(key=natural_sort_key)
    return files_list, output_dir, None

def create_archive(file_path):
    if not shutil.which("7z"):
        return file_path, False
    zip_path = file_path + ".zip"
    cmd = ["7z", "a", zip_path, file_path, "-mx1"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return zip_path, True

async def compress_video(file_path, message):
    if not shutil.which("ffmpeg"):
        return file_path, False
    output_path = f"{os.path.splitext(file_path)[0]}_480p.mp4"
    cmd = ["ffmpeg", "-i", file_path, "-vf", "scale=-2:480", "-c:v", "libx264", "-crf", "28",
           "-preset", "ultrafast", "-c:a", "aac", "-b:a", "64k", output_path, "-y"]
    await message.edit_text("📉 <b>Compressing to 480p...</b>\nThis may take time.")
    process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await process.wait()
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path, True
    return file_path, False

def split_large_file(file_path):
    limit = 2000 * 1024 * 1024
    if os.path.getsize(file_path) <= limit:
        return [file_path], False
    out_dir = f"split_{int(time.time())}"
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["7z", "a", f"-v{2000}m", os.path.join(out_dir, os.path.basename(file_path) + ".7z"), file_path, "-mx0"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL)
    parts = [os.path.join(out_dir, f) for f in os.listdir(out_dir)]
    parts.sort(key=natural_sort_key)
    return parts, True

# ─────────────────────────────────────────
# Download a thumbnail from Telegram to disk
# ─────────────────────────────────────────
async def download_thumb_from_file_id(client, file_id, user_id):
    try:
        thumb_dir = "thumbnails"
        os.makedirs(thumb_dir, exist_ok=True)
        path = os.path.join(thumb_dir, f"thumb_{user_id}.jpg")
        await client.download_media(file_id, file_name=path)
        return path
    except Exception as e:
        print(f"Thumb download error: {e}")
        return None

# ─────────────────────────────────────────
# Upload helpers
# ─────────────────────────────────────────
async def upload_file(client, message, file_path, user_mention, task_info=None, batch_info=None,
                      overall_current=0, overall_total=0, start_time=None, custom_name=None,
                      user_id=None, target_chat=None):
    """
    Upload file to target_chat. If target_chat is None, upload to message.chat.id (PM).
    Respects user settings: send_as (media/document), custom thumbnail.
    """
    try:
        if message.id in abort_dict:
            return False
        file_path = str(file_path)
        file_name = custom_name or os.path.basename(file_path)

        CONVERT_EXTS = ('.wvm', '.wmv', '.m4v', '.avi', '.f4v')
        if os.path.splitext(file_name)[1].lower() in CONVERT_EXTS:
            converted_path, success = await convert_to_streamable(file_path, message)
            if success:
                orig_stem = os.path.splitext(file_name)[0]
                file_path = converted_path
                new_name = orig_stem + '.mp4'
                named_path = os.path.join(os.path.dirname(file_path), new_name)
                try:
                    os.rename(file_path, named_path)
                    file_path = named_path
                    file_name = new_name
                except:
                    file_name = os.path.basename(file_path)

        # Get user settings
        uid = user_id or message.chat.id
        settings = await get_user_settings(uid)
        send_as = settings.get("send_as", "media")
        thumb_file_id = settings.get("thumbnail", None)

        thumb_path = None
        duration = 0
        VIDEO_EXTS = ('.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.m4v')
        is_video = file_name.lower().endswith(VIDEO_EXTS)

        # Determine thumbnail
        if thumb_file_id:
            # Use user's custom thumbnail
            thumb_path = await download_thumb_from_file_id(client, thumb_file_id, uid)
        elif is_video:
            # Auto-generate from video
            thumb_path = await take_screenshot(file_path)

        if is_video:
            duration = await get_video_duration(file_path)

        caption = clean_html(file_name)

        # Determine destination
        if target_chat is None:
            # No dump selected — send to PM
            dest_chat = message.chat.id
        else:
            dest_chat = target_chat

        current_total = overall_total if overall_total > 0 else os.path.getsize(file_path)
        file_size = os.path.getsize(file_path)

        async def progress_func(current, total):
            if file_size > 10 * 1024 * 1024:
                actual_current = overall_current + current
                await update_progress_ui(actual_current, current_total, message, start_time,
                                         "📤 Uploading...", filename=file_name, task_info=task_info, batch_info=batch_info)

        try:
            if send_as == "document":
                # Send as document (with thumbnail if any)
                await client.send_document(
                    chat_id=dest_chat,
                    document=file_path,
                    caption=caption,
                    thumb=thumb_path,
                    progress=progress_func if file_size > 10 * 1024 * 1024 else None
                )
            else:
                # Send as media (video/document based on type)
                if is_video:
                    await client.send_video(
                        chat_id=dest_chat, video=file_path, caption=caption,
                        thumb=thumb_path, duration=duration, supports_streaming=True,
                        progress=progress_func if file_size > 10 * 1024 * 1024 else None
                    )
                else:
                    await client.send_document(
                        chat_id=dest_chat, document=file_path, caption=caption,
                        thumb=thumb_path,
                        progress=progress_func if file_size > 10 * 1024 * 1024 else None
                    )
        except Exception as e:
            try:
                await message.reply_text(f"❌ <b>Upload Error for {clean_html(file_name)}:</b>\n<code>{clean_html(str(e))}</code>")
            except:
                pass
            return False

        if thumb_path and os.path.exists(thumb_path) and not thumb_file_id:
            os.remove(thumb_path)

        return True
    except Exception as e:
        try:
            await message.reply_text(f"❌ <b>Upload Error:</b>\n<code>{clean_html(str(e))}</code>")
        except:
            pass
        return False

async def rclone_upload_file(message, file_path, task_info=None, batch_info=None):
    if message.id in abort_dict:
        return False
    if not os.path.exists("rclone.conf"):
        return await message.edit_text("❌ rclone.conf missing!")
    file_name = os.path.basename(file_path)
    cmd = ["rclone", "copy", file_path, RCLONE_PATH, "--config", "rclone.conf", "-P"]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    last_update = 0
    while True:
        if message.id in abort_dict:
            process.kill()
            return await message.edit_text("❌ Cancelled")
        line = await process.stdout.readline()
        if not line:
            break
        decoded = line.decode().strip()
        now = time.time()
        if "%" in decoded and (now - last_update) > 5:
            match = re.search(r"(\d+)%", decoded)
            if match:
                try:
                    await message.edit_text(f"☁️ <b>Cloud Upload</b>\n📂 {file_name}\n📊 {match.group(1)}% Done")
                except:
                    pass
                last_update = now
    await process.wait()
    return True

# ─────────────────────────────────────────
# Download logic (torrent / ytdl / direct)
# ─────────────────────────────────────────
async def download_logic(url, message, user_id, mode, task_info=None, format_id=None, rename=None, seed=False):
    try:
        file_path = None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive"
        }

        if mode == "leech" or mode == "leech_file" or (url and ("magnet:" in url or ".torrent" in url.lower())):
            if not aria2:
                return "ERROR: Aria2 Not Connected. Please restart bot."

            tracker_list = [
                "http://tracker.opentrackr.org:1337/announce",
                "udp://tracker.opentrackr.org:1337/announce",
                "udp://open.tracker.cl:1337/announce",
                "udp://exodus.desync.com:6969/announce"
            ]
            options = {
                'bt-tracker': ",".join(tracker_list),
                'seed-time': '0'
            }

            if seed:
                options['seed-time'] = '525600'

            try:
                download = None
                if url and url.startswith("http"):
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, headers=headers) as resp:
                            if resp.status == 200:
                                torrent_bytes = await resp.read()
                                with open("task.torrent", "wb") as f:
                                    f.write(torrent_bytes)
                                download = aria2.add_torrent("task.torrent", options=options)
                            else:
                                return f"ERROR: HTTP {resp.status}"
                elif url and url.startswith("magnet:"):
                    download = aria2.add_magnet(url, options=options)
                elif mode == "leech_file":
                    download = aria2.add_torrent(url, options=options)
                else:
                    return "ERROR: Invalid Torrent Link"
            except Exception as e:
                return f"ERROR: Aria2 Add Failed: {e}"

            if download is None:
                return "ERROR: Failed to add torrent to aria2"

            await asyncio.sleep(2)
            try:
                download = aria2.get_download(download.gid)
            except Exception as e:
                return f"ERROR: {e}"

            meta_wait = 0
            while True:
                try:
                    download = aria2.get_download(download.gid)
                except Exception as e:
                    return f"ERROR: {e}"

                if message.id in abort_dict:
                    try:
                        aria2.remove([download.gid], force=True)
                    except:
                        pass
                    return "CANCELLED"

                if not download.is_metadata:
                    break

                if download.followed_by_ids:
                    try:
                        download = aria2.get_download(download.followed_by_ids[0])
                        break
                    except:
                        pass

                meta_wait += 2
                if meta_wait > 120:
                    return "ERROR: Metadata timeout"
                await asyncio.sleep(2)

            try:
                download = aria2.get_download(download.gid)
                if download.status in ["active", "waiting"]:
                    aria2.client.pause(download.gid)
                    await asyncio.sleep(1)
            except Exception:
                pass

            task_id = secrets.token_hex(4)
            try:
                file_list = [{"index": f.index, "name": os.path.basename(str(f.path)), "size": f.length}
                             for f in download.files]
            except Exception as e:
                return f"ERROR: Cannot read file list: {e}"

            pending_selections[task_id] = {
                "gid": download.gid,
                "files": file_list,
                "selected": None,
                "status": "waiting",
                "action": None
            }

            web_url = f"{BASE_URL}/?id={task_id}" if BASE_URL else f"http://YOUR_APP_URL/?id={task_id}"
            btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("🖥 Select Files (Web UI)", url=web_url)],
                [
                    InlineKeyboardButton("✅ Download All", callback_data=f"torrent_all_{task_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"torrent_cancel_{task_id}")
                ]
            ])
            await message.edit_text(
                f"⏸ <b>Torrent Paused!</b>\n"
                f"📂 <b>Files:</b> {len(file_list)}\n\n"
                f"Select files via Web UI, or choose below:",
                reply_markup=btn
            )

            timeout = 0
            while pending_selections[task_id]["status"] == "waiting":
                await asyncio.sleep(2)
                timeout += 2
                if message.id in abort_dict:
                    try:
                        aria2.client.remove(download.gid)
                    except:
                        pass
                    del pending_selections[task_id]
                    return "CANCELLED"
                if timeout > 600:
                    try:
                        aria2.client.remove(download.gid)
                    except:
                        pass
                    del pending_selections[task_id]
                    return "ERROR: Selection timeout"

            action = pending_selections[task_id].get("action")
            sel_idx = pending_selections[task_id].get("selected", [])
            del pending_selections[task_id]

            if action == "cancel":
                try:
                    aria2.client.remove(download.gid)
                except:
                    pass
                return "CANCELLED"

            try:
                current_download = aria2.get_download(download.gid)
                if action == "all" or not sel_idx:
                    all_indices = [str(f.index) for f in current_download.files]
                    try:
                        aria2.client.change_option(download.gid, {'select-file': ",".join(all_indices)})
                    except:
                        pass
                else:
                    try:
                        aria2.client.change_option(download.gid, {'select-file': ",".join(map(str, sel_idx))})
                    except Exception as e:
                        print(f"select-file warning: {e}")

                aria2.client.unpause(download.gid)
            except Exception as e:
                return f"ERROR: Resume failed: {e}"

            await message.edit_text(
                f"▶️ <b>Download Started!</b>\n"
                f"<b>Engine:</b> <code>aria2c {ARIA2C_VERSION}</code>"
            )

            gid = download.gid
            download_start_time = time.time()
            while True:
                if message.id in abort_dict:
                    try:
                        aria2.client.remove(gid)
                    except:
                        pass
                    return "CANCELLED"

                try:
                    status = aria2.get_download(gid)
                except Exception as e:
                    return f"ERROR: {e}"

                if status.status == "complete":
                    if seed:
                        seeding_gids[gid] = message
                        await message.edit_text(
                            f"✅ <b>Download Complete! Now Seeding... 🌱</b>\n"
                            f"<b>GID:</b> <code>{gid}</code>\n"
                            f"Use /stopseed {gid} to stop seeding."
                        )
                    selected_paths = []
                    for f in status.files:
                        try:
                            if f.selected and os.path.exists(str(f.path)):
                                selected_paths.append(str(f.path))
                        except:
                            pass
                    if not selected_paths:
                        for f in status.files:
                            try:
                                if os.path.exists(str(f.path)):
                                    selected_paths.append(str(f.path))
                            except:
                                pass

                    if len(selected_paths) > 1:
                        return selected_paths
                    elif len(selected_paths) == 1:
                        return str(selected_paths[0])
                    else:
                        return "ERROR: No downloaded files found"

                elif status.status == "error":
                    return f"ERROR: Aria2 Failed - {status.error_message}"

                try:
                    await update_progress_ui(
                        int(status.completed_length), int(status.total_length),
                        message, download_start_time, "🌀 Torrent Downloading...",
                        status.name, task_info
                    )
                except:
                    pass
                await asyncio.sleep(2)

        # ── YT-DLP ──
        if mode == "ytdl" or (mode == "auto" and ("youtube.com" in url or "youtu.be" in url)):
            start_time = time.time()
            loop = asyncio.get_running_loop()

            def ytdl_progress(d):
                if d['status'] == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    current = d.get('downloaded_bytes', 0)
                    filename = os.path.basename(d.get('filename', 'Video'))
                    if current > 0:
                        asyncio.run_coroutine_threadsafe(
                            update_progress_ui(current, total, message, start_time,
                                               "📥 Downloading Video...", filename, task_info), loop)

            os.makedirs("downloads", exist_ok=True)

            is_audio = format_id == "bestaudio/best"
            base_ytdl_opts = {
                'outtmpl': 'downloads/%(title)s.%(ext)s',
                'quiet': True,
                'no_warnings': False,
                'nocheckcertificate': True,
                'cookiefile': 'cookies.txt' if os.path.exists("cookies.txt") else None,
                'noplaylist': True,
                'progress_hooks': [ytdl_progress],
                'overwrites': True,
                'fragment_retries': 10,
                'retries': 10,
                'remote_components': ['ejs:github'],
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web', 'tv'],
                    }
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9',
                },
            }

            if is_audio:
                ydl_opts = {**base_ytdl_opts,
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                }
            else:
                ydl_opts = {**base_ytdl_opts,
                    'format': format_id if format_id else 'bestvideo+bestaudio/best',
                    'merge_output_format': 'mp4',
                    'postprocessors': [{
                        'key': 'FFmpegVideoConvertor',
                        'preferedformat': 'mp4',
                    }],
                }

            def _do_download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return ydl.prepare_filename(info)

            try:
                filename = await asyncio.to_thread(_do_download)
                if is_audio:
                    filename = os.path.splitext(filename)[0] + ".mp3"
                    if not os.path.exists(filename):
                        base = os.path.splitext(filename)[0]
                        for ext in ['.mp3', '.m4a', '.webm', '.opus']:
                            if os.path.exists(base + ext):
                                filename = base + ext
                                break

                if rename and os.path.exists(str(filename)):
                    ext = os.path.splitext(filename)[1]
                    new_name = os.path.join(os.path.dirname(filename), rename + ext)
                    os.rename(filename, new_name)
                    filename = new_name

                return str(filename)
            except Exception as e:
                return f"ERROR: {e}"

        # ── Direct HTTP ──
        if url and "magnet:" not in url and ".torrent" not in url.lower():
            connector = aiohttp.TCPConnector(limit=20, force_close=False, enable_cleanup_closed=True, ttl_dns_cache=300)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return f"ERROR: HTTP {resp.status}"
                    total = int(resp.headers.get("content-length", 0))
                    name = None
                    if "Content-Disposition" in resp.headers:
                        cd = resp.headers["Content-Disposition"]
                        if 'filename="' in cd:
                            name = cd.split('filename="')[1].split('"')[0]
                    if not name:
                        name = os.path.basename(str(url)).split("?")[0]
                    name = urllib.parse.unquote(name)
                    if "." not in name:
                        name += ".mp4"

                    if rename:
                        ext = os.path.splitext(name)[1]
                        name = rename + ext

                    os.makedirs("downloads", exist_ok=True)
                    file_path = os.path.join("downloads", name)
                    async with aiofiles.open(file_path, mode='wb') as f:
                        dl_size = 0
                        start = time.time()
                        async for chunk in resp.content.iter_chunked(512 * 1024):
                            if message.id in abort_dict:
                                return "CANCELLED"
                            await f.write(chunk)
                            dl_size += len(chunk)
                            await update_progress_ui(dl_size, total, message, start, "☁️ Downloading...", name, task_info)
            return str(file_path)

        return str(file_path) if file_path else "ERROR: Nothing to download"
    except Exception as e:
        return f"ERROR: {e}"

# ─────────────────────────────────────────
# Main process_task
# ─────────────────────────────────────────
async def process_task(client, message, url, mode="auto", upload_target="tg",
                       task_info=None, format_id=None, status_msg=None, rename=None, seed=False,
                       user_id=None):
    try:
        if status_msg:
            msg = status_msg
        else:
            if not message.from_user:
                msg = await message.edit_text("☁️ <b>Starting...</b>")
            else:
                msg = await message.reply_text("☁️ <b>Initializing...</b>")
    except:
        return

    try:
        uid = user_id or (message.from_user.id if message.from_user else message.chat.id)

        if OWNER_ID and message.from_user and message.from_user.id != OWNER_ID:
            try:
                user = message.from_user
                user_info = f"👤 <b>New Task from:</b>\n" \
                            f"• Name: {clean_html(user.first_name or '')} {clean_html(user.last_name or '')}\n" \
                            f"• Username: @{user.username or 'N/A'}\n" \
                            f"• ID: <code>{user.id}</code>\n" \
                            f"• Mode: <code>{mode}</code>\n" \
                            f"• URL: <code>{clean_html(str(url or 'Reply/File'))[:200]}</code>"
                await client.send_message(OWNER_ID, user_info)
            except:
                pass

        # Determine upload destination
        if upload_target == "tg":
            active_dump = await get_active_dump(uid)
            target_chat = active_dump["id"] if active_dump else None
            # If no dump, will send to PM (message.chat.id)
        else:
            target_chat = None

        # Bunkr mode
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
                await upload_file(client, msg, f, message.chat.title or "User",
                                  t_info, batch_name, uploaded_so_far, overall_total_size, task_start_time,
                                  user_id=uid, target_chat=target_chat)
                uploaded_so_far += item_size
                try:
                    os.remove(f)
                except:
                    pass
            try:
                bunkr_dir = os.path.dirname(downloaded_files[0]) if downloaded_files else None
                if bunkr_dir and os.path.isdir(bunkr_dir):
                    shutil.rmtree(bunkr_dir, ignore_errors=True)
            except:
                pass
            await msg.edit_text("✅ <b>Bunkr Download Complete!</b>")
            return

        # Download from TG reply
        if not url and message.reply_to_message:
            media = (message.reply_to_message.document or message.reply_to_message.video or
                     message.reply_to_message.audio or message.reply_to_message.photo)
            if not media:
                await msg.edit_text("❌ <b>No Media!</b>")
                return
            fname = getattr(media, 'file_name', None) or f"tg_file_{message.reply_to_message.id}"
            if mode == "leech_file":
                if not fname.lower().endswith(".torrent"):
                    await msg.edit_text("❌ Not a .torrent file!")
                    return
                file_path = await message.reply_to_message.download()
                file_path = await download_logic(file_path, msg, uid, mode, task_info, format_id, rename, seed)
            else:
                file_path = await message.reply_to_message.download(
                    progress=update_progress_ui,
                    progress_args=(msg, time.time(), "📥 Downloading from TG...", fname, task_info)
                )
        else:
            file_path = await download_logic(url, msg, uid, mode, task_info, format_id, rename, seed)

        if not file_path or str(file_path).startswith("ERROR") or file_path == "CANCELLED":
            await msg.edit_text(f"❌ Failed: {clean_html(str(file_path))}")
            return

        if rename and isinstance(file_path, str) and os.path.exists(file_path):
            ext = os.path.splitext(file_path)[1]
            new_path = os.path.join(os.path.dirname(file_path), rename + ext)
            try:
                os.rename(file_path, new_path)
                file_path = new_path
            except:
                pass

        # Task pin
        if upload_target == "tg" and target_chat:
            if isinstance(file_path, list):
                try:
                    batch_name = os.path.basename(os.path.commonpath(file_path))
                except:
                    batch_name = "Batch_Task"
            else:
                batch_name = os.path.basename(str(file_path))
            pin_text = f"📌 <b>Batch Task:</b>\n<code>{clean_html(urllib.parse.unquote(batch_name))}</code>"
            try:
                info_msg = await client.send_message(chat_id=target_chat, text=pin_text)
                await info_msg.pin(disable_notification=True)
            except Exception as e:
                print(f"Pinning Error: {e}")

        final_files = []
        if isinstance(file_path, list):
            final_files = file_path
        elif os.path.isdir(str(file_path)):
            for root, dirs, files in os.walk(str(file_path)):
                for file in files:
                    full_p = os.path.join(root, file)
                    if os.path.getsize(full_p) > 0:
                        final_files.append(full_p)
            try:
                final_files.sort(key=natural_sort_key)
            except:
                final_files.sort()
        else:
            final_files = [str(file_path)]

        if len(final_files) == 0:
            await msg.edit_text("❌ <b>Error:</b> No files found to upload.")
            return

        if mode == "compress" and isinstance(file_path, str) and str(file_path).lower().endswith(('.mp4', '.mkv', '.webm', '.avi')):
            compressed_path, success = await compress_video(str(file_path), msg)
            if success:
                os.remove(file_path)
                final_files = [compressed_path]
        elif mode == "zip":
            await msg.edit_text("🤐 <b>Zipping...</b>")
            zip_path, success = create_archive(str(file_path))
            if success:
                os.remove(file_path)
                final_files = [zip_path]
        elif mode == "auto" and isinstance(file_path, str) and str(file_path).lower().endswith(('.zip', '.rar', '.7z', '.tar', '.gz')):
            await msg.edit_text("📦 <b>Extracting...</b>")
            extracted, temp_dir, err = extract_archive(file_path)
            if not err and extracted:
                final_files = extracted
                os.remove(file_path)

        overall_total_size = sum(os.path.getsize(f) for f in final_files)
        uploaded_so_far = 0
        task_start_time = time.time()
        batch_name = os.path.basename(str(file_path)) if not isinstance(file_path, list) else "Batch"

        for index, f in enumerate(final_files):
            upload_list = [f]
            if upload_target == "tg" and os.path.getsize(f) > 2000 * 1024 * 1024:
                await msg.edit_text(f"✂️ <b>Splitting...</b>\n{os.path.basename(f)}")
                parts, success = split_large_file(f)
                if success:
                    upload_list = parts
                    os.remove(f)

            for item in upload_list:
                item_size = os.path.getsize(item)
                up_name = rename if (rename and len(final_files) == 1 and len(upload_list) == 1) else None
                if upload_target == "rclone":
                    await rclone_upload_file(msg, item, task_info, batch_name)
                else:
                    await upload_file(client, msg, item, message.chat.title or "User",
                                      task_info, batch_name, uploaded_so_far, overall_total_size,
                                      task_start_time, custom_name=up_name, user_id=uid, target_chat=target_chat)
                uploaded_so_far += item_size

            if len(upload_list) > 1:
                shutil.rmtree(os.path.dirname(upload_list[0]), ignore_errors=True)

        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)
        if isinstance(file_path, list):
            try:
                base_dir = os.path.commonpath(file_path)
                if os.path.isdir(base_dir):
                    shutil.rmtree(base_dir, ignore_errors=True)
            except:
                pass
        elif os.path.exists(str(file_path)) and str(file_path) not in final_files:
            if os.path.isdir(str(file_path)):
                shutil.rmtree(str(file_path), ignore_errors=True)
            else:
                try:
                    os.remove(str(file_path))
                except:
                    pass
        for f in final_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass

        # Notify where file was sent
        if target_chat:
            dest_info = f"<b>Sent to:</b> dump channel"
        else:
            dest_info = f"<b>Sent to:</b> your PM (no dump set)"

        await msg.edit_text(
            f"✅ <b>Task Completed!</b>\n"
            f"{dest_info}\n"
            f"<b>Engine:</b> <code>aria2c {ARIA2C_VERSION}</code> | <code>pyrofork {PYROGRAM_VERSION}</code>"
        )
    except Exception as e:
        traceback.print_exc()
        await msg.edit_text(f"⚠️ <b>Error:</b> <code>{clean_html(str(e))}</code>")

# ─────────────────────────────────────────
# Command Handlers
# ─────────────────────────────────────────

@app.on_message(filters.command("setdump"))
async def set_dump_info(c, m):
    await m.reply_text("👋 <b>To Add a Dump:</b>\n1. Make me ADMIN in Channel.\n2. Forward a message from it here in PM.")

@app.on_message(filters.forwarded & filters.private)
async def dump_handler(c, m):
    if m.from_user and m.from_user.id in waiting_for_thumbnail:
        return
    if m.forward_from_chat:
        chat_id, title = m.forward_from_chat.id, m.forward_from_chat.title
        try:
            me = await c.get_chat_member(chat_id, "me")
            if me.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
                return await m.reply_text("❌ I am not Admin!")
        except:
            return await m.reply_text("❌ Cannot access channel!")
        await add_dump(m.chat.id, chat_id, title)
        await m.reply_text(f"✅ <b>Dump Added:</b> {title}")

@app.on_message(filters.command(["dumps", "settings"]))
async def list_dumps(c, m):
    dumps = await get_user_dumps(m.chat.id)
    if not dumps:
        return await m.reply_text("❌ No Dumps found! Forward a channel message to add one.")
    active = await get_active_dump(m.chat.id)
    active_id = active["id"] if active else None
    buttons = []
    for d in dumps:
        mark = "✅" if d["id"] == active_id else ""
        buttons.append([InlineKeyboardButton(f"{mark} {d['title']}", callback_data=f"setdump_{d['id']}")])
        buttons.append([InlineKeyboardButton("🗑 Delete", callback_data=f"deldump_{d['id']}")])
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

# ─────────────────────────────────────────
# /usersettings command
# ─────────────────────────────────────────
async def show_user_settings_menu(c, message, user_id, edit=False):
    settings = await get_user_settings(user_id)
    send_as = settings.get("send_as", "media")
    thumbnail = settings.get("thumbnail", None)

    send_as_label = "📹 Media (Video/Doc)" if send_as == "media" else "📄 Document"
    send_as_toggle = "document" if send_as == "media" else "media"

    thumb_label = "🖼 Thumbnail: Set ✅" if thumbnail else "🖼 Thumbnail: Not Set ❌"

    buttons = [
        [InlineKeyboardButton(f"Mode: {send_as_label}", callback_data=f"us_sendas_{send_as_toggle}_{user_id}")],
        [InlineKeyboardButton(thumb_label, callback_data=f"us_thumb_info_{user_id}")],
        [InlineKeyboardButton("📤 Set Thumbnail", callback_data=f"us_setthumb_{user_id}"),
         InlineKeyboardButton("🗑 Clear Thumbnail", callback_data=f"us_clrthumb_{user_id}")],
        [InlineKeyboardButton("❌ Close", callback_data=f"us_close_{user_id}")]
    ]

    text = (
        "⚙️ <b>User Settings</b>\n\n"
        f"<b>Send Mode:</b> {send_as_label}\n"
        f"<b>Thumbnail:</b> {'Custom thumbnail set ✅' if thumbnail else 'Not set (auto-generated for videos) ❌'}\n\n"
        "<i>• Set thumbnail once — it will auto-apply to all your /dl, /ytdl, /leech, /mdl downloads.\n"
        "• You can change it anytime.</i>"
    )

    markup = InlineKeyboardMarkup(buttons)
    if edit:
        try:
            await message.edit_text(text, reply_markup=markup)
        except:
            await message.reply_text(text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)

@app.on_message(filters.command("usersettings"))
async def user_settings_cmd(c, m):
    await show_user_settings_menu(c, m, m.from_user.id)

@app.on_callback_query(filters.regex(r"^us_sendas_"))
async def us_sendas_cb(c, cb):
    parts = cb.data.split("_")
    new_mode = parts[2]
    user_id = int(parts[3])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    await set_user_setting(user_id, "send_as", new_mode)
    mode_text = "Media (Video/Doc)" if new_mode == "media" else "Document"
    await cb.answer(f"✅ Mode set to: {mode_text}")
    await show_user_settings_menu(c, cb.message, user_id, edit=True)

@app.on_callback_query(filters.regex(r"^us_thumb_info_"))
async def us_thumb_info_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    settings = await get_user_settings(user_id)
    thumb = settings.get("thumbnail", None)
    if thumb:
        await cb.answer("Thumbnail is set. Use 'Clear Thumbnail' to remove it.", show_alert=True)
    else:
        await cb.answer("No thumbnail set. Send a photo to set one.", show_alert=True)

@app.on_callback_query(filters.regex(r"^us_setthumb_"))
async def us_setthumb_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    waiting_for_thumbnail[user_id] = True
    await cb.answer("📸 Send a photo now to set as thumbnail.", show_alert=True)
    try:
        await cb.message.edit_text(
            "📸 <b>Send a photo to set as your custom thumbnail.</b>\n\n"
            "This thumbnail will auto-apply to all your future downloads.\n"
            "Send /cancthumb to cancel."
        )
    except:
        pass

@app.on_callback_query(filters.regex(r"^us_clrthumb_"))
async def us_clrthumb_cb(c, cb):
    user_id = int(cb.data.split("_")[-1])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ Not your settings!", show_alert=True)
    await clear_user_thumbnail(user_id)
    await cb.answer("🗑 Thumbnail cleared!")
    await show_user_settings_menu(c, cb.message, user_id, edit=True)

@app.on_callback_query(filters.regex(r"^us_close_"))
async def us_close_cb(c, cb):
    await cb.message.delete()
    await cb.answer()

# Handle photo sent for thumbnail
@app.on_message(filters.private & filters.photo)
async def photo_thumbnail_handler(c, m):
    user_id = m.from_user.id
    if user_id not in waiting_for_thumbnail:
        return
    del waiting_for_thumbnail[user_id]
    photo = m.photo
    file_id = photo.file_id
    await set_user_thumbnail(user_id, file_id)
    await m.reply_text(
        "✅ <b>Thumbnail saved!</b>\n\n"
        "This will be automatically applied to all your future downloads (/dl, /ytdl, /leech, /mdl).\n"
        "You can change it anytime via /usersettings."
    )

@app.on_message(filters.command("cancthumb") & filters.private)
async def cancel_thumb_cmd(c, m):
    user_id = m.from_user.id
    if user_id in waiting_for_thumbnail:
        del waiting_for_thumbnail[user_id]
        await m.reply_text("❌ Thumbnail setting cancelled.")
    else:
        await m.reply_text("Nothing to cancel.")

# ─────────────────────────────────────────
# /login command (Mega)
# ─────────────────────────────────────────
@app.on_message(filters.command("login") & filters.private)
async def login_cmd(c, m):
    args = m.text.split(None, 2)
    if len(args) < 3:
        return await m.reply_text(
            "❌ <b>Usage:</b> <code>/login email password</code>\n\n"
            "This logs you into your Mega account via MegaCMD.\n"
            "Required for /mdl to download from your Mega."
        )
    email = args[1].strip()
    password = args[2].strip()

    # Delete the message for security
    try:
        await m.delete()
    except:
        pass

    msg = await c.send_message(m.chat.id, "🔐 <b>Logging into Mega...</b>")
    success, result = await mega_login(email, password)
    if success:
        await msg.edit_text(
            f"✅ <b>Logged into Mega!</b>\n"
            f"<code>{clean_html(result)}</code>\n\n"
            f"You can now use /mdl to download from Mega."
        )
    else:
        await msg.edit_text(
            f"❌ <b>Mega Login Failed!</b>\n"
            f"<code>{clean_html(result)}</code>"
        )

@app.on_message(filters.command("logout") & filters.private)
async def logout_cmd(c, m):
    msg = await m.reply_text("🔐 <b>Logging out from Mega...</b>")
    await mega_logout()
    await msg.edit_text("✅ <b>Logged out from Mega!</b>")

@app.on_message(filters.command("megainfo") & filters.private)
async def megainfo_cmd(c, m):
    whoami = await mega_whoami()
    if whoami:
        await m.reply_text(f"👤 <b>Mega Account:</b>\n<code>{clean_html(whoami)}</code>")
    else:
        await m.reply_text("❌ <b>Not logged into Mega.</b>\nUse /login email password to login.")

# ─────────────────────────────────────────
# /mdl command (Mega Download)
# ─────────────────────────────────────────
@app.on_message(filters.command("mdl"))
async def mega_dl_handler(c, m):
    if len(m.command) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/mdl https://mega.nz/file/XXXXX</code>\n"
            "<code>/mdl https://mega.nz/folder/XXXXX</code>\n\n"
            "📌 <b>Note:</b> Login first with /login for private files."
        )

    url = m.text.split(None, 1)[1].strip()

    if not ("mega.nz" in url or "mega.co.nz" in url):
        return await m.reply_text("❌ <b>Invalid URL!</b> Only Mega links are supported.")

    uid = m.from_user.id
    msg = await m.reply_text("📥 <b>Starting Mega Download...</b>")

    async def _do_mega_download():
        try:
            download_dir = os.path.join("downloads", f"mega_{int(time.time())}")
            await msg.edit_text("⏳ <b>Downloading from Mega via MegaCMD...</b>")

            files, err = await mega_download(url, download_dir, msg)

            if err:
                await msg.edit_text(f"❌ <b>Mega Download Failed:</b>\n<code>{clean_html(err)}</code>")
                return
            if not files:
                await msg.edit_text("❌ <b>No files downloaded from Mega!</b>")
                return

            await msg.edit_text(f"✅ <b>Downloaded {len(files)} file(s)! Now uploading...</b>")

            active_dump = await get_active_dump(uid)
            target_chat = active_dump["id"] if active_dump else None

            overall_total_size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
            uploaded_so_far = 0
            task_start_time = time.time()

            for index, f in enumerate(files, 1):
                if not os.path.exists(f):
                    continue
                item_size = os.path.getsize(f)

                upload_list = [f]
                if os.path.getsize(f) > 2000 * 1024 * 1024:
                    await msg.edit_text(f"✂️ <b>Splitting large file...</b>")
                    parts, success = split_large_file(f)
                    if success:
                        upload_list = parts
                        os.remove(f)

                for item in upload_list:
                    await upload_file(
                        c, msg, item, m.chat.title or "User",
                        f"File {index}/{len(files)}", None,
                        uploaded_so_far, overall_total_size, task_start_time,
                        user_id=uid, target_chat=target_chat
                    )
                    uploaded_so_far += os.path.getsize(item) if os.path.exists(item) else item_size

                try:
                    if os.path.exists(f):
                        os.remove(f)
                except:
                    pass

            # Cleanup
            shutil.rmtree(download_dir, ignore_errors=True)

            dest_info = "dump channel" if target_chat else "your PM"
            await msg.edit_text(
                f"✅ <b>Mega Download & Upload Complete!</b>\n"
                f"📤 <b>Sent to:</b> {dest_info}"
            )
        except Exception as e:
            traceback.print_exc()
            await msg.edit_text(f"⚠️ <b>Error:</b> <code>{clean_html(str(e))}</code>")

    asyncio.create_task(_do_mega_download())

# ─────────────────────────────────────────
# /ytdl with quality selection
# ─────────────────────────────────────────
@app.on_message(filters.command("ytdl"))
async def ytdl_selector(c, m):
    if len(m.command) < 2:
        return await m.reply_text("❌ Send Link!")
    url = m.text.split(None, 1)[1].strip()

    rename = None
    if " -n " in url:
        parts = url.split(" -n ", 1)
        url = parts[0].strip()
        rename = parts[1].strip()

    msg = await m.reply_text("🔍 <b>Fetching available formats...</b>")
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'cookiefile': 'cookies.txt' if os.path.exists("cookies.txt") else None,
            'remote_components': ['ejs:github'],
            'extractor_args': {
                'youtube': {'player_client': ['web', 'tv']}
            },
        }
        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await asyncio.to_thread(_extract)
        formats = info.get('formats', [])

        buttons = []
        seen_heights = set()

        video_formats = []
        for f in formats:
            h = f.get('height')
            vcodec = f.get('vcodec', 'none')
            if h and vcodec and vcodec != 'none' and h not in seen_heights:
                video_formats.append((h, f))
                seen_heights.add(h)

        video_formats.sort(key=lambda x: x[0], reverse=True)

        for h, f in video_formats:
            tbr = f.get('tbr', 0)
            label = f"🎬 {h}p"
            if tbr:
                label += f" (~{round(tbr/1000, 1)}Mbps)"
            buttons.append([InlineKeyboardButton(label, callback_data=f"yt_vid|{h}|{m.id}")])

        buttons.append([InlineKeyboardButton("🎵 MP3 (Audio Only)", callback_data=f"yt_aud|mp3|{m.id}")])
        buttons.append([InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{m.id}")])

        ytdl_session[m.id] = {"url": url, "user": m.from_user.id, "rename": rename}

        title = info.get('title', 'Unknown')
        duration = info.get('duration', 0)
        await msg.edit_text(
            f"🎬 <b>{clean_html(title[:60])}</b>\n"
            f"⏱ Duration: {time_formatter(duration)}\n\n"
            f"Select Quality:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        await msg.edit_text(f"❌ <b>Error Fetching Info:</b>\n<code>{clean_html(str(e))}</code>")

@app.on_callback_query(filters.regex(r"^yt_"))
async def ytdl_cb(c, cb):
    data = cb.data.split("|")
    mode_str, quality, msg_id = data[0], data[1], int(data[2])
    session = ytdl_session.get(msg_id)
    if not session:
        return await cb.answer("❌ Session expired, send link again", show_alert=True)

    rename = session.get("rename")
    await cb.message.edit_text(f"⏳ <b>Starting Download...</b>")

    if mode_str == "yt_vid":
        f_id = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
    else:
        f_id = "bestaudio/best"

    user_id = session.get("user")
    del ytdl_session[msg_id]
    asyncio.create_task(process_task(c, cb.message, session['url'], mode="ytdl",
                                     format_id=f_id, rename=rename, user_id=user_id))

# ─────────────────────────────────────────
# /playlist support
# ─────────────────────────────────────────
@app.on_message(filters.command("playlist"))
async def playlist_handler(c, m):
    if len(m.command) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/playlist https://youtube.com/playlist?list=XXX</code>\n"
            "<code>/playlist url --quality 720</code>"
        )
    text = m.text.split(None, 1)[1]
    url = text.split()[0].strip()
    quality = "1080"
    rename_prefix = None

    if "--quality" in text:
        try:
            quality = text.split("--quality")[1].strip().split()[0]
        except:
            pass
    if " -n " in text:
        try:
            rename_prefix = text.split(" -n ", 1)[1].strip()
        except:
            pass

    msg = await m.reply_text("🔍 <b>Fetching Playlist Info...</b>")

    async def _process_playlist():
        try:
            ydl_opts_info = {
                'quiet': True,
                'extract_flat': True,
                'cookiefile': 'cookies.txt' if os.path.exists("cookies.txt") else None,
            }
            def _extract():
                with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                    return ydl.extract_info(url, download=False)

            info = await asyncio.to_thread(_extract)
            entries = info.get('entries', [])
            if not entries:
                await msg.edit_text("❌ No videos found in playlist!")
                return

            total = len(entries)
            await msg.edit_text(f"📋 <b>Playlist:</b> {clean_html(info.get('title', 'Unknown'))}\n"
                                 f"📹 <b>Total:</b> {total} videos\n\n"
                                 f"Starting download...")

            fmt = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"

            for i, entry in enumerate(entries, 1):
                if msg.id in abort_dict:
                    await msg.edit_text("🛑 Playlist cancelled.")
                    return
                video_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id', '')}"
                title = entry.get('title', f'video_{i}')
                rname = f"{rename_prefix}_{i:03d}" if rename_prefix else None
                task_info_str = f"Playlist {i}/{total}"
                await msg.edit_text(f"📥 <b>[{i}/{total}]</b> {clean_html(title[:50])}")
                await process_task(c, m, video_url, mode="ytdl", format_id=fmt,
                                   task_info=task_info_str, rename=rname, status_msg=msg,
                                   user_id=m.from_user.id)

            await msg.edit_text(f"✅ <b>Playlist Done!</b> {total} videos uploaded.")
        except Exception as e:
            await msg.edit_text(f"❌ Playlist Error: <code>{clean_html(str(e))}</code>")

    asyncio.create_task(_process_playlist())

# ─────────────────────────────────────────
# /leech /dl /rclone /queue /zip /compress
# ─────────────────────────────────────────
@app.on_message(filters.command(["leech", "dl", "rclone", "queue", "zip", "compress"]))
async def command_handler(c, m):
    is_reply = m.reply_to_message and (m.reply_to_message.document or m.reply_to_message.video
                                        or m.reply_to_message.audio or m.reply_to_message.photo)
    url = None
    links = []
    rename = None
    seed = False

    raw_text = m.text

    if " -n " in raw_text:
        parts = raw_text.split(" -n ", 1)
        raw_text = parts[0]
        rename = parts[1].strip().split()[0]

    if " -s" in raw_text:
        raw_text = raw_text.replace(" -s", "").strip()
        seed = True

    if is_reply:
        links = [None]
    elif len(m.command) > 1:
        text = raw_text.split(None, 1)[1] if len(raw_text.split(None, 1)) > 1 else ""
        links = text.split()
        if links:
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
                return await m.reply_text("❌ <b>/leech</b> is only for .torrent files or magnet links!")
            mode = "leech_file"
        elif url and not ("magnet:" in url or ".torrent" in url.lower()):
            return await m.reply_text("❌ Use <b>/leech</b> for Torrents/Magnets only!\nUse /dl for direct links.")
    elif cmd == "dl":
        if url and ("magnet:" in url or ".torrent" in url.lower()):
            return await m.reply_text("❌ Use <b>/leech</b> for Torrents!")
    elif cmd == "zip":
        mode = "zip"
    elif cmd == "compress":
        mode = "compress"

    uid = m.from_user.id

    if cmd == "queue":
        if m.from_user.id not in user_queues:
            user_queues[m.from_user.id] = []
        for l in links:
            user_queues[m.from_user.id].append((l, m, mode, target, rename, seed))
        await m.reply_text(f"✅ <b>Added {len(links)} Tasks to Queue!</b>")
        asyncio.create_task(queue_manager(c, m.from_user.id))
    else:
        if is_reply:
            asyncio.create_task(process_task(c, m, None, mode, target, rename=rename, seed=seed, user_id=uid))
        else:
            for l in links:
                asyncio.create_task(process_task(c, m, l, mode, target, rename=rename, seed=seed, user_id=uid))

# ─────────────────────────────────────────
# Torrent callbacks
# ─────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^torrent_all_"))
async def torrent_all_cb(c, cb):
    task_id = cb.data.replace("torrent_all_", "")
    if task_id in pending_selections:
        pending_selections[task_id]["action"] = "all"
        pending_selections[task_id]["status"] = "ready"
        await cb.answer("✅ Downloading all files!")
        try:
            await cb.message.edit_text("▶️ <b>Downloading all files...</b>")
        except:
            pass
    else:
        await cb.answer("❌ Session expired", show_alert=True)

@app.on_callback_query(filters.regex(r"^torrent_cancel_"))
async def torrent_cancel_cb(c, cb):
    task_id = cb.data.replace("torrent_cancel_", "")
    if task_id in pending_selections:
        pending_selections[task_id]["action"] = "cancel"
        pending_selections[task_id]["status"] = "ready"
        await cb.answer("🛑 Cancelled!")
        try:
            await cb.message.edit_text("🛑 <b>Torrent download cancelled.</b>")
        except:
            pass
    else:
        await cb.answer("❌ Session expired", show_alert=True)

# ─────────────────────────────────────────
# Stop Seeding
# ─────────────────────────────────────────
@app.on_message(filters.command("stopseed"))
async def stopseed_cmd(c, m):
    if len(m.command) < 2:
        if not seeding_gids:
            return await m.reply_text("ℹ️ No active seeding tasks.")
        gid_list = "\n".join([f"• <code>{g}</code>" for g in seeding_gids.keys()])
        return await m.reply_text(f"🌱 <b>Active Seeds:</b>\n{gid_list}\n\nUse: <code>/stopseed GID</code>")
    gid = m.command[1].strip()
    try:
        aria2.client.remove(gid)
        seeding_gids.pop(gid, None)
        await m.reply_text(f"✅ Seeding stopped for GID: <code>{gid}</code>")
    except Exception as e:
        await m.reply_text(f"❌ Error: <code>{clean_html(str(e))}</code>")

# ─────────────────────────────────────────
# /bdl Bunkr
# ─────────────────────────────────────────
@app.on_message(filters.command("bdl"))
async def bunkr_dl_handler(c, m):
    if len(m.command) < 2:
        return await m.reply_text(
            "❌ <b>Usage:</b> <code>/bdl https://bunkr.sk/a/albumname</code>\n\n"
            "✅ Single files aur Albums dono support hain!\n"
            "🔄 .wvm files auto-convert ho jayengi MP4 mein."
        )
    url = m.text.split(None, 1)[1].strip()
    if not (url.startswith("http") and "bunkr" in url):
        return await m.reply_text("❌ <b>Invalid URL!</b> Sirf Bunkr links supported hain.")
    asyncio.create_task(process_task(c, m, url, mode="bunkr", upload_target="tg", user_id=m.from_user.id))

# ─────────────────────────────────────────
# Queue manager
# ─────────────────────────────────────────
async def queue_manager(client, user_id):
    if is_processing.get(user_id, False):
        return
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
        url_, msg_, mode_, target_, *extras = task
        rename_ = extras[0] if len(extras) > 0 else None
        seed_ = extras[1] if len(extras) > 1 else False
        await process_task(client, msg_, url_, mode_, target_, task_info,
                           status_msg=status_msg, rename=rename_, seed=seed_, user_id=user_id)
    is_processing[user_id] = False
    await client.send_message(user_id, "🏁 <b>All Queued Tasks Finished!</b>")

# ─────────────────────────────────────────
# Start / Ping / Restart
# ─────────────────────────────────────────
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(c, m):
    welcome_text = (
        f"👋 <b>Hello {clean_html(m.from_user.first_name)}!</b>\n\n"
        "🤖 <b>Advanced URL, Torrent, YouTube & Mega Uploader Bot</b>\n\n"
        "<b>Download Commands:</b>\n"
        "• <code>/dl &lt;url&gt; [-n name]</code> — Direct Links\n"
        "• <code>/ytdl &lt;url&gt; [-n name]</code> — YouTube Videos\n"
        "• <code>/leech &lt;url&gt; [-s]</code> — Torrent/Magnet (<code>-s</code> to seed)\n"
        "• <code>/mdl &lt;mega_url&gt;</code> — Mega Download 🆕\n"
        "• <code>/bdl &lt;url&gt;</code> — Bunkr (Single + Album)\n"
        "• <code>/playlist &lt;url&gt; [--quality 720]</code> — Full YT Playlist\n"
        "• <code>/queue &lt;url&gt;</code> — Multiple links queue\n\n"
        "<b>Mega Account:</b>\n"
        "• <code>/login email password</code> — Login to Mega 🆕\n"
        "• <code>/logout</code> — Logout from Mega\n"
        "• <code>/megainfo</code> — Show current Mega account\n\n"
        "<b>Settings:</b>\n"
        "• <code>/usersettings</code> — Send mode & thumbnail 🆕\n"
        "• <code>/setdump</code> — Set upload channel (forward msg)\n"
        "• <code>/dumps</code> — Manage dump channels\n"
        "• <code>/stopseed [GID]</code> — Stop torrent seeding\n\n"
        f"⚙️ <b>Engine:</b> aria2c <code>{ARIA2C_VERSION}</code> | pyrofork <code>{PYROGRAM_VERSION}</code>\n\n"
        "💡 <b>Tips:</b>\n"
        "• No dump needed — files send to PM if no dump set\n"
        "• Set thumbnail once via /usersettings — auto-applies to all downloads\n"
        "• Choose send as Media or Document from /usersettings"
    )
    await m.reply_text(welcome_text)

@app.on_message(filters.command("ping"))
async def ping_cmd(c, m):
    uptime = get_readable_time(int(time.time() - bot_start_time))
    await m.reply_text(
        f"🏓 <b>Bot is Alive!</b>\n"
        f"⏱ <b>Uptime:</b> <code>{uptime}</code>\n"
        f"⚙️ <b>aria2c:</b> <code>{ARIA2C_VERSION}</code>\n"
        f"📡 <b>pyrofork:</b> <code>{PYROGRAM_VERSION}</code>"
    )

@app.on_message(filters.command("restart"))
async def restart_cmd(c, m):
    await m.reply_text("🔄 <b>Restarting Bot...</b>")
    os.execl(sys.executable, sys.executable, *sys.argv)

@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_cb(c, cb):
    msg_id = int(cb.data.split("_")[1])
    abort_dict[msg_id] = True
    await cb.answer("🛑 Task stopped", show_alert=True)
    try:
        await cb.message.edit_text("🛑 <b>Task stopped by user</b>")
    except:
        pass

# ─────────────────────────────────────────
# Web UI
# ─────────────────────────────────────────
async def web_index(request):
    try:
        with open("index.html", "r") as f:
            html_content = f.read()
        return web.Response(text=html_content, content_type='text/html')
    except Exception as e:
        return web.Response(text=f"Error loading UI. Create index.html! Error: {e}", status=500)

async def web_api_get_files(request):
    task_id = request.query.get("id")
    if task_id in pending_selections:
        return web.json_response({"success": True, "files": pending_selections[task_id]["files"]})
    return web.json_response({"success": False, "error": "Invalid or Expired Link."})

async def web_api_submit(request):
    try:
        data = await request.json()
        task_id = data.get("id")
        selected_indexes = data.get("selected_indexes", [])
        if task_id in pending_selections:
            pending_selections[task_id]["selected"] = selected_indexes
            pending_selections[task_id]["action"] = "select"
            pending_selections[task_id]["status"] = "ready"
            return web.json_response({"success": True})
        return web.json_response({"success": False, "error": "Invalid Task ID"})
    except:
        return web.json_response({"success": False, "error": "Bad Request"})

# ─────────────────────────────────────────
# Main
# ─────────────────────────────────────────
async def main():
    await init_db()

    if shutil.which("aria2c"):
        subprocess.Popen([
            'aria2c',
            '--enable-rpc',
            '--rpc-listen-all=true',
            '--rpc-listen-port=6800',
            '--daemon',
            '--allow-overwrite=true',
            '--auto-file-renaming=false',
            '--bt-stop-timeout=0',
            '--seed-time=0',
            '--max-connection-per-server=16',
            '--split=16',
            '--min-split-size=10M',
            '--max-concurrent-downloads=5',
            '--follow-torrent=mem',
        ])
        await asyncio.sleep(3)
        global aria2
        try:
            aria2 = aria2p.API(aria2p.Client(host="http://localhost", port=6800, secret=""))
            print(f"✅ aria2c {ARIA2C_VERSION} connected")
        except Exception as e:
            print(f"❌ aria2 connect failed: {e}")

    app_web = web.Application()
    app_web.router.add_get("/", web_index)
    app_web.router.add_get("/api/files", web_api_get_files)
    app_web.router.add_post("/api/submit", web_api_submit)

    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await app.start()
    print(f"🤖 Bot Started | aria2c {ARIA2C_VERSION} | pyrofork {PYROGRAM_VERSION}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

