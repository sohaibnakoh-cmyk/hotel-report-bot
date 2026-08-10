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
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
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

# مهم:
# ضع في Environment:
#
# ADMIN_USERNAME = admin
# ADMIN_PASSWORD = كلمة_مرور_قوية_جداً
#
ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    "admin"
).strip()

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD",
    ""
)

DATABASE_FILE = "hotel_reports.db"

IMAGE_FILE = "images.png"

PAGE_WIDTH, PAGE_HEIGHT = A4


# =========================================================
# حالات تسجيل الدخول
# =========================================================

LOGIN_USERNAME = 1
LOGIN_PASSWORD = 2

ADMIN_LOGIN_USERNAME = 3
ADMIN_LOGIN_PASSWORD = 4

ADD_HOTEL_USERNAME = 5
ADD_HOTEL_PASSWORD = 6
ADD_HOTEL_NAME = 7


# =========================================================
# حالات نموذج النزيل
# =========================================================

GUEST_NAME = 20
GUEST_MOTHER = 21
GUEST_BIRTH = 22
GUEST_HOME = 23
GUEST_GOVERNORATE = 24
GUEST_ROOM = 25
GUEST_SUITE = 26
GUEST_CHECKIN = 27
GUEST_DURATION = 28
GUEST_REASON = 29

GUEST_PHOTO_1 = 30
GUEST_PHOTO_2 = 31
GUEST_PHOTO_3 = 32

GUEST_CONFIRM = 33


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

    except Exception as e:

        print(
            "Arabic font error:",
            e
        )

        PDF_FONT = "Helvetica"

else:

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

        return get_display(reshaped)

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
    # جلسات الفنادق
    # -----------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (

            telegram_user_id TEXT PRIMARY KEY,

            hotel_account_id INTEGER,

            login_time TEXT,

            active INTEGER DEFAULT 1
        )
        """
    )

    # -----------------------------------------
    # جلسة المدير
    # -----------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_sessions (

            telegram_user_id TEXT PRIMARY KEY,

            login_time TEXT,

            active INTEGER DEFAULT 1
        )
        """
    )

    connection.commit()

    connection.close()


# =========================================================
# تشفير كلمات المرور
# =========================================================

def hash_password(
    password,
    salt=None
):

    if salt is None:
        salt = secrets.token_hex(16)

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
# التحقق من حساب الفندق
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
# الفنادق
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


def delete_hotel(
    hotel_id
):

    connection = get_db()

    cursor = connection.cursor()

    # تعطيل الحساب
    cursor.execute(
        """
        UPDATE hotel_accounts
        SET active = 0
        WHERE id = ?
        """,
        (hotel_id,)
    )

    # إنهاء جلسة الفندق
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
# جلسات الفندق
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
            login_time,
            active
        )
        VALUES (?, ?, ?, 1)
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


def logout_hotel(
    telegram_user_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM sessions
        WHERE telegram_user_id = ?
        """,
        (str(telegram_user_id),)
    )

    connection.commit()

    connection.close()


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
# جلسات المدير
# =========================================================

def create_admin_session(
    telegram_user_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO admin_sessions
        (
            telegram_user_id,
            login_time,
            active
        )
        VALUES (?, ?, 1)
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


def logout_admin(
    telegram_user_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM admin_sessions
        WHERE telegram_user_id = ?
        """,
        (str(telegram_user_id),)
    )

    connection.commit()

    connection.close()


def is_admin_logged(
    telegram_user_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM admin_sessions
        WHERE telegram_user_id = ?
          AND active = 1
        """,
        (str(telegram_user_id),)
    )

    row = cursor.fetchone()

    connection.close()

    return row is not None


# =========================================================
# حفظ النزيل
# =========================================================

def save_guest(
    guest,
    update,
    hotel_account_id
):

    now = datetime.now()

    user_id = str(
        update.effective_user.id
    )

    username = (
        update.effective_user.username
        or ""
    )

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
            hotel_account_id
        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "غير مذكور"
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

            user_id,

            username,

            hotel_account_id
        )
    )

    connection.commit()

    guest_id = cursor.lastrowid

    connection.close()

    return guest_id


# =========================================================
# صورة من Telegram
# =========================================================

async def download_photo(
    message
):

    if not message or not message.photo:
        return None

    try:

        photo = message.photo[-1]

        telegram_file = await photo.get_file()

        image_buffer = BytesIO()

        await telegram_file.download_to_memory(
            image_buffer
        )

        image_buffer.seek(0)

        return image_buffer

    except Exception as e:

        print(
            "Photo download error:",
            e
        )

        return None


# =========================================================
# حفظ صور النموذج
# =========================================================

async def save_guest_photo(
    update,
    context,
    key
):

    photo = await download_photo(
        update.message
    )

    if not photo:

        return False

    context.user_data[key] = photo.getvalue()

    return True


# =========================================================
# صورة الترحيب
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
            "login",
            "🔐 تسجيل الدخول"
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
            "Hotel command error:",
            e
        )


# =========================================================
# أوامر الفندق بعد الدخول
# =========================================================

async def set_logged_hotel_commands(
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
            "Logged hotel command error:",
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
            "delete_hotel",
            "🗑 حذف فندق"
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
            "Admin command error:",
            e
        )


# =========================================================
# الصفحة الرئيسية
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    # المدير المسجل
    if is_admin_logged(user_id):

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(
            update
        )

        await update.message.reply_text(

            "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

            "﴿وَقُل رَّبِّ زِدْنِي عِلْمًا﴾\n\n"

            "🤲 أهلاً وسهلاً بك في نظام معلومات "
            "الفنادق والعقارات.\n\n"

            "نسأل الله أن يوفقنا وإياكم لما فيه "
            "الخير، وأن يجعل هذا العمل نافعاً "
            "وخادماً للناس.\n\n"

            "👨‍💼 تم التعرف على حسابك كحساب مدير.\n\n"

            "يمكنك إدارة الفنادق واستقبال التقارير "
            "من خلال القائمة."
        )

        return

    # الفندق المسجل
    hotel = get_logged_hotel(
        user_id
    )

    if hotel:

        await set_logged_hotel_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(
            update
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "➕ تسجيل نزيل جديد",
                    callback_data="new_guest"
                )
            ]
        ]

        await update.message.reply_text(

            "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

            "﴿وَقُلِ اعْمَلُوا فَسَيَرَى اللَّهُ "
            "عَمَلَكُمْ﴾\n\n"

            f"🏨 أهلاً وسهلاً بكم\n"
            f"فندق: {hotel['hotel_name']}\n\n"

            "بارك الله في جهودكم، ويمكنكم من خلال "
            "هذا النظام إرسال بيانات النزلاء "
            "بسهولة وتنظيم.\n\n"

            "🔐 تم تسجيل الدخول مسبقاً.\n"
            "لن نطلب اسم المستخدم أو كلمة المرور "
            "مرة أخرى حتى تسجيل الخروج.\n\n"

            "اختر من الأسفل للبدء:",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    # غير مسجل
    await set_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await send_welcome_image(
        update
    )

    await update.message.reply_text(

        "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

        "﴿وَقُل رَّبِّ زِدْنِي عِلْمًا﴾\n\n"

        "🤲 أهلاً وسهلاً ومرحباً بكم في\n"
        "🏨 نظام معلومات الفنادق\n\n"

        "نرجو تعبئة البيانات بدقة وأمانة، "
        "والله ولي التوفيق.\n\n"

        "🔐 لتسجيل الدخول استخدم:\n"
        "/login"
    )


# =========================================================
# بدء تسجيل الدخول للفندق
# =========================================================

async def login_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if is_admin_logged(user_id):

        await update.message.reply_text(
            "👨‍💼 أنت مسجل الدخول كمدير."
        )

        return ConversationHandler.END

    hotel = get_logged_hotel(
        user_id
    )

    if hotel:

        await update.message.reply_text(

            "✅ أنت مسجل الدخول بالفعل.\n\n"
            f"🏨 الفندق: {hotel['hotel_name']}"
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "🔐 تسجيل دخول الفندق\n\n"
        "أرسل اسم المستخدم:"
    )

    return LOGIN_USERNAME


async def login_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = update.message.text.strip()

    context.user_data[
        "login_username"
    ] = username

    try:
        await update.message.delete()
    except Exception:
        pass

    await update.message.reply_text(
        "🔑 أرسل كلمة المرور:"
    )

    return LOGIN_PASSWORD


async def login_password(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    password = update.message.text.strip()

    username = context.user_data.get(
        "login_username"
    )

    try:
        await update.message.delete()
    except Exception:
        pass

    if not username:

        await update.message.reply_text(
            "❌ انتهت عملية الدخول.\n"
            "استخدم /login من جديد."
        )

        return ConversationHandler.END

    account = authenticate_hotel(
        username,
        password
    )

    if not account:

        context.user_data.pop(
            "login_username",
            None
        )

        await update.message.reply_text(

            "❌ اسم المستخدم أو كلمة المرور "
            "غير صحيحة.\n\n"

            "استخدم /login للمحاولة مرة أخرى."
        )

        return ConversationHandler.END

    old_telegram_id = account[
        "telegram_user_id"
    ]

    current_telegram_id = str(
        update.effective_user.id
    )

    if (
        old_telegram_id
        and old_telegram_id != current_telegram_id
    ):

        await update.message.reply_text(

            "⚠️ هذا الحساب مرتبط حالياً "
            "بحساب Telegram آخر.\n\n"

            "يرجى التواصل مع الإدارة."
        )

        return ConversationHandler.END

    create_hotel_session(
        current_telegram_id,
        account["id"]
    )

    context.user_data.pop(
        "login_username",
        None
    )

    await set_logged_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await update.message.reply_text(

        "✅ تم تسجيل الدخول بنجاح\n\n"

        f"🏨 الفندق: {account['hotel_name']}\n\n"

        "بارك الله فيكم.\n"
        "يمكنكم الآن البدء بتسجيل بيانات النزيل."
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ تسجيل نزيل جديد",
                callback_data="new_guest"
            )
        ]
    ]

    await update.message.reply_text(
        "اضغط الزر للبدء:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    return ConversationHandler.END


# =========================================================
# تسجيل خروج
# =========================================================

async def logout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    # مدير
    if is_admin_logged(user_id):

        logout_admin(
            user_id
        )

        context.user_data.clear()

        await set_hotel_commands(
            context.application,
            update.effective_chat.id
        )

        await update.message.reply_text(
            "🚪 تم تسجيل خروج المدير بنجاح."
        )

        return

    # فندق
    logout_hotel(
        user_id
    )

    context.user_data.clear()

    await set_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await update.message.reply_text(

        "🚪 تم تسجيل الخروج بنجاح.\n\n"

        "نسأل الله لكم التوفيق.\n\n"

        "🔐 عند العودة استخدم /login"
    )


# =========================================================
# دخول المدير
# =========================================================

async def admin_login_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if is_admin_logged(user_id):

        await update.message.reply_text(
            "✅ أنت مسجل الدخول كمدير بالفعل."
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "👨‍💼 تسجيل دخول المدير\n\n"

        "أرسل اسم مستخدم المدير:"
    )

    return ADMIN_LOGIN_USERNAME


async def admin_login_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = update.message.text.strip()

    context.user_data[
        "admin_username"
    ] = username

    try:
        await update.message.delete()
    except Exception:
        pass

    await update.message.reply_text(
        "🔑 أرسل كلمة مرور المدير:"
    )

    return ADMIN_LOGIN_PASSWORD


async def admin_login_password(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = context.user_data.get(
        "admin_username"
    )

    password = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    # =====================================================
    # الإصلاح المهم:
    # لم نعد نشترط أن يكون Telegram username = admin
    # =====================================================

    if (
        username == ADMIN_USERNAME
        and ADMIN_PASSWORD
        and password == ADMIN_PASSWORD
    ):

        create_admin_session(
            update.effective_user.id
        )

        context.user_data.pop(
            "admin_username",
            None
        )

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await update.message.reply_text(

            "✅ تم تسجيل دخول المدير بنجاح.\n\n"

            "🤲 بارك الله فيكم ووفقكم لكل خير.\n\n"

            "يمكنك الآن إدارة الفنادق واستقبال "
            "تقارير النزلاء."
        )

    else:

        context.user_data.pop(
            "admin_username",
            None
        )

        await update.message.reply_text(

            "❌ اسم المستخدم أو كلمة المرور "
            "غير صحيحة.\n\n"

            "تأكد من أن القيم الموجودة في "
            "Environment Variables صحيحة."
        )

    return ConversationHandler.END


# =========================================================
# إضافة فندق
# =========================================================

async def add_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ يجب تسجيل الدخول كمدير أولاً."
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "🏨 إضافة فندق جديد\n\n"
        "أرسل اسم المستخدم للفندق:"
    )

    return ADD_HOTEL_USERNAME


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
            "والنقطة أو الشرطة."
        )

        return ADD_HOTEL_USERNAME

    context.user_data[
        "new_hotel_username"
    ] = username

    await update.message.reply_text(
        "🔑 أرسل كلمة المرور:\n\n"
        "يفضل 8 أحرف أو أكثر."
    )

    return ADD_HOTEL_PASSWORD


async def add_hotel_password(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    password = update.message.text.strip()

    if len(password) < 8:

        await update.message.reply_text(
            "❌ كلمة المرور يجب ألا تقل عن 8 أحرف."
        )

        return ADD_HOTEL_PASSWORD

    context.user_data[
        "new_hotel_password"
    ] = password

    try:
        await update.message.delete()
    except Exception:
        pass

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
            "❌ يجب إدخال اسم الفندق."
        )

        return ADD_HOTEL_NAME

    hotel_id, error = create_hotel_account(
        hotel_name,
        username,
        password
    )

    context.user_data.pop(
        "new_hotel_password",
        None
    )

    context.user_data.pop(
        "new_hotel_username",
        None
    )

    if error:

        await update.message.reply_text(
            f"❌ {error}"
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "✅ تم إنشاء حساب الفندق بنجاح.\n\n"

        f"🏨 الفندق: {hotel_name}\n"
        f"👤 اسم المستخدم: {username}\n\n"

        "🔐 تم حفظ كلمة المرور بشكل آمن.\n\n"

        "يمكنك الآن إرسال بيانات الدخول "
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

    if not is_admin_logged(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    hotels = get_all_hotels()

    if not hotels:

        await update.message.reply_text(
            "📋 لا توجد فنادق."
        )

        return

    text = "🏨 قائمة الفنادق\n\n"

    for hotel in hotels:

        status = (
            "🟢 فعال"
            if hotel["active"]
            else "🔴 محذوف/موقوف"
        )

        connected = (
            "🔗 مرتبط"
            if hotel["telegram_user_id"]
            else "⚪ غير مسجل"
        )

        text += (

            f"━━━━━━━━━━━━━━\n"
            f"🆔 الرقم: {hotel['id']}\n"
            f"🏨 الفندق: {hotel['hotel_name']}\n"
            f"👤 المستخدم: {hotel['username']}\n"
            f"📌 الحالة: {status}\n"
            f"📱 Telegram: {connected}\n"
            f"📅 الإنشاء: {hotel['created_at']}\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# حذف فندق
# =========================================================

async def delete_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    hotels = get_all_hotels()

    active_hotels = [
        h for h in hotels
        if h["active"]
    ]

    if not active_hotels:

        await update.message.reply_text(
            "📋 لا توجد فنادق فعالة لحذفها."
        )

        return

    keyboard = []

    for hotel in active_hotels:

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🗑 {hotel['hotel_name']}",
                    callback_data=f"delete_hotel:{hotel['id']}"
                )
            ]
        )

    await update.message.reply_text(

        "🗑 اختر الفندق الذي تريد حذفه:\n\n"

        "⚠️ بعد الحذف لن يستطيع صاحب الفندق "
        "تسجيل الدخول.",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def delete_hotel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if not is_admin_logged(
        query.from_user.id
    ):

        await query.edit_message_text(
            "⛔ غير مصرح."
        )

        return

    data = query.data

    if not data.startswith(
        "delete_hotel:"
    ):
        return

    try:

        hotel_id = int(
            data.split(":")[1]
        )

    except Exception:

        await query.edit_message_text(
            "❌ رقم الفندق غير صحيح."
        )

        return

    hotels = get_all_hotels()

    hotel = next(
        (
            h for h in hotels
            if h["id"] == hotel_id
        ),
        None
    )

    if not hotel:

        await query.edit_message_text(
            "❌ الفندق غير موجود."
        )

        return

    delete_hotel(
        hotel_id
    )

    await query.edit_message_text(

        "🗑 تم حذف/تعطيل الفندق بنجاح.\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n\n"

        "🔒 لن يستطيع صاحب الفندق تسجيل "
        "الدخول بهذا الحساب."
    )


# =========================================================
# بدء نموذج النزيل
# =========================================================

async def new_guest_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query:

        await query.answer()

        user_id = query.from_user.id

        message = query.message

    else:

        user_id = update.effective_user.id

        message = update.message

    hotel = get_logged_hotel(
        user_id
    )

    if not hotel:

        await message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً."
        )

        return ConversationHandler.END

    # تنظيف النموذج السابق
    for key in list(
        context.user_data.keys()
    ):

        if key.startswith("guest_"):

            context.user_data.pop(
                key,
                None
            )

    context.user_data[
        "guest_hotel_id"
    ] = hotel["id"]

    context.user_data[
        "guest_hotel_name"
    ] = hotel["hotel_name"]

    await message.reply_text(

        "📋 نموذج تسجيل نزيل جديد\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n\n"

        "سيتم الآن طلب بيانات النزيل سؤالاً "
        "بعد سؤال.\n\n"

        "⚠️ يرجى التأكد من صحة البيانات قبل "
        "إرسالها للإدارة.\n\n"

        "👤 أولاً:\n"
        "أرسل الاسم الثلاثي للنزيل."
    )

    return GUEST_NAME


# =========================================================
# الاسم
# =========================================================

async def guest_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    if not value:

        await update.message.reply_text(
            "❌ أرسل الاسم الثلاثي."
        )

        return GUEST_NAME

    context.user_data[
        "guest_name"
    ] = value

    await update.message.reply_text(
        "👩 أرسل اسم الأم:"
    )

    return GUEST_MOTHER


# =========================================================
# اسم الأم
# =========================================================

async def guest_mother(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    if not value:

        await update.message.reply_text(
            "❌ أرسل اسم الأم."
        )

        return GUEST_MOTHER

    context.user_data[
        "guest_mother"
    ] = value

    await update.message.reply_text(
        "📅 أرسل مكان وتاريخ الولادة:"
    )

    return GUEST_BIRTH


# =========================================================
# الولادة
# =========================================================

async def guest_birth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "guest_birth"
    ] = value

    await update.message.reply_text(
        "🏠 أرسل السكن الأصلي:"
    )

    return GUEST_HOME


# =========================================================
# السكن
# =========================================================

async def guest_home(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "guest_home"
    ] = value

    await update.message.reply_text(
        "🗺 أرسل المحافظة:"
    )

    return GUEST_GOVERNORATE


# =========================================================
# المحافظة
# =========================================================

async def guest_governorate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "guest_governorate"
    ] = value

    await update.message.reply_text(
        "🚪 أرسل رقم الغرفة:"
    )

    return GUEST_ROOM


# =========================================================
# الغرفة
# =========================================================

async def guest_room(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "guest_room"
    ] = value

    await update.message.reply_text(
        "🏢 أرسل رقم الجناح.\n\n"
        "إذا لم يوجد جناح اكتب: لا يوجد"
    )

    return GUEST_SUITE


# =========================================================
# الجناح
# =========================================================

async def guest_suite(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "guest_suite"
    ] = value

    await update.message.reply_text(
        "📅 أرسل تاريخ النزول:"
    )

    return GUEST_CHECKIN


# =========================================================
# تاريخ النزول
# =========================================================

async def guest_checkin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "guest_checkin"
    ] = value

    await update.message.reply_text(
        "⏱ أرسل مدة الإقامة:"
    )

    return GUEST_DURATION


# =========================================================
# مدة الإقامة
# =========================================================

async def guest_duration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "guest_duration"
    ] = value

    await update.message.reply_text(
        "🎯 أرسل سبب الإقامة:"
    )

    return GUEST_REASON


# =========================================================
# سبب الإقامة
# =========================================================

async def guest_reason(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    context.user_data[
        "guest_reason"
    ] = value

    await update.message.reply_text(

        "📷 الآن الصور المطلوبة.\n\n"

        "سيتم طلب 3 صور.\n\n"

        "⚠️ الصورة الأولى إلزامية.\n"
        "⚠️ الصورة الثانية إلزامية.\n"
        "📌 الصورة الثالثة اختيارية.\n\n"

        "أرسل الصورة الأولى الآن."
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
            "⚠️ الصورة الأولى إلزامية.\n\n"
            "أرسل صورة وليس نصاً."
        )

        return GUEST_PHOTO_1

    success = await save_guest_photo(
        update,
        context,
        "guest_photo_1"
    )

    if not success:

        await update.message.reply_text(
            "❌ تعذر حفظ الصورة.\n"
            "أرسلها مرة أخرى."
        )

        return GUEST_PHOTO_1

    await update.message.reply_text(

        "✅ تم استلام الصورة الأولى.\n\n"

        "📷 أرسل الصورة الثانية.\n"
        "⚠️ الصورة الثانية إلزامية."
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
            "⚠️ الصورة الثانية إلزامية.\n\n"
            "أرسل صورة."
        )

        return GUEST_PHOTO_2

    success = await save_guest_photo(
        update,
        context,
        "guest_photo_2"
    )

    if not success:

        await update.message.reply_text(
            "❌ تعذر حفظ الصورة.\n"
            "أرسلها مرة أخرى."
        )

        return GUEST_PHOTO_2

    keyboard = [

        [
            InlineKeyboardButton(
                "📷 إضافة الصورة الثالثة",
                callback_data="add_photo_3"
            )
        ],

        [
            InlineKeyboardButton(
                "➡️ متابعة بدون الصورة الثالثة",
                callback_data="skip_photo_3"
            )
        ]

    ]

    await update.message.reply_text(

        "✅ تم استلام الصورة الثانية.\n\n"

        "📷 يمكنك الآن إضافة صورة ثالثة "
        "اختيارية، أو المتابعة بدونها.",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
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

            "📌 الصورة الثالثة اختيارية.\n\n"

            "إذا أردت إضافتها أرسل صورة، "
            "أو استخدم الزر السابق للمتابعة."
        )

        return GUEST_PHOTO_3

    success = await save_guest_photo(
        update,
        context,
        "guest_photo_3"
    )

    if not success:

        await update.message.reply_text(
            "❌ تعذر حفظ الصورة."
        )

        return GUEST_PHOTO_3

    await show_guest_confirmation(
        update.message,
        context
    )

    return GUEST_CONFIRM


# =========================================================
# عرض التأكيد
# =========================================================

async def show_guest_confirmation(
    message,
    context
):

    keyboard = [

        [
            InlineKeyboardButton(
                "📤 إرسال المعلومات للإدارة",
                callback_data="submit_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="cancel_guest"
            )
        ]

    ]

    hotel_name = context.user_data.get(
        "guest_hotel_name",
        "غير معروف"
    )

    summary = (

        "📋 مراجعة بيانات النزيل\n\n"

        f"🏨 الفندق: {hotel_name}\n"

        f"👤 الاسم: "
        f"{context.user_data.get('guest_name', '')}\n"

        f"👩 اسم الأم: "
        f"{context.user_data.get('guest_mother', '')}\n"

        f"📅 الولادة: "
        f"{context.user_data.get('guest_birth', '')}\n"

        f"🏠 السكن: "
        f"{context.user_data.get('guest_home', '')}\n"

        f"🗺 المحافظة: "
        f"{context.user_data.get('guest_governorate', '')}\n"

        f"🚪 الغرفة: "
        f"{context.user_data.get('guest_room', '')}\n"

        f"🏢 الجناح: "
        f"{context.user_data.get('guest_suite', '')}\n"

        f"📅 تاريخ النزول: "
        f"{context.user_data.get('guest_checkin', '')}\n"

        f"⏱ مدة الإقامة: "
        f"{context.user_data.get('guest_duration', '')}\n"

        f"🎯 سبب الإقامة: "
        f"{context.user_data.get('guest_reason', '')}\n\n"

        "📷 الصور:\n"
        "✅ الصورة الأولى\n"
        "✅ الصورة الثانية\n"

    )

    if context.user_data.get(
        "guest_photo_3"
    ):

        summary += "✅ الصورة الثالثة\n"

    else:

        summary += "➖ الصورة الثالثة غير مضافة\n"

    summary += (

        "\n⚠️ يرجى مراجعة البيانات جيداً.\n\n"

        "بعد الضغط على «إرسال المعلومات للإدارة» "
        "سيتم حفظ البيانات وإرسال التقرير."
    )

    await message.reply_text(

        summary,

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# تخطي الصورة الثالثة
# =========================================================

async def skip_photo_3_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if not get_logged_hotel(
        query.from_user.id
    ):

        await query.edit_message_text(
            "🔐 انتهت جلسة الدخول."
        )

        return

    await query.edit_message_text(
        "✅ تم تجاوز الصورة الثالثة."
    )

    await show_guest_confirmation(
        query.message,
        context
    )


# =========================================================
# إضافة الصورة الثالثة
# =========================================================

async def add_photo_3_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(

        "📷 أرسل الصورة الثالثة الآن.\n\n"
        "هذه الصورة اختيارية."
    )


# =========================================================
# إرسال النزيل للإدارة
# =========================================================

async def submit_guest_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    hotel = get_logged_hotel(
        user_id
    )

    if not hotel:

        await query.edit_message_text(
            "🔐 انتهت جلسة الدخول."
        )

        return

    # -----------------------------------------
    # التأكد من الصور الإلزامية
    # -----------------------------------------

    if not context.user_data.get(
        "guest_photo_1"
    ):

        await query.edit_message_text(
            "❌ الصورة الأولى إلزامية."
        )

        return

    if not context.user_data.get(
        "guest_photo_2"
    ):

        await query.edit_message_text(
            "❌ الصورة الثانية إلزامية."
        )

        return

    guest = {

        "الاسم الثلاثي":
            context.user_data.get(
                "guest_name",
                "غير مذكور"
            ),

        "اسم الأم":
            context.user_data.get(
                "guest_mother",
                "غير مذكور"
            ),

        "مكان وتاريخ الولادة":
            context.user_data.get(
                "guest_birth",
                "غير مذكور"
            ),

        "السكن الأصلي":
            context.user_data.get(
                "guest_home",
                "غير مذكور"
            ),

        "المحافظة":
            context.user_data.get(
                "guest_governorate",
                "غير مذكور"
            ),

        "اسم الفندق":
            hotel["hotel_name"],

        "رقم الجناح":
            context.user_data.get(
                "guest_suite",
                "غير مذكور"
            ),

        "رقم الغرفة":
            context.user_data.get(
                "guest_room",
                "غير مذكور"
            ),

        "تاريخ النزول":
            context.user_data.get(
                "guest_checkin",
                "غير مذكور"
            ),

        "مدة الإقامة":
            context.user_data.get(
                "guest_duration",
                "غير مذكور"
            ),

        "سبب الإقامة":
            context.user_data.get(
                "guest_reason",
                "غير مذكور"
            ),
    }

    # -----------------------------------------
    # حفظ البيانات
    # -----------------------------------------

    save_guest(
        guest,
        update,
        hotel["id"]
    )

    # -----------------------------------------
    # إنشاء PDF
    # -----------------------------------------

    photos = [

        context.user_data.get(
            "guest_photo_1"
        ),

        context.user_data.get(
            "guest_photo_2"
        ),

        context.user_data.get(
            "guest_photo_3"
        ),
    ]

    pdf_file = create_guest_pdf_with_photos(
        guest,
        photos
    )

    guest_name = guest[
        "الاسم الثلاثي"
    ]

    filename = safe_filename(
        guest_name
    )

    # -----------------------------------------
    # إرسال للمدير
    # -----------------------------------------

    sent_to_admin = False

    admin_ids = get_admin_ids()

    for admin_id in admin_ids:

        try:

            pdf_file.seek(0)

            await context.application.bot.send_document(

                chat_id=admin_id,

                document=pdf_file,

                filename=filename,

                caption=(

                    "📥 تقرير نزيل جديد\n\n"

                    f"🏨 الفندق: {hotel['hotel_name']}\n"

                    f"👤 النزيل: {guest_name}\n"

                    f"🚪 الغرفة: "
                    f"{guest['رقم الغرفة']}\n\n"

                    "✅ تم استلام التقرير من الفندق."
                )
            )

            sent_to_admin = True

        except Exception as e:

            print(
                "Admin send error:",
                e
            )

    # -----------------------------------------
    # رسالة الفندق
    # -----------------------------------------

    if sent_to_admin:

        await query.edit_message_text(

            "✅ تم إرسال معلومات النزيل بنجاح "
            "إلى الإدارة.\n\n"

            f"🏨 الفندق: {hotel['hotel_name']}\n"
            f"👤 النزيل: {guest_name}\n\n"

            "بارك الله فيكم.\n"
            "يمكنكم تسجيل نزيل جديد."
        )

    else:

        await query.edit_message_text(

            "⚠️ تم حفظ البيانات في قاعدة البيانات، "
            "لكن تعذر إرسال الملف إلى الإدارة حالياً.\n\n"

            "يرجى التواصل مع الإدارة."
        )

    # تنظيف بيانات النموذج
    clear_guest_data(
        context
    )

    # زر نزيل جديد
    keyboard = [

        [
            InlineKeyboardButton(
                "➕ تسجيل نزيل جديد",
                callback_data="new_guest"
            )
        ]

    ]

    await query.message.reply_text(

        "هل تريد تسجيل نزيل آخر؟",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# إلغاء نموذج النزيل
# =========================================================

async def cancel_guest_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    clear_guest_data(
        context
    )

    await query.edit_message_text(

        "❌ تم إلغاء تسجيل النزيل.\n\n"

        "لم يتم إرسال التقرير للإدارة."
    )


# =========================================================
# تنظيف نموذج النزيل
# =========================================================

def clear_guest_data(
    context
):

    keys = [

        "guest_name",
        "guest_mother",
        "guest_birth",
        "guest_home",
        "guest_governorate",
        "guest_room",
        "guest_suite",
        "guest_checkin",
        "guest_duration",
        "guest_reason",
        "guest_photo_1",
        "guest_photo_2",
        "guest_photo_3",
        "guest_hotel_id",
        "guest_hotel_name",
    ]

    for key in keys:

        context.user_data.pop(
            key,
            None
        )


# =========================================================
# الحصول على معرفات المديرين
# =========================================================

def get_admin_ids():

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT telegram_user_id
        FROM admin_sessions
        WHERE active = 1
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return [
        row["telegram_user_id"]
        for row in rows
    ]


# =========================================================
# PDF نزيل + 3 صور
# =========================================================

def create_guest_pdf_with_photos(
    guest,
    photos
):

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

    for index, photo_data in enumerate(
        photos,
        start=1
    ):

        if not photo_data:
            continue

        try:

            if y < 300:

                pdf.showPage()

                draw_pdf_header(
                    pdf,
                    f"الصورة رقم {index}"
                )

                y = PAGE_HEIGHT - 125

            image_buffer = BytesIO(
                photo_data
            )

            image = ImageReader(
                image_buffer
            )

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

            y -= 20

            pdf.drawImage(
                image,
                50,
                y - 230,
                width=350,
                height=230,
                preserveAspectRatio=True,
                anchor="sw",
                mask="auto"
            )

            y -= 255

        except Exception as e:

            print(
                "PDF photo error:",
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
# رسم رأس PDF
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


# =========================================================
# حقل PDF
# =========================================================

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


# =========================================================
# اسم ملف آمن
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

    if not is_admin_logged(
        update.effective_user.id
    ):

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

        "📋 التقرير اليومي\n\n"

        f"📅 التاريخ: {target_date}\n"
        f"👥 إجمالي النزلاء: {total}\n\n"

        "🏠 حسب المحافظة:\n"
    )

    for name, count in governorates.most_common():

        text += f"• {name}: {count}\n"

    text += "\n🏨 حسب الفندق:\n"

    for name, count in hotels.most_common():

        text += f"• {name}: {count}\n"

    text += "\n🎯 حسب سبب الإقامة:\n"

    for name, count in reasons.most_common():

        text += f"• {name}: {count}\n"

    await update.message.reply_text(
        text
    )


# =========================================================
# تقرير أمس
# =========================================================

async def yesterday_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    yesterday = (
        date.today()
        - timedelta(days=1)
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

        f"📅 تقرير أمس: {yesterday}\n\n"
        f"👥 عدد النزلاء: {len(rows)}"
    )


# =========================================================
# التقرير الشهري
# =========================================================

async def monthly_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(
        update.effective_user.id
    ):

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

    text = (

        "📊 التقرير الشهري\n\n"

        f"📅 الشهر: {current_month}\n"
        f"👥 إجمالي النزلاء: {len(rows)}\n\n"

        "🏠 المحافظات:\n"
    )

    for name, count in governorates.most_common():

        text += f"• {name}: {count}\n"

    text += "\n🏨 الفنادق:\n"

    for name, count in hotels.most_common():

        text += f"• {name}: {count}\n"

    await update.message.reply_text(
        text
    )


# =========================================================
# إلغاء المحادثة
# =========================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    clear_guest_data(
        context
    )

    for key in [
        "login_username",
        "admin_username",
        "new_hotel_username",
        "new_hotel_password",
    ]:

        context.user_data.pop(
            key,
            None
        )

    await update.message.reply_text(
        "❌ تم إلغاء العملية."
    )

    return ConversationHandler.END


# =========================================================
# خادم Render
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
            b"Hotel Report Bot is running"
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
# إنشاء التطبيق
# =========================================================

if not TOKEN:

    print(
        "ERROR: BOT_TOKEN is not set!"
    )


app = (
    ApplicationBuilder()
    .token(TOKEN)
    .build()
)


# =========================================================
# محادثة تسجيل دخول الفندق
# =========================================================

hotel_login_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "login",
            login_start
        )
    ],

    states={

        LOGIN_USERNAME: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                login_username
            )
        ],

        LOGIN_PASSWORD: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
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
# محادثة المدير
# =========================================================

admin_login_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "admin_login",
            admin_login_start
        )
    ],

    states={

        ADMIN_LOGIN_USERNAME: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                admin_login_username
            )
        ],

        ADMIN_LOGIN_PASSWORD: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                admin_login_password
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
# إضافة الفندق
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
                filters.TEXT & ~filters.COMMAND,
                add_hotel_username
            )
        ],

        ADD_HOTEL_PASSWORD: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                add_hotel_password
            )
        ],

        ADD_HOTEL_NAME: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
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
# نموذج النزيل
# =========================================================

guest_form_handler = ConversationHandler(

    entry_points=[

        CallbackQueryHandler(
            new_guest_start,
            pattern=r"^new_guest$"
        )
    ],

    states={

        GUEST_NAME: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_name
            )
        ],

        GUEST_MOTHER: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_mother
            )
        ],

        GUEST_BIRTH: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_birth
            )
        ],

        GUEST_HOME: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_home
            )
        ],

        GUEST_GOVERNORATE: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_governorate
            )
        ],

        GUEST_ROOM: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_room
            )
        ],

        GUEST_SUITE: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_suite
            )
        ],

        GUEST_CHECKIN: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_checkin
            )
        ],

        GUEST_DURATION: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_duration
            )
        ],

        GUEST_REASON: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
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
            )
        ],
    },

    fallbacks=[
        CommandHandler(
            "cancel",
            cancel
        ),

        CallbackQueryHandler(
            cancel_guest_callback,
            pattern=r"^cancel_guest$"
        ),
    ],

    allow_reentry=True
)


# =========================================================
# الأوامر العامة
# =========================================================

app.add_handler(
    CommandHandler(
        "start",
        start
    )
)

app.add_handler(
    hotel_login_handler
)

app.add_handler(
    admin_login_handler
)

app.add_handler(
    add_hotel_handler
)

app.add_handler(
    guest_form_handler
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
        "delete_hotel",
        delete_hotel_start
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
# أزرار الإدارة
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        delete_hotel_callback,
        pattern=r"^delete_hotel:"
    )
)


# =========================================================
# أزرار الصور والإرسال
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        skip_photo_3_callback,
        pattern=r"^skip_photo_3$"
    )
)

app.add_handler(
    CallbackQueryHandler(
        add_photo_3_callback,
        pattern=r"^add_photo_3$"
    )
)

app.add_handler(
    CallbackQueryHandler(
        submit_guest_callback,
        pattern=r"^submit_guest$"
    )
)

app.add_handler(
    CallbackQueryHandler(
        cancel_guest_callback,
        pattern=r"^cancel_guest$"
    )
)


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
# تشغيل البرنامج
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
