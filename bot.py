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

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.getenv("CHANNEL_USERNAME", "@AnimeArmyChan")
DELETE_AFTER = int(os.getenv("DELETE_AFTER_SECONDS", "20"))
PORT = int(os.getenv("PORT", "8000"))

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

DB = sqlite3.connect("erling.db", check_same_thread=False)
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


def get_setting(key):
    row = DB.execute(
        "SELECT value FROM settings WHERE key=?",
        (key,)
    ).fetchone()

    return row["value"] if row else None


def set_setting(key, value):
    DB.execute(
        "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
        (key, str(value))
    )
    DB.commit()


def is_admin(user_id):
    admin = get_setting("admin_id")

    return admin is not None and int(admin) == user_id


def generate_code():
    while True:
        code = secrets.token_urlsafe(8)

        exists = DB.execute(
            "SELECT id FROM files WHERE code=?",
            (code,)
        ).fetchone()

        if not exists:
            return code


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not context.args:

        if get_setting("admin_id") is None:

            await update.message.reply_text(
                "سلام 👋\n\n"
                "برای تبدیل این حساب به مدیر Erling، "
                "دستور /claim را بفرست."
            )

            return

        if is_admin(user.id):

            await update.message.reply_text(
                "🤖 Erling آماده است!\n\n"
                "یک فایل برای من بفرست تا لینک اختصاصی آن ساخته شود.\n\n"
                "/stats - آمار"
            )

        else:

            await update.message.reply_text(
                "سلام 👋\n"
                "برای دریافت فایل، لینک اختصاصی آن را باز کن."
            )

        return

    code = context.args[0]

    file = DB.execute(
        "SELECT * FROM files WHERE code=?",
        (code,)
    ).fetchone()

    if not file:

        await update.message.reply_text(
            "❌ این لینک معتبر نیست یا فایل حذف شده است."
        )

        return

    if not await check_membership(context, user.id):

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
            "برای دریافت فایل ابتدا باید عضو کانال AnimeArmy شوی.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    await send_file(update.effective_chat.id, context, file)


async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if get_setting("admin_id") is not None:

        await update.message.reply_text(
            "❌ مدیر ربات قبلاً تعیین شده است."
        )

        return

    set_setting(
        "admin_id",
        update.effective_user.id
    )

    await update.message.reply_text(
        "✅ این حساب مدیر Erling شد!\n\n"
        "حالا یک فایل برای من بفرست."
    )


async def check_membership(context, user_id):

    try:

        member = await context.bot.get_chat_member(
            CHANNEL,
            user_id
        )

        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER
        )

    except Exception as error:

        logging.error(
            "Membership check error: %s",
            error
        )

        return False


async def send_file(chat_id, context, file):

    try:

        sent = await context.bot.copy_message(

            chat_id=chat_id,

            from_chat_id=file["chat_id"],

            message_id=file["message_id"]

        )

        DB.execute(
            "UPDATE files SET downloads=downloads+1 WHERE id=?",
            (file["id"],)
        )

        DB.commit()

        context.job_queue.run_once(
            delete_file_message,
            DELETE_AFTER,
            data={
                "chat_id": chat_id,
                "message_id": sent.message_id
            }
        )

    except Exception as error:

        logging.error(
            "File delivery error: %s",
            error
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ ارسال فایل انجام نشد."
        )


async def delete_file_message(context):

    data = context.job.data

    try:

        await context.bot.delete_message(
            chat_id=data["chat_id"],
            message_id=data["message_id"]
        )

    except Exception as error:

        logging.info(
            "Message already deleted or unavailable: %s",
            error
        )


async def membership_check(update, context):

    query = update.callback_query

    await query.answer()

    code = query.data.split(":", 1)[1]

    file = DB.execute(
        "SELECT * FROM files WHERE code=?",
        (code,)
    ).fetchone()

    if not file:

        await query.edit_message_text(
            "❌ فایل پیدا نشد."
        )

        return

    if not await check_membership(
        context,
        query.from_user.id
    ):

        await query.answer(
            "❌ هنوز عضو کانال نیستی.",
            show_alert=True
        )

        return

    await query.message.delete()

    await send_file(
        query.message.chat_id,
        context,
        file
    )


async def receive_file(update, context):

    user = update.effective_user

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ فقط مدیر می‌تواند فایل آپلود کند."
        )

        return

    message = update.message

    code = generate_code()

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
        f"https://t.me/{bot.username}?start={code}"
    )

    await message.reply_text(
        "✅ فایل ثبت شد!\n\n"
        f"🔗 لینک دریافت:\n{link}\n\n"
        f"⏱ فایل بعد از {DELETE_AFTER} ثانیه حذف می‌شود."
    )


async def stats(update, context):

    if not is_admin(update.effective_user.id):
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
        f"📁 فایل‌ها: {result['files']}\n"
        f"📥 دریافت‌ها: {result['downloads']}"
    )


async def post_init(application):

    webhook_url = os.environ["WEBHOOK_URL"]

    await application.bot.set_webhook(
        url=webhook_url
    )

    logging.info(
        "Webhook configured: %s",
        webhook_url
    )


application = (
    Application.builder()
    .token(TOKEN)
    .post_init(post_init)
    .build()
)


application.add_handler(
    CommandHandler("start", start)
)

application.add_handler(
    CommandHandler("claim", claim)
)

application.add_handler(
    CommandHandler("stats", stats)
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


if __name__ == "__main__":

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="telegram",
        webhook_url=os.environ["WEBHOOK_URL"]
    )
