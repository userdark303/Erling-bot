import logging
import os
import secrets

from supabase import create_client
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==================== SETTINGS ====================

TOKEN = os.environ["BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

CHANNEL = os.getenv("CHANNEL_USERNAME", "@AnimeArmyChan")
DELETE_AFTER = int(os.getenv("DELETE_AFTER_SECONDS", "20"))
PORT = int(os.getenv("PORT", "8000"))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

db = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("erling")


# ==================== DATABASE ====================

def get_setting(key):
    result = (
        db.table("settings")
        .select("value")
        .eq("key", key)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0]["value"] if rows else None


def is_admin(user_id):
    admin_id = get_setting("admin_id")
    return bool(admin_id) and int(admin_id) == user_id


def get_file(code):
    result = (
        db.table("files")
        .select("*")
        .eq("code", code)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def generate_code():
    while True:
        code = secrets.token_urlsafe(8)

        result = (
            db.table("files")
            .select("id")
            .eq("code", code)
            .limit(1)
            .execute()
