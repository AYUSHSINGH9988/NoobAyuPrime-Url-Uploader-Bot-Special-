import re
import requests
from bs4 import BeautifulSoup
import secrets

def get_wh_data(url, proxy=None):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    
    try:
        r = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        
        title_tag = soup.find("title")
        title = title_tag.text.replace(" - WatchHentai", "").strip() if title_tag else f"WH_Video_{secrets.token_hex(2)}"
        
        video_url = None
        
        # 1. Search in iframe (Usually WH embeds their own player)
        iframe = soup.find("iframe", src=True)
        if iframe and "watchhentai" in iframe["src"]:
            try:
                r2 = requests.get(iframe["src"], headers=headers, proxies=proxies, timeout=15)
                match = re.search(r'file:\s*["\'](https?://[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']', r2.text)
                if match: video_url = match.group(1)
            except Exception:
                pass

        # 2. Search in main page source
        if not video_url:
            match = re.search(r'file:\s*["\'](https?://[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']', r.text)
            if match: video_url = match.group(1)
            
        if not video_url: 
            return None
            
        # Standard formats list for Inline Keyboard
        formats = [
            {"format_id": "360p", "height": 360, "url": video_url, "ext": "mp4"},
            {"format_id": "480p", "height": 480, "url": video_url, "ext": "mp4"},
            {"format_id": "720p", "height": 720, "url": video_url, "ext": "mp4"},
            {"format_id": "1080p", "height": 1080, "url": video_url, "ext": "mp4"}
        ]
        
        return {
            "title": title,
            "formats": formats,
            "original_info": {"title": title, "thumbnail": ""}
        }
    except Exception as e:
        print(f"WH Extraction Error: {e}")
        return None
