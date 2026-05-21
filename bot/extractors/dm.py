import yt_dlp


def get_dm_data(url, proxy=None):
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
        return {"title": info.get("title", "DM Video"), "formats": formats}
    except Exception as e:
        print(f"DM Error: {e}")
        return None
