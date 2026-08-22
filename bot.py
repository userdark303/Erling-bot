import os
import sqlite3
import secrets
import logging
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# Settings
# =========================

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.getenv("CHANNEL_USERNAME", "@AnimeArmyChan")
DELETE_AFTER = int(os.getenv("DELETE_AFTER_SECONDS", "20"))
PORT = int(os.getenv("PORT", "8000"))

logging.basicConfig(level=logging.INFO)

# =========================
# Database
# =========================

db = sqlite3.connect("erling.db", check_same_thread=False)

db.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    chat_id INTEGER,
    message_id INTEGER,
    downloads INTEGER DEFAULT 0
)
""")

db.commit()


def get_setting(key):
    result = db.execute(
        "SELECT value FROM settings WHERE key=?",
        (key,)
    ).fetchone()

    return result[0] if result else None


def set_setting(key, value):
    db.execute(
        "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
        (key, str(value))
    )
    db.commit()


def is_admin(user_id):
    admin = get_setting("admin_id")

    if not admin:
        return False

    return int(admin) == user_id


def generate_code():

    while True:

        code = secrets.token_urlsafe(8)

        exists = db.execute(
            "SELECT id FROM files WHERE code=?",
            (code,)
        ).fetchone()

        if not exists:
            return code


# =========================
# Telegram Application
# =========================

application = Application.builder().token(TOKEN).build()


# =========================
# Start
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    # First start
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
                "🤖 Erling آماده است.\n\n"
                "یک فایل برای من بفرست تا لینک اختصاصی آن ساخته شود.\n\n"
                "/stats - آمار"
            )

        else:

            await update.message.reply_text(
                "سلام 👋\n"
                "برای دریافت فایل، لینک اختصاصی آن را باز کن."
            )

        return

    # =========================
    # File link
    # =========================

    code = context.args[0]

    file = db.execute(
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
                    callback_data=f"check_{code}"
                )
            ]

        ]

        await update.message.reply_text(
            "برای دریافت فایل ابتدا باید عضو کانال AnimeArmy شوی.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    await send_file(update, context, file)


# =========================
# Claim Admin
# =========================

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
        "✅ این حساب با موفقیت مدیر Erling شد.\n\n"
        "حالا یک فایل برای من بفرست."
    )


# =========================
# Membership
# =========================

async def check_membership(context, user_id):

    try:

        member = await context.bot.get_chat_member(
            CHANNEL,
            user_id
        )

        return member.status in [
            "member",
            "administrator",
            "creator"
        ]

    except Exception as error:

        logging.error(error)

        return False


# =========================
# File delivery
# =========================

async def send_file(update, context, file):

    chat_id = update.effective_chat.id

    try:

        sent = await context.bot.copy_message(

            chat_id=chat_id,

            from_chat_id=file[2],

            message_id=file[3]

        )

        db.execute(
            "UPDATE files SET downloads=downloads+1 WHERE id=?",
            (file[0],)
        )

        db.commit()

        # Delete after 20 seconds

        context.job_queue.run_once(

            delete_message,

            DELETE_AFTER,

            data={
                "chat_id": chat_id,
                "message_id": sent.message_id
            }

        )

    except Exception as error:

        logging.error(error)

        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ ارسال فایل انجام نشد."
        )


async def delete_message(context):

    data = context.job.data

    try:

        await context.bot.delete_message(

            chat_id=data["chat_id"],

            message_id=data["message_id"]

        )

    except Exception as error:

        logging.error(error)


# =========================
# Membership button
# =========================

async def membership_button(update, context):

    query = update.callback_query

    await query.answer()

    code = query.data.replace(
        "check_",
        ""
    )

    file = db.execute(
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
        update,
        context,
        file
    )


# =========================
# Receive files
# =========================

async def receive_file(update, context):

    user = update.effective_user

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ فقط مدیر می‌تواند فایل آپلود کند."
        )

        return

    message = update.message

    code = generate_code()

    db.execute(

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

    db.commit()

    bot = await context.bot.get_me()

    link = (
        f"https://t.me/{bot.username}"
        f"?start={code}"
    )

    await message.reply_text(

        "✅ فایل ثبت شد!\n\n"

        "🔗 لینک دریافت:\n"
        f"{link}\n\n"

        "⏱ فایل برای کاربر بعد از "
        f"{DELETE_AFTER} ثانیه حذف می‌شود."
    )


# =========================
# Statistics
# =========================

async def stats(update, context):

    if not is_admin(update.effective_user.id):

        return

    result = db.execute(

        """
        SELECT
        COUNT(*),
        COALESCE(SUM(downloads),0)
        FROM files
        """

    ).fetchone()

    await update.message.reply_text(

        "📊 آمار Erling\n\n"

        f"📁 تعداد فایل‌ها: {result[0]}\n"
        f"📥 تعداد دریافت‌ها: {result[1]}"
    )


# =========================
# Flask Webhook
# =========================

web = Flask(__name__)


@web.route("/", methods=["GET"])
def home():

    return "Erling is running."


@web.route("/webhook", methods=["POST"])
async def webhook():

    data = request.get_json(force=True)

    update = Update.de_json(
        data,
        application.bot
    )

    await application.process_update(update)

    return "OK"


# =========================
# Handlers
# =========================

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
        membership_button,
        pattern=r"^check_"
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


# =========================
# Run
# =========================

if __name__ == "__main__":

    import asyncio
    import threading
    import uvicorn

    async def run_bot():

        await application.initialize()

        await application.start()

        await application.bot.set_webhook(
            os.environ["WEBHOOK_URL"]
        )

    asyncio.run(run_bot())

    uvicorn.run(
        web,
        host="0.0.0.0",
        port=PORT
    )
