import os
import re
import asyncio


def fix_gdrive_url(url: str) -> str:
    if "drive.google.com/uc?id=" in url:
        return url
    if "usercontent.google.com" in url:
        m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
        if m:
            return f"https://drive.google.com/uc?id={m.group(1)}"
        return url
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://drive.google.com/uc?id={m.group(1)}"
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://drive.google.com/uc?id={m.group(1)}"
    return url


def gdown_blocking(url: str, out_dir: str):
    try:
        import gdown
    except ImportError:
        raise Exception("gdown not installed.")
    os.makedirs(out_dir, exist_ok=True)
    cwd = os.getcwd()
    try:
        os.chdir(out_dir)
        fixed = fix_gdrive_url(url)
        try:
            result = gdown.download(url=fixed, quiet=True, fuzzy=True)
        except TypeError:
            result = gdown.download(url=fixed, quiet=True)
        if not result:
            try:
                listing = gdown.download_folder(url=fixed, quiet=True, use_cookies=False)
                if listing and len(listing) == 1:
                    return os.path.abspath(listing[0])
            except Exception:
                pass
            return None
        return os.path.abspath(result)
    finally:
        os.chdir(cwd)


async def gdrive_upload_with_token(local_path: str, token_path: str) -> str:
    def _sync():
        import pickle
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        try:
            with open(token_path, "rb") as f:
                creds = pickle.load(f)
        except Exception as _pe:
            raise Exception(f"token.pickle corrupted: {_pe}")
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        media   = MediaFileUpload(local_path, resumable=True)
        meta    = {"name": os.path.basename(local_path)}
        created = service.files().create(body=meta, media_body=media, fields="id, webViewLink").execute()
        return created.get("webViewLink") or f"https://drive.google.com/file/d/{created.get('id')}/view"
    return await asyncio.to_thread(_sync)
