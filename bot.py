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

# تنظیمات محیطی Render
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


# ---------- DATABASE ----------

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
        )
        if not (result.data or []):
            return code


def save_file(code, chat_id, message_id):
    result = (
        db.table("files")
        .insert({
            "code": code,
            "chat_id": chat_id,
            "message_id": message_id,
        })
        .execute()
    )
    return result.data[0]


def increment_downloads(file_id):
    db.rpc(
        "increment_file_downloads",
        {"p_file_id": file_id},
    ).execute()


# ---------- CHANNEL MEMBERSHIP ----------

async def check_membership(context, user_id):
    try:
        member = await context.bot.get_chat_member(
            chat_id=CHANNEL,
            user_id=user_id,
        )
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception:
        logger.exception("Membership check failed")
        return False


# ---------- START ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    try:
        if not context.args:
            admin_id = get_setting("admin_id")

            if admin_id is None:
                await message.reply_text(
                    "سلام، خوش اومدی! 🌸\n\n"
                    "برای شروع، دستور /claim رو بفرست تا مدیر ربات ثبت بشه."
                )
            elif is_admin(user.id):
                await message.reply_text(
                    "سلام، من آماده‌ام! 💗\n\n"
                    "فایلت رو برام بفرست تا لینک دریافتش رو بسازم.\n"
                    "📊 آ
