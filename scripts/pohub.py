# scripts/pohub.py
import yt_dlp

def get_ph_data(url):
    """
    PornHub URL se video ka pura JSON data nikalta hai.
    """
    print(f"⏳ Fetching data for: {url}")
    
    # yt-dlp ki settings
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        # 'proxy': 'http://138.249.190.195:62694',  # Agar VPS/Termux par block ho toh isko un-comment karke proxy daal dena
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # download=False matlab sirf data nikalna hai, video download nahi karni
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        print(f"❌ Error in pohub script: {e}")
        return None

# Testing ke liye (Jab aap direct termux me 'python scripts/pohub.py' run karoge)
if __name__ == "__main__":
    test_url = input("🔗 Link daaliye: ")
    data = get_ph_data(test_url)
    if data:
        print(f"✅ Title: {data.get('title')}")
        print(f"🎥 Formats Found: {len(data.get('formats', []))}")
      
