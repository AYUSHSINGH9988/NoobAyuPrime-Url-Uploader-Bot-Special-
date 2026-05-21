"""
Archive / compress / split helpers.
"""

import os
import shutil
import subprocess
import time

from bot.helper.time_format import natural_sort_key


def extract_archive(file_path: str) -> tuple[list, str | None, str | None]:
    output_dir = f"extracted_{int(time.time())}"
    os.makedirs(output_dir, exist_ok=True)
    if not shutil.which("7z"):
        return [], None, "7z not found — install p7zip."
    cmd = ["7z", "x", str(file_path), f"-o{output_dir}", "-y"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files_list: list[str] = []
    for root, _, files in os.walk(output_dir):
        for file in files:
            files_list.append(os.path.join(root, file))
    files_list.sort(key=natural_sort_key)
    if not files_list:
        shutil.rmtree(output_dir, ignore_errors=True)
        return [], None, "No files found after extraction."
    return files_list, output_dir, None


def create_zip(file_path: str) -> tuple[str, bool]:
    if not shutil.which("7z"):
        return file_path, False
    zip_path = file_path + ".zip"
    cmd = ["7z", "a", zip_path, file_path, "-mx1"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return (zip_path, True) if os.path.exists(zip_path) else (file_path, False)


def split_large_file(file_path: str, limit_mb: int = 2000) -> tuple[list[str], bool]:
    limit = limit_mb * 1024 * 1024
    if os.path.getsize(file_path) <= limit:
        return [file_path], False
    out_dir = f"split_{int(time.time())}"
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        "7z", "a",
        f"-v{limit_mb}m",
        os.path.join(out_dir, os.path.basename(file_path) + ".7z"),
        file_path, "-mx0",
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL)
    parts = sorted(
        [os.path.join(out_dir, f) for f in os.listdir(out_dir)],
        key=natural_sort_key,
    )
    return (parts, True) if parts else ([file_path], False)
