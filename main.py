import os
import time
import asyncio
import aiohttp
import aiofiles
import aria2p
import subprocess
import shutil
import traceback
import re
import urllib.parse
from pyrogram import Client, filters, enums
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

app = Client("queue_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=enums.ParseMode.HTML)

# ==========================================
#           GLOBAL VARIABLES
# ==========================================
aria2 = None
task_queue = []  # List to store tasks
is_processing = False  # Flag to check if bot is busy

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

# Natural Sorting (1, 2, 10 sequence fix)
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
    if message.id in last_update_time and (now - last_update_time[message.id]) < 5:
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
#           CORE WORKER (PROCESSOR)
# ==========================================
 async def process_queue():
    global is_processing
    is_processing = True
    
    while task_queue:
        client, message, link, is_reply_file = task_queue.pop(0)
        
        msg = await message.reply_text(f"♻️ <b>Processing Task...</b>\nQueue Left: {len(task_queue)}")
        file_path = None
        
        try:
            # --- DOWNLOAD STEP ---
            if is_reply_file:
                # FIX: Pehle file ka naam nikalo taaki extension (.zip) mile
                media = message.reply_to_message.document or message.reply_to_message.video or message.reply_to_message.audio
                
                # Agar naam mil gaya to thik, warna default 'temp.zip' maan lenge
                original_filename = media.file_name if hasattr(media, 'file_name') and media.file_name else f"temp_{int(time.time())}.zip"
                
                await msg.edit_text(f"📥 <b>Downloading:</b> {clean_html(original_filename)}")
                
                # Force Save with Name
                file_path = os.path.join("downloads", original_filename)
                
                await message.reply_to_message.download(
                    file_name=file_path,
                    progress=progress_bar,
                    progress_args=(msg, time.time(), "📥 Downloading from TG...")
                )

            elif link:
                await msg.edit_text("📥 <b>Downloading URL...</b>")
                # (URL Download Logic Same as Before)
                if "magnet:" in link or link.endswith(".torrent"):
                    if not aria2: 
                        await msg.edit_text("❌ Aria2 not running!")
                        continue
                    download = aria2.add_magnet(link)
                    while not download.is_complete:
                        download.update()
                        if download.status == "error": 
                            await msg.edit_text("❌ Aria2 Error")
                            file_path = None
                            break
                        await progress_bar(download.completed_length, download.total_length, msg, time.time(), "📥 Downloading Torrent...")
                        await asyncio.sleep(4)
                    if download.status == "complete":
                        file_path = download.files[0].path
                else:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(link) as resp:
                            if resp.status != 200: 
                                await msg.edit_text("❌ Invalid Link")
                                continue
                            fname = os.path.basename(urllib.parse.unquote(link))
                            if not fname: fname = "downloaded_file"
                            file_path = f"downloads/{fname}"
                            os.makedirs("downloads", exist_ok=True)
                            f = await aiofiles.open(file_path, mode='wb')
                            total = int(resp.headers.get('content-length', 0))
                            dl = 0
                            start = time.time()
                            async for chunk in resp.content.iter_chunked(1024*1024):
                                await f.write(chunk)
                                dl += len(chunk)
                                await progress_bar(dl, total, msg, start, "📥 Downloading HTTP...")
                            await f.close()

            # --- CHECK FILE EXISTENCE ---
            if not file_path or not os.path.exists(file_path):
                await msg.edit_text("❌ Download Failed or Skipped")
                continue

            # --- EXTRACTION & PINNING STEP ---
            await msg.edit_text("⚙️ <b>Processing & Sorting...</b>")
            
            final_files = []
            is_extracted = False
            original_name = os.path.basename(file_path)
            pin_name = os.path.splitext(original_name)[0]
            
            # EXTENSION CHECK (Lowercase karke check karega)
            if file_path.lower().endswith(('.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.xz')):
                output_dir = f"downloads/{int(time.time())}"
                if not os.path.exists(output_dir): os.makedirs(output_dir)
                
                # Extract
                cmd = ["7z", "x", file_path, f"-o{output_dir}", "-y"]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                for root, _, files in os.walk(output_dir):
                    for file in files:
                        final_files.append(os.path.join(root, file))
                
                final_files.sort(key=natural_sort_key)
                is_extracted = True
                os.remove(file_path)
            else:
                final_files = [file_path]

            if not final_files:
                await msg.edit_text("❌ Empty Archive or No File!")
                if is_extracted: shutil.rmtree(output_dir, ignore_errors=True)
                continue

            # --- PIN HEADER ---
            if DUMP_CHANNEL:
                try:
                    header_text = f"📂 <b>Upload Batch:</b>\n<code>{clean_html(pin_name)}</code>\n\n🔹 Files: {len(final_files)}"
                    header_msg = await client.send_message(DUMP_CHANNEL, header_text)
                    await header_msg.pin(both_sides=True)
                    await asyncio.sleep(1) 
                except Exception as e:
                    print(f"Pin Error: {e}")

                        # --- UPLOAD LOOP ---
            await msg.edit_text(f"☁️ <b>Uploading {len(final_files)} Files...</b>")
            target_chat = DUMP_CHANNEL if DUMP_CHANNEL else message.chat.id
            
            for i, file in enumerate(final_files):
                fname = os.path.basename(file)
                fsize = os.path.getsize(file)
                if fsize == 0: continue

                thumb = None
                is_video = fname.lower().endswith(('.mp4', '.mkv', '.webm', '.avi'))
                if is_video: thumb = await take_screenshot(file)
                
                caption = f"<code>{clean_html(fname)}</code>\n📦 <b>Size:</b> {humanbytes(fsize)}\n🔢 <b>Index:</b> {i+1}/{len(final_files)}"
                
                try:
                    await client.send_document(
                        chat_id=target_chat,
                        document=file,
                        thumb=thumb,
                        caption=caption,
                        progress=progress_bar,
                        progress_args=(msg, time.time(), f"☁️ Uploading {i+1}/{len(final_files)}")
                    )
                except Exception as e:
                    print(f"File Upload Error: {e}")
                    await asyncio.sleep(5)
                
                if thumb and os.path.exists(thumb): os.remove(thumb)

                # ✅ YAHAN HAI 2 SECOND KA DELAY
                await asyncio.sleep(2) 

            await msg.edit_text("✅ <b>Task Completed!</b>")
  
            # Cleanup Folder
            if is_extracted: shutil.rmtree(output_dir, ignore_errors=True)
            elif os.path.exists(file_path): os.remove(file_path)
            
            if aria2: aria2.purge()

        except Exception as e:
            traceback.print_exc()
            await msg.edit_text(f"❌ Critical Error: {e}")
            # Try to cleanup even on error
            try:
                if 'output_dir' in locals() and os.path.exists(output_dir): shutil.rmtree(output_dir)
            except: pass

    is_processing = False
    await client.send_message(message.chat.id, "🏁 <b>All Queue Tasks Finished!</b>")

# ==========================================
#           COMMAND HANDLERS
# ==========================================
@app.on_message(filters.command(["leech", "up"]))
async def add_to_queue(client, message):
    link = None
    is_reply_file = False
    
    if message.reply_to_message and message.reply_to_message.document:
        is_reply_file = True
    elif len(message.command) > 1:
        link = message.text.split(None, 1)[1]
    else:
        return await message.reply_text("❌ Send Link or Reply to File!")

    # Add to Queue
    task_queue.append((client, message, link, is_reply_file))
    
    pos = len(task_queue)
    if is_processing:
        await message.reply_text(f"⏳ <b>Added to Queue!</b>\nPosition: {pos}")
    else:
        # Start Worker if not running
        asyncio.create_task(process_queue())

@app.on_message(filters.command("start"))
async def start(c, m):
    await m.reply_text("👋 <b>Queue & Pin Bot</b>\n\nJo bhi bhejoge, queue me lagega aur extract hokar dump me pin ke sath upload hoga.")

# ==========================================
#           MAIN RUNNER
# ==========================================
async def main():
    global aria2
    # Check dependencies
    if not shutil.which("7z"): print("⚠️ 7Zip missing!")
    
    try:
        if shutil.which("aria2c"):
            subprocess.Popen(['aria2c', '--enable-rpc', '--rpc-listen-port=6800', '--daemon', '--allow-overwrite=true', '--max-connection-per-server=10'])
            await asyncio.sleep(3)
            aria2 = aria2p.API(aria2p.Client(host="http://localhost", port=6800, secret=""))
            print("✅ Aria2 Started")
    except: print("❌ Aria2 Failed")

    # Keep-Alive Server for Koyeb
    app_web = web.Application()
    app_web.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await app.start()
    print("🤖 Bot Started with Queue System")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
  
