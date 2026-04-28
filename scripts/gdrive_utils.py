import os
import re
import asyncio


def fix_gdrive_url(url: str) -> str:
    """
    Fix Google Drive / UserContent URLs to a direct download form.
    Handles:
      - drive.usercontent.google.com/u/0/uc?id=XXX  → standardised
      - drive.google.com/file/d/ID/view              → uc?id=ID
      - drive.google.com/uc?id=ID                    → unchanged
    """
    # Already a uc?id= URL — pass through
    if "drive.google.com/uc?id=" in url:
        return url

    # usercontent.google.com → extract id param
    if "usercontent.google.com" in url:
        m = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
        if m:
            return f"https://drive.google.com/uc?id={m.group(1)}"
        return url

    # /file/d/<ID>/... → uc?id=ID
    m = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if m:
        return f"https://drive.google.com/uc?id={m.group(1)}"

    # /open?id=ID  or  ?id=ID  fallback
    m = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    if m:
        return f"https://drive.google.com/uc?id={m.group(1)}"

    return url


def gdown_blocking(url: str, out_dir: str):
    """
    Synchronous gdown download with:
      - UserContent / weird GDrive URL fix applied first
      - fuzzy= TypeError handled for older gdown versions
    Returns absolute path of downloaded file, or None on failure.
    """
    try:
        import gdown
    except ImportError as _ie:
        raise Exception("gdown not installed. Run: pip install gdown") from _ie

    os.makedirs(out_dir, exist_ok=True)
    cwd = os.getcwd()
    try:
        os.chdir(out_dir)
        fixed = fix_gdrive_url(url)
        # Attempt 1: with fuzzy=True (newer gdown versions)
        try:
            result = gdown.download(url=fixed, quiet=True, fuzzy=True)
        except TypeError:
            # Older gdown does not support fuzzy=
            result = gdown.download(url=fixed, quiet=True)

        if not result:
            # Try folder download as fallback
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
    """
    Upload local_path to user's Google Drive root using token.pickle.
    Returns the webViewLink of the created file.
    Raises a clear exception if token.pickle is corrupted / text-mode saved.
    """
    def _sync():
        import pickle
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        # Clear error for corrupted pickle (common \xef BOM / invalid load key)
        try:
            with open(token_path, "rb") as f:
                creds = pickle.load(f)
        except Exception as _pe:
            raise Exception(
                "❌ token.pickle is corrupted or invalid!\n"
                "The file may have been saved in text mode or is empty.\n"
                "Please generate a fresh token.pickle and upload it again via "
                "/usersettings → Mirror Configs.\n"
                f"(Detail: {_pe})"
            )

        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        media   = MediaFileUpload(local_path, resumable=True)
        meta    = {"name": os.path.basename(local_path)}
        created = service.files().create(
            body=meta, media_body=media, fields="id, webViewLink"
        ).execute()
        return (
            created.get("webViewLink")
            or f"https://drive.google.com/file/d/{created.get('id')}/view"
        )

    return await asyncio.to_thread(_sync)
