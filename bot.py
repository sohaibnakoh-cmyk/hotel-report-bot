import os
import re
import sqlite3
import asyncio
import threading
import hashlib
import secrets

from io import BytesIO
from datetime import datetime, date, timedelta
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    BotCommand,
    BotCommandScopeChat,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
    CallbackQueryHandler,
)

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import arabic_reshaper
from bidi.algorithm import get_display


# =========================================================
# الإعدادات
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    "admin"
).strip()

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD",
    ""
).strip()

DATABASE_FILE = "hotel_reports.db"

IMAGE_FILE = "images.png"

PAGE_WIDTH, PAGE_HEIGHT = A4


# =========================================================
# حالات المحادثة
# =========================================================

LOGIN_USERNAME = 1
LOGIN_PASSWORD = 2

GUEST_NAME = 10
GUEST_MOTHER = 11
GUEST_BIRTH = 12
GUEST_HOME = 13
GUEST_GOVERNORATE = 14
GUEST_ROOM = 15
GUEST_CHECKIN = 16
GUEST_DURATION = 17
GUEST_REASON = 18

GUEST_PHOTO_1 = 19
GUEST_PHOTO_2 = 20
GUEST_PHOTO_3 = 21
GUEST_CONFIRM = 22


# =========================================================
# البحث عن الخط العربي
# =========================================================

def find_arabic_font():

    fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    for font_path in fonts:
        if os.path.exists(font_path):
            return font_path

    return None


ARABIC_FONT_PATH = find_arabic_font()


if ARABIC_FONT_PATH:

    try:

        pdfmetrics.registerFont(
            TTFont(
                "ArabicFont",
                ARABIC_FONT_PATH
            )
        )

        PDF_FONT = "ArabicFont"

        print(
            "Arabic font found:",
            ARABIC_FONT_PATH
        )

    except Exception as e:

        print(
            "Arabic font error:",
            e
        )

        PDF_FONT = "Helvetica"

else:

    print(
        "WARNING: Arabic font not found"
    )

    PDF_FONT = "Helvetica"


# =========================================================
# معالجة النص العربي
# =========================================================

def arabic_text(text):

    if text is None:
        return ""

    text = str(text)

    try:

        reshaped = arabic_reshaper.reshape(text)

        return get_display(
            reshaped
        )

    except Exception:

        return text


# =========================================================
# قاعدة البيانات
# =========================================================

def get_db():

    connection = sqlite3.connect(
        DATABASE_FILE,
        check_same_thread=False
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_database():

    connection = get_db()

    cursor = connection.cursor()

    # -----------------------------------------
    # جدول النزلاء
    # -----------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS guests (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            guest_name TEXT,
            mother_name TEXT,
            birth TEXT,
            home TEXT,
            governorate TEXT,
            hotel TEXT,
            suite TEXT,
            room TEXT,
            checkin_date TEXT,
            duration TEXT,
            reason TEXT,

            record_date TEXT,
            record_time TEXT,

            telegram_user_id TEXT,
            telegram_username TEXT,

            hotel_account_id INTEGER
        )
        """
    )

    # -----------------------------------------
    # إضافة أعمدة الصور إذا كانت قاعدة البيانات قديمة
    # -----------------------------------------

    existing_columns = []

    cursor.execute(
        "PRAGMA table_info(guests)"
    )

    for row in cursor.fetchall():

        existing_columns.append(
            row["name"]
        )

    if "photo_1" not in existing_columns:

        cursor.execute(
            "ALTER TABLE guests ADD COLUMN photo_1 BLOB"
        )

    if "photo_2" not in existing_columns:

        cursor.execute(
            "ALTER TABLE guests ADD COLUMN photo_2 BLOB"
        )

    if "photo_3" not in existing_columns:

        cursor.execute(
            "ALTER TABLE guests ADD COLUMN photo_3 BLOB"
        )

    # -----------------------------------------
    # جدول الفنادق
    # -----------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS hotel_accounts (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            hotel_name TEXT NOT NULL,

            username TEXT UNIQUE NOT NULL,

            password_hash TEXT NOT NULL,

            salt TEXT NOT NULL,

            active INTEGER DEFAULT 1,

            telegram_user_id TEXT UNIQUE,

            created_at TEXT
        )
        """
    )

    # -----------------------------------------
    # جدول الجلسات
    # -----------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (

            telegram_user_id TEXT PRIMARY KEY,

            hotel_account_id INTEGER,

            role TEXT,

            login_time TEXT,

            active INTEGER DEFAULT 1
        )
        """
    )

    # -----------------------------------------
    # إضافة role إذا كانت القاعدة قديمة
    # -----------------------------------------

    cursor.execute(
        "PRAGMA table_info(sessions)"
    )

    session_columns = [
        row["name"]
        for row in cursor.fetchall()
    ]

    if "role" not in session_columns:

        cursor.execute(
            "ALTER TABLE sessions ADD COLUMN role TEXT"
        )

    connection.commit()

    connection.close()

    print(
        "Database initialized successfully"
    )


# =========================================================
# تشفير كلمات المرور
# =========================================================

def hash_password(
    password,
    salt=None
):

    if salt is None:

        salt = secrets.token_hex(
            16
        )

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000
    ).hex()

    return password_hash, salt


def verify_password(
    password,
    stored_hash,
    salt
):

    password_hash, _ = hash_password(
        password,
        salt
    )

    return secrets.compare_digest(
        password_hash,
        stored_hash
    )


# =========================================================
# إنشاء حساب فندق
# =========================================================

def create_hotel_account(
    hotel_name,
    username,
    password
):

    hotel_name = hotel_name.strip()

    username = username.strip().lower()

    password_hash, salt = hash_password(
        password
    )

    connection = get_db()

    cursor = connection.cursor()

    try:

        cursor.execute(
            """
            INSERT INTO hotel_accounts
            (
                hotel_name,
                username,
                password_hash,
                salt,
                active,
                created_at
            )

            VALUES (?, ?, ?, ?, 1, ?)
            """,

            (
                hotel_name,
                username,
                password_hash,
                salt,
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )
        )

        connection.commit()

        hotel_id = cursor.lastrowid

        connection.close()

        return hotel_id, None

    except sqlite3.IntegrityError:

        connection.close()

        return None, "اسم المستخدم مستخدم مسبقاً."


# =========================================================
# التحقق من الفندق
# =========================================================

def authenticate_hotel(
    username,
    password
):

    username = username.strip().lower()

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM hotel_accounts
        WHERE username = ?
        """,
        (username,)
    )

    account = cursor.fetchone()

    connection.close()

    if not account:
        return None

    if not account["active"]:
        return None

    if not verify_password(
        password,
        account["password_hash"],
        account["salt"]
    ):
        return None

    return account


# =========================================================
# قائمة الفنادق
# =========================================================

def get_all_hotels():

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            hotel_name,
            username,
            active,
            telegram_user_id,
            created_at
        FROM hotel_accounts
        ORDER BY id ASC
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# =========================================================
# تعطيل الفندق
# =========================================================

def disable_hotel(
    hotel_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE hotel_accounts
        SET active = 0,
            telegram_user_id = NULL
        WHERE id = ?
        """,
        (hotel_id,)
    )

    cursor.execute(
        """
        DELETE FROM sessions
        WHERE hotel_account_id = ?
        """,
        (hotel_id,)
    )

    connection.commit()

    connection.close()


# =========================================================
# تفعيل الفندق
# =========================================================

def enable_hotel(
    hotel_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE hotel_accounts
        SET active = 1
        WHERE id = ?
        """,
        (hotel_id,)
    )

    connection.commit()

    connection.close()


# =========================================================
# إنشاء جلسة
# =========================================================

def create_hotel_session(
    telegram_user_id,
    hotel_account_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO sessions
        (
            telegram_user_id,
            hotel_account_id,
            role,
            login_time,
            active
        )

        VALUES (?, ?, 'hotel', ?, 1)
        """,

        (
            str(telegram_user_id),
            hotel_account_id,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
    )

    cursor.execute(
        """
        UPDATE hotel_accounts
        SET telegram_user_id = ?
        WHERE id = ?
        """,

        (
            str(telegram_user_id),
            hotel_account_id
        )
    )

    connection.commit()

    connection.close()


# =========================================================
# جلسة المدير
# =========================================================

def create_admin_session(
    telegram_user_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO sessions
        (
            telegram_user_id,
            hotel_account_id,
            role,
            login_time,
            active
        )

        VALUES (?, NULL, 'admin', ?, 1)
        """,

        (
            str(telegram_user_id),
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
    )

    connection.commit()

    connection.close()


# =========================================================
# تسجيل الخروج
# =========================================================

def logout_session(
    telegram_user_id
):

    connection = get_db()

    cursor = connection.cursor()

    # إزالة ارتباط الفندق بالحساب الحالي
    cursor.execute(
        """
        UPDATE hotel_accounts
        SET telegram_user_id = NULL
        WHERE telegram_user_id = ?
        """,
        (str(telegram_user_id),)
    )

    cursor.execute(
        """
        DELETE FROM sessions
        WHERE telegram_user_id = ?
        """,
        (str(telegram_user_id),)
    )

    connection.commit()

    connection.close()


# =========================================================
# الحصول على جلسة المستخدم
# =========================================================

def get_session(
    telegram_user_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM sessions
        WHERE telegram_user_id = ?
          AND active = 1
        """,

        (
            str(telegram_user_id),
        )
    )

    session = cursor.fetchone()

    connection.close()

    return session


# =========================================================
# الحصول على الفندق المسجل
# =========================================================

def get_logged_hotel(
    telegram_user_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT h.*
        FROM sessions s
        JOIN hotel_accounts h
            ON h.id = s.hotel_account_id
        WHERE s.telegram_user_id = ?
          AND s.active = 1
          AND s.role = 'hotel'
          AND h.active = 1
        """,

        (
            str(telegram_user_id),
        )
    )

    account = cursor.fetchone()

    connection.close()

    return account


# =========================================================
# التحقق من المدير من بيانات الدخول
# =========================================================

def authenticate_admin(
    username,
    password
):

    if not ADMIN_USERNAME:
        return False

    if not ADMIN_PASSWORD:
        return False

    return (
        username.strip().lower()
        ==
        ADMIN_USERNAME.strip().lower()
        and
        password.strip()
        ==
        ADMIN_PASSWORD.strip()
    )


# =========================================================
# التحقق من المدير
# =========================================================

def is_logged_admin(
    update
):

    if not update.effective_user:
        return False

    session = get_session(
        update.effective_user.id
    )

    if not session:
        return False

    return session["role"] == "admin"


# =========================================================
# إرسال صورة الترحيب
# =========================================================

async def send_welcome_image(
    update
):

    if not update.message:
        return

    if not os.path.exists(
        IMAGE_FILE
    ):
        return

    try:

        with open(
            IMAGE_FILE,
            "rb"
        ) as photo:

            await update.message.reply_photo(
                photo=photo
            )

    except Exception as e:

        print(
            "Welcome image error:",
            e
        )


# =========================================================
# أوامر المدير
# =========================================================

async def set_admin_commands(
    application,
    chat_id
):

    commands = [

        BotCommand(
            "start",
            "🏠 الرئيسية"
        ),

        BotCommand(
            "add_hotel",
            "🏨 إضافة فندق"
        ),

        BotCommand(
            "hotels",
            "📋 الفنادق"
        ),

        BotCommand(
            "daily",
            "📊 التقرير اليومي"
        ),

        BotCommand(
            "yesterday",
            "📅 تقرير أمس"
        ),

        BotCommand(
            "monthly",
            "📈 التقرير الشهري"
        ),

        BotCommand(
            "logout",
            "🚪 تسجيل الخروج"
        ),
    ]

    try:

        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(
                chat_id
            )
        )

    except Exception as e:

        print(
            "Admin commands error:",
            e
        )


# =========================================================
# أوامر الفندق
# =========================================================

async def set_hotel_commands(
    application,
    chat_id
):

    commands = [

        BotCommand(
            "start",
            "🏠 الرئيسية"
        ),

        BotCommand(
            "logout",
            "🚪 تسجيل الخروج"
        ),
    ]

    try:

        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(
                chat_id
            )
        )

    except Exception as e:

        print(
            "Hotel commands error:",
            e
        )


# =========================================================
# أوامر المستخدم غير المسجل
# =========================================================

async def set_guest_commands(
    application,
    chat_id
):

    commands = [

        BotCommand(
            "start",
            "🏠 بدء"
        ),

        BotCommand(
            "logout",
            "🚪 تسجيل الخروج"
        ),
    ]

    try:

        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(
                chat_id
            )
        )

    except Exception as e:

        print(
            "Guest commands error:",
            e
        )


# =========================================================
# القائمة الرئيسية للفندق
# =========================================================

def hotel_main_keyboard():

    keyboard = [

        [
            InlineKeyboardButton(
                "📝 تسجيل نزيل جديد",
                callback_data="new_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "🚪 تسجيل الخروج",
                callback_data="logout"
            )
        ]

    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# =========================================================
# البداية
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    session = get_session(
        user_id
    )

    # -----------------------------------------
    # المدير
    # -----------------------------------------

    if session and session["role"] == "admin":

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(
            update
        )

        await update.message.reply_text(

            "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

            "بسم الله الرحمن الرحيم\n\n"

            "🤲 الحمد لله الذي بنعمته تتم الصالحات، "
            "ونسأله سبحانه أن يوفقنا لما فيه الخير "
            "والصلاح.\n\n"

            "🏛️ أهلاً وسهلاً بك في\n"
            "مكتب أمن الفنادق والعقارات\n\n"

            "👨‍💼 تم التعرف على حسابك كحساب مدير.\n\n"

            "يمكنك الآن إدارة حسابات الفنادق "
            "ومتابعة التقارير والبيانات."
        )

        return ConversationHandler.END

    # -----------------------------------------
    # الفندق
    # -----------------------------------------

    hotel = get_logged_hotel(
        user_id
    )

    if hotel:

        await set_hotel_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(
            update
        )

        await update.message.reply_text(

            "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

            "بسم الله الرحمن الرحيم\n\n"

            "🤲 أهلاً وسهلاً ومرحباً بكم، "
            "ونسأل الله أن يوفقنا وإياكم لما فيه الخير.\n\n"

            "🏨 نظام معلومات الفنادق\n\n"

            f"اسم الفندق: {hotel['hotel_name']}\n\n"

            "يمكنكم الآن تسجيل بيانات النزلاء "
            "بشكل منظم خطوة بخطوة.\n\n"

            "اضغط على الزر أدناه للبدء.",
            reply_markup=hotel_main_keyboard()
        )

        return ConversationHandler.END

    # -----------------------------------------
    # غير مسجل
    # -----------------------------------------

    await set_guest_commands(
        context.application,
        update.effective_chat.id
    )

    await send_welcome_image(
        update
    )

    await update.message.reply_text(

        "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

        "بسم الله الرحمن الرحيم\n\n"

        "﴿ وَقُلْ رَبِّ زِدْنِي عِلْمًا ﴾\n\n"

        "أهلاً وسهلاً ومرحباً بكم في "
        "نظام معلومات الفنادق.\n\n"

        "🔐 يرجى إرسال اسم المستخدم الخاص بك "
        "للدخول إلى النظام:"
    )

    return LOGIN_USERNAME


# =========================================================
# تسجيل الدخول - اسم المستخدم
# =========================================================

async def login_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = update.message.text.strip()

    if not username:

        await update.message.reply_text(
            "❌ يرجى إرسال اسم المستخدم."
        )

        return LOGIN_USERNAME

    context.user_data[
        "login_username"
    ] = username

    await update.message.reply_text(

        "🔑 تم استلام اسم المستخدم.\n\n"
        "الآن يرجى إرسال كلمة المرور:"
    )

    return LOGIN_PASSWORD


# =========================================================
# تسجيل الدخول - كلمة المرور
# =========================================================

async def login_password(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    password = update.message.text.strip()

    username = context.user_data.get(
        "login_username",
        ""
    )

    if not username:

        await update.message.reply_text(

            "❌ انتهت عملية تسجيل الدخول.\n\n"
            "استخدم /start للمحاولة من جديد."
        )

        return ConversationHandler.END

    telegram_id = update.effective_user.id

    # -----------------------------------------
    # أولاً: المدير
    # -----------------------------------------

    if authenticate_admin(
        username,
        password
    ):

        create_admin_session(
            telegram_id
        )

        context.user_data.clear()

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await update.message.reply_text(

            "الحمد لله رب العالمين 🌹\n\n"

            "✅ تم تسجيل الدخول بنجاح.\n\n"

            "👨‍💼 تم التعرف على الحساب كحساب مدير.\n\n"

            "أهلاً وسهلاً بك، ونسأل الله أن يعيننا "
            "على أداء الأمانة بما يرضيه سبحانه."
        )

        return ConversationHandler.END

    # -----------------------------------------
    # ثانياً: الفندق
    # -----------------------------------------

    account = authenticate_hotel(
        username,
        password
    )

    if account:

        old_telegram_id = account[
            "telegram_user_id"
        ]

        current_telegram_id = str(
            telegram_id
        )

        # إذا كان الحساب مرتبطاً بحساب آخر
        if (
            old_telegram_id
            and
            old_telegram_id != current_telegram_id
        ):

            await update.message.reply_text(

                "⚠️ هذا الحساب مرتبط حالياً "
                "بحساب Telegram آخر.\n\n"

                "يرجى التواصل مع الإدارة."
            )

            context.user_data.clear()

            return ConversationHandler.END

        create_hotel_session(
            current_telegram_id,
            account["id"]
        )

        context.user_data.clear()

        await set_hotel_commands(
            context.application,
            update.effective_chat.id
        )

        await update.message.reply_text(

            "الحمد لله رب العالمين 🌹\n\n"

            "✅ تم تسجيل الدخول بنجاح.\n\n"

            f"🏨 الفندق: {account['hotel_name']}\n\n"

            "أهلاً وسهلاً بكم، ونسأل الله أن "
            "يوفقنا وإياكم لكل خير.\n\n"

            "يمكنكم الآن البدء بتسجيل بيانات النزيل.",
            reply_markup=hotel_main_keyboard()
        )

        return ConversationHandler.END

    # -----------------------------------------
    # خطأ
    # -----------------------------------------

    context.user_data.clear()

    await update.message.reply_text(

        "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"

        "يرجى التأكد من البيانات والمحاولة "
        "مرة أخرى باستخدام /start."
    )

    return ConversationHandler.END


# =========================================================
# تسجيل الخروج
# =========================================================

async def logout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    logout_session(
        user_id
    )

    context.user_data.clear()

    await set_guest_commands(
        context.application,
        update.effective_chat.id
    )

    await update.message.reply_text(

        "🚪 تم تسجيل الخروج بنجاح.\n\n"

        "جزاكم الله خيراً.\n\n"

        "عند العودة يمكنكم استخدام /start "
        "لتسجيل الدخول من جديد."
    )


# =========================================================
# إضافة فندق
# =========================================================

async def add_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "🏨 إضافة حساب فندق جديد\n\n"

        "أرسل اسم المستخدم للفندق:"
    )

    return ADD_HOTEL_USERNAME


# =========================================================
# حالة إضافة الفندق
# =========================================================

ADD_HOTEL_USERNAME = 30
ADD_HOTEL_PASSWORD = 31
ADD_HOTEL_NAME = 32


async def add_hotel_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = update.message.text.strip().lower()

    if not re.match(
        r"^[a-zA-Z0-9_.-]{3,50}$",
        username
    ):

        await update.message.reply_text(

            "❌ اسم المستخدم غير صالح.\n\n"

            "استخدم الأحرف الإنجليزية والأرقام "
            "والنقطة أو الشرطة فقط."
        )

        return ADD_HOTEL_USERNAME

    context.user_data[
        "new_hotel_username"
    ] = username

    await update.message.reply_text(

        "🔑 أرسل كلمة المرور للحساب.\n\n"

        "يفضل أن تكون 8 أحرف على الأقل."
    )

    return ADD_HOTEL_PASSWORD


async def add_hotel_password(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    password = update.message.text.strip()

    if len(password) < 8:

        await update.message.reply_text(

            "❌ كلمة المرور قصيرة.\n\n"
            "يجب أن تكون 8 أحرف على الأقل."
        )

        return ADD_HOTEL_PASSWORD

    context.user_data[
        "new_hotel_password"
    ] = password

    await update.message.reply_text(
        "🏨 أرسل اسم الفندق:"
    )

    return ADD_HOTEL_NAME


async def add_hotel_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    hotel_name = update.message.text.strip()

    username = context.user_data.get(
        "new_hotel_username"
    )

    password = context.user_data.get(
        "new_hotel_password"
    )

    if not hotel_name:

        await update.message.reply_text(
            "❌ يرجى إرسال اسم الفندق."
        )

        return ADD_HOTEL_NAME

    hotel_id, error = create_hotel_account(
        hotel_name,
        username,
        password
    )

    context.user_data.pop(
        "new_hotel_username",
        None
    )

    context.user_data.pop(
        "new_hotel_password",
        None
    )

    if error:

        await update.message.reply_text(
            f"❌ {error}"
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "✅ تم إنشاء حساب الفندق بنجاح.\n\n"

        f"🏨 اسم الفندق: {hotel_name}\n"
        f"👤 اسم المستخدم: {username}\n\n"

        "🔐 تم حفظ كلمة المرور بشكل آمن.\n\n"

        "يمكنك الآن إعطاء بيانات الدخول "
        "لصاحب الفندق."
    )

    return ConversationHandler.END


# =========================================================
# قائمة الفنادق
# =========================================================

async def hotels_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    hotels = get_all_hotels()

    if not hotels:

        await update.message.reply_text(
            "📋 لا توجد حسابات فنادق حالياً."
        )

        return

    for hotel in hotels:

        status = (
            "🟢 فعال"
            if hotel["active"]
            else "🔴 محذوف/موقوف"
        )

        connected = (
            "🔗 مرتبط"
            if hotel["telegram_user_id"]
            else "⚪ غير مرتبط"
        )

        keyboard = []

        if hotel["active"]:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        "🗑 حذف / إيقاف الفندق",
                        callback_data=f"delete_hotel_{hotel['id']}"
                    )
                ]
            )

        else:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        "♻️ إعادة تفعيل الفندق",
                        callback_data=f"enable_hotel_{hotel['id']}"
                    )
                ]
            )

        await update.message.reply_text(

            f"🏨 الفندق: {hotel['hotel_name']}\n"
            f"👤 اسم المستخدم: {hotel['username']}\n"
            f"📌 الحالة: {status}\n"
            f"📱 الحساب: {connected}\n"
            f"📅 الإنشاء: {hotel['created_at']}",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )


# =========================================================
# تأكيد حذف الفندق
# =========================================================

async def hotel_management_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if not is_logged_admin(update):

        await query.edit_message_text(
            "⛔ غير مصرح لك."
        )

        return

    data = query.data

    if data.startswith(
        "delete_hotel_"
    ):

        hotel_id = int(
            data.split("_")[-1]
        )

        keyboard = [

            [
                InlineKeyboardButton(
                    "⚠️ نعم، حذف الفندق",
                    callback_data=f"confirm_delete_{hotel_id}"
                )
            ],

            [
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data="cancel_delete"
                )
            ]

        ]

        await query.edit_message_text(

            "⚠️ هل أنت متأكد من حذف/إيقاف هذا الفندق؟\n\n"

            "بعد الحذف لن يستطيع صاحب الفندق "
            "تسجيل الدخول بهذا الحساب.",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    elif data.startswith(
        "confirm_delete_"
    ):

        hotel_id = int(
            data.split("_")[-1]
        )

        disable_hotel(
            hotel_id
        )

        await query.edit_message_text(

            "✅ تم حذف/إيقاف حساب الفندق.\n\n"

            "لن يستطيع صاحب الفندق تسجيل الدخول "
            "بهذا الحساب حتى يتم تفعيله من الإدارة."
        )

    elif data.startswith(
        "enable_hotel_"
    ):

        hotel_id = int(
            data.split("_")[-1]
        )

        enable_hotel(
            hotel_id
        )

        await query.edit_message_text(
            "♻️ تم إعادة تفعيل حساب الفندق."
        )

    elif data == "cancel_delete":

        await query.edit_message_text(
            "❌ تم إلغاء عملية الحذف."
        )


# =========================================================
# بدء تسجيل نزيل
# =========================================================

async def new_guest_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query:

        await query.answer()

        user_id = query.from_user.id

    else:

        user_id = update.effective_user.id

    hotel = get_logged_hotel(
        user_id
    )

    if not hotel:

        if query:

            await query.message.reply_text(
                "🔐 يجب تسجيل الدخول أولاً."
            )

        return ConversationHandler.END

    context.user_data[
        "new_guest"
    ] = {}

    context.user_data[
        "guest_photos"
    ] = []

    if query:

        await query.message.reply_text(

            "📝 بسم الله نبدأ بتسجيل بيانات النزيل.\n\n"

            "سأطرح عليك الأسئلة واحداً تلو الآخر، "
            "وبعد إكمال جميع البيانات ستتمكن من "
            "مراجعتها وإرسالها إلى الإدارة."
        )

    else:

        await update.message.reply_text(

            "📝 بسم الله نبدأ بتسجيل بيانات النزيل."
        )

    await asyncio.sleep(
        0.5
    )

    await (
        query.message
        if query
        else update.message
    ).reply_text(

        "1️⃣ ما الاسم الثلاثي للنزيل؟"
    )

    return GUEST_NAME


# =========================================================
# السؤال 1
# =========================================================

async def guest_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    if not value:

        await update.message.reply_text(
            "❌ يرجى إدخال الاسم الثلاثي."
        )

        return GUEST_NAME

    context.user_data[
        "new_guest"
    ]["الاسم الثلاثي"] = value

    await update.message.reply_text(
        "2️⃣ ما اسم الأم؟"
    )

    return GUEST_MOTHER


# =========================================================
# السؤال 2
# =========================================================

async def guest_mother(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    if not value:

        await update.message.reply_text(
            "❌ يرجى إدخال اسم الأم."
        )

        return GUEST_MOTHER

    context.user_data[
        "new_guest"
    ]["اسم الأم"] = value

    await update.message.reply_text(
        "3️⃣ ما مكان وتاريخ الولادة؟"
    )

    return GUEST_BIRTH


# =========================================================
# السؤال 3
# =========================================================

async def guest_birth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "new_guest"
    ]["مكان وتاريخ الولادة"] = value

    await update.message.reply_text(
        "4️⃣ ما السكن الأصلي؟"
    )

    return GUEST_HOME


# =========================================================
# السؤال 4
# =========================================================

async def guest_home(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "new_guest"
    ]["السكن الأصلي"] = value

    await update.message.reply_text(
        "5️⃣ ما المحافظة؟"
    )

    return GUEST_GOVERNORATE


# =========================================================
# السؤال 5
# =========================================================

async def guest_governorate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "new_guest"
    ]["المحافظة"] = value

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if hotel:

        context.user_data[
            "new_guest"
        ]["اسم الفندق"] = hotel[
            "hotel_name"
        ]

    await update.message.reply_text(
        "6️⃣ ما رقم الغرفة؟"
    )

    return GUEST_ROOM


# =========================================================
# السؤال 6
# =========================================================

async def guest_room(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "new_guest"
    ]["رقم الغرفة"] = value

    await update.message.reply_text(
        "7️⃣ ما تاريخ النزول؟"
    )

    return GUEST_CHECKIN


# =========================================================
# السؤال 7
# =========================================================

async def guest_checkin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "new_guest"
    ]["تاريخ النزول"] = value

    await update.message.reply_text(
        "8️⃣ ما مدة الإقامة؟"
    )

    return GUEST_DURATION


# =========================================================
# السؤال 8
# =========================================================

async def guest_duration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "new_guest"
    ]["مدة الإقامة"] = value

    await update.message.reply_text(
        "9️⃣ ما سبب الإقامة؟"
    )

    return GUEST_REASON


# =========================================================
# السؤال 9
# =========================================================

async def guest_reason(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "new_guest"
    ]["سبب الإقامة"] = value

    await update.message.reply_text(

        "📷 الآن ننتقل إلى صور النزيل.\n\n"

        "سيتم طلب 3 صور.\n\n"

        "🔴 الصورة الأولى إلزامية."
    )

    return GUEST_PHOTO_1


# =========================================================
# الصورة الأولى
# =========================================================

async def guest_photo_1(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.photo:

        await update.message.reply_text(

            "❌ هذه الصورة إلزامية.\n\n"
            "يرجى إرسال الصورة الأولى."
        )

        return GUEST_PHOTO_1

    photo = update.message.photo[-1]

    telegram_file = await photo.get_file()

    image_buffer = BytesIO()

    await telegram_file.download_to_memory(
        image_buffer
    )

    image_buffer.seek(0)

    context.user_data[
        "guest_photos"
    ].append(
        image_buffer.getvalue()
    )

    await update.message.reply_text(

        "✅ تم استلام الصورة الأولى.\n\n"

        "📷 أرسل الصورة الثانية.\n"
        "🔴 الصورة الثانية إلزامية."
    )

    return GUEST_PHOTO_2


# =========================================================
# الصورة الثانية
# =========================================================

async def guest_photo_2(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.photo:

        await update.message.reply_text(

            "❌ الصورة الثانية إلزامية.\n\n"
            "يرجى إرسال الصورة الثانية."
        )

        return GUEST_PHOTO_2

    photo = update.message.photo[-1]

    telegram_file = await photo.get_file()

    image_buffer = BytesIO()

    await telegram_file.download_to_memory(
        image_buffer
    )

    image_buffer.seek(0)

    context.user_data[
        "guest_photos"
    ].append(
        image_buffer.getvalue()
    )

    await update.message.reply_text(

        "✅ تم استلام الصورة الثانية.\n\n"

        "📷 أرسل الصورة الثالثة.\n\n"

        "🟢 الصورة الثالثة اختيارية.\n\n"

        "إذا لم تكن موجودة اضغط /skip"
    )

    return GUEST_PHOTO_3


# =========================================================
# الصورة الثالثة
# =========================================================

async def guest_photo_3(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.photo:

        await update.message.reply_text(

            "❌ يرجى إرسال صورة أو استخدام /skip."
        )

        return GUEST_PHOTO_3

    photo = update.message.photo[-1]

    telegram_file = await photo.get_file()

    image_buffer = BytesIO()

    await telegram_file.download_to_memory(
        image_buffer
    )

    image_buffer.seek(0)

    context.user_data[
        "guest_photos"
    ].append(
        image_buffer.getvalue()
    )

    await show_guest_confirmation(
        update,
        context
    )

    return GUEST_CONFIRM


# =========================================================
# تخطي الصورة الثالثة
# =========================================================

async def skip_photo_3(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await show_guest_confirmation(
        update,
        context
    )

    return GUEST_CONFIRM


# =========================================================
# عرض مراجعة البيانات
# =========================================================

async def show_guest_confirmation(
    update,
    context
):

    guest = context.user_data.get(
        "new_guest",
        {}
    )

    photos = context.user_data.get(
        "guest_photos",
        []
    )

    text = (

        "📋 مراجعة بيانات النزيل\n\n"

        f"👤 الاسم: {guest.get('الاسم الثلاثي', '')}\n"
        f"👩 اسم الأم: {guest.get('اسم الأم', '')}\n"
        f"🎂 الولادة: {guest.get('مكان وتاريخ الولادة', '')}\n"
        f"🏠 السكن: {guest.get('السكن الأصلي', '')}\n"
        f"📍 المحافظة: {guest.get('المحافظة', '')}\n"
        f"🏨 الفندق: {guest.get('اسم الفندق', '')}\n"
        f"🚪 الغرفة: {guest.get('رقم الغرفة', '')}\n"
        f"📅 تاريخ النزول: {guest.get('تاريخ النزول', '')}\n"
        f"⏱️ مدة الإقامة: {guest.get('مدة الإقامة', '')}\n"
        f"🎯 سبب الإقامة: {guest.get('سبب الإقامة', '')}\n\n"

        f"📷 عدد الصور: {len(photos)}\n\n"

        "يرجى مراجعة البيانات قبل الإرسال."
    )

    keyboard = [

        [
            InlineKeyboardButton(
                "📤 إرسال المعلومات للإدارة",
                callback_data="send_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="cancel_guest"
            )
        ]

    ]

    if update.message:

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    else:

        await update.callback_query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )


# =========================================================
# إرسال النزيل
# =========================================================

async def send_guest_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer(
        "جارٍ إرسال المعلومات..."
    )

    if query.data == "cancel_guest":

        context.user_data.clear()

        await query.edit_message_text(

            "❌ تم إلغاء تسجيل النزيل.\n\n"
            "يمكنك البدء بتسجيل نزيل جديد."
        )

        return ConversationHandler.END

    if query.data != "send_guest":
        return ConversationHandler.END

    hotel = get_logged_hotel(
        query.from_user.id
    )

    if not hotel:

        await query.edit_message_text(
            "🔐 انتهت جلسة الدخول. يرجى تسجيل الدخول من جديد."
        )

        context.user_data.clear()

        return ConversationHandler.END

    guest = context.user_data.get(
        "new_guest"
    )

    photos = context.user_data.get(
        "guest_photos",
        []
    )

    if not guest:

        await query.edit_message_text(
            "❌ لا توجد بيانات لإرسالها."
        )

        return ConversationHandler.END

    # -----------------------------------------
    # التأكد من الصور الإلزامية
    # -----------------------------------------

    if len(photos) < 2:

        await query.edit_message_text(

            "❌ لا يمكن إرسال البيانات.\n\n"

            "يجب إرسال الصورتين الإلزاميتين أولاً."
        )

        return GUEST_PHOTO_1

    # -----------------------------------------
    # حفظ الصور
    # -----------------------------------------

    photo_1 = photos[0]

    photo_2 = photos[1]

    photo_3 = (
        photos[2]
        if len(photos) >= 3
        else None
    )

    # -----------------------------------------
    # حفظ قاعدة البيانات
    # -----------------------------------------

    guest_id = save_guest(
        guest,
        query.from_user.id,
        hotel["id"],
        photo_1,
        photo_2,
        photo_3
    )

    # -----------------------------------------
    # إنشاء PDF
    # -----------------------------------------

    pdf_file = create_guest_pdf(
        guest,
        photos
    )

    filename = safe_filename(
        guest.get(
            "الاسم الثلاثي",
            "تقرير_نزيل"
        )
    )

    # -----------------------------------------
    # العثور على المديرين
    # -----------------------------------------

    admin_ids = get_admin_ids()

    if not admin_ids:

        await query.edit_message_text(

            "⚠️ تم حفظ البيانات بنجاح، "
            "ولكن لا يوجد مدير مسجل حالياً "
            "لاستلام التقرير."
        )

        context.user_data.clear()

        return ConversationHandler.END

    # -----------------------------------------
    # إرسال التقرير للمدير
    # -----------------------------------------

    caption = (

        "📋 بيانات نزيل جديدة\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n"
        f"👤 الاسم: {guest.get('الاسم الثلاثي', '')}\n"
        f"🚪 الغرفة: {guest.get('رقم الغرفة', '')}\n"
        f"📅 تاريخ النزول: {guest.get('تاريخ النزول', '')}\n\n"

        "📤 تم إرسال المعلومات من صاحب الفندق."
    )

    for admin_id in admin_ids:

        try:

            await context.bot.send_document(

                chat_id=admin_id,

                document=pdf_file,

                filename=filename,

                caption=caption
            )

            # إعادة مؤشر الملف للبداية
            pdf_file.seek(0)

        except Exception as e:

            print(
                "Admin send error:",
                e
            )

    await query.edit_message_text(

        "✅ تم إرسال المعلومات إلى الإدارة بنجاح.\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n"
        f"👤 النزيل: {guest.get('الاسم الثلاثي', '')}\n\n"

        "جزاكم الله خيراً."
    )

    context.user_data.clear()

    return ConversationHandler.END


# =========================================================
# حفظ النزيل
# =========================================================

def save_guest(
    guest,
    telegram_user_id,
    hotel_account_id,
    photo_1=None,
    photo_2=None,
    photo_3=None
):

    now = datetime.now()

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO guests
        (
            guest_name,
            mother_name,
            birth,
            home,
            governorate,
            hotel,
            suite,
            room,
            checkin_date,
            duration,
            reason,
            record_date,
            record_time,
            telegram_user_id,
            telegram_username,
            hotel_account_id,
            photo_1,
            photo_2,
            photo_3
        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,

        (
            guest.get(
                "الاسم الثلاثي",
                "غير مذكور"
            ),

            guest.get(
                "اسم الأم",
                "غير مذكور"
            ),

            guest.get(
                "مكان وتاريخ الولادة",
                "غير مذكور"
            ),

            guest.get(
                "السكن الأصلي",
                "غير مذكور"
            ),

            guest.get(
                "المحافظة",
                "غير مذكور"
            ),

            guest.get(
                "اسم الفندق",
                "غير مذكور"
            ),

            guest.get(
                "رقم الجناح",
                "×"
            ),

            guest.get(
                "رقم الغرفة",
                "غير مذكور"
            ),

            guest.get(
                "تاريخ النزول",
                "غير مذكور"
            ),

            guest.get(
                "مدة الإقامة",
                "غير مذكور"
            ),

            guest.get(
                "سبب الإقامة",
                "غير مذكور"
            ),

            now.strftime(
                "%Y-%m-%d"
            ),

            now.strftime(
                "%H:%M:%S"
            ),

            str(
                telegram_user_id
            ),

            "",

            hotel_account_id,

            photo_1,

            photo_2,

            photo_3
        )
    )

    connection.commit()

    guest_id = cursor.lastrowid

    connection.close()

    return guest_id


# =========================================================
# الحصول على معرفات المديرين
# =========================================================

def get_admin_ids():

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT telegram_user_id
        FROM sessions
        WHERE role = 'admin'
          AND active = 1
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return [
        row["telegram_user_id"]
        for row in rows
        if row["telegram_user_id"]
    ]


# =========================================================
# PDF لنزيل
# =========================================================

def draw_pdf_header(
    pdf,
    title
):

    pdf.setFillColor(
        colors.HexColor("#17365D")
    )

    pdf.rect(
        0,
        PAGE_HEIGHT - 90,
        PAGE_WIDTH,
        90,
        fill=1,
        stroke=0
    )

    pdf.setFillColor(
        colors.white
    )

    pdf.setFont(
        PDF_FONT,
        18
    )

    pdf.drawRightString(
        PAGE_WIDTH - 45,
        PAGE_HEIGHT - 45,
        arabic_text(title)
    )

    pdf.setFillColor(
        colors.black
    )


def draw_field(
    pdf,
    y,
    key,
    value
):

    pdf.setFillColor(
        colors.HexColor("#F2F4F7")
    )

    pdf.roundRect(
        45,
        y - 22,
        PAGE_WIDTH - 90,
        28,
        5,
        fill=1,
        stroke=0
    )

    pdf.setFillColor(
        colors.black
    )

    pdf.setFont(
        PDF_FONT,
        10
    )

    line = f"{key}: {value}"

    pdf.drawRightString(
        PAGE_WIDTH - 60,
        y - 13,
        arabic_text(line)
    )

    return y - 38


def create_guest_pdf(
    guest,
    photos=None
):

    if photos is None:
        photos = []

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    draw_pdf_header(
        pdf,
        "مكتب أمن الفنادق والعقارات"
    )

    y = PAGE_HEIGHT - 125

    pdf.setFont(
        PDF_FONT,
        15
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            "تقرير نزيل فندق"
        )
    )

    y -= 40

    for key, value in guest.items():

        if y < 100:

            pdf.showPage()

            draw_pdf_header(
                pdf,
                "تقرير نزيل فندق"
            )

            y = PAGE_HEIGHT - 125

        y = draw_field(
            pdf,
            y,
            key,
            value
        )

    # -----------------------------------------
    # الصور
    # -----------------------------------------

    for index, photo_bytes in enumerate(
        photos,
        start=1
    ):

        if not photo_bytes:
            continue

        try:

            if y < 300:

                pdf.showPage()

                draw_pdf_header(
                    pdf,
                    "صور النزيل"
                )

                y = PAGE_HEIGHT - 125

            pdf.setFont(
                PDF_FONT,
                12
            )

            pdf.drawRightString(
                PAGE_WIDTH - 50,
                y,
                arabic_text(
                    f"الصورة رقم {index}"
                )
            )

            y -= 25

            image_buffer = BytesIO(
                photo_bytes
            )

            image = ImageReader(
                image_buffer
            )

            pdf.drawImage(
                image,
                70,
                y - 230,
                width=300,
                height=220,
                preserveAspectRatio=True,
                anchor="sw",
                mask="auto"
            )

            y -= 250

        except Exception as e:

            print(
                "PDF image error:",
                e
            )

    pdf.setFont(
        PDF_FONT,
        8
    )

    pdf.setFillColor(
        colors.grey
    )

    pdf.drawString(
        45,
        30,
        arabic_text(
            "تم إنشاء التقرير آلياً بواسطة نظام معلومات الفنادق"
        )
    )

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# اسم ملف PDF
# =========================================================

def safe_filename(
    name
):

    if not name:

        name = "تقرير_نزيل"

    name = re.sub(
        r'[\\/:*?"<>|]',
        "",
        name
    )

    name = re.sub(
        r"\s+",
        "_",
        name.strip()
    )

    if not name:

        name = "تقرير_نزيل"

    return name + ".pdf"


# =========================================================
# التقارير
# =========================================================

def get_guests_by_date(
    target_date
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM guests
        WHERE record_date = ?
        ORDER BY id ASC
        """,
        (target_date,)
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


def get_guests_by_month(
    year_month
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM guests
        WHERE substr(record_date, 1, 7) = ?
        ORDER BY id ASC
        """,
        (year_month,)
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# =========================================================
# التقرير اليومي
# =========================================================

async def daily_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    target_date = date.today().isoformat()

    rows = get_guests_by_date(
        target_date
    )

    if not rows:

        await update.message.reply_text(
            "📋 لا توجد بيانات مسجلة اليوم."
        )

        return

    total = len(rows)

    governorates = Counter(
        row["governorate"]
        for row in rows
    )

    hotels = Counter(
        row["hotel"]
        for row in rows
    )

    reasons = Counter(
        row["reason"]
        for row in rows
    )

    text = (

        "📋 تقرير عمل قسم معلومات الفنادق\n\n"

        f"📅 التاريخ: {target_date}\n\n"

        f"👥 إجمالي النزلاء: {total}\n\n"

        "🏠 حسب المحافظة:\n"
    )

    for name, count in governorates.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    text += "\n🏨 حسب الفندق:\n"

    for name, count in hotels.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    text += "\n🎯 أسباب الإقامة:\n"

    for name, count in reasons.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# التقرير أمس
# =========================================================

async def yesterday_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    yesterday = (
        date.today()
        -
        timedelta(days=1)
    ).isoformat()

    rows = get_guests_by_date(
        yesterday
    )

    if not rows:

        await update.message.reply_text(
            f"📋 لا توجد بيانات بتاريخ {yesterday}."
        )

        return

    await update.message.reply_text(

        f"📋 تقرير أمس\n\n"
        f"📅 التاريخ: {yesterday}\n"
        f"👥 عدد النزلاء: {len(rows)}"
    )


# =========================================================
# التقرير الشهري
# =========================================================

async def monthly_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    current_month = date.today().strftime(
        "%Y-%m"
    )

    rows = get_guests_by_month(
        current_month
    )

    if not rows:

        await update.message.reply_text(
            "📋 لا توجد بيانات خلال الشهر الحالي."
        )

        return

    governorates = Counter(
        row["governorate"]
        for row in rows
    )

    hotels = Counter(
        row["hotel"]
        for row in rows
    )

    reasons = Counter(
        row["reason"]
        for row in rows
    )

    text = (

        "📊 التقرير الشهري\n\n"

        f"📅 الشهر: {current_month}\n\n"

        f"👥 إجمالي النزلاء: {len(rows)}\n\n"

        "🏠 المحافظات:\n"
    )

    for name, count in governorates.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    text += "\n🏨 الفنادق:\n"

    for name, count in hotels.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    text += "\n🎯 أسباب الإقامة:\n"

    for name, count in reasons.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# إلغاء
# =========================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ تم إلغاء العملية."
    )

    return ConversationHandler.END


# =========================================================
# Web Server - Render
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(
        self
    ):

        self.send_response(
            200
        )

        self.send_header(
            "Content-type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            "Hotel Report Bot is running".encode(
                "utf-8"
            )
        )

    def log_message(
        self,
        format,
        *args
    ):

        pass


def run_web_server():

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    server = HTTPServer(
        (
            "0.0.0.0",
            port
        ),
        HealthHandler
    )

    print(
        f"Web server running on port {port}"
    )

    server.serve_forever()


# =========================================================
# التحقق من وجود التوكن
# =========================================================

if not TOKEN:

    print(
        "WARNING: BOT_TOKEN is not set!"
    )


# =========================================================
# إنشاء التطبيق
# =========================================================

app = (
    ApplicationBuilder()
    .token(TOKEN)
    .build()
)


# =========================================================
# تسجيل الدخول
# =========================================================

login_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "start",
            start
        )
    ],

    states={

        LOGIN_USERNAME: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                login_username
            )
        ],

        LOGIN_PASSWORD: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                login_password
            )
        ],
    },

    fallbacks=[
        CommandHandler(
            "cancel",
            cancel
        )
    ],

    allow_reentry=True
)


# =========================================================
# إضافة فندق
# =========================================================

add_hotel_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "add_hotel",
            add_hotel_start
        )
    ],

    states={

        ADD_HOTEL_USERNAME: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                add_hotel_username
            )
        ],

        ADD_HOTEL_PASSWORD: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                add_hotel_password
            )
        ],

        ADD_HOTEL_NAME: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                add_hotel_name
            )
        ],
    },

    fallbacks=[
        CommandHandler(
            "cancel",
            cancel
        )
    ],

    allow_reentry=True
)


# =========================================================
# تسجيل النزيل
# =========================================================

guest_handler = ConversationHandler(

    entry_points=[

        CallbackQueryHandler(
            new_guest_start,
            pattern="^new_guest$"
        )
    ],

    states={

        GUEST_NAME: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                guest_name
            )
        ],

        GUEST_MOTHER: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                guest_mother
            )
        ],

        GUEST_BIRTH: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                guest_birth
            )
        ],

        GUEST_HOME: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                guest_home
            )
        ],

        GUEST_GOVERNORATE: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                guest_governorate
            )
        ],

        GUEST_ROOM: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                guest_room
            )
        ],

        GUEST_CHECKIN: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                guest_checkin
            )
        ],

        GUEST_DURATION: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                guest_duration
            )
        ],

        GUEST_REASON: [

            MessageHandler(
                filters.TEXT
                &
                ~filters.COMMAND,
                guest_reason
            )
        ],

        GUEST_PHOTO_1: [

            MessageHandler(
                filters.PHOTO,
                guest_photo_1
            )
        ],

        GUEST_PHOTO_2: [

            MessageHandler(
                filters.PHOTO,
                guest_photo_2
            )
        ],

        GUEST_PHOTO_3: [

            MessageHandler(
                filters.PHOTO,
                guest_photo_3
            ),

            CommandHandler(
                "skip",
                skip_photo_3
            )
        ],

        GUEST_CONFIRM: [

            CallbackQueryHandler(
                send_guest_callback,
                pattern="^(send_guest|cancel_guest)$"
            )
        ],
    },

    fallbacks=[
        CommandHandler(
            "cancel",
            cancel
        )
    ],

    allow_reentry=True
)


# =========================================================
# ترتيب الـ Handlers
# =========================================================

app.add_handler(
    login_handler
)

app.add_handler(
    add_hotel_handler
)

app.add_handler(
    guest_handler
)

app.add_handler(
    CallbackQueryHandler(
        hotel_management_callback,
        pattern="^(delete_hotel_|confirm_delete_|enable_hotel_|cancel_delete)"
    )
)

app.add_handler(
    CallbackQueryHandler(
        async def_callback_logout
        if False
        else logout_callback,
        pattern="^logout$"
    )
)

app.add_handler(
    CommandHandler(
        "logout",
        logout
    )
)

app.add_handler(
    CommandHandler(
        "hotels",
        hotels_list
    )
)

app.add_handler(
    CommandHandler(
        "daily",
        daily_report
    )
)

app.add_handler(
    CommandHandler(
        "yesterday",
        yesterday_report
    )
)

app.add_handler(
    CommandHandler(
        "monthly",
        monthly_report
    )
)


# =========================================================
# زر تسجيل الخروج
# =========================================================

async def logout_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    logout_session(
        query.from_user.id
    )

    context.user_data.clear()

    await set_guest_commands(
        context.application,
        query.message.chat.id
    )

    await query.edit_message_text(

        "🚪 تم تسجيل الخروج بنجاح.\n\n"

        "جزاكم الله خيراً.\n\n"

        "عند العودة استخدم /start."
    )


# =========================================================
# إضافة Handler لاستقبال /start بعد تسجيل الدخول
# =========================================================

# =========================================================
# MAIN
# =========================================================

async def main():

    init_database()

    if not TOKEN:

        print(
            "ERROR: BOT_TOKEN is not set!"
        )

        return

    if not ADMIN_USERNAME:

        print(
            "WARNING: ADMIN_USERNAME is empty!"
        )

    if not ADMIN_PASSWORD:

        print(
            "WARNING: ADMIN_PASSWORD is empty!"
        )

    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    await app.initialize()

    await app.start()

    await app.updater.start_polling()

    print(
        "Telegram Bot is running successfully!"
    )

    try:

        await asyncio.Event().wait()

    finally:

        await app.updater.stop()

        await app.stop()

        await app.shutdown()


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
        )
