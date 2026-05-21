import os

API_ID              = int(os.environ.get("API_ID", 0))
API_HASH            = os.environ.get("API_HASH", "")
BOT_TOKEN           = os.environ.get("BOT_TOKEN", "")
MONGO_URL           = os.environ.get("MONGO_URL", "")
OWNER_ID            = int(os.environ.get("OWNER_ID", 0))
PORT                = int(os.environ.get("PORT", 8080))
BASE_URL            = os.environ.get("BASE_URL", "").rstrip("/")

RCLONE_PATH         = os.environ.get("RCLONE_PATH", "remote:")
PROXY_URL           = os.environ.get("PROXY_URL", None)

ALLOWED_GROUP_ID    = int(os.environ.get("ALLOWED_GROUP_ID", "0"))
AUTH_GROUP          = ALLOWED_GROUP_ID
JOIN_LINK           = os.environ.get("JOIN_LINK", "")
DEV_NAME            = os.environ.get("DEV_NAME", "Admin")

USER_SESSION_STRING = os.environ.get("USER_SESSION_STRING") or os.environ.get("STRING_SESSION")
AUTO_DELETE_SECONDS = int(os.environ.get("AUTO_DELETE_SECONDS", "60"))

USER_CONFIG_DIR     = "user_configs"

COOKIES_FILE = None
for _p in ["cookies.txt", os.path.expanduser("~/cookies.txt")]:
    if os.path.exists(_p):
        COOKIES_FILE = _p
        break
