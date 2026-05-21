import re
import requests
from bs4 import BeautifulSoup
import secrets


def get_wh_data(url, proxy=None):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r    = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        title_tag = soup.find("title")
        title     = title_tag.text.strip() if title_tag else f"WH_{secrets.token_hex(2)}"
        video_url = None
        source    = soup.find("source", src=True)
        if source:
            video_url = source["src"]
        if not video_url:
            for pattern in [
                r'file:\s*["\']( https?://[^"\']+\.m3u8[^"\']*)["\']',
                r'"file"\s*:\s*"( https?://[^"]+\.m3u8[^"]*)"',
                r'src:\s*["\']( https?://[^"\']+\.m3u8[^"\']*)["\']',
            ]:
                m = re.search(pattern, r.text)
                if m:
                    video_url = m.group(1).strip()
                    break
        if not video_url:
            return None
        formats = [
            {"format_id": "360p",  "height": 360,  "url": video_url, "ext": "mp4"},
            {"format_id": "720p",  "height": 720,  "url": video_url, "ext": "mp4"},
            {"format_id": "1080p", "height": 1080, "url": video_url, "ext": "mp4"},
        ]
        return {"title": title, "formats": formats}
    except Exception as e:
        print(f"WH Error: {e}")
        return None
