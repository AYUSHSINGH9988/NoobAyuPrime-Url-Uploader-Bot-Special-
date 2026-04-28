import os
import subprocess

QUOTA_ENV = {
    "MEGA_IGNORE_UPLOAD_QUOTA":      "1",
    "MEGA_FORCE_FULL_ACCOUNT_CACHE": "1",
}


def megacmd_login(email: str, password: str):
    """
    Login to MEGA via MegaCMD.
    - Force-logout first to clear any stale/corrupt session.
    - Returns (True, None) on success, (False, error_str) on failure.
    """
    env = os.environ.copy()
    env.update(QUOTA_ENV)

    # Force-clear stale session BEFORE login attempt
    subprocess.run(
        ["mega-logout"],
        capture_output=True, text=True, timeout=15, env=env
    )

    r = subprocess.run(
        ["mega-login", email, password],
        capture_output=True, text=True, timeout=60, env=env
    )
    already_in = "Already logged in" in (r.stdout + r.stderr)
    if r.returncode == 0 or already_in:
        return True, None

    # Retry with --no-ask-for-confirmation
    r2 = subprocess.run(
        ["mega-login", "--no-ask-for-confirmation", email, password],
        capture_output=True, text=True, timeout=60, env=env
    )
    already_in2 = "Already logged in" in (r2.stdout + r2.stderr)
    if r2.returncode == 0 or already_in2:
        return True, None

    err = (r.stderr or r.stdout or r2.stderr or r2.stdout or "").strip()
    return False, err or "mega-login failed (unknown error)"


def megacmd_download(url: str, out_dir: str):
    """
    Download a MEGA public link via mega-get.
    Returns the path of the downloaded file/folder, or None on failure.
    """
    os.makedirs(out_dir, exist_ok=True)
    env = os.environ.copy()
    env.update(QUOTA_ENV)

    r = subprocess.run(
        ["mega-get", "--ignore-quota-warn", url, out_dir],
        capture_output=True, text=True, timeout=3600, env=env
    )
    if r.returncode == 0:
        files = [f for f in os.listdir(out_dir) if not f.startswith(".")]
        if len(files) == 1:
            return os.path.join(out_dir, files[0])
        if files:
            return out_dir   # folder with multiple files
    return None
