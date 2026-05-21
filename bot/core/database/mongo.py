from motor.motor_asyncio import AsyncIOMotorClient
from bot.core.config import MONGO_URL

mongo_client = None
db           = None
users_col    = None
bot_settings_col = None


async def init_db():
    global mongo_client, db, users_col, bot_settings_col
    try:
        mongo_client   = AsyncIOMotorClient(MONGO_URL)
        db             = mongo_client["URL_Uploader_Bot"]
        users_col      = db["users"]
        bot_settings_col = db["bot_settings"]
        print("MongoDB Connected!")
    except Exception as e:
        print(f"MongoDB Failed: {e}")


# ── Dump helpers ──────────────────────────────────────────────────────────────

async def add_dump(user_id, chat_id, chat_title):
    user = await users_col.find_one({"_id": user_id})
    new_dump = {"id": chat_id, "title": chat_title}
    if not user:
        await users_col.insert_one({"_id": user_id, "dumps": [new_dump], "active_dump": chat_id})
    else:
        dumps = user.get("dumps", [])
        if not any(d["id"] == chat_id for d in dumps):
            dumps.append(new_dump)
        await users_col.update_one(
            {"_id": user_id},
            {"$set": {"dumps": dumps, "active_dump": chat_id}},
        )


async def get_user_dumps(user_id):
    user = await users_col.find_one({"_id": user_id})
    return user.get("dumps", []) if user else []


async def set_active_dump(user_id, chat_id):
    await users_col.update_one({"_id": user_id}, {"$set": {"active_dump": chat_id}}, upsert=True)


async def get_active_dump(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return None
    active_id = user.get("active_dump")
    if not active_id:
        return None
    for d in user.get("dumps", []):
        if d["id"] == active_id:
            return d
    return None


async def delete_dump(user_id, chat_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return
    dumps = [d for d in user.get("dumps", []) if d["id"] != chat_id]
    active = user.get("active_dump")
    if active == chat_id:
        active = dumps[0]["id"] if dumps else None
    await users_col.update_one(
        {"_id": user_id},
        {"$set": {"dumps": dumps, "active_dump": active}},
    )


# ── User settings helpers ─────────────────────────────────────────────────────

async def get_user_settings(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return {"send_as": "media", "thumbnail": None}
    return {
        "send_as":   user.get("send_as", "media"),
        "thumbnail": user.get("thumbnail", None),
    }


async def set_user_setting(user_id, key, value):
    await users_col.update_one({"_id": user_id}, {"$set": {key: value}}, upsert=True)


async def get_user_thumbnail(user_id):
    user = await users_col.find_one({"_id": user_id})
    return user.get("thumbnail", None) if user else None


async def set_user_thumbnail(user_id, file_id):
    await users_col.update_one({"_id": user_id}, {"$set": {"thumbnail": file_id}}, upsert=True)


async def clear_user_thumbnail(user_id):
    await users_col.update_one({"_id": user_id}, {"$unset": {"thumbnail": ""}})


# ── Per-user proxy ────────────────────────────────────────────────────────────

async def get_user_proxy(user_id):
    if users_col is None or not user_id:
        return None
    try:
        u = await users_col.find_one({"_id": user_id})
    except Exception:
        return None
    return (u.get("proxy") or None) if u else None


async def set_user_proxy(user_id, proxy_url):
    await users_col.update_one({"_id": user_id}, {"$set": {"proxy": proxy_url}}, upsert=True)


async def clear_user_proxy(user_id):
    await users_col.update_one({"_id": user_id}, {"$unset": {"proxy": ""}})


# ── Ban / warn ────────────────────────────────────────────────────────────────

async def is_user_banned(user_id):
    if users_col is None:
        return False
    user = await users_col.find_one({"_id": user_id})
    return bool(user and user.get("is_banned"))


async def ensure_user(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        await users_col.insert_one({
            "_id": user_id, "dumps": [], "active_dump": None,
            "warns": 0, "is_banned": False,
        })


# ── Bot settings (admin limits) ───────────────────────────────────────────────

DEFAULT_BSETTINGS = {
    "max_tasks_per_user": 3,
    "max_size_gb_ytdl":   8,
    "max_size_gb_mdl":    8,
    "max_size_gb_bdl":    8,
    "max_size_gb_leech":  8,
}


async def get_bsettings():
    if bot_settings_col is None:
        return dict(DEFAULT_BSETTINGS)
    try:
        doc = await bot_settings_col.find_one({"_id": "global"}) or {}
    except Exception:
        doc = {}
    out = dict(DEFAULT_BSETTINGS)
    for k in DEFAULT_BSETTINGS:
        if k in doc:
            try:
                out[k] = float(doc[k]) if "size" in k else int(doc[k])
            except Exception:
                pass
    return out


async def set_bsetting(key, value):
    if bot_settings_col is None or key not in DEFAULT_BSETTINGS:
        return False
    try:
        await bot_settings_col.update_one(
            {"_id": "global"}, {"$set": {key: value}}, upsert=True
        )
        return True
    except Exception:
        return False
