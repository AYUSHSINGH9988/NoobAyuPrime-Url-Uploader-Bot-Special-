import requests
import re
from bs4 import BeautifulSoup
import yt_dlp
import os
import secrets

# ---------------------------------------------------------
# FUNCTION 1: Normal Bot ke liye (Extract Info)
# ---------------------------------------------------------
def get_hc_data(url, proxy_url=None):
    print(f"⏳ Extracting HentaiCity (BS4 Method) for: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.hentaicity.com"
    }
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    try:
        r = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        html = r.text
        soup = BeautifulSoup(html, "html.parser")

        title = soup.title.text.strip() if soup.title else "HentaiCity Video"
        thumb = soup.find("meta", property="og:image")
        thumbnail = thumb.get("content") if thumb else None

        stream_match = re.search(r'https://[^"]+\.m3u8[^"]*', html)
        if not stream_match:
            print("❌ HentaiCity: Page mein m3u8 link nahi mila.")
            return None
        
        m3u8_url = stream_match.group(0)

        ydl_opts = {
            'quiet': True,
            'extract_flat': False,
        }
        if proxy_url:
            ydl_opts['proxy'] = proxy_url 

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            m3u8_data = ydl.extract_info(m3u8_url, download=False)
            m3u8_data['title'] = title
            if thumbnail:
                m3u8_data['thumbnail'] = thumbnail

            refined_data = {'title': title, 'formats': [], 'original_info': m3u8_data}

            for fmt in m3u8_data.get('formats', []):
                h = fmt.get('height') or fmt.get('format_id') or 'Original'
                link = fmt.get('url')
                if link:
                    refined_data['formats'].append({'height': h, 'url': link, 'ext': fmt.get('ext', 'mp4'), 'format_id': fmt.get('format_id')})
                    
            return refined_data

    except Exception as e:
        print(f"❌ Script Error in hc.py: {e}")
        return None

# ---------------------------------------------------------
# FUNCTION 2: Naye /scriptdl ke liye (Direct Download)
# ---------------------------------------------------------
def download_hc(url, proxy_url=None, progress_hook=None):
    print(f"⏳ [HC Script] Starting download for: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.hentaicity.com"
    }
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
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

        os.makedirs("downloads", exist_ok=True)
        dl_dir = os.path.join("downloads", secrets.token_hex(4))
        os.makedirs(dl_dir, exist_ok=True)
        out_tmpl = os.path.join(dl_dir, f"{safe_title[:100]}.%(ext)s")

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

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(m3u8_url, download=True)
            if not info: return None, "yt-dlp extraction failed"
            fp = ydl.prepare_filename(info)
            if os.path.exists(fp):
                return fp, None
            return None, "File not saved"

    except Exception as e:
        return None, str(e)
