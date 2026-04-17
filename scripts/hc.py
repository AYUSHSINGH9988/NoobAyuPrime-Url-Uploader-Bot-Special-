import subprocess
import json

def get_hc_data(url):
    """
    HentaiCity URLs se video metadata aur direct links nikalta hai.
    """
    print(f"⏳ Fetching HentaiCity data for: {url}")
    try:
        # yt-dlp CLI se JSON extract karna
        command = ['yt-dlp', '-j', url]
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode == 0:
            data = json.loads(result.stdout.strip().split('\n')[-1])
            
            # Bot ke liye clean dictionary banana
            refined_data = {
                'title': data.get('title', 'HentaiCity Video'),
                'formats': [],
                'original_info': data  # WZML-X parser ke liye original data
            }
            
            # Direct links nikalna
            for fmt in data.get('formats', []):
                # FIX: HentaiCity height nahi deta, isliye format_id ya 'Original' use kar rahe hain
                h = fmt.get('height') or fmt.get('format_id') or 'Original'
                link = fmt.get('url')
                
                if link:  # Agar sirf link mil jaye, toh dictionary me add kar do
                    refined_data['formats'].append({
                        'height': h,
                        'url': link,
                        'ext': fmt.get('ext', 'mp4'),
                        'format_id': fmt.get('format_id')
                    })
            return refined_data
        else:
            print(f"❌ yt-dlp HentaiCity Error:\n{result.stderr}")
            return None
    except Exception as e:
        print(f"❌ Script Error in hc.py: {e}")
        return None