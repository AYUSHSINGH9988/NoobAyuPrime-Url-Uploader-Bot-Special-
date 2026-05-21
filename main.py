"""
Entry point for the Telegram Leech Bot.
Modular wzml-x style structure.
"""

import asyncio
import importlib
import os
import subprocess
import sys

import aria2p
import pyrogram
import yt_dlp

from bot.core.client import app, user_app
from bot.core.config import PORT
from bot.core.database.mongo import init_db
from bot.helper import progress as _prog
from bot.helper.download_utils import init_aria2


# ── Version probes ─────────────────────────────────────────────────────────────

def _get_aria2c_version() -> str:
    try:
        r = subprocess.run(["aria2c", "--version"], capture_output=True, text=True, timeout=5)
        return r.stdout.split("\n")[0].replace("aria2 version ", "").strip()
    except Exception:
        return "N/A"


def _get_ytdlp_version() -> str:
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception:
        return "N/A"


def _get_pyrogram_version() -> str:
    try:
        return pyrogram.__version__
    except Exception:
        return "N/A"


# ── Module import list ─────────────────────────────────────────────────────────
# Strict group lock must be first (group=-3).
_MODULES = [
    "bot.modules.group_lock",
    "bot.modules.start",
    "bot.modules.status",
    "bot.modules.settings",
    "bot.modules.dumps",
    "bot.modules.leech",
    "bot.modules.ytdl",
    "bot.modules.scriptdl",
    "bot.modules.bunkr",
    "bot.modules.teradl",
    "bot.modules.mega",
    "bot.modules.tgdl",
    "bot.modules.mirror",
    "bot.modules.cmd",
    "bot.modules.video_tools",
    "bot.modules.admin",
    "bot.modules.renameall",
    "bot.modules.text_capture",
]


def _load_modules():
    for mod in _MODULES:
        try:
            importlib.import_module(mod)
            print(f"[✓] {mod}")
        except Exception as e:
            print(f"[✗] {mod}: {e}")


# ── aria2c daemon ──────────────────────────────────────────────────────────────

def _start_aria2c():
    try:
        subprocess.Popen(
            [
                "aria2c",
                "--enable-rpc",
                "--rpc-listen-all=false",
                "--rpc-listen-port=6800",
                "--max-connection-per-server=10",
                "--rpc-max-request-size=1024M",
                "--seed-time=0",
                "--max-upload-limit=10K",
                "--max-concurrent-downloads=5",
                "--min-split-size=10M",
                "--follow-torrent=mem",
                "--split=10",
                "--bt-save-metadata=false",
                "--daemon=true",
                "--allow-overwrite=true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time
        time.sleep(3)
        print("[aria2c] Daemon started")
    except Exception as e:
        print(f"[aria2c] Could not start: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    # Versions
    _prog.ARIA2C_VERSION   = _get_aria2c_version()
    _prog.YTDLP_VERSION    = _get_ytdlp_version()
    _prog.PYROGRAM_VERSION = _get_pyrogram_version()

    print(f"\n╔══════════════════════════════════════╗")
    print(f"║  Leech Bot — Modular wzml-x edition  ║")
    print(f"╠══════════════════════════════════════╣")
    print(f"║  aria2c   : {_prog.ARIA2C_VERSION:<25}║")
    print(f"║  yt-dlp   : {_prog.YTDLP_VERSION:<25}║")
    print(f"║  pyrogram : {_prog.PYROGRAM_VERSION:<25}║")
    print(f"╚══════════════════════════════════════╝\n")

    # Start aria2c daemon
    _start_aria2c()

    # Connect aria2p client
    init_aria2()

    # Connect DB
    await init_db()

    # Load all handler modules (registers @app.on_message decorators)
    _load_modules()

    # Startup: create needed dirs
    for d in ("downloads", "thumbnails", "user_configs"):
        os.makedirs(d, exist_ok=True)

    # Start user client if set
    if user_app:
        try:
            await user_app.start()
            me = await user_app.get_me()
            print(f"[user_app] Logged in as: {me.first_name} ({me.id})")
        except Exception as e:
            print(f"[user_app] Could not start: {e}")

    # Start bot
    await app.start()
    me = await app.get_me()
    print(f"\n✅ Bot started as @{me.username} ({me.id})")
    print("   Send /start to begin.\n")

    await pyrogram.idle()

    # Graceful shutdown
    await app.stop()
    if user_app:
        with __import__("contextlib").suppress(Exception):
            await user_app.stop()


if __name__ == "__main__":
   loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
