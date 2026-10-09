import os
import secrets
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from supabase import create_client, Client


# -------------------- Configuration --------------------

BOT_TOKEN = os.environ["BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME", "@AnimeArmyChan"
)

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL", ""
).rstrip("/")

PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_PATH = "telegram"
DELETE_AFTER_SECONDS = int(
    os.getenv("DELETE_AFTER_SECONDS", "20")
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
)


# -------------------- Database --------------------

def get_file(code: str):
    result = (
        supabase.table("files")
        .select("*")
        .eq("code", code)
        .limit(1)
        .execute()
    )

    return result.data[0] if result.data else None


def save_file(code: str, chat_id: int, message_id: int):
    result = (
        supabase.table("files")
        .insert({
            "code": code,
            "chat_id": chat_id,
            "message_id": message_id,
        })
        .execute()
    )

    return result.data[0] if result.data else None


def increment_downloads(file_id: int):
    return supabase.rpc(
        "increment_file_downloads",
        {"p_file_id": file_id},
    ).execute()


def get_total_stats():
    result = (
        supabase.table("files")
        .select("id, downloads")
        .execute()
    )

    files = result.data or []
    total_files = len(files)
    total_downloads = sum(
        int(item.get("downloads", 0)) for item in files
    )

    return total_files, total_downloads


# -------------------- Helpers --------------------

async def is_channel_admin(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> bool:
    try:
        member = await context.bot.get_chat_member(
            CHANNEL_USERNAME,
            user_id,
        )

        return member.status in ("administrator", "creator")

    except TelegramError:
        logger.exception("Could not check channel administrator")
        return False


async def is_subscribed(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> bool:
    try:
        member = await context.bot.get_chat_member(
            CHANNEL_USERNAME,
            user_id,
        )

        if member.status in ("creator", "administrator", "member"):
            return True

        if member.status == "restricted":
            return bool(getattr(member, "is_member", False))

        return False

    except TelegramError:
        logger.exception("Could not check channel membership")
        return False


def join_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "عضویت در کانال 📢",
                url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}",
            )
        ],
        [
            InlineKeyboardButton(
                "بررسی عضویت ✅",
                callback_data="check_subscription",
            )
        ],
    ])


# -------------------- Start / Download --------------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    if not context.args:
        await message.reply_text(
            "سلام، من آماده‌ام! 💗\n\n"
            "برای دریافت فایل، لینک اختصاصی آن را باز کن.\n"
            "اگر مدیر کانال هستی، می‌توانی فایل را هم برای من بفرستی."
        )
        return

    code = context.args[0]

    try:
        file_record = get_file(code)
    except Exception:
        logger.exception("Database error while retrieving file")
        await message.reply_text(
            "فعلاً در دریافت فایل مشکلی پیش آمده. کمی بعد دوباره تلاش کن."
        )
        return

    if not file_record:
        await message.reply_text(
            "این لینک معتبر نیست یا فایل موردنظر پیدا نشد. ❌"
        )
        return

    context.user_data["pending_file_code"] = code

    if not await is_subscribed(context, user.id):
        await message.reply_text(
            "برای دریافت فایل، ابتدا عضو کانال شو و سپس "
            "روی «بررسی عضویت» بزن. 👇",
            reply_markup=join_keyboard(),
        )
        return

    await deliver_file(update, context, file_record)


async def deliver_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_record: dict,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    try:
        copied = await context.bot.copy_message(
            chat_id=user.id,
            from_chat_id=int(file_record["chat_id"]),
            message_id=int(file_record["message_id"]),
        )

        increment_downloads(int(file_record["id"]))

        if context.job_queue:
            context.job_queue.run_once(
                delete_delivered_message,
                when=DELETE_AFTER_SECONDS,
                data={
                    "chat_id": user.id,
                    "message_id": copied.message_id,
                },
                name=f"delete-{user.id}-{copied.message_id}",
            )
        else:
            logger.error("JobQueue is unavailable")

        await message.reply_text(
            f"فایل ارسال شد! 📥\n"
            f"این پیام پس از {DELETE_AFTER_SECONDS} ثانیه حذف می‌شود؛ "
            "اگر به آن نیاز داری، به‌موقع ذخیره‌اش کن."
        )

        context.user_data.pop("pending_file_code", None)

    except TelegramError:
        logger.exception("Could not deliver file")
        await message.reply_text(
            "ارسال فایل ناموفق بود. ممکن است پیام اصلی حذف شده باشد "
            "یا دسترسی لازم وجود نداشته باشد."
        )

    except Exception:
        logger.exception("Unexpected error while delivering file")
        await message.reply_text(
            "در حال حاضر مشکلی در پردازش فایل پیش آمده."
        )


async def delete_delivered_message(
    context: ContextTypes.DEFAULT_TYPE,
):
    job = context.job

    if not job or not job.data:
        return

    chat_id = job.data["chat_id"]
    message_id = job.data["message_id"]

    try:
        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=message_id,
        )
    except TelegramError:
        logger.warning(
            "Could not delete delivered message %s",
            message_id,
        )


# -------------------- Subscription Callback --------------------

async def check_subscription(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query or not query.from_user:
        return

    await query.answer()

    if not await is_subscribed(context, query.from_user.id):
        await query.answer(
            "هنوز عضویتت تأیید نشده است!",
            show_alert=True,
        )
        return

    code = context.user_data.get("pending_file_code")

    if not code:
        await query.edit_message_text(
            "عضویت تأیید شد! حالا لینک فایل را دوباره باز کن. ✅"
        )
        return

    try:
        file_record = get_file(code)
    except Exception:
        logger.exception("Database error after subscription check")
        await query.edit_message_text(
            "خطا در دریافت اطلاعات فایل. دوباره تلاش کن."
        )
        return

    if not file_record:
        context.user_data.pop("pending_file_code", None)
        await query.edit_message_text(
            "این لینک دیگر معتبر نیست."
        )
        return

    await query.edit_message_text(
        "عضویت تأیید شد؛ فایل در حال ارسال است... ✅"
    )

    await deliver_file(update, context, file_record)


# -------------------- Admin Upload --------------------

async def receive_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    if message.chat.type != "private":
        return

    if not await is_channel_admin(context, user.id):
        await message.reply_text(
            "فقط مدیران کانال می‌توانند فایل جدید ثبت کنند. 🔒"
        )
        return

    code = secrets.token_urlsafe(9)

    try:
        record = save_file(
            code=code,
            chat_id=message.chat_id,
            message_id=message.message_id,
        )

        if not record:
            raise RuntimeError("Supabase did not return the inserted row")

    except Exception:
        logger.exception("Could not save uploaded file")
        await message.reply_text(
            "ثبت فایل ناموفق بود. تنظیمات دیتابیس را بررسی کن."
        )
        return

    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"

    await message.reply_text(
        "فایل با موفقیت ثبت شد! ✅\n\n"
        "لینک دریافت فایل:\n"
        f"{link}\n\n"
        "این لینک را می‌توانی به کاربران بدهی."
    )


# -------------------- Statistics --------------------

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    if not await is_channel_admin(context, user.id):
        await message.reply_text(
            "این دستور فقط برای مدیران کانال فعال است. 🔒"
        )
        return

    try:
        total_files, total_downloads = get_total_stats()

        await message.reply_text(
            "📊 آمار ربات Erling\n\n"
            f"📁 تعداد فایل‌های ثبت‌شده: {total_files}\n"
            f"📥 تعداد کل دانلودها: {total_downloads}"
        )

    except Exception:
        logger.exception("Could not retrieve statistics")
        await message.reply_text(
            "دریافت آمار ناموفق بود. تنظیمات دیتابیس را بررسی کن."
        )


# -------------------- Error Handler --------------------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Unhandled exception while processing update",
        exc_info=context.error,
    )


# -------------------- Main --------------------

def main():
    if not RENDER_EXTERNAL_URL:
        raise RuntimeError(
            "RENDER_EXTERNAL_URL environment variable is missing"
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("claim", start))
    application.add_handler(CommandHandler("stats", stats))

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & (
                filters.Document.ALL
                | filters.PHOTO
                | filters.VIDEO
                | filters.AUDIO
                | filters.VOICE
                | filters.VIDEO_NOTE
                | filters.ANIMATION
            ),
            receive_file,
        )
    )

    application.add_handler(
        __import__(
            "telegram.ext",
            fromlist=["CallbackQueryHandler"],
        ).CallbackQueryHandler(
            check_subscription,
            pattern="^check_subscription$",
        )
    )

    application.add_error_handler(error_handler)

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=f"{RENDER_EXTERNAL_URL}/{WEBHOOK_PATH}",
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
