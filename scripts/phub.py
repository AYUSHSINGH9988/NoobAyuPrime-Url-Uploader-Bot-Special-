import yt_dlp
import os
import secrets
import re

# ---------------------------------------------------------
# FUNCTION 1: Normal Bot ke liye (Extract Info)
# ---------------------------------------------------------
def get_direct_info(url, proxy_url=None):
    # 🚨 FIX: Auto-convert .com to .org to bypass 412 Precondition Failed
    url = re.sub(r'pornhub\.com', 'pornhub.org', url)
    print(f"⏳ Fetching Pornhub for: {url}")
    
    ydl_opts = {'quiet': True, 'extract_flat': False}

    if proxy_url:
        ydl_opts['proxy'] = proxy_url

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(url, download=False)
            if not data: return None

            refined_data = {'title': data.get('title', 'Video'), 'formats': [], 'original_info': data}
            for fmt in data.get('formats', []):
                if fmt.get('height') and fmt.get('url'):
                    refined_data['formats'].append({'height': fmt.get('height'), 'url': fmt.get('url'), 'ext': fmt.get('ext', 'mp4'), 'format_id': fmt.get('format_id')})
            return refined_data
    except Exception as e:
        print(f"❌ Script Error in phub.py: {e}")
        return None

# ---------------------------------------------------------
# FUNCTION 2: Naye /scriptdl ke liye (Direct Download)
# ---------------------------------------------------------
def download_phub(url, proxy_url=None, progress_hook=None):
    # 🚨 FIX: Auto-convert .com to .org to bypass 412 Precondition Failed
    url = re.sub(r'pornhub\.com', 'pornhub.org', url)
    print(f"⏳ [PHUB Script] Starting download for: {url}")

    os.makedirs("downloads", exist_ok=True)
    dl_dir = os.path.join("downloads", secrets.token_hex(4))
    os.makedirs(dl_dir, exist_ok=True)
    out_tmpl = os.path.join(dl_dir, "%(title).100s.%(ext)s")

    ydl_opts = {
        'outtmpl': out_tmpl,
        'quiet': True,
        'format': 'best', 
        'nocheckcertificate': True
    }

    if proxy_url:
        ydl_opts['proxy'] = proxy_url
    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info: 
                return None, "yt-dlp extraction failed"

            fp = ydl.prepare_filename(info)
            if os.path.exists(fp):
                return fp, None
            return None, "File not saved"

    except Exception as e:
        return None, str(e)
