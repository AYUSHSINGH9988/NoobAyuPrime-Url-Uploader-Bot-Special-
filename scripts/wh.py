import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
import sys
import json

def get_wh_data(url, proxy_url=None):
    print(f"⏳ Extracting WatchHentai for: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0.0.0 Safari/537.36",
        "Referer": "https://watchhentai.net/"
    }
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
        r = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        
        # 🚨 FIX: Poore HTML ko decode kar lenge taaki 'https%3A%2F%2F' normal 'https://' ban jaye
        decoded_html = unquote(r.text) 
        soup = BeautifulSoup(r.text, "html.parser")

        title = soup.title.text.strip() if soup.title else "WatchHentai Video"
        
        # 🚨 FIX: Thumbnail ke aage se '\r' hata rahe hain
        thumb_meta = soup.find("meta", property="og:image")
        thumbnail = thumb_meta.get("content", "").replace('\r', '').replace('\n', '').strip() if thumb_meta else None

        # Direct Video Link dhundhna
        direct_url = None
        match = re.search(r'(https?://[^\s\'"<>]*?\.(?:mp4|m3u8))', decoded_html)
        if match:
            direct_url = match.group(1)

        if not direct_url:
            print("❌ WatchHentai: Direct link not found in HTML.")
            return None

        print(f"✅ Found WH Direct Link: {direct_url}")

        # Fake yt-dlp dictionary banayenge taaki main.py isko aaram se process kar le
        fake_ytdlp_info = {
            'title': title,
            'thumbnail': thumbnail,
            'duration': 0,
            'formats': [{
                'format_id': 'best',
                'url': direct_url,
                'ext': 'mp4' if '.mp4' in direct_url else 'm3u8',
                'height': 1080,
                'tbr': 2000, 
                'vcodec': 'avc1',
                'acodec': 'mp4a'
            }]
        }

        refined_data = {
            'title': title,
            'formats': [{'format_id': 'best', 'height': 1080, 'url': direct_url, 'ext': 'mp4'}],
            'original_info': fake_ytdlp_info
        }
        return refined_data

    except Exception as e:
        print(f"❌ Script Error in wh.py: {e}")
        return None

# ---------------------------------------------------------
# TERMINAL ME TEST KARNE KE LIYE
# ---------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
    else:
        test_url = input("🔗 WatchHentai ka URL dalo: ").strip()
        
    if test_url:
        print("\n🚀 Starting extraction...\n")
        result = get_wh_data(test_url)
        
        if result:
            print("\n✅ EXTRACTION SUCCESSFUL!")
            print("🎬 Title:", result['title'])
            print("🖼️ Thumbnail:", result['original_info'].get('thumbnail', 'Not Found'))
            print("🔗 Formats Extracted:", len(result['formats']))
            print("\n📦 Full Data JSON Dump:")
            print(json.dumps(result, indent=4))
        else:
            print("\n❌ Failed to extract data.")
