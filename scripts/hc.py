import requests
import re
import os
import secrets
import yt_dlp
from bs4 import BeautifulSoup

def download_hc(url, proxy_url=None, progress_hook=None):
    print(f"⏳ [HC Script] Starting download for: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.hentaicity.com"
    }
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
        # 1. Bypass and Get m3u8
        r = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        html = r.text
        title_match = re.search(r'<title>(.*?)</title>', html)
        safe_title = "HentaiCity_Video"
        if title_match:
            safe_title = re.sub(r'[<>:"/\\|?*]', "", title_match.group(1).replace(" | HentaiCity", "")).strip()

        stream_match = re.search(r'https://[^"]+\.m3u8[^"]*', html)
        if not stream_match:
            return None, "M3U8 stream not found on page."
        
        m3u8_url = stream_match.group(0)

        # 2. Download via yt-dlp
        os.makedirs("downloads", exist_ok=True)
        dl_dir = os.path.join("downloads", secrets.token_hex(4))
        os.makedirs(dl_dir, exist_ok=True)
        out_tmpl = os.path.join(dl_dir, f"{safe_title[:100]}.%(ext)s")

        ydl_opts = {
            'outtmpl': out_tmpl,
            'quiet': True,
            'format': 'best', # Auto best quality
            'nocheckcertificate': True
        }
        if proxy_url:
            ydl_opts['proxy'] = proxy_url
        if progress_hook:
            ydl_opts['progress_hooks'] = [progress_hook]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(m3u8_url, download=True)
            if not info: return None, "yt-dlp extraction failed"
            fp = ydl.prepare_filename(info)
            if os.path.exists(fp):
                return fp, None
            return None, "File not saved"

    except Exception as e:
        return None, str(e)
