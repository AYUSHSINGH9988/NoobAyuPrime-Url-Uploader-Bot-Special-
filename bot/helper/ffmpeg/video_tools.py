"""
Video tools module (-vt flag):
  - Extract MP3 audio from video
  - Merge MP3 audio into video
  - Extract subtitles from video
  - Merge subtitle file into video (soft-sub)
"""

import asyncio
import os
import shutil


async def extract_mp3(video_path: str, output_dir: str | None = None) -> tuple[str | None, str | None]:
    """
    Extract audio track as MP3 from a video file.
    Returns (output_path, error_msg).
    """
    if not shutil.which("ffmpeg"):
        return None, "ffmpeg not found."
    out_dir  = output_dir or os.path.dirname(video_path) or "."
    basename = os.path.splitext(os.path.basename(video_path))[0]
    out_path = os.path.join(out_dir, f"{basename}_audio.mp3")
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k",
        out_path, "-y",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        return None, err.decode(errors="ignore")[-300:]
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path, None
    return None, "Output file not created."


async def merge_audio_into_video(
    video_path: str,
    audio_path: str,
    output_dir: str | None = None,
) -> tuple[str | None, str | None]:
    """
    Merge an external audio file into a video, replacing its audio track.
    Returns (output_path, error_msg).
    """
    if not shutil.which("ffmpeg"):
        return None, "ffmpeg not found."
    out_dir  = output_dir or os.path.dirname(video_path) or "."
    basename = os.path.splitext(os.path.basename(video_path))[0]
    out_path = os.path.join(out_dir, f"{basename}_merged.mp4")
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        out_path, "-y",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        return None, err.decode(errors="ignore")[-300:]
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path, None
    return None, "Output file not created."


async def extract_subtitles(
    video_path: str,
    stream_index: int = 0,
    output_dir: str | None = None,
    fmt: str = "srt",
) -> tuple[str | None, str | None]:
    """
    Extract subtitle stream from a video file.
    Returns (output_path, error_msg).
    """
    if not shutil.which("ffmpeg"):
        return None, "ffmpeg not found."
    out_dir  = output_dir or os.path.dirname(video_path) or "."
    basename = os.path.splitext(os.path.basename(video_path))[0]
    out_path = os.path.join(out_dir, f"{basename}_sub{stream_index}.{fmt}")
    cmd = [
        "ffmpeg", "-i", video_path,
        "-map", f"0:s:{stream_index}",
        out_path, "-y",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        msg = err.decode(errors="ignore")
        if "subtitle stream" in msg.lower() or "does not contain" in msg.lower():
            return None, f"No subtitle stream #{stream_index} found in this video."
        return None, msg[-300:]
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path, None
    return None, "Output subtitle file not created."


async def merge_subtitle_into_video(
    video_path: str,
    sub_path: str,
    output_dir: str | None = None,
) -> tuple[str | None, str | None]:
    """
    Soft-mux a subtitle file into a video (adds as subtitle stream, does NOT burn in).
    Returns (output_path, error_msg).
    """
    if not shutil.which("ffmpeg"):
        return None, "ffmpeg not found."
    out_dir  = output_dir or os.path.dirname(video_path) or "."
    basename = os.path.splitext(os.path.basename(video_path))[0]
    out_path = os.path.join(out_dir, f"{basename}_subbed.mkv")
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-i", sub_path,
        "-map", "0",
        "-map", "1",
        "-c", "copy",
        out_path, "-y",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        return None, err.decode(errors="ignore")[-300:]
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path, None
    return None, "Output file not created."
