import re
import cloudscraper
from bs4 import BeautifulSoup
import urllib.parse

def get_xh_data(url, proxy=None):
    """Single video stream extractor (Cloudflare Bypass)"""
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        r = scraper.get(url, proxies=proxies, timeout=15)
        html = r.text.replace('\\/', '/')
        
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("title")
        title = title_tag.text.replace(" - xHamster", "").strip() if title_tag else "xHamster_Video"

        match = re.search(r'https://[^"\'\s]+_TPL_[^"\'\s]+\.m3u8', html)
        if not match: return None
            
        template_url = match.group(0)
        qualities = ["144p", "240p", "480p", "720p", "1080p"]
        formats = []
        
        for q in qualities:
            formats.append({
                "format_id": q,
                "height": int(q.replace('p', '')),
                "url": template_url.replace("_TPL_", q),
                "ext": "mp4"
            })
            
        return {
            "title": title,
            "formats": sorted(formats, key=lambda x: x["height"], reverse=True),
            "original_info": {"title": title, "thumbnail": ""}
        }

    except Exception as e:
        print(f"XH Single Error: {e}")
        return None

def get_xh_profile_videos(profile_url, proxy=None, max_pages=15):
    """Profile/Pornstar ke saare videos nikalne ke liye (Cloudflare Bypass)"""
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    cookies = {"age_verified": "1"}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    
    base = profile_url.split("?")[0].rstrip("/")
    if not base.endswith("/videos"):
        base = base + "/videos"

    seen = set()
    out = []
    
    for page in range(1, max_pages + 1):
        page_url = base if page == 1 else f"{base}/{page}"
        try:
            r = scraper.get(page_url, cookies=cookies, proxies=proxies, timeout=15)
            if r.status_code != 200: break
            
            soup = BeautifulSoup(r.text, "html.parser")
            links = soup.find_all("a", href=True)
            
            new_in_page = 0
            for a in links:
                href = a["href"]
                if "/videos/" in href and not href.endswith("/videos"):
                    full_url = urllib.parse.urljoin(base, href)
                    clean_url = full_url.split("?")[0]
                    if clean_url not in seen:
                        seen.add(clean_url)
                        out.append(clean_url)
                        new_in_page += 1
                        
            if new_in_page == 0: break 
            
        except Exception:
            break
            
    return out
