"""
Video helpers: screenshot, duration probe, streamable conversion.
"""

import asyncio
import os
import subprocess
import time


async def get_video_duration(file_path: str) -> int:
    try:
        result = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await result.communicate()
        return int(float(out.decode().strip()))
    except Exception:
        return 0


async def take_screenshot(video_path: str, duration: int = 1) -> str | None:
    try:
        seek = max(1, duration // 4)
        out  = video_path + "_thumb.jpg"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-ss", str(seek), "-i", video_path,
            "-vframes", "1", "-q:v", "2", out, "-y",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return out if os.path.exists(out) and os.path.getsize(out) > 0 else None
    except Exception:
        return None


async def convert_to_streamable(file_path: str, message) -> tuple[str, bool]:
    """Re-encode non-streamable formats to mp4 via ffmpeg."""
    import shutil
    if not shutil.which("ffmpeg"):
        return file_path, False
    out = os.path.splitext(file_path)[0] + "_stream.mp4"
    cmd = [
        "ffmpeg", "-i", file_path,
        "-c:v", "libx264", "-crf", "23", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "128k",
        out, "-y",
    ]
    try:
        await message.edit_text("🔄 <b>Converting to streamable mp4...</b>")
    except Exception:
        pass
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out, True
    return file_path, False
