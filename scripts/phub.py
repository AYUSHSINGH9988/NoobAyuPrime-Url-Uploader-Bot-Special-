import yt_dlp

def get_direct_info(url, proxy_url=None):
    """
    Website URL se direct m3u8/mp4 links aur video metadata nikalta hai.
    """
    print(f"⏳ Fetching Pornhub for: {url}")
    ydl_opts = {'quiet': True, 'extract_flat': False}
    
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
                if fmt.get('height') and fmt.get('url'):
                    refined_data['formats'].append({
                        'height': fmt.get('height'),
                        'url': fmt.get('url'),
                        'ext': fmt.get('ext', 'mp4'),
                        'format_id': fmt.get('format_id')
                    })
            return refined_data
    except Exception as e:
        print(f"❌ Script Error in phub.py: {e}")
        return None
