import yt_dlp
import os
import secrets
import re

# ---------------------------------------------------------
# 1. PROFILE SCANNER (Bot ke batch/queue system ke liye)
# ---------------------------------------------------------
def get_profile_videos(url, proxy_url=None):
    # Step 1: Clean URL aur redirect fix (/videos append karna)
    url = url.split('?')[0].rstrip('/')
    if ('/model/' in url or '/pornstar/' in url or '/channels/' in url) and not url.endswith('/videos'):
        url += '/videos'

    print(f"⏳ [PHUB Script] Scanning Profile: {url}")
    
    ydl_opts = {
        'quiet': True, 
        'extract_flat': 'in_playlist', # Playlist/Model scanning ke liye best option
        'nocheckcertificate': True,
        'ignoreerrors': True
    }
    if proxy_url:
        ydl_opts['proxy'] = proxy_url

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(url, download=False)
            
            if not data:
                return []
                
            # Agar single video aa jaye galti se
            if 'entries' not in data:
                if data.get('id'):
                    return [f"https://www.pornhub.com/view_video.php?viewkey={data['id']}"]
                return []
            
            video_urls = []
            for entry in data['entries']:
                if not entry: continue
                
                v_url = entry.get('url')
                v_id = entry.get('id')
                
                # Agar URL nahi mil raha toh ID se create karo
                if not v_url and v_id:
                    v_url = f"https://www.pornhub.com/view_video.php?viewkey={v_id}"
                
                if v_url:
                    if v_url.startswith('/'):
                        v_url = "https://www.pornhub.com" + v_url
                    video_urls.append(v_url)
                    
            return video_urls
    except Exception as e:
        print(f"❌ [PHUB Script] Profile Scan Error: {e}")
        return []

# ---------------------------------------------------------
# 2. DIRECT INFO (Stream nikalne ke liye - Bot Interface)
# ---------------------------------------------------------
def get_direct_info(url, proxy_url=None):
    # Bypass: Auto-convert .com to .org for extraction
    url = re.sub(r'pornhub\.com', 'pornhub.org', url)
    print(f"⏳ [PHUB Script] Extracting Streams: {url}")
    
    ydl_opts = {
        'quiet': True, 
        'extract_flat': False, 
        'nocheckcertificate': True
    }

    if proxy_url:
        ydl_opts['proxy'] = proxy_url

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(url, download=False)
            if not data: return None

            refined_data = {
                'title': data.get('title', 'Video'), 
                'formats': [], 
                'original_info': data
            }
            
            for fmt in data.get('formats', []):
                # Sirf wo formats lo jisme video resolution (height) aur URL ho
                if fmt.get('height') and fmt.get('url'):
                    refined_data['formats'].append({
                        'height': fmt.get('height'), 
                        'url': fmt.get('url'), 
                        'ext': fmt.get('ext', 'mp4'), 
                        'format_id': fmt.get('format_id')
                    })
            return refined_data
    except Exception as e:
        print(f"❌ [PHUB Script] Metadata Error: {e}")
        return None

# ---------------------------------------------------------
# 3. DOWNLOAD ENGINE (Bot ke worker ke liye)
# ---------------------------------------------------------
def download_phub(url, proxy_url=None, progress_hook=None):
    # Bypass: Auto-convert .com to .org for download
    url = re.sub(r'pornhub\.com', 'pornhub.org', url)
    
    os.makedirs("downloads", exist_ok=True)
    dl_dir = os.path.join("downloads", secrets.token_hex(4))
    os.makedirs(dl_dir, exist_ok=True)
    out_tmpl = os.path.join(dl_dir, "%(title).100s.%(ext)s")

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

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info: 
                return None, "Extraction failed during download phase"

            fp = ydl.prepare_filename(info)
            if os.path.exists(fp):
                return fp, None
            return None, "File was not saved correctly"

    except Exception as e:
        return None, str(e)
