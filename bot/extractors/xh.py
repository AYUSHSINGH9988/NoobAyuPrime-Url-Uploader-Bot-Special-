import re
import requests
from bs4 import BeautifulSoup
import yt_dlp
import urllib.parse


def get_xh_data(url, proxy=None):
    ydl_opts = {"proxy": proxy, "quiet": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        formats = []
        for f in info.get("formats", []):
            if f.get("vcodec") != "none" and f.get("height"):
                formats.append({
                    "format_id": f.get("format_id"),
                    "height":    f.get("height"),
                    "url":       f.get("url"),
                    "ext":       f.get("ext", "mp4"),
                })
        return {"title": info.get("title", "XH Video"), "formats": formats}
    except Exception as e:
        print(f"XH Error: {e}")
        return None


def get_xh_profile_videos(profile_url, proxy=None, max_pages=30):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    seen, out = set(), []
    for page in range(1, max_pages + 1):
        sep      = "&" if "?" in profile_url else "?"
        page_url = profile_url if page == 1 else f"{profile_url}{sep}page={page}"
        try:
            r = requests.get(page_url, headers=headers, proxies=proxies, timeout=20)
        except Exception:
            break
        if r.status_code != 200:
            break
        soup  = BeautifulSoup(r.text, "html.parser")
        items = soup.select("div.thumb-list__item") or soup.select("li.videoList__item")
        if not items:
            break
        added = 0
        for li in items:
            a = li.find("a", href=True)
            if not a:
                continue
            href = urllib.parse.urljoin("https://xhamster.com", a["href"])
            if href in seen:
                continue
            seen.add(href)
            out.append(href)
            added += 1
        if added == 0:
            break
    return out
