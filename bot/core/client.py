import os
from pyrogram import Client, enums
from bot.core.config import (
    API_ID, API_HASH, BOT_TOKEN, USER_SESSION_STRING,
    ALLOWED_GROUP_ID,
)
from contextlib import suppress

app = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML,
    workers=16,
    max_concurrent_transmissions=5,
)

user_app = None
if USER_SESSION_STRING:
    try:
        user_app = Client(
            "user_session",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=USER_SESSION_STRING,
            parse_mode=enums.ParseMode.HTML,
            no_updates=True,
            in_memory=True,
        )
    except Exception as _ue:
        print(f"[user_app] Failed to construct: {_ue}")
        user_app = None
