import yt_dlp
import os
import secrets

def download_phub(url, proxy_url=None, progress_hook=None):
    print(f"⏳ [PHUB Script] Starting download for: {url}")
    
    os.makedirs("downloads", exist_ok=True)
    dl_dir = os.path.join("downloads", secrets.token_hex(4))
    os.makedirs(dl_dir, exist_ok=True)
    out_tmpl = os.path.join(dl_dir, "%(title).100s.%(ext)s")

    ydl_opts = {
        'outtmpl': out_tmpl,
        'quiet': True,
        'format': 'best', # Auto best quality
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
                return None, "yt-dlp extraction failed"
            
            fp = ydl.prepare_filename(info)
            if os.path.exists(fp):
                return fp, None
            return None, "File not saved"

    except Exception as e:
        return None, str(e)
