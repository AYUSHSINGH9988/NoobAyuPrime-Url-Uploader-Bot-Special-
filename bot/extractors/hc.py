import re
import secrets
import requests
from bs4 import BeautifulSoup


def get_hc_data(url, proxy=None):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r    = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        title_tag = soup.find("title")
        title     = title_tag.text.replace(" - HentaiCity", "").strip() if title_tag \
                    else f"HC_Video_{secrets.token_hex(2)}"
        video_url = None
        source    = soup.find("source", src=True)
        if source:
            video_url = source["src"]
        if not video_url:
            m = re.search(r'file:\s*["\']( https?://[^"\']+\.m3u8[^"\']*)["\']', r.text)
            if m:
                video_url = m.group(1).strip()
        if not video_url:
            m = re.search(r'src["\']\s*:\s*["\']( https?://[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']', r.text)
            if m:
                video_url = m.group(1).strip()
        if not video_url:
            return None
        formats = [
            {"format_id": "360p",  "height": 360,  "url": video_url, "ext": "mp4"},
            {"format_id": "480p",  "height": 480,  "url": video_url, "ext": "mp4"},
            {"format_id": "720p",  "height": 720,  "url": video_url, "ext": "mp4"},
            {"format_id": "1080p", "height": 1080, "url": video_url, "ext": "mp4"},
        ]
        return {"title": title, "formats": formats, "original_info": {"title": title, "thumbnail": ""}}
    except Exception as e:
        print(f"HC Error: {e}")
        return None
