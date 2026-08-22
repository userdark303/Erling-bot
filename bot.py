import os
import sqlite3
import secrets
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.environ["BOT_TOKEN"]

CHANNEL = os.getenv(
    "CHANNEL_USERNAME",
    "@AnimeArmyChan"
)

DELETE_AFTER = int(
    os.getenv(
        "DELETE_AFTER_SECONDS",
        "20"
    )
)

PORT = int(
    os.getenv(
        "PORT",
        "8000"
    )
)

RENDER_URL = os.environ.get(
    "RENDER_EXTERNAL_URL"
)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

DB = sqlite3.connect(
    "erling.db",
    check_same_thread=False
)

DB.row_factory = sqlite3.Row


DB.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
""")


DB.execute("""
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    downloads INTEGER DEFAULT 0
)
""")


DB.commit()


# =========================================================
# DATABASE FUNCTIONS
# =========================================================

def get_setting(key):

    result = DB.execute(
        "SELECT value FROM settings WHERE key=?",
        (key,)
    ).fetchone()

    if result:
        return result["value"]

    return None


def set_setting(key, value):

    DB.execute(
        """
        INSERT OR REPLACE INTO settings
        (key, value)
        VALUES (?, ?)
        """,
        (
            key,
            str(value)
        )
    )

    DB.commit()


def is_admin(user_id):

    admin_id = get_setting("admin_id")

    if not admin_id:
        return False

    return int(admin_id) == user_id


def generate_code():

    while True:

        code = secrets.token_urlsafe(8)

        result = DB.execute(
            "SELECT id FROM files WHERE code=?",
            (code,)
        ).fetchone()

        if not result:
            return code


# =========================================================
# CHANNEL MEMBERSHIP
# =========================================================

async def check_membership(
    context,
    user_id
):

    try:

        member = await context.bot.get_chat_member(
            chat_id=CHANNEL,
            user_id=user_id
        )

        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER
        )

    except Exception as error:

        logger.error(
            "Membership check failed: %s",
            error
        )

        return False


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    # -----------------------------------------------------
    # Normal /start
    # -----------------------------------------------------

    if not context.args:

        if get_setting("admin_id") is None:

            await update.message.reply_text(

                "سلام 👋\n\n"
                "به ربات Erling خوش آمدی.\n\n"
                "اگر صاحب ربات هستی، دستور زیر را بفرست:\n\n"
                "/claim"

            )

            return


        if is_admin(user.id):

            await update.message.reply_text(

                "🤖 Erling آماده است!\n\n"
                "فایل موردنظر را برای من بفرست "
                "تا لینک اختصاصی آن ساخته شود.\n\n"
                "📊 /stats"

            )

        else:

            await update.message.reply_text(

                "سلام 👋\n\n"
                "برای دریافت فایل، لینک اختصاصی آن "
                "را باز کن."

            )

        return


    # -----------------------------------------------------
    # FILE LINK
    # -----------------------------------------------------

    code = context.args[0]

    file = DB.execute(
        """
        SELECT *
        FROM files
        WHERE code=?
        """,
        (code,)
    ).fetchone()


    if not file:

        await update.message.reply_text(

            "❌ این لینک معتبر نیست "
            "یا فایل پیدا نشد."

        )

        return


    # -----------------------------------------------------
    # CHECK CHANNEL
    # -----------------------------------------------------

    joined = await check_membership(
        context,
        user.id
    )


    if not joined:

        keyboard = [

            [
                InlineKeyboardButton(
                    "📢 عضویت در AnimeArmy",
                    url="https://t.me/AnimeArmyChan"
                )
            ],

            [
                InlineKeyboardButton(
                    "✅ بررسی عضویت",
                    callback_data=f"check:{code}"
                )
            ]

        ]


        await update.message.reply_text(

            "🔒 برای دریافت فایل ابتدا باید "
            "عضو کانال AnimeArmy شوی.",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )

        )

        return


    # -----------------------------------------------------
    # SEND FILE
    # -----------------------------------------------------

    await send_file(
        update.effective_chat.id,
        context,
        file
    )


# =========================================================
# /CLAIM
# =========================================================

async def claim(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    current_admin = get_setting(
        "admin_id"
    )


    if current_admin:

        if is_admin(
            update.effective_user.id
        ):

            await update.message.reply_text(
                "✅ تو قبلاً مدیر Erling هستی."
            )

        else:

            await update.message.reply_text(
                "❌ مدیر ربات قبلاً تعیین شده است."
            )

        return


    set_setting(
        "admin_id",
        update.effective_user.id
    )


    await update.message.reply_text(

        "✅ حساب شما با موفقیت "
        "به‌عنوان مدیر Erling ثبت شد!\n\n"
        "حالا یک فایل برای من بفرست."

    )


# =========================================================
# SEND FILE
# =========================================================

async def send_file(
    chat_id,
    context,
    file
):

    try:

        sent_message = await context.bot.copy_message(

            chat_id=chat_id,

            from_chat_id=file["chat_id"],

            message_id=file["message_id"]

        )


        # Increase download counter

        DB.execute(

            """
            UPDATE files
            SET downloads = downloads + 1
            WHERE id=?
            """,

            (file["id"],)

        )

        DB.commit()


        # Delete after X seconds

        context.job_queue.run_once(

            delete_message,

            DELETE_AFTER,

            data={
                "chat_id": chat_id,
                "message_id": sent_message.message_id
            }

        )


    except Exception as error:

        logger.error(
            "Could not send file: %s",
            error
        )


        try:

            await context.bot.send_message(

                chat_id=chat_id,

                text="❌ هنگام ارسال فایل مشکلی رخ داد."

            )

        except Exception:

            pass


# =========================================================
# DELETE MESSAGE
# =========================================================

async def delete_message(
    context: ContextTypes.DEFAULT_TYPE
):

    data = context.job.data


    try:

        await context.bot.delete_message(

            chat_id=data["chat_id"],

            message_id=data["message_id"]

        )

        logger.info(
            "File message deleted."
        )


    except Exception as error:

        logger.info(
            "Could not delete message: %s",
            error
        )


# =========================================================
# MEMBERSHIP BUTTON
# =========================================================

async def membership_check(
    update,
    context
):

    query = update.callback_query

    await query.answer()


    code = query.data.split(
        ":",
        1
    )[1]


    file = DB.execute(

        """
        SELECT *
        FROM files
        WHERE code=?
        """,

        (code,)

    ).fetchone()


    if not file:

        await query.edit_message_text(
            "❌ فایل پیدا نشد."
        )

        return


    joined = await check_membership(

        context,

        query.from_user.id

    )


    if not joined:

        await query.answer(

            "❌ هنوز عضو کانال نیستی.",

            show_alert=True

        )

        return


    try:

        await query.message.delete()

    except Exception:

        pass


    await send_file(

        query.message.chat_id,

        context,

        file

    )


# =========================================================
# RECEIVE FILE
# =========================================================

async def receive_file(
    update,
    context
):

    user = update.effective_user


    if not is_admin(user.id):

        await update.message.reply_text(

            "❌ فقط مدیر می‌تواند "
            "فایل آپلود کند."

        )

        return


    message = update.message


    # Create unique file code

    code = generate_code()


    # Save Telegram message information

    DB.execute(

        """
        INSERT INTO files
        (code, chat_id, message_id)
        VALUES (?, ?, ?)
        """,

        (
            code,
            message.chat_id,
            message.message_id
        )

    )


    DB.commit()


    bot = await context.bot.get_me()


    link = (

        f"https://t.me/"
        f"{bot.username}"
        f"?start={code}"

    )


    await message.reply_text(

        "✅ فایل با موفقیت ثبت شد!\n\n"

        "🔗 لینک دریافت:\n"
        f"{link}\n\n"

        f"⏱ فایل برای کاربر بعد از "
        f"{DELETE_AFTER} ثانیه حذف می‌شود."

    )


# =========================================================
# /STATS
# =========================================================

async def stats(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return


    result = DB.execute(

        """
        SELECT
            COUNT(*) AS files,
            COALESCE(SUM(downloads), 0) AS downloads
        FROM files
        """
    ).fetchone()


    await update.message.reply_text(

        "📊 آمار Erling\n\n"

        f"📁 تعداد فایل‌ها: "
        f"{result['files']}\n\n"

        f"📥 تعداد دریافت‌ها: "
        f"{result['downloads']}"

    )


# =========================================================
# APPLICATION
# =========================================================

application = (

    Application.builder()

    .token(TOKEN)

    .build()

)


# =========================================================
# HANDLERS
# =========================================================

application.add_handler(

    CommandHandler(
        "start",
        start
    )

)


application.add_handler(

    CommandHandler(
        "claim",
        claim
    )

)


application.add_handler(

    CommandHandler(
        "stats",
        stats
    )

)


application.add_handler(

    CallbackQueryHandler(

        membership_check,

        pattern=r"^check:"

    )

)


application.add_handler(

    MessageHandler(

        filters.Document.ALL
        | filters.VIDEO
        | filters.AUDIO
        | filters.PHOTO,

        receive_file

    )

)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    if not RENDER_URL:

        raise RuntimeError(

            "RENDER_EXTERNAL_URL is not available. "
            "Make sure this is running as a Render Web Service."

        )


    webhook_url = (

        f"{RENDER_URL}"
        f"/telegram"

    )


    logger.info(
        "Starting Erling..."
    )

    logger.info(
        "Webhook URL: %s",
        webhook_url
    )


    application.run_webhook(

        listen="0.0.0.0",

        port=PORT,

        url_path="telegram",

        webhook_url=webhook_url

    )
