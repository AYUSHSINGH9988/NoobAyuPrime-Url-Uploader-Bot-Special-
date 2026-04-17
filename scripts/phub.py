import subprocess
import json

def get_direct_info(url):
    """
    Website URL se direct m3u8/mp4 links aur video metadata nikalta hai.
    """
    try:
        # CLI se JSON nikalna (PhantomJS error bypass karne ke liye)
        command = ['yt-dlp', '-j', url]
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode == 0:
            data = json.loads(result.stdout.strip().split('\n')[-1])
            
            # Metadata filter karna
            refined_data = {
                'title': data.get('title', 'Video'),
                'formats': [],
                'original_info': data # Backup ke liye
            }
            
            # Sirf kaam ke formats (Direct Links) nikalna
            for fmt in data.get('formats', []):
                if fmt.get('height') and fmt.get('url'):
                    refined_data['formats'].append({
                        'height': fmt.get('height'),
                        'url': fmt.get('url'),
                        'ext': fmt.get('ext', 'mp4'),
                        'format_id': fmt.get('format_id')
                    })
            return refined_data
        return None
    except:
        return None
