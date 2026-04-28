"""
scripts/dm.py — Dailymotion downloader using yt-dlp format selectors.
Place this file in the scripts/ folder.
"""
import re
import secrets
import yt_dlp


def get_dm_data(url: str, proxy: str = None):
    """
    Extract Dailymotion video info and return quality options.
    Uses yt-dlp format selector strings as format_id so scriptdl_quality_cb
    can pass them directly to _blocking_download without a pre-muxed direct URL.

    Returns dict with keys: title, formats, original_info
    or None on failure.
    """
    ydl_opts = {
        "quiet":       True,
        "no_warnings": True,
    }
    if proxy:
        ydl_opts["proxy"] = proxy

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title = info.get("title") or f"DM_{secrets.token_hex(3)}"
        # Strip characters that break file paths
        clean_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or f"DM_{secrets.token_hex(3)}"

        formats = []
        for h in (360, 480, 720, 1080):
            formats.append({
                # format_id IS the yt-dlp selector string — scriptdl passes it straight to yt-dlp
                "format_id": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best",
                "height":    str(h),
                # url = original DM page URL (passed back for Referer header etc.)
                "url":       url,
                "ext":       "mp4",
            })

        return {
            "title":         clean_title,
            "formats":       formats,
            "original_info": {
                "title":     clean_title,
                "thumbnail": info.get("thumbnail", ""),
                "duration":  info.get("duration", 0),
            },
        }

    except Exception as e:
        print(f"[dm.py] Extraction error: {e}")
        return None


def download_dm():
    """Stub — kept so `from scripts.dm import download_dm` doesn't fail."""
    pass
