"""
Central store for all runtime state dicts.
Import these from here — never redefine them in individual modules.
"""

# msg_id -> dict(user_id, user_name, name, action, current, total, speed, eta, start_time, engine)
ACTIVE_TASKS: dict = {}

# msg_id -> True  (set to cancel a task)
abort_dict: dict = {}

# msg_id -> last_update_time (float)
progress_status: dict = {}

# user_id -> list of (url, message, mode, target, rename, seed)
user_queues: dict = {}

# user_id -> bool
is_processing: dict = {}

# msg_id -> session dict for yt-dlp quality picker
ytdl_session: dict = {}

# gid -> True  (seeding torrents)
seeding_gids: dict = {}

# user_id -> True  (waiting for thumbnail photo)
waiting_for_thumbnail: dict = {}

# user_id -> "rclone" | "gdrive"
waiting_for_config_upload: dict = {}

# user_id -> True  (waiting for proxy URL text)
waiting_for_proxy: dict = {}

# user_id -> setting_key  (admin bsettings input)
waiting_for_bsetting: dict = {}

# chat_id -> {"message": Message, "task": asyncio.Task}
GLOBAL_STATUS: dict = {}

# msg_id -> phub select session
phub_select_sessions: dict = {}

# failed scriptdl links per user: user_id -> list[str]
failed_scriptdl: dict = {}

# Pending quality selections: msg_id -> dict
pending_selections: dict = {}

# msg_id -> renameall session dict (keyed by original message id)
renameall_sessions: dict = {}

# user_id -> msg_id  (waiting for renameall text input)
waiting_for_renameall_text: dict = {}

# aria2 client (set at startup)
aria2 = None
