import yt_dlp
import requests
from bs4 import BeautifulSoup
import urllib.parse

def get_direct_info(url, proxy=None):
    """Single video ki qualities aur info nikalne ke liye (Uses yt-dlp backend)"""
    ydl_opts = {
        'proxy': proxy,
        'quiet': True,
        'no_warnings': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
        formats = []
        for f in info.get('formats', []):
            if f.get('vcodec') != 'none' and f.get('height'):
                formats.append({
                    'format_id': f.get('format_id'),
                    'height': f.get('height'),
                    'url': f.get('url'),
                    'ext': f.get('ext', 'mp4')
                })
                
        return {
            "title": info.get('title', 'PornHub Video'),
            "formats": formats,
            "original_info": info
        }
    except Exception as e:
        print(f"PHub Error: {e}")
        return None

def get_profile_videos(profile_url, proxy=None, max_pages=30):
    """Model, Pornstar ya Playlist ke saare videos nikalne ke liye"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Cookie": "age_verified=1; platform=pc; accessAgeDisclaimerPH=1",
        "Accept-Language": "en-US,en;q=0.9",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None

    base = profile_url.split("?")[0].rstrip("/")
    is_playlist = "/playlist/" in base
    if not is_playlist and not base.endswith("/videos"):
        videos_url = base + "/videos"
    else:
        videos_url = base

    seen = set()
    out = []
    
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in videos_url else "?"
        page_url = videos_url if page == 1 else f"{videos_url}{sep}page={page}"
        try:
            r = requests.get(page_url, headers=headers, proxies=proxies, timeout=30)
        except Exception:
            break
        if r.status_code != 200:
            break

        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("li.pcVideoListItem") or soup.select("li.videoblock") or soup.select("div.phimage")
        if not items:
            break

        new_in_page = 0
        for li in items:
            a = li.find("a", href=True)
            if not a: continue
            
            href = urllib.parse.urljoin("https://www.pornhub.com", a["href"])
            if "viewkey=" not in href: continue
            
            href = href.split("&")[0]
            if href in seen: continue
            
            seen.add(href)
            out.append(href)  # main.py expects a list of URLs
            new_in_page += 1

        if new_in_page == 0:
            break

    return out

def download_phub(url):
    pass # Dummy function to prevent import errors in main.py
