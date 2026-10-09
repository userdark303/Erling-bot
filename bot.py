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
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from supabase import create_client


# ==========================================
# تنظیمات ربات
# ==========================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

SUPABASE_URL = os.environ["SUPABASE_URL"]

SUPABASE_SERVICE_ROLE_KEY = os.environ[
    "SUPABASE_SERVICE_ROLE_KEY"
]

# فقط خودت اجازه ثبت فایل داری
OWNER_ID = 5314399778

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME",
    "@AnimeArmyChan",
)

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    "",
).rstrip("/")

PORT = int(os.getenv("PORT", "10000"))

WEBHOOK_PATH = "telegram"

DELETE_AFTER_SECONDS = int(
    os.getenv("DELETE_AFTER_SECONDS", "20")
)


# ==========================================
# راه‌اندازی دیتابیس و گزارش خطاها
# ==========================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("Erling")

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
)


# ==========================================
# کار با دیتابیس
# ==========================================

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

    if not result.data:
        raise RuntimeError("File was not saved")

    return result.data[0]


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

    return (
        len(files),
        sum(int(item.get("downloads", 0)) for item in files),
    )


# ==========================================
# بررسی عضویت در کانال
# ==========================================

async def is_subscribed(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> bool:
    try:
        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user_id,
        )

        if member.status in (
            "creator",
            "administrator",
            "member",
        ):
            return True

        if member.status == "restricted":
            return bool(
                getattr(member, "is_member", False)
            )

        return False

    except TelegramError:
        logger.exception("خطا در بررسی عضویت کاربر")
        return False


def join_keyboard():
    channel = CHANNEL_USERNAME.lstrip("@")

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 عضویت توی کانال",
                url=f"https://t.me/{channel}",
            )
        ],
        [
            InlineKeyboardButton(
                "✅ عضو شدم، بررسی کن",
                callback_data="check_subscription",
            )
        ],
    ])


# ==========================================
# دستور شروع
# ==========================================

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
            "سلام رفیق! 💗\n\n"
            "برای دریافت فایل، لینک اختصاصی اون رو باز کن.\n"
            "اگه صاحب رباتی و می‌خوای فایل جدید ثبت کنی، "
            "دستور /claim رو بفرست."
        )
        return

    code = context.args[0]

    try:
        file_record = get_file(code)

    except Exception:
        logger.exception("خطا در دریافت فایل از دیتابیس")

        await message.reply_text(
            "یه مشکلی پیش اومده رفیق! 😕\n"
            "یکم بعد دوباره امتحان کن."
        )
        return

    if not file_record:
        await message.reply_text(
            "این لینک رو پیدا نکردم رفیق! ❌\n"
            "شاید لینک اشتباهه یا فایل دیگه موجود نیست."
        )
        return

    context.user_data["pending_file_code"] = code

    if not await is_subscribed(context, user.id):
        await message.reply_text(
            "برای گرفتن فایل، اول عضو کانال شو و بعد "
            "روی دکمهٔ بررسی عضویت بزن. 👇",
            reply_markup=join_keyboard(),
        )
        return

    await deliver_file(
        update,
        context,
        file_record,
    )


# ==========================================
# دستور اختصاصی ثبت فایل
# ==========================================

async def claim(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    if user.id != OWNER_ID:
        await message.reply_text(
            "این دستور فقط برای صاحب رباته رفیق! 🔒"
        )
        return

    if message.chat.type != "private":
        await message.reply_text(
            "این دستور رو توی گفت‌وگوی خصوصی ربات بفرست."
        )
        return

    context.user_data["awaiting_upload"] = True

    await message.reply_text(
        "خب رفیق، آماده‌ام! 💗\n\n"
        "حالا عکس، ویدئو یا فایل موردنظرت رو بفرست "
        "تا ثبتش کنم و لینک اختصاصیش رو بهت بدم.\n\n"
        "اگه هم منصرف شدی، /cancel رو بفرست."
    )


# ==========================================
# لغو ثبت فایل
# ==========================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    if user.id != OWNER_ID:
        await message.reply_text(
            "این دستور فقط برای صاحب ربات فعاله. 🔒"
        )
        return

    context.user_data["awaiting_upload"] = False

    await message.reply_text(
        "باشه رفیق، ثبت فایل رو لغو کردم. ✅"
    )


# ==========================================
# دریافت و ثبت فایل
# ==========================================

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

    # فقط صاحب ربات اجازه ثبت فایل داره
    if user.id != OWNER_ID:
        return

    # قبل از ارسال فایل باید /claim زده باشی
    if not context.user_data.get("awaiting_upload", False):
        await message.reply_text(
            "برای ثبت فایل جدید، اول /claim رو بفرست رفیق. 💗"
        )
        return

    # حالت ثبت فایل رو غیرفعال می‌کنیم
    context.user_data["awaiting_upload"] = False

    code = secrets.token_urlsafe(9)

    try:
        save_file(
            code=code,
            chat_id=message.chat_id,
            message_id=message.message_id,
        )

    except Exception:
        logger.exception("خطا در ثبت فایل")

        await message.reply_text(
            "وای، ثبت فایل انجام نشد! 😕\n"
            "دوباره /claim رو بفرست و یه بار دیگه امتحان کن."
        )
        return

    try:
        bot_info = await context.bot.get_me()

        link = (
            f"https://t.me/{bot_info.username}"
            f"?start={code}"
        )

        await message.reply_text(
            "فایلت با موفقیت ثبت شد رفیق! ✅💗\n\n"
            "🔗 لینک اختصاصی دریافت فایل:\n"
            f"{link}\n\n"
            "حالا می‌تونی لینک رو برای بقیه بفرستی.\n"
            "برای ثبت فایل بعدی، دوباره /claim رو بزن."
        )

    except Exception:
        logger.exception("خطا در ساخت یا ارسال لینک")

        await message.reply_text(
            "فایل ثبت شد، ولی نتونستم لینکش رو برات بفرستم. 😕"
        )


# ==========================================
# ارسال فایل به کاربر
# ==========================================

async def deliver_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_record: dict,
):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    try:
        copied_message = await context.bot.copy_message(
            chat_id=user.id,
            from_chat_id=int(file_record["chat_id"]),
            message_id=int(file_record["message_id"]),
        )

    except TelegramError:
        logger.exception("ارسال فایل ناموفق بود")

        await message.reply_text(
            "نتونستم فایل رو بفرستم رفیق! 😕\n"
            "ممکنه پیام اصلی دیگه در دسترس نباشه."
        )
        return

    try:
        increment_downloads(
            int(file_record["id"])
        )

    except Exception:
        logger.exception("ثبت آمار دانلود ناموفق بود")

    if context.job_queue:
        context.job_queue.run_once(
            delete_delivered_message,
            when=DELETE_AFTER_SECONDS,
            data={
                "chat_id": user.id,
                "message_id": copied_message.message_id,
            },
            name=f"delete-{user.id}-{copied_message.message_id}",
        )
    else:
        logger.error("JobQueue در دسترس نیست")

    await message.reply_text(
        "بفرما رفیق، فایلت ارسال شد! 📥💗\n\n"
        f"پیام فایل بعد از {DELETE_AFTER_SECONDS} ثانیه "
        "از گفت‌وگوی ربات حذف می‌شه."
    )

    context.user_data.pop("pending_file_code", None)


# ==========================================
# حذف خودکار پیام فایل
# ==========================================

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
            "حذف پیام ناموفق بود: %s",
            message_id,
        )


# ==========================================
# بررسی عضویت بعد از زدن دکمه
# ==========================================

async def check_subscription(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query or not query.from_user:
        return

    user_id = query.from_user.id

    if not await is_subscribed(context, user_id):
        await query.answer(
            "هنوز عضو کانال نشدی رفیق! اول عضو شو. 👀",
            show_alert=True,
        )
        return

    await query.answer("عضویتت تأیید شد! ✅")

    code = context.user_data.get("pending_file_code")

    if not code:
        await query.edit_message_text(
            "عضویتت تأیید شد رفیق! 💗\n"
            "حالا لینک فایل رو دوباره باز کن."
        )
        return

    try:
        file_record = get_file(code)

    except Exception:
        logger.exception("خطا در دریافت فایل پس از بررسی عضویت")

        await query.edit_message_text(
            "یه مشکلی پیش اومده؛ لطفاً دوباره تلاش کن."
        )
        return

    if not file_record:
        context.user_data.pop("pending_file_code", None)

        await query.edit_message_text(
            "این فایل دیگه پیدا نمی‌شه رفیق! ❌"
        )
        return

    await query.edit_message_text(
        "عضویتت تأیید شد! دارم فایلت رو می‌فرستم... 💗"
    )

    await deliver_file(
        update,
        context,
        file_record,
    )


# ==========================================
# آمار ربات
# ==========================================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    if user.id != OWNER_ID:
        await message.reply_text(
            "آمار ربات فقط برای صاحبش قابل مشاهده‌ست. 🔒"
        )
        return

    try:
        total_files, total_downloads = get_total_stats()

        await message.reply_text(
            "📊 آمار ربات ارلینگ 💗\n\n"
            f"📁 تعداد فایل‌های ثبت‌شده: {total_files}\n"
            f"📥 تعداد دانلودها: {total_downloads}"
        )

    except Exception:
        logger.exception("دریافت آمار ناموفق بود")

        await message.reply_text(
            "نتونستم آمار رو دریافت کنم رفیق! 😕"
        )


# ==========================================
# مدیریت خطاها
# ==========================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "خطای پیش‌بینی‌نشده در ربات",
        exc_info=context.error,
    )


# ==========================================
# اجرای ربات
# ==========================================

def main():
    if not RENDER_EXTERNAL_URL:
        raise RuntimeError(
            "متغیر RENDER_EXTERNAL_URL تنظیم نشده."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # دستورات ربات
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("claim", claim)
    )

    application.add_handler(
        CommandHandler("cancel", cancel)
    )

    application.add_handler(
        CommandHandler("stats", stats)
    )

    # دکمه بررسی عضویت
    application.add_handler(
        CallbackQueryHandler(
            check_subscription,
            pattern="^check_subscription$",
        )
    )

    # انواع فایل‌هایی که می‌شه ثبت کرد
    media_filter = (
        filters.PHOTO
        | filters.VIDEO
        | filters.Document.ALL
        | filters.AUDIO
        | filters.VOICE
        | filters.VIDEO_NOTE
        | filters.ANIMATION
    )

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & media_filter,
            receive_file,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("ربات ارلینگ داره اجرا می‌شه...")

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=(
            f"{RENDER_EXTERNAL_URL}/{WEBHOOK_PATH}"
        ),
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
