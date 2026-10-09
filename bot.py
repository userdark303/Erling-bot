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
        .maybe_single()
        .execute()
    )
    return result.data["value"] if result.data else None


def is_admin(user_id):
    admin_id = get_setting("admin_id")
    return bool(admin_id) and int(admin_id) == user_id


def get_file(code):
    result = (
        db.table("files")
        .select("*")
        .eq("code", code)
        .maybe_single()
        .execute()
    )
    return result.data


def generate_code():
    while True:
        code = secrets.token_urlsafe(8)
        result = (
            db.table("files")
            .select("id")
            .eq("code", code)
            .maybe_single()
            .execute()
        )
        if not result.data:
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


# ==================== CHANNEL CHECK ====================

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


# ==================== /START ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not context.args:
        if get_setting("admin_id") is None:
            await message.reply_text(
                "سلام 👋\n\n"
                "به ربات Erling خوش آمدی.\n\n"
                "برای ثبت مدیر ربات دستور زیر را بفرست:\n\n"
                "/claim"
            )
        elif is_admin(user.id):
            await message.reply_text(
                "🤖 Erling آماده است!\n\n"
                "فایل موردنظر را برای من بفرست تا لینک اختصاصی آن ساخته شود.\n\n"
                "📊 /stats"
            )
        else:
            await message.reply_text(
                "سلام 👋\n\n"
                "برای دریافت فایل، لینک اختصاصی آن را باز کن."
            )
        return

    code = context.args[0]
    file_data = get_file(code)

    if not file_data:
        await message.reply_text(
            "❌ این لینک معتبر نیست یا فایل پیدا نشد."
        )
        return

    if not await check_membership(context, user.id):
        keyboard = [
            [
                InlineKeyboardButton(
                    "📢 عضویت در AnimeArmy",
                    url="https://t.me/AnimeArmyChan",
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ بررسی عضویت",
                    callback_data=f"check:{code}",
                )
            ],
        ]

        await message.reply_text(
            "🔒 برای دریافت فایل ابتدا باید عضو کانال AnimeArmy شوی.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    await send_file(message.chat_id, context, file_data)


# ==================== /CLAIM ====================

async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    current_admin = get_setting("admin_id")

    if current_admin:
        if int(current_admin) == user.id:
            await update.effective_message.reply_text(
                "✅ تو قبلاً مدیر Erling هستی."
            )
        else:
            await update.effective_message.reply_text(
                "❌ مدیر ربات قبلاً تعیین شده است."
            )
        return

    # ثبت مدیر بدون جایگزین کردن مدیر قبلی
    try:
        db.table("settings").insert({
            "key": "admin_id",
            "value": str(user.id),
        }).execute()
    except Exception:
        logger.exception("Admin claim failed")
        await update.effective_message.reply_text(
            "❌ ثبت مدیر انجام نشد. لطفاً دوباره تلاش کن."
        )
        return

    await update.effective_message.reply_text(
        "✅ حساب شما به‌عنوان مدیر Erling ثبت شد!\n\n"
        "حالا یک فایل برای من بفرست."
    )


# ==================== SEND FILE ====================

async def send_file(chat_id, context, file_data):
    try:
        sent = await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=int(file_data["chat_id"]),
            message_id=int(file_data["message_id"]),
        )

        try:
            increment_downloads(int(file_data["id"]))
        except Exception:
            logger.exception("Download counter update failed")

        if context.job_queue is not None:
            context.job_queue.run_once(
                delete_message,
                DELETE_AFTER,
                data={
                    "chat_id": chat_id,
                    "message_id": sent.message_id,
                },
            )
        else:
            logger.error("JobQueue is unavailable; deletion not scheduled")

    except Exception:
        logger.exception("File delivery failed")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ هنگام ارسال فایل مشکلی رخ داد.",
            )
        except Exception:
            logger.exception("Could not send error notification")


# ==================== DELETE AFTER 20 SECONDS ====================

async def delete_message(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.delete_message(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
        )
        logger.info("Delivered message deleted")
    except Exception:
        logger.exception("Message deletion failed")


# ==================== MEMBERSHIP BUTTON ====================

async def membership_check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    code = query.data.split(":", 1)[1]
    file_data = get_file(code)

    if not file_data:
        await query.answer()
        await query.edit_message_text("❌ فایل پیدا نشد.")
        return

    if not await check_membership(context, query.from_user.id):
        await query.answer(
            "❌ هنوز عضو کانال نیستی.",
            show_alert=True,
        )
        return

    await query.answer()

    try:
        await query.message.delete()
    except Exception:
        logger.exception("Could not remove membership prompt")

    await send_file(
        query.message.chat_id,
        context,
        file_data,
    )


# ==================== RECEIVE FILE ====================

async def receive_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    message = update.effective_message

    if not is_admin(user.id):
        await message.reply_text(
            "❌ فقط مدیر می‌تواند فایل آپلود کند."
        )
        return

    try:
        code = generate_code()
        save_file(code, message.chat_id, message.message_id)

        bot_info = await context.bot.get_me()
        link = f"https://t.me/{bot_info.username}?start={code}"

        await message.reply_text(
            "✅ فایل با موفقیت ثبت شد!\n\n"
            "🔗 لینک دریافت:\n"
            f"{link}\n\n"
            f"⏱ فایل بعد از {DELETE_AFTER} ثانیه حذف می‌شود."
        )
    except Exception:
        logger.exception("File registration failed")
        await message.reply_text(
            "❌ ثبت فایل انجام نشد. لطفاً دوباره تلاش کن."
        )


# ==================== /STATS ====================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        total_files = 0
        total_downloads = 0
        offset = 0
        page_size = 1000

        while True:
            result = (
                db.table("files")
                .select("id,downloads")
                .range(offset, offset + page_size - 1)
                .execute()
            )

            rows = result.data or []
            total_files += len(rows)
            total_downloads += sum(
                int(row.get("downloads") or 0)
                for row in rows
            )

            if len(rows) < page_size:
                break

            offset += page_size

        await update.effective_message.reply_text(
            "📊 آمار Erling\n\n"
            f"📁 تعداد فایل‌ها: {total_files}\n\n"
            f"📥 تعداد دریافت‌ها: {total_downloads}"
        )
    except Exception:
        logger.exception("Statistics query failed")
        await update.effective_message.reply_text(
            "❌ دریافت آمار با مشکل روبه‌رو شد."
        )


# ==================== APPLICATION ====================

application = Application.builder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("claim", claim))
application.add_handler(CommandHandler("stats", stats))

application.add_handler(
    CallbackQueryHandler(
        membership_check,
        pattern=r"^check:",
    )
)

application.add_handler(
    MessageHandler(
        filters.Document.ALL
        | filters.VIDEO
        | filters.AUDIO
        | filters.PHOTO,
        receive_file,
    )
)


# ==================== WEBHOOK / RENDER ====================

if __name__ == "__main__":
    if not RENDER_URL:
        raise RuntimeError(
            "RENDER_EXTERNAL_URL is missing. "
            "Run this application as a Render Web Service."
        )

    webhook_url = f"{RENDER_URL}/telegram"

    logger.info("Starting Erling...")
    logger.info("Webhook URL: %s", webhook_url)

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="telegram",
        webhook_url=webhook_url,
    )
