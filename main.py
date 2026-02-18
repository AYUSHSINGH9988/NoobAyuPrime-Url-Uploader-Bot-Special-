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
import psutil
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

# ==========================================
#         ENVIRONMENT VARIABLES
# ==========================================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")
RCLONE_PATH = os.environ.get("RCLONE_PATH", "remote:")
AUTH_CHAT = int(os.environ.get("AUTH_CHAT", "0"))
PORT = int(os.environ.get("PORT", 8080))

app = Client("ayuprime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=enums.ParseMode.HTML)

# ==========================================
#           DATABASE (MONGODB)
# ==========================================
mongo_client = None
db = None
users_col = None

async def init_db():
    global mongo_client, db, users_col
    if MONGO_URL:
        try:
            mongo_client = AsyncIOMotorClient(MONGO_URL)
            db = mongo_client["URL_Uploader_Bot"]
            users_col = db["users"]
            print("✅ MongoDB Connected!")
        except Exception as e:
            print(f"❌ MongoDB Failed: {e}")

# --- Dump Management Functions ---

async def add_dump(user_id, chat_id, chat_title):
    """Add a new dump to user's list"""
    user = await users_col.find_one({"_id": user_id})
    new_dump = {"id": chat_id, "title": chat_title}
    
    if not user:
        # Create new user entry with this dump as active
        await users_col.insert_one({
            "_id": user_id,
            "dumps": [new_dump],
            "active_dump": chat_id
        })
    else:
        # Check if dump already exists
        dumps = user.get("dumps", [])
        if not any(d["id"] == chat_id for d in dumps):
            dumps.append(new_dump)
            await users_col.update_one(
                {"_id": user_id}, 
                {"$set": {"dumps": dumps}}
            )
            # If no active dump set, set this one
            if not user.get("active_dump"):
                await users_col.update_one({"_id": user_id}, {"$set": {"active_dump": chat_id}})

async def get_user_dumps(user_id):
    """Get list of all dumps"""
    user = await users_col.find_one({"_id": user_id})
    return user.get("dumps", []) if user else []

async def set_active_dump(user_id, chat_id):
    """Set the currently active dump"""
    await users_col.update_one({"_id": user_id}, {"$set": {"active_dump": chat_id}})

async def get_active_dump(user_id):
    """Get the active dump ID and Title"""
    user = await users_col.find_one({"_id": user_id})
    if not user: return None
    
    active_id = user.get("active_dump")
    dumps = user.get("dumps", [])
    
    for d in dumps:
        if d["id"] == active_id:
            return d
    
    # If active not found but dumps exist, return first one
    if dumps:
        await set_active_dump(user_id, dumps[0]["id"])
        return dumps[0]
    return None

async def delete_dump(user_id, chat_id):
    """Delete a dump"""
    user = await users_col.find_one({"_id": user_id})
    if not user: return
    
    dumps = user.get("dumps", [])
    new_dumps = [d for d in dumps if d["id"] != chat_id]
    
    update_data = {"dumps": new_dumps}
    
    # If we deleted the active dump, reset active
    if user.get("active_dump") == chat_id:
        update_data["active_dump"] = new_dumps[0]["id"] if new_dumps else None
        
    await users_col.update_one({"_id": user_id}, {"$set": update_data})

# ==========================================
#           GLOBAL VARIABLES
# ==========================================
BOT_START_TIME = time.time()
abort_dict = {} 
user_queues = {}
is_processing = {}
progress_status = {}
ytdl_session = {} 
aria2 = None

# ==========================================
#           HELPER FUNCTIONS
# ==========================================
def humanbytes(size):
    if not size: return "0B"
    for unit in ['B','KiB','MiB','GiB','TiB']:
        if size < 1024: return f"{round(size, 2)} {unit}"
        size /= 1024

def time_formatter(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = ((f"{days}d " if days else "") + (f"{hours}h " if hours else "") + 
           (f"{minutes}m " if minutes else "") + (f"{seconds}s" if seconds else ""))
    return tmp if tmp else "0s"

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

async def update_progress_ui(current, total, message, start_time, action, filename="Processing...", queue_pos=None):
    if message.id in abort_dict: return 
    now = time.time()
    if (now - progress_status.get(message.id, 0) < 5) and (current != total): return
    progress_status[message.id] = now
    
    perc = current * 100 / total if total > 0 else 0
    speed = current / (now - start_time) if (now - start_time) > 0 else 0
    eta = time_formatter((total - current) / speed) if speed > 0 else "0s"
    bar = '☁️' * int(perc // 10) + '◌' * (10 - int(perc // 10))
    
    text = f"☁️ <b>{action}</b>\n\n📂 <b>File:</b> {clean_html(urllib.parse.unquote(filename))}\n"
    if queue_pos: text += f"🔢 <b>Queue:</b> <code>{queue_pos}</code>\n"
    text += f"{bar}  <code>{round(perc, 1)}%</code>\n💾 <b>Size:</b> {humanbytes(current)} / {humanbytes(total)}\n🚀 <b>Speed:</b> {humanbytes(speed)}/s\n⏳ <b>ETA:</b> {eta}"
    
    try: await message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{message.id}")]])
    except: pass

# ==========================================
#           CORE LOGIC (Extract/Split)
# ==========================================
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
#           UPLOADERS (User Dump Logic)
# ==========================================
async def upload_file(client, message, file_path, user_mention, queue_pos=None):
    try:
        if message.id in abort_dict: return False
        file_path = str(file_path)
        file_name = os.path.basename(file_path)
        thumb_path = None
        if file_name.lower().endswith(('.mp4', '.mkv', '.webm')): thumb_path = await take_screenshot(file_path)
        
        caption = f"☁️ <b>File:</b> {clean_html(file_name)}\n📦 <b>Size:</b> {humanbytes(os.path.getsize(file_path))}\n👤 <b>User:</b> {user_mention}"
        
        # ✅ NEW LOGIC: Get User's Active Dump
        user_id = message.chat.id
        active_dump = await get_active_dump(user_id)
        
        if active_dump:
            target_chat = active_dump["id"]
            upload_status = f"Uploading to {active_dump['title']}..."
            
            try:
                # Upload ONLY to Dump Channel (Not to User PM)
                await client.send_document(
                    chat_id=target_chat, 
                    document=file_path, 
                    caption=caption, 
                    thumb=thumb_path,
                    progress=update_progress_ui, 
                    progress_args=(message, time.time(), upload_status, file_name, queue_pos)
                )
            except Exception as e:
                await message.edit_text(f"❌ <b>Upload Failed!</b>\nMake sure I am Admin in: <b>{active_dump['title']}</b>\nError: {e}")
                return False
        else:
            await message.edit_text("❌ <b>No Dump Selected!</b>\nUse /setdump to add a channel first.")
            return False
        
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        return True
    except: return False

async def rclone_upload_file(message, file_path, queue_pos=None):
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
                try: await message.edit_text(f"☁️ <b>Uploading to Cloud...</b>\n📂 {file_name}\n📊 {match.group(1)}% Done")
                except: pass
                last_update = now
    await process.wait()
    return True

# ==========================================
#           DOWNLOAD LOGIC
# ==========================================
async def download_logic(url, message, user_id, mode, queue_pos=None, format_id=None):
    try:
        file_path = None
        if "pixeldrain.com" in url:
            try:
                file_id = url.split("pixeldrain.com/u/")[1].split("/")[0] if "/u/" in url else url.split("/")[-1]
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"https://pixeldrain.com/api/file/{file_id}/info") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            file_name = data.get("name", f"pixeldrain_{file_id}")
                            url = f"https://pixeldrain.com/api/file/{file_id}"
                            await message.edit_text(f"📥 <b>Downloading PixelDrain...</b>\n<code>{clean_html(file_name)}</code>")
            except: pass

        if url.startswith("magnet:") or url.endswith(".torrent"):
            if not aria2: return "ERROR: Aria2 Not Connected"
            download = aria2.add_magnet(url) if url.startswith("magnet:") else aria2.add_torrent(url)
            gid = download.gid
            while True:
                if message.id in abort_dict: aria2.remove([gid]); return "CANCELLED"
                status = aria2.get_download(gid)
                if status.status == "complete": return str(status.files[0].path)
                elif status.status == "error": return "ERROR: Aria2 Failed"
                await update_progress_ui(int(status.completed_length), int(status.total_length), message, time.time(), "☁️ Downloading...", status.name, queue_pos)
                await asyncio.sleep(2)

        elif mode == "ytdl" or "youtube.com" in url or "youtu.be" in url:
            ydl_opts = {'format': f"{format_id}+bestaudio/best" if format_id else 'bestvideo+bestaudio/best', 'outtmpl': '%(title)s.%(ext)s', 'quiet': True, 'nocheckcertificate': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return str(ydl.prepare_filename(info))
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
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
                    async for chunk in resp.content.iter_chunked(1024*1024):
                        if message.id in abort_dict: await f.close(); return "CANCELLED"
                        await f.write(chunk); dl_size += len(chunk)
                        await update_progress_ui(dl_size, total, message, start, "☁️ Downloading...", name, queue_pos)
                    await f.close()
        return str(file_path)
    except Exception as e: return f"ERROR: {e}"

# ==========================================
#           PROCESSOR
# ==========================================
async def process_task(client, message, url, mode="auto", upload_target="tg", queue_pos=None, format_id=None):
    try: 
        if not message.from_user: msg = await message.edit_text("☁️ <b>Starting...</b>")
        else: msg = await message.reply_text("☁️ <b>Initializing...</b>")
    except: return

    try:
        # Check Dump before download
        if upload_target == "tg":
            active_dump = await get_active_dump(message.chat.id)
            if not active_dump:
                await msg.edit_text("❌ <b>No Dump Selected!</b>\n\nUse /setdump to add a channel.\nForward a message from your channel to set it.")
                return

        if not url and message.reply_to_message:
            media = message.reply_to_message.document or message.reply_to_message.video
            fname = getattr(media, 'file_name', None) or f"tg_file_{int(time.time())}"
            file_path = await message.reply_to_message.download(progress=update_progress_ui, progress_args=(msg, time.time(), "📥 Downloading...", fname, queue_pos))
        else:
            file_path = await download_logic(url, msg, message.chat.id, mode, queue_pos, format_id)

        if not file_path or str(file_path).startswith("ERROR") or file_path == "CANCELLED":
            await msg.edit_text(f"❌ Failed: {file_path}")
            return

        final_files = [str(file_path)]
        is_archive = str(file_path).lower().endswith(('.zip','.rar','.7z','.tar','.gz'))
        if is_archive:
            await msg.edit_text("📦 <b>Extracting...</b>")
            extracted, temp_dir, err = extract_archive(file_path)
            if not err and extracted: final_files = extracted; os.remove(file_path)
        
        for f in final_files:
            upload_list = [f]
            if upload_target == "tg" and os.path.getsize(f) > 2000*1024*1024:
                await msg.edit_text(f"✂️ <b>Splitting...</b>\n{os.path.basename(f)}")
                parts, success = split_large_file(f)
                if success: upload_list = parts; os.remove(f)

            for item in upload_list:
                if upload_target == "rclone": await rclone_upload_file(msg, item, queue_pos)
                else: await upload_file(client, msg, item, message.chat.title or "User", queue_pos)
            
            if len(upload_list) > 1: shutil.rmtree(os.path.dirname(upload_list[0]), ignore_errors=True)
        
        if is_archive: shutil.rmtree(temp_dir, ignore_errors=True)
        elif os.path.exists(str(file_path)): os.remove(str(file_path))

        await msg.edit_text("✅ <b>Task Completed!</b>")
    except Exception as e:
        traceback.print_exc()
        await msg.edit_text(f"⚠️ Error: {e}")

# ==========================================
#           DUMP MANAGEMENT COMMANDS
# ==========================================
@app.on_message(filters.command("setdump"))
async def set_dump_info(c, m):
    await m.reply_text("👋 <b>To Add a Dump:</b>\n\n1. Add me as ADMIN in your Channel/Group.\n2. <b>Forward</b> a message from that Channel/Group to me here.\n\nI will automatically save it as your dump.")

@app.on_message(filters.forwarded & filters.private)
async def dump_handler(c, m):
    if m.forward_from_chat:
        chat_id = m.forward_from_chat.id
        title = m.forward_from_chat.title
        
        # Verify Admin
        try:
            me = await c.get_chat_member(chat_id, "me")
            if me.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
                return await m.reply_text(f"❌ I am not Admin in <b>{title}</b>!")
        except:
             return await m.reply_text(f"❌ I cannot access <b>{title}</b>. Make me admin first!")

        await add_dump(m.chat.id, chat_id, title)
        await m.reply_text(f"✅ <b>Dump Added!</b>\n\n📢 <b>{title}</b>\n\nIt is now set as your Active Dump.")

@app.on_message(filters.command(["dumps", "settings"]))
async def list_dumps(c, m):
    dumps = await get_user_dumps(m.chat.id)
    if not dumps: return await m.reply_text("❌ No Dumps found! Forward a channel post to add one.")
    
    active = await get_active_dump(m.chat.id)
    active_id = active["id"] if active else None
    
    buttons = []
    for d in dumps:
        mark = "✅" if d["id"] == active_id else ""
        buttons.append([InlineKeyboardButton(f"{mark} {d['title']}", callback_data=f"setdump_{d['id']}")])
        buttons.append([InlineKeyboardButton(f"🗑 Delete {d['title']}", callback_data=f"deldump_{d['id']}")])
    
    await m.reply_text("⚙️ <b>Your Dumps</b>\nSelect one to make it Active:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex(r"setdump_"))
async def set_active_cb(c, cb):
    chat_id = int(cb.data.split("_")[1])
    await set_active_dump(cb.message.chat.id, chat_id)
    
      # Refresh List
    dumps = await get_user_dumps(cb.message.chat.id)
    buttons = []
    for d in dumps:
        mark = "✅" if d["id"] == chat_id else ""
        buttons.append([InlineKeyboardButton(f"{mark} {d['title']}", callback_data=f"setdump_{d['id']}")])
        buttons.append([InlineKeyboardButton(f"🗑 Delete {d['title']}", callback_data=f"deldump_{d['id']}")])
    
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex(r"deldump_"))
async def del_dump_cb(c, cb):
    chat_id = int(cb.data.split("_")[1])
    await delete_dump(cb.message.chat.id, chat_id)
    await cb.answer("Deleted!")
    
    # Refresh List
    dumps = await get_user_dumps(cb.message.chat.id)
    if not dumps: return await cb.message.edit_text("❌ No Dumps left.")
    
    active = await get_active_dump(cb.message.chat.id)
    active_id = active["id"] if active else None
    
    buttons = []
    for d in dumps:
        mark = "✅" if d["id"] == active_id else ""
        buttons.append([InlineKeyboardButton(f"{mark} {d['title']}", callback_data=f"setdump_{d['id']}")])
        buttons.append([InlineKeyboardButton(f"🗑 Delete {d['title']}", callback_data=f"deldump_{d['id']}")])
    
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))

# ==========================================
#           YTDL & COMMANDS
# ==========================================
@app.on_message(filters.command("ytdl"))
async def ytdl_selector(c, m):
    if len(m.command) < 2: return await m.reply_text("❌ Send Link!")
    url = m.text.split(None, 1)[1]
    msg = await m.reply_text("🔍 <b>Fetching Formats...</b>")
    try:
        ydl_opts = {'listformats': True, 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats, title = info.get('formats', []), info.get('title', 'Video')
        buttons = []
        seen = set()
        for f in formats:
            h = f.get('height')
            if h and h not in seen and f.get('ext') == 'mp4':
                size_str = humanbytes(f.get('filesize', 0))
                buttons.append([InlineKeyboardButton(f"🎬 {h}p ({size_str})", callback_data=f"yt_vid|{h}|{m.id}")])
                seen.add(h)
        buttons.append([InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data=f"yt_aud|mp3|{m.id}")])
        buttons.append([InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{m.id}")])
        ytdl_session[m.id] = {"url": url, "user": m.from_user.id}
        await msg.edit_text(f"📺 <b>{clean_html(title)}</b>", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e: await msg.edit_text(f"❌ Error: {e}")

@app.on_callback_query(filters.regex(r"^yt_"))
async def ytdl_cb(c, cb):
    data = cb.data.split("|")
    mode, quality, msg_id = data[0], data[1], int(data[2])
    session = ytdl_session.get(msg_id)
    if not session: return await cb.answer("❌ Expired", show_alert=True)
    await cb.message.edit_text(f"⏳ <b>Queued: {quality}...</b>")
    f_id = f"bestvideo[height<={quality}]" if mode == "yt_vid" else "bestaudio"
    asyncio.create_task(process_task(c, cb.message, session['url'], mode="ytdl", format_id=f_id))

@app.on_message(filters.command(["leech", "dl", "rclone", "queue"]))
async def command_handler(c, m):
    if not m.reply_to_message and len(m.command) < 2: return await m.reply_text("❌ Send Link!")
    text = m.reply_to_message.text if m.reply_to_message else m.text.split(None, 1)[1]
    cmd = m.command[0]
    target = "rclone" if cmd == "rclone" else "tg"
    mode = "ytdl" if cmd == "ytdl" else "auto"
    
    if cmd == "queue":
        if m.from_user.id not in user_queues: user_queues[m.from_user.id] = []
        for l in text.split(): user_queues[m.from_user.id].append((l, m, mode, target))
        await m.reply_text(f"✅ <b>Added {len(text.split())} Links!</b>")
        asyncio.create_task(queue_manager(c, m.from_user.id))
    else:
        for l in text.split(): asyncio.create_task(process_task(c, m, l, mode, target))

async def queue_manager(client, user_id):
    if is_processing.get(user_id, False): return
    is_processing[user_id] = True
    while user_queues.get(user_id):
        task = user_queues[user_id].pop(0)
        q_text = f"Task {1}/{len(user_queues[user_id])+1}"
        await process_task(client, task[1], task[0], task[2], task[3], q_text)
    is_processing[user_id] = False
    await client.send_message(user_id, "🏁 <b>Queue Finished!</b>")

@app.on_callback_query(filters.regex(r"cancel_"))
async def cancel(c, cb): abort_dict[cb.message.id]=True; await cb.answer("🛑 Cancelled")

@app.on_message(filters.command("start"))
async def start_cmd(c, m): await m.reply_text("👋 <b>Bot Active!</b>\nUse /setdump to setup your channel.")

async def main():
    await init_db()
    if shutil.which("aria2c"):
        subprocess.Popen(['aria2c', '--enable-rpc', '--rpc-listen-port=6800', '--daemon', '--seed-time=0', '--allow-overwrite=true'])
        await asyncio.sleep(3)
        global aria2
        aria2 = aria2p.API(aria2p.Client(host="http://localhost", port=6800, secret=""))
    app_web = web.Application()
    app_web.router.add_get("/", lambda r: web.Response(text="Running"))
    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await app.start()
    print("🤖 Bot Started")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())