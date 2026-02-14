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
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

# ==========================================
#         ENVIRONMENT VARIABLES
# ==========================================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Dump Channel Parsing
try:
    dump_id = str(os.environ.get("DUMP_CHANNEL", "0")).strip()
    if dump_id.startswith("-100"):
        DUMP_CHANNEL = int(dump_id)
    elif dump_id.startswith("-"):
        DUMP_CHANNEL = int(f"-100{dump_id[1:]}")
    else:
        DUMP_CHANNEL = int(f"-100{dump_id}") if dump_id != "0" else 0
except:
    DUMP_CHANNEL = 0

PORT = int(os.environ.get("PORT", 8080))

app = Client("url_uploader", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=enums.ParseMode.HTML)

# ==========================================
#           GLOBAL VARIABLES
# ==========================================
aria2 = None
task_queue = []
is_processing = False
YTDLP_LIMIT = 2000 * 1024 * 1024  # 2GB Limit

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

def time_formatter(seconds):
    return time.strftime("%H:%M:%S", time.gmtime(seconds))

def clean_html(text):
    return str(text).replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('(\d+)', s)]

async def take_screenshot(video_path):
    thumb_path = f"{video_path}.jpg"
    cmd = ["ffmpeg", "-ss", "00:00:01", "-i", video_path, "-vframes", "1", "-q:v", "2", thumb_path, "-y"]
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await process.wait()
        if os.path.exists(thumb_path): return thumb_path
    except: pass
    return None

# ==========================================
#           PROGRESS BAR
# ==========================================
last_update_time = {}

async def progress_bar(current, total, message, start_time, status_text):
    now = time.time()
    if message.id in last_update_time and (now - last_update_time[message.id]) < 4:
        return
    last_update_time[message.id] = now

    percentage = current * 100 / total if total > 0 else 0
    speed = current / (now - start_time) if (now - start_time) > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0

    bar = '▓' * int(percentage // 10) + '░' * (10 - int(percentage // 10))

    text = f"""
<b>{status_text}</b>
{bar} <code>{round(percentage, 1)}%</code>

💾 <b>Size:</b> <code>{humanbytes(current)} / {humanbytes(total)}</code>
🚀 <b>Speed:</b> <code>{humanbytes(speed)}/s</code>
⏳ <b>ETA:</b> <code>{time_formatter(eta)}</code>
    """
    try: await message.edit_text(text)
    except: pass

# ==========================================
#           DOWNLOAD LOGIC (FIXED FILENAME)
# ==========================================
async def download_logic(url, message):
    try:
        file_path = None
        
        # 1. Magnet / Torrent
        if url.startswith("magnet:") or url.endswith(".torrent"):
            if not aria2: return None, "❌ Aria2 not running!"
            try:
                if url.startswith("magnet:"): download = aria2.add_magnet(url)
                else: 
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url) as resp:
                            if resp.status != 200: return None, "❌ Torrent Download Failed"
                            with open("task.torrent", "wb") as f: f.write(await resp.read())
                    download = aria2.add_torrent("task.torrent")
                
                while not download.is_complete:
                    download.update()
                    if download.status == "error": return None, "❌ Aria2 Error"
                    await progress_bar(download.completed_length, download.total_length, message, time.time(), "📥 Downloading Torrent...")
                    await asyncio.sleep(2)
                
                if download.status == "complete":
                    file_path = download.files[0].path
            except Exception as e: return None, f"❌ Aria2 Error: {e}"

        # 2. Universal YT-DLP (First Try)
        # Check if URL looks like a direct file (ends with extension)
        is_direct = any(url.lower().endswith(ext) for ext in ['.mp4', '.mkv', '.zip', '.rar', '.7z', '.iso', '.pdf', '.jpg', '.png'])
        
        # If NOT direct file, try YT-DLP first
        if not is_direct and not file_path:
            try:
                ydl_opts = {
                    'format': 'bestvideo+bestaudio/best',
                    'outtmpl': '%(title)s.%(ext)s',
                    'noplaylist': True,
                    'quiet': True,
                    'writethumbnail': True
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info.get('filesize', 0) > YTDLP_LIMIT: return None, "❌ File too big for Bot (2GB+)"
                    
                    await message.edit_text("📥 <b>Downloading via YT-DLP...</b> ⚡")
                    ydl.download([url])
                    file_path = ydl.prepare_filename(info)
            except:
                file_path = None # Fallback to HTTP

        # 3. Direct HTTP (Fallback & Direct Files) - FILENAME FIX HERE
        if not file_path:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200: return None, f"❌ HTTP Error: {resp.status}"
                    
                    # --- FILENAME DETECTION LOGIC ---
                    name = None
                    
                    # A) Check Content-Disposition Header
                    if "Content-Disposition" in resp.headers:
                        cd = resp.headers["Content-Disposition"]
                        if 'filename="' in cd:
                            name = cd.split('filename="')[1].split('"')[0]
                        elif "filename=" in cd:
                            name = cd.split("filename=")[1].split(";")[0]

                    # B) Check URL Path (Removes ?query=params)
                    if not name:
                        parsed_url = urllib.parse.urlparse(url)
                        name = os.path.basename(parsed_url.path)

                    # C) URL Decode (Convert %20 to space)
                    name = urllib.parse.unquote(name)

                    # D) Fallback if empty
                    if not name: name = f"download_{int(time.time())}"

                    # E) Ensure Extension
                    if "." not in name:
                        ct = resp.headers.get("Content-Type")
                        ext = mimetypes.guess_extension(ct)
                        if ext: name += ext
                        else: name += ".mp4" # Last resort
                    
                    file_path = f"downloads/{name}"
                    os.makedirs("downloads", exist_ok=True)
                    
                    total = int(resp.headers.get('content-length', 0))
                    f = await aiofiles.open(file_path, mode='wb')
                    dl_size = 0
                    start_time = time.time()
                    
                    async for chunk in resp.content.iter_chunked(1024*1024):
                        await f.write(chunk)
                        dl_size += len(chunk)
                        await progress_bar(dl_size, total, message, start_time, "📥 Downloading HTTP...")
                    await f.close()

        return file_path, None

    except Exception as e:
        traceback.print_exc()
        return None, f"❌ Error: {e}"

# ==========================================
#           PROCESSOR
# ==========================================
async def process_queue():
    global is_processing
    is_processing = True

    while task_queue:
        client, message, link, is_reply_file = task_queue.pop(0)
        msg = await message.reply_text(f"♻️ <b>Processing Task...</b> ⚙️\n**Queue Left:** `{len(task_queue)}`")
        file_path = None
        error = None

        try:
            # --- DOWNLOAD ---
            if is_reply_file:
                media = message.reply_to_message.document or message.reply_to_message.video or message.reply_to_message.audio
                # Filename fix for TG files
                fname = media.file_name if hasattr(media, 'file_name') and media.file_name else f"temp_{int(time.time())}.zip"
                file_path = os.path.join("downloads", fname)
                os.makedirs("downloads", exist_ok=True)
                
                await msg.edit_text(f"📥 <b>Downloading from TG...</b>\n<code>{clean_html(fname)}</code>")
                await message.reply_to_message.download(file_name=file_path, progress=progress_bar, progress_args=(msg, time.time(), "📥 Downloading..."))
            else:
                await msg.edit_text("📥 <b>Starting Download...</b>")
                file_path, error = await download_logic(link, msg)

            if error or not file_path or not os.path.exists(file_path):
                await msg.edit_text(error if error else "❌ Download Failed")
                continue

            # --- EXTRACT / SPLIT CHECK ---
            await msg.edit_text("⚙️ <b>Checking File...</b>")
            final_files = []
            is_extracted = False
            
            # Check for Archive OR Split files (.001, .002)
            is_archive = file_path.lower().endswith(('.zip', '.rar', '.7z', '.tar', '.gz', '.iso'))
            is_split = bool(re.search(r'\.\d{3}$', file_path))

            if is_archive or is_split:
                output_dir = f"downloads/{int(time.time())}"
                os.makedirs(output_dir, exist_ok=True)
                
                await msg.edit_text("📦 <b>Extracting Archive...</b>")
                cmd = ["7z", "x", file_path, f"-o{output_dir}", "-y"]
                proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                if proc.returncode == 0:
                    for root, _, files in os.walk(output_dir):
                        for file in files:
                            final_files.append(os.path.join(root, file))
                    final_files.sort(key=natural_sort_key)
                    is_extracted = True
                    os.remove(file_path)
                else:
                    final_files = [file_path] # Extract failed, upload original
            else:
                final_files = [file_path]

            if not final_files:
                await msg.edit_text("❌ No files found to upload!")
                continue

            # --- PIN HEADER ---
            if DUMP_CHANNEL:
                try:
                    pin_name = os.path.splitext(os.path.basename(file_path))[0]
                    header = await client.send_message(DUMP_CHANNEL, f"📂 <b>Batch:</b> {clean_html(pin_name)}")
                    await header.pin(both_sides=True)
                except: pass

            # --- UPLOAD ---
            await msg.edit_text(f"☁️ <b>Uploading {len(final_files)} Files...</b>")
            target = DUMP_CHANNEL if DUMP_CHANNEL else message.chat.id
            
            for i, file in enumerate(final_files):
                fname = os.path.basename(file)
                fsize = os.path.getsize(file)
                if fsize == 0: continue
                
                thumb = None
                if fname.lower().endswith(('.mp4', '.mkv', '.webm', '.avi')):
                    thumb = await take_screenshot(file)

                caption = f"<code>{clean_html(fname)}</code>\n📦 <b>Size:</b> {humanbytes(fsize)}\n🔢 <b>Index:</b> {i+1}/{len(final_files)}"
                
                try:
                    await client.send_document(
                        chat_id=target,
                        document=file,
                        thumb=thumb,
                        caption=caption,
                        progress=progress_bar,
                        progress_args=(msg, time.time(), f"☁️ Uploading {i+1}/{len(final_files)}")
                    )
                except Exception as e:
                    print(f"Upload Error: {e}")
                    await asyncio.sleep(5)
                
                if thumb and os.path.exists(thumb): os.remove(thumb)
                await asyncio.sleep(2) # Floodwait protection

            await msg.edit_text("✅ <b>Task Completed!</b>")
            
            # Cleanup
            if is_extracted: shutil.rmtree(output_dir, ignore_errors=True)
            elif os.path.exists(file_path): os.remove(file_path)
            if aria2: aria2.purge()

        except Exception as e:
            traceback.print_exc()
            await msg.edit_text(f"❌ Error: {e}")

    is_processing = False
    await client.send_message(message.chat.id, "🏁 <b>Queue Finished!</b>")

# ==========================================
#           COMMANDS
# ==========================================
@app.on_message(filters.command(["leech", "up"]))
async def add_to_queue(client, message):
    link = None
    is_reply = False
    
    if message.reply_to_message and (message.reply_to_message.document or message.reply_to_message.video):
        is_reply = True
    elif len(message.command) > 1:
        link = message.text.split(None, 1)[1]
    else:
        return await message.reply_text("❌ Send Link or Reply to File!")

    task_queue.append((client, message, link, is_reply))
    
    if not is_processing:
        asyncio.create_task(process_queue())
    else:
        await message.reply_text(f"⏳ <b>Added to Queue!</b> (#{len(task_queue)})")

@app.on_message(filters.command("start"))
async def start(c, m):
    await m.reply_text("👋 <b>Simple Queue Bot</b>\nSend Link or Reply to file.")

# ==========================================
#           RUNNER
# ==========================================
async def main():
    global aria2
    if not shutil.which("7z"): print("⚠️ 7Zip missing!")
    
    # Start Aria2
    if shutil.which("aria2c"):
        try:
            subprocess.Popen(['aria2c', '--enable-rpc', '--rpc-listen-port=6800', '--daemon', '--allow-overwrite=true', '--max-connection-per-server=10'])
            await asyncio.sleep(3)
            aria2 = aria2p.API(aria2p.Client(host="http://localhost", port=6800, secret=""))
            print("✅ Aria2 Started")
        except: print("❌ Aria2 Failed")

    # Web Server
    app_web = web.Application()
    app_web.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await app.start()
    print("🤖 Bot Started")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
