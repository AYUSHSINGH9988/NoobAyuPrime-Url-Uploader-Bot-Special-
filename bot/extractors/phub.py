import yt_dlp
import requests
from bs4 import BeautifulSoup
import urllib.parse


def get_direct_info(url, proxy=None):
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
        return {"title": info.get("title", "PornHub Video"), "formats": formats, "original_info": info}
    except Exception as e:
        print(f"PHub Error: {e}")
        return None


def scrape_phub_profile_videos(profile_url, proxy=None, max_pages=30):
    """Scrape a PornHub profile/model/channel/playlist page.
    Returns list of {url, title} dicts for use with phub_select_sessions."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Cookie": "age_verified=1; platform=pc; accessAgeDisclaimerPH=1",
        "Accept-Language": "en-US,en;q=0.9",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    base = profile_url.split("?")[0].rstrip("/")
    is_playlist = "/playlist/" in base
    videos_url = base if is_playlist or base.endswith("/videos") else base + "/videos"

    seen, out = set(), []
    for page in range(1, max_pages + 1):
        sep      = "&" if "?" in videos_url else "?"
        page_url = videos_url if page == 1 else f"{videos_url}{sep}page={page}"
        try:
            r = requests.get(page_url, headers=headers, proxies=proxies, timeout=30)
        except Exception:
            break
        if r.status_code != 200:
            break
        soup  = BeautifulSoup(r.text, "html.parser")
        items = (
            soup.select("li.pcVideoListItem")
            or soup.select("li.videoblock")
            or soup.select("div.phimage")
        )
        if not items:
            break
        new_in_page = 0
        for li in items:
            a = li.find("a", href=True)
            if not a:
                continue
            href = urllib.parse.urljoin("https://www.pornhub.com", a["href"])
            if "viewkey=" not in href:
                continue
            href = href.split("&")[0]
            if href in seen:
                continue
            seen.add(href)
            title_tag = li.find(["span", "a"], class_=lambda c: c and "title" in c.lower())
            title = (title_tag.get_text(strip=True) if title_tag else None) or f"Video {len(out)+1}"
            out.append({"url": href, "title": title})
            new_in_page += 1
        if new_in_page == 0:
            break
    return out


def get_profile_videos(profile_url, proxy=None, max_pages=30):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": "age_verified=1; platform=pc; accessAgeDisclaimerPH=1",
        "Accept-Language": "en-US,en;q=0.9",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    base = profile_url.split("?")[0].rstrip("/")
    is_playlist = "/playlist/" in base
    videos_url = base if is_playlist or base.endswith("/videos") else base + "/videos"

    seen, out = set(), []
    for page in range(1, max_pages + 1):
        sep      = "&" if "?" in videos_url else "?"
        page_url = videos_url if page == 1 else f"{videos_url}{sep}page={page}"
        try:
            r = requests.get(page_url, headers=headers, proxies=proxies, timeout=30)
        except Exception:
            break
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        items = (
            soup.select("li.pcVideoListItem")
            or soup.select("li.videoblock")
            or soup.select("div.phimage")
        )
        if not items:
            break
        new_in_page = 0
        for li in items:
            a = li.find("a", href=True)
            if not a:
                continue
            href = urllib.parse.urljoin("https://www.pornhub.com", a["href"])
            if "viewkey=" not in href:
                continue
            href = href.split("&")[0]
            if href in seen:
                continue
            seen.add(href)
            out.append(href)
            new_in_page += 1
        if new_in_page == 0:
            break
    return out
