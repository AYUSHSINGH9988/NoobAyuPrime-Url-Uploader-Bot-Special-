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
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
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

# Dump Channel Configuration
DUMP_CHANNEL = 0
try:
    dump_id = str(os.environ.get("DUMP_CHANNEL", os.environ.get("LOG_CHANNEL", "0"))).strip()
    if dump_id == "0":
        DUMP_CHANNEL = 0
    elif dump_id.startswith("-100"):
        DUMP_CHANNEL = int(dump_id)
    elif dump_id.startswith("-"):
        DUMP_CHANNEL = int(f"-100{dump_id[1:]}")
    else:
        DUMP_CHANNEL = int(f"-100{dump_id}")
    print(f"✅ Dump Channel Configured: {DUMP_CHANNEL}")
except Exception as e:
    print(f"⚠️ Error parsing DUMP_CHANNEL: {e}")
    DUMP_CHANNEL = 0

PORT = int(os.environ.get("PORT", 8080))

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=enums.ParseMode.HTML)

# ==========================================
#           GLOBAL VARIABLES
# ==========================================
abort_dict = {} 
user_queues = {}
is_processing = {}
progress_status = {} 
YTDLP_LIMIT = 2000 * 1024 * 1024 

mongo_client = None
mongo_db = None
users_col = None
aria2 = None

# ==========================================
#           HELPER FUNCTIONS
# ==========================================
def humanbytes(size):
    if not size: return "0B"
    power = 2**10
    n = 0
    dic = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power: 
        size /= power
        n += 1
    return str(round(size, 2)) + " " + dic[n] + 'B'

def time_formatter(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return "{:02d}:{:02d}:{:02d}".format(int(hours), int(minutes), int(seconds))

def clean_html(text):
    if not text: return ""
    return str(text).replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

# ✅ Natural Sorting (1, 2, 10...)
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

# ==========================================
#           PROGRESS BAR
# ==========================================
async def update_progress_ui(current, total, message, start_time, action, filename="Processing...", queue_pos=None):
    if message.id in abort_dict: return 
    
    now = time.time()
    last_update = progress_status.get(message.id, 0)
    if (now - last_update < 5) and (current != total): return
    progress_status[message.id] = now
    
    percentage = current * 100 / total if total > 0 else 0
    speed = current / (now - start_time) if (now - start_time) > 0 else 0
    eta = round((total - current) / speed) if speed > 0 else 0
    
    filled = int(percentage // 10)
    bar = '☁️' * filled + '◌' * (10 - filled)
    display_name = urllib.parse.unquote(filename)
    
    text = f"☁️ <a href='tg://user?id={message.chat.id}'>Powered by Ayuprime</a>\n\n📂 <b>File:</b> {clean_html(display_name)}\n"
    if queue_pos: text += f"🔢 <b>Queue:</b> <code>{queue_pos}</code>\n"
    text += f"<b>{action}</b>\n{bar}  <code>{round(percentage, 1)}%</code>\n💾 <b>Size:</b> <code>{humanbytes(current)}</code> / <code>{humanbytes(total)}</code>\n🚀 <b>Speed:</b> <code>{humanbytes(speed)}/s</code>\n⏳ <b>ETA:</b> <code>{time_formatter(eta)}</code>"
    
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{message.id}")]])
    try: await message.edit_text(text, reply_markup=buttons)
    except: pass

# ==========================================
#           CORE LOGIC (Extraction)
# ==========================================
def extract_archive(file_path):
    output_dir = f"extracted_{int(time.time())}"
    if not os.path.exists(output_dir): os.makedirs(output_dir)
    
    if not shutil.which("7z"): return [], None, "7z missing!"

    # Smart Extraction for split files (.001) handled by 7z automatically
    cmd = ["7z", "x", file_path, f"-o{output_dir}", "-y"]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    if process.returncode != 0: 
        return [], output_dir, f"Extraction Error: {process.stderr.decode()}"

    files_list = []
    for root, dirs, files in os.walk(output_dir):
        for file in files: files_list.append(os.path.join(root, file))
    
    # ✅ Sort files naturally
    files_list.sort(key=natural_sort_key)
    return files_list, output_dir, None

def get_files_from_folder(folder_path):
    files_list = []
    for root, dirs, files in os.walk(folder_path):
        for file in files: files_list.append(os.path.join(root, file))
    files_list.sort(key=natural_sort_key)
    return files_list

# ==========================================
#           UPLOADERS (TG & RCLONE)
# ==========================================
async def upload_file(client, message, file_path, user_mention, queue_pos=None):
    try:
        if message.id in abort_dict: return False
        
        file_path = str(file_path)
        file_name = os.path.basename(file_path)
        thumb_path = None
        is_video = file_name.lower().endswith(('.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv'))
        if is_video: thumb_path = await take_screenshot(file_path)
        
        caption = f"☁️ <b>File:</b> {clean_html(file_name)}\n📦 <b>Size:</b> <code>{humanbytes(os.path.getsize(file_path))}</code>\n👤 <b>User:</b> {user_mention}"
        target_chat_id = DUMP_CHANNEL if DUMP_CHANNEL != 0 else message.chat.id
        upload_status = "☁️ Uploading..."

        try:
            sent_msg = await client.send_document(
                chat_id=target_chat_id, document=file_path, caption=caption, thumb=thumb_path, 
                progress=update_progress_ui, progress_args=(message, time.time(), upload_status, file_name, queue_pos)
            )
            # If uploaded to Dump, notify user
            if DUMP_CHANNEL != 0:
                link = f"https://t.me/c/{str(DUMP_CHANNEL)[4:]}/{sent_msg.id}"
                await message.edit_text(f"✅ <b>Uploaded to Dump!</b>\n\n📂 {clean_html(file_name)}\n🔗 <a href='{link}'>View File</a>", disable_web_page_preview=True)
            else:
                await message.delete() # Clean up status message in private
        except Exception as e:
            await message.edit_text(f"❌ Upload Failed: {e}")
            return False
        
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        return True
    except: return False

async def rclone_upload_file(message, file_path, queue_pos=None):
    if message.id in abort_dict: return False
    file_name = os.path.basename(file_path)
    if not os.path.exists("rclone.conf"): 
        await message.edit_text("❌ rclone.conf missing!") 
        return False

    display_name = clean_html(file_name)
    await message.edit_text(f"🚀 <b>Rclone Uploading...</b>\nFile: {display_name}")
    
    cmd = ["rclone", "copy", file_path, RCLONE_PATH, "--config", "rclone.conf", "-P"]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    last_update = 0
    while True:
        if message.id in abort_dict: 
            process.kill()
            await message.edit_text("❌ Upload Cancelled.")
            return False
        line = await process.stdout.readline()
        if not line: break
        
        decoded = line.decode().strip()
        now = time.time()
        if "%" in decoded and (now - last_update) > 5:
            match = re.search(r"(\d+)%", decoded)
            if match:
                text = f"🚀 <b>Rclone Upload...</b>\n📂 {display_name}\n📊 {match.group(1)}% Done"
                try: await message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{message.id}")]]))
                except: pass
                last_update = now

    await process.wait()
    if process.returncode == 0: 
        await message.edit_text(f"✅ <b>Rclone Uploaded!</b>\nFile: {display_name}")
        return True
    return False

# ==========================================
#           DOWNLOAD LOGIC
# ==========================================
async def download_logic(url, message, user_id, mode, queue_pos=None):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        file_path = None
        
        # 1. Aria2 (Magnet/Torrent)
        if url.startswith("magnet:") or url.endswith(".torrent"):
            if not aria2: return "ERROR: Aria2 Not Connected"
            if url.startswith("magnet:"): download = aria2.add_magnet(url)
            else: 
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers) as resp:
                        with open("task.torrent", "wb") as f: f.write(await resp.read())
                download = aria2.add_torrent("task.torrent")
            
            gid = download.gid
            while True:
                if message.id in abort_dict: 
                    aria2.remove([gid])
                    return "CANCELLED"
                
                status = aria2.get_download(gid)
                if status.status == "complete": 
                    file_path = str(status.files[0].path)
                    break
                elif status.status == "error": return "ERROR: Aria2 Failed"
                
                # Stop Seeding immediately logic (covered by --seed-time=0 in startup)
                if status.total_length > 0 and status.completed_length >= status.total_length:
                     file_path = str(status.files[0].path)
                     break

                await update_progress_ui(int(status.completed_length), int(status.total_length), message, time.time(), "☁️ Torrent Downloading...", status.name, queue_pos)
                await asyncio.sleep(2)

        # 2. YT-DLP (Only if Mode=ytdl)
        elif mode == "ytdl":
             ydl_opts = {'format': 'best', 'outtmpl': '%(title)s.%(ext)s', 'quiet': True}
             with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                 info = ydl.extract_info(url, download=True)
                 file_path = ydl.prepare_filename(info)

        # 3. Direct HTTP
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200: return f"ERROR: HTTP {resp.status}"
                    total = int(resp.headers.get("content-length", 0))
                    name = os.path.basename(str(url)).split("?")[0]
                    name = urllib.parse.unquote(name)
                    if "." not in name: name += ".mp4"
                    file_path = name
                    
                    f = await aiofiles.open(file_path, mode='wb')
                    dl_size = 0
                    start = time.time()
                    async for chunk in resp.content.iter_chunked(1024*1024):
                        if message.id in abort_dict: 
                            await f.close()
                            return "CANCELLED"
                        await f.write(chunk)
                        dl_size += len(chunk)
                        await update_progress_ui(dl_size, total, message, start, "☁️ Downloading...", file_path, queue_pos)
                    await f.close()
        
        return str(file_path)
    except Exception as e: return f"ERROR: {e}"

# ==========================================
#           PROCESSOR
# ==========================================
async def process_task(client, message, url, mode="auto", upload_target="tg", queue_pos=None):
    try: msg = await message.reply_text("☁️ <b>Initializing Task...</b>")
    except: return

    try:
        # --- DOWNLOAD STEP ---
        file_path = None
        
        # Case A: Telegram File Reply
        if not url and message.reply_to_message:
            media = message.reply_to_message.document or message.reply_to_message.video
            fname = media.file_name if hasattr(media, 'file_name') else f"tg_file_{int(time.time())}"
            file_path = fname
            await msg.edit_text(f"📥 <b>Downloading from TG...</b>\n<code>{clean_html(fname)}</code>")
            await message.reply_to_message.download(file_name=file_path, progress=update_progress_ui, progress_args=(msg, time.time(), "📥 Downloading...", fname, queue_pos))
        
        # Case B: URL Download
        elif url:
            file_path = await download_logic(url, msg, message.from_user.id, mode, queue_pos)
        
        # Check Success
        if not file_path or str(file_path).startswith("ERROR") or file_path == "CANCELLED":
            await msg.edit_text(f"❌ Failed: {file_path}")
            return

        # --- EXTRACT CHECK ---
        final_files = []
        is_extracted = False
        original_name = os.path.basename(str(file_path))
        
        # Check for Archive (Zip/Rar/7z/001)
        # Regex to check for .001 type split files or normal archives
        is_archive = str(file_path).lower().endswith((".zip", ".rar", ".7z", ".tar", ".gz")) or re.search(r'\.\d{3}$', str(file_path))

        if is_archive:
            await msg.edit_text(f"📦 <b>Extracting Archive...</b>")
            extracted_list, temp_dir, error_msg = extract_archive(file_path)
            
            if not error_msg and extracted_list:
                final_files = extracted_list # Sorted Naturally
                is_extracted = True
                os.remove(file_path) # Delete original zip
            else:
                final_files = [file_path] # Extract failed, upload as is
        else:
            final_files = [file_path]

        # --- PIN HEADER (DUMP) ---
        if DUMP_CHANNEL != 0:
            try:
                pin_title = os.path.splitext(original_name)[0]
                pin_msg = await client.send_message(DUMP_CHANNEL, f"📌 <b>Batch Upload Started:</b>\n<code>{clean_html(pin_title)}</code>")
                await pin_msg.pin(both_sides=True)
            except Exception as e: print(f"Pin Error: {e}")

        # --- UPLOAD ---
        if upload_target == "rclone":
            for f in final_files: await rclone_upload_file(msg, f, queue_pos)
        else:
            await msg.edit_text(f"☁️ <b>Uploading {len(final_files)} Files...</b>")
            for index, f in enumerate(final_files):
                if message.id in abort_dict: break
                current_status = f"{index+1}/{len(final_files)}"
                await upload_file(client, msg, f, message.from_user.mention, current_status)
                await asyncio.sleep(2) # Floodwait Safety
        
        # --- CLEANUP ---
        if is_extracted: shutil.rmtree(temp_dir, ignore_errors=True)
        elif os.path.isfile(file_path): os.remove(file_path)
        
        if message.id not in abort_dict:
            await msg.edit_text("✅ <b>Task Completed!</b>")
            
    except Exception as e:
        traceback.print_exc()
        await msg.edit_text(f"⚠️ Error: {e}")

# ==========================================
#           QUEUE MANAGER
# ==========================================
async def queue_manager(client, user_id):
    if is_processing.get(user_id, False): return
    is_processing[user_id] = True
    
    while user_queues.get(user_id):
        # Task Structure: (Link, Message, Mode, Target)
        task = user_queues[user_id].pop(0)
        link, msg_obj, mode, target = task
        
        q_text = f"1/{len(user_queues[user_id])+1}"
        # Await ensures sequential processing
        await process_task(client, msg_obj, link, mode, target, q_text)
        
    is_processing[user_id] = False
    await client.send_message(user_id, "✅ <b>All Queue Tasks Finished!</b>")

# ==========================================
#           COMMANDS
# ==========================================
@app.on_message(filters.command(["leech", "rclone", "queue", "ytdl"]))
async def command_handler(c, m):
    # Determine Command Details
    cmd = m.command[0]
    target = "rclone" if cmd == "rclone" else "tg"
    mode = "ytdl" if cmd == "ytdl" else "auto"
    
    # Check for Reply (Telegram File) or Link
    is_reply = m.reply_to_message and (m.reply_to_message.document or m.reply_to_message.video)
    
    links = []
    if is_reply:
        links = [None] # None indicates Reply Message Processing
    elif len(m.command) > 1:
        text = m.text.split(None, 1)[1]
        links = text.split()
    else:
        return await m.reply_text("❌ Send Link or Reply to File!")

    # Add to Queue
    user_id = m.from_user.id
    if user_id not in user_queues: user_queues[user_id] = []
    
    for l in links:
        user_queues[user_id].append((l, m, mode, target))
    
    await m.reply_text(f"✅ <b>Added {len(links)} Tasks to Queue!</b>")
    asyncio.create_task(queue_manager(c, user_id))

@app.on_callback_query(filters.regex(r"cancel_(\d+)"))
async def cancel_handler(c, cb):
    msg_id = int(cb.data.split("_")[1])
    abort_dict[msg_id] = True
    await cb.answer("🛑 Cancelling...")

@app.on_message(filters.command("start"))
async def start_cmd(c, m):
    await m.reply_text("👋 <b>Advanced Uploader Bot</b>\n\nSupports: URL, Magnet, Telegram Files, Rclone.\n/leech, /rclone, /queue, /ytdl")

# ==========================================
#           MAIN RUNNER
# ==========================================
async def main():
    # Mongo
    if MONGO_URL:
        try:
            global mongo_client
            mongo_client = AsyncIOMotorClient(MONGO_URL)
            print("✅ MongoDB Connected")
        except: print("❌ Mongo Failed")

    # Aria2
    global aria2
    if shutil.which("aria2c"):
        subprocess.Popen(['aria2c', '--enable-rpc', '--rpc-listen-port=6800', '--daemon', '--seed-time=0', '--max-connection-per-server=10', '--allow-overwrite=true'])
        await asyncio.sleep(4)
        aria2 = aria2p.API(aria2p.Client(host="http://localhost", port=6800, secret=""))
        print("✅ Aria2 Started")

    # Web Server
    web_app = web.Application()
    web_app.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await app.start()
    print("🤖 Bot Started")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
