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
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

import arabic_reshaper
from bidi.algorithm import get_display


# =========================================================
# الإعدادات
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_TELEGRAM_ID = os.getenv(
    "ADMIN_TELEGRAM_ID",
    ""
).strip()

DATABASE_FILE = "hotel_reports.db"
IMAGE_FILE = "images.png"

PAGE_WIDTH, PAGE_HEIGHT = A4


# =========================================================
# الفنادق
# =========================================================

HOTELS = [
    "قرطبة",
    "النيل",
    "سرمدا",
    "باب الهوى",
    "الحميدية",
    "فور ستار",
    "دريم لاند",
    "برج التجارة",
    "مساكن سوريا",
]


# =========================================================
# المحافظات والدول
# =========================================================

GOVERNORATES = [
    "إدلب",
    "حلب",
    "دمشق",
    "ريف دمشق",
    "حمص",
    "حماة",
    "اللاذقية",
    "طرطوس",
    "درعا",
    "السويداء",
    "القنيطرة",
    "دير الزور",
    "الرقة",
    "الحسكة",
    "تركيا",
    "السعودية",
    "الصين",
    "الهند",
    "أخرى",
]


# =========================================================
# حالات تسجيل الدخول
# =========================================================

LOGIN_USERNAME = 1
LOGIN_PASSWORD = 2


# =========================================================
# حالات إنشاء حساب الفندق
# =========================================================

ADD_HOTEL_SELECT = 10
ADD_HOTEL_USERNAME = 11
ADD_HOTEL_PASSWORD = 12


# =========================================================
# حالات النزيل
# =========================================================

GUEST_NAME = 20
GUEST_MOTHER = 21
GUEST_BIRTH = 22
GUEST_HOME = 23
GUEST_GOVERNORATE = 24
GUEST_HOTEL = 25
GUEST_SUITE = 26
GUEST_ROOM = 27
GUEST_CHECKIN = 28
GUEST_DURATION = 29
GUEST_REASON = 30
GUEST_ID_FRONT = 31
GUEST_ID_BACK = 32


# =========================================================
# الخط العربي
# =========================================================

def find_arabic_font():

    fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    for path in fonts:
        if os.path.exists(path):
            return path

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
    except Exception:
        PDF_FONT = "Helvetica"
else:
    PDF_FONT = "Helvetica"


def arabic_text(text):

    if text is None:
        return ""

    text = str(text)

    try:
        return get_display(
            arabic_reshaper.reshape(text)
        )
    except Exception:
        return text


# =========================================================
# قاعدة البيانات
# =========================================================

def get_db():

    db = sqlite3.connect(
        DATABASE_FILE,
        check_same_thread=False,
        timeout=30
    )

    db.row_factory = sqlite3.Row

    return db


def init_database():

    db = get_db()
    cur = db.cursor()

    cur.execute("""
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
    """)

    cur.execute("""
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
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            telegram_user_id TEXT PRIMARY KEY,
            hotel_account_id INTEGER,
            login_time TEXT,
            active INTEGER DEFAULT 1
        )
    """)

    db.commit()
    db.close()

    print("Database initialized.")


# =========================================================
# كلمات المرور
# =========================================================

def hash_password(password, salt=None):

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
# إنشاء حساب الفندق
# =========================================================

def create_hotel_account(
    hotel_name,
    username,
    password
):

    hotel_name = hotel_name.strip()
    username = username.strip().lower()

    if hotel_name not in HOTELS:
        return None, "الفندق غير موجود ضمن القائمة."

    if not re.fullmatch(
        r"[a-zA-Z0-9_.-]{3,50}",
        username
    ):
        return None, (
            "اسم المستخدم غير صالح.\n"
            "استخدم أحرف إنجليزية وأرقام فقط."
        )

    if len(password) < 8:
        return None, (
            "كلمة المرور يجب أن تكون 8 أحرف على الأقل."
        )

    db = get_db()
    cur = db.cursor()

    # -----------------------------------------------------
    # التحقق من أن الفندق لا يملك حساباً سابقاً
    # -----------------------------------------------------

    cur.execute(
        """
        SELECT id
        FROM hotel_accounts
        WHERE hotel_name = ?
        """,
        (hotel_name,)
    )

    existing_hotel = cur.fetchone()

    if existing_hotel:
        db.close()

        return None, (
            f"❌ الفندق {hotel_name} لديه حساب مسبقاً."
        )

    # -----------------------------------------------------
    # التحقق من اسم المستخدم
    # -----------------------------------------------------

    cur.execute(
        """
        SELECT id
        FROM hotel_accounts
        WHERE username = ?
        """,
        (username,)
    )

    existing_username = cur.fetchone()

    if existing_username:
        db.close()

        return None, (
            "❌ اسم المستخدم مستخدم مسبقاً."
        )

    password_hash, salt = hash_password(password)

    try:

        cur.execute(
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
                1,
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )
        )

        db.commit()

        hotel_id = cur.lastrowid

        db.close()

        return hotel_id, None

    except sqlite3.IntegrityError as e:

        db.rollback()
        db.close()

        print(
            "Create hotel DB error:",
            e
        )

        return None, (
            "❌ تعذر إنشاء الحساب بسبب وجود بيانات مكررة."
        )

    except Exception as e:

        db.rollback()
        db.close()

        print(
            "Create hotel error:",
            e
        )

        return None, (
            "❌ حدث خطأ أثناء إنشاء الحساب."
        )


# =========================================================
# تسجيل الدخول
# =========================================================

def authenticate_hotel(
    username,
    password
):

    username = username.strip().lower()

    db = get_db()
    cur = db.cursor()

    cur.execute(
        """
        SELECT *
        FROM hotel_accounts
        WHERE username = ?
        """,
        (username,)
    )

    account = cur.fetchone()

    db.close()

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

    db = get_db()
    cur = db.cursor()

    cur.execute(
        """
        SELECT *
        FROM hotel_accounts
        ORDER BY id ASC
        """
    )

    rows = cur.fetchall()

    db.close()

    return rows


def disable_hotel(hotel_id):

    db = get_db()
    cur = db.cursor()

    cur.execute(
        """
        UPDATE hotel_accounts
        SET active = 0
        WHERE id = ?
        """,
        (hotel_id,)
    )

    cur.execute(
        """
        DELETE FROM sessions
        WHERE hotel_account_id = ?
        """,
        (hotel_id,)
    )

    db.commit()
    db.close()


def enable_hotel(hotel_id):

    db = get_db()
    cur = db.cursor()

    cur.execute(
        """
        UPDATE hotel_accounts
        SET active = 1
        WHERE id = ?
        """,
        (hotel_id,)
    )

    db.commit()
    db.close()


# =========================================================
# الجلسات
# =========================================================

def create_session(
    telegram_user_id,
    hotel_account_id
):

    db = get_db()
    cur = db.cursor()

    cur.execute(
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

    cur.execute(
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

    db.commit()
    db.close()


def logout_session(telegram_user_id):

    db = get_db()
    cur = db.cursor()

    cur.execute(
        """
        DELETE FROM sessions
        WHERE telegram_user_id = ?
        """,
        (str(telegram_user_id),)
    )

    db.commit()
    db.close()


def get_logged_hotel(telegram_user_id):

    db = get_db()
    cur = db.cursor()

    cur.execute(
        """
        SELECT h.*
        FROM sessions s
        JOIN hotel_accounts h
        ON h.id = s.hotel_account_id
        WHERE s.telegram_user_id = ?
        AND s.active = 1
        AND h.active = 1
        """,
        (str(telegram_user_id),)
    )

    account = cur.fetchone()

    db.close()

    return account


# =========================================================
# المدير
# =========================================================

def is_admin(update):

    if not update.effective_user:
        return False

    if not ADMIN_TELEGRAM_ID:
        return False

    return (
        str(update.effective_user.id)
        == str(ADMIN_TELEGRAM_ID)
    )


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

    db = get_db()
    cur = db.cursor()

    cur.execute(
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
            guest.get("الاسم الثلاثي", ""),
            guest.get("اسم الأم", ""),
            guest.get("مكان وتاريخ الولادة", ""),
            guest.get("السكن الأصلي", ""),
            guest.get("المحافظة", ""),
            guest.get("اسم الفندق", ""),
            guest.get("رقم الجناح", ""),
            guest.get("رقم الغرفة", ""),
            guest.get("تاريخ النزول", ""),
            guest.get("مدة الإقامة", ""),
            guest.get("سبب الإقامة", ""),
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            user_id,
            username,
            hotel_account_id
        )
    )

    db.commit()

    guest_id = cur.lastrowid

    db.close()

    return guest_id


# =========================================================
# التقارير
# =========================================================

def get_guests_by_date(target_date):

    db = get_db()
    cur = db.cursor()

    cur.execute(
        """
        SELECT *
        FROM guests
        WHERE record_date = ?
        ORDER BY id ASC
        """,
        (target_date,)
    )

    rows = cur.fetchall()

    db.close()

    return rows


def get_guests_by_month(month):

    db = get_db()
    cur = db.cursor()

    cur.execute(
        """
        SELECT *
        FROM guests
        WHERE substr(record_date, 1, 7) = ?
        ORDER BY id ASC
        """,
        (month,)
    )

    rows = cur.fetchall()

    db.close()

    return rows


# =========================================================
# القوائم
# =========================================================

def hotel_keyboard(prefix):

    keyboard = []
    row = []

    for index, hotel in enumerate(HOTELS):

        row.append(
            InlineKeyboardButton(
                f"🏨 {hotel}",
                callback_data=f"{prefix}:{index}"
            )
        )

        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)


def governorate_keyboard():

    keyboard = []
    row = []

    for index, item in enumerate(GOVERNORATES):

        row.append(
            InlineKeyboardButton(
                f"📍 {item}",
                callback_data=f"governorate:{index}"
            )
        )

        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# أوامر البوت
# =========================================================

async def set_admin_commands(
    application,
    chat_id
):

    commands = [
        BotCommand("start", "🏠 الرئيسية"),
        BotCommand("add_hotel", "🏨 إضافة فندق"),
        BotCommand("hotels", "📋 قائمة الفنادق"),
        BotCommand("delete_hotel", "🗑️ تعطيل فندق"),
        BotCommand("daily", "📊 التقرير اليومي"),
        BotCommand("yesterday", "📅 تقرير أمس"),
        BotCommand("monthly", "📈 التقرير الشهري"),
        BotCommand("logout", "🚪 تسجيل الخروج"),
    ]

    try:
        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(chat_id)
        )
    except Exception as e:
        print("Admin commands:", e)


async def set_hotel_commands(
    application,
    chat_id
):

    commands = [
        BotCommand("start", "🏠 الرئيسية"),
        BotCommand("login", "🔐 تسجيل الدخول"),
    ]

    try:
        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(chat_id)
        )
    except Exception as e:
        print("Hotel commands:", e)


async def set_logged_hotel_commands(
    application,
    chat_id
):

    commands = [
        BotCommand("start", "🏠 الرئيسية"),
        BotCommand("new_guest", "👤 تسجيل نزيل"),
        BotCommand("logout", "🚪 تسجيل الخروج"),
    ]

    try:
        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(chat_id)
        )
    except Exception as e:
        print("Logged commands:", e)


# =========================================================
# الترحيب
# =========================================================

async def send_welcome_image(update):

    if not update.message:
        return

    if not os.path.exists(IMAGE_FILE):
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
        print("Image:", e)


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if is_admin(update):

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(update)

        await update.message.reply_text(
            "بسم الله الرحمن الرحيم 🌿\n\n"
            "السلام عليكم ورحمة الله وبركاته\n\n"
            "👨‍💼 أهلاً بك في حساب الإدارة.\n\n"
            "🏨 يمكنك إدارة حسابات الفنادق "
            "ومتابعة التقارير."
        )

        return

    hotel = await asyncio.to_thread(
        get_logged_hotel,
        update.effective_user.id
    )

    if hotel:

        await set_logged_hotel_commands(
            context.application,
            update.effective_chat.id
        )

        await update.message.reply_text(
            "🏨 أهلاً بكم\n\n"
            f"الفندق: {hotel['hotel_name']}\n\n"
            "✅ تم تسجيل الدخول.\n\n"
            "/new_guest"
        )

        return

    await set_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await send_welcome_image(update)

    await update.message.reply_text(
        "🏨 أهلاً وسهلاً بكم في نظام معلومات الفنادق.\n\n"
        "🔐 للبدء:\n"
        "/login"
    )


# =========================================================
# تسجيل الدخول
# =========================================================

async def login_start(
    update,
    context
):

    if is_admin(update):

        await update.message.reply_text(
            "👨‍💼 أنت المدير."
        )

        return ConversationHandler.END

    hotel = await asyncio.to_thread(
        get_logged_hotel,
        update.effective_user.id
    )

    if hotel:

        await update.message.reply_text(
            f"✅ أنت مسجل الدخول بالفعل.\n"
            f"🏨 {hotel['hotel_name']}"
        )

        return ConversationHandler.END

    context.user_data.pop(
        "login_username",
        None
    )

    await update.message.reply_text(
        "🔐 تسجيل دخول الفندق\n\n"
        "👤 أرسل اسم المستخدم:"
    )

    return LOGIN_USERNAME


async def login_username(
    update,
    context
):

    username = update.message.text.strip()

    context.user_data[
        "login_username"
    ] = username

    await update.message.reply_text(
        "🔑 أرسل كلمة المرور:"
    )

    return LOGIN_PASSWORD


async def login_password(
    update,
    context
):

    password = update.message.text.strip()

    username = context.user_data.get(
        "login_username"
    )

    if not username:

        await update.message.reply_text(
            "❌ انتهت العملية.\n"
            "استخدم /login من جديد."
        )

        return ConversationHandler.END

    account = await asyncio.to_thread(
        authenticate_hotel,
        username,
        password
    )

    if not account:

        context.user_data.pop(
            "login_username",
            None
        )

        await update.message.reply_text(
            "❌ اسم المستخدم أو كلمة المرور غير صحيحة."
        )

        return ConversationHandler.END

    current_id = str(
        update.effective_user.id
    )

    old_id = account["telegram_user_id"]

    if old_id and old_id != current_id:

        await update.message.reply_text(
            "⚠️ هذا الحساب مرتبط بحساب Telegram آخر."
        )

        return ConversationHandler.END

    await asyncio.to_thread(
        create_session,
        current_id,
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
        "✅ تم تسجيل الدخول بنجاح.\n\n"
        f"🏨 الفندق: {account['hotel_name']}\n\n"
        "/new_guest"
    )

    return ConversationHandler.END


# =========================================================
# تسجيل الخروج
# =========================================================

async def logout(
    update,
    context
):

    if is_admin(update):

        await update.message.reply_text(
            "👨‍💼 حساب المدير."
        )

        return

    await asyncio.to_thread(
        logout_session,
        update.effective_user.id
    )

    context.user_data.clear()

    await set_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await update.message.reply_text(
        "🚪 تم تسجيل الخروج."
    )


# =========================================================
# إضافة حساب فندق - البداية
# =========================================================

async def add_hotel_start(
    update,
    context
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return ConversationHandler.END

    context.user_data.pop(
        "new_hotel_name",
        None
    )

    context.user_data.pop(
        "new_hotel_username",
        None
    )

    context.user_data.pop(
        "new_hotel_password",
        None
    )

    await update.message.reply_text(
        "🏨 إنشاء حساب فندق جديد\n\n"
        "اختر الفندق:",
        reply_markup=hotel_keyboard(
            "create_hotel"
        )
    )

    return ADD_HOTEL_SELECT


# =========================================================
# إضافة حساب الفندق - اختيار الفندق
# =========================================================

async def add_hotel_select(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not is_admin(update):

        await query.edit_message_text(
            "⛔ غير مصرح لك."
        )

        return ConversationHandler.END

    try:

        index = int(
            query.data.split(":")[1]
        )

        hotel_name = HOTELS[index]

    except Exception:

        await query.edit_message_text(
            "❌ اختيار الفندق غير صالح."
        )

        return ADD_HOTEL_SELECT

    # -----------------------------------------------------
    # فحص إذا كان الفندق لديه حساب
    # -----------------------------------------------------

    db = get_db()
    cur = db.cursor()

    cur.execute(
        """
        SELECT id, username, active
        FROM hotel_accounts
        WHERE hotel_name = ?
        """,
        (hotel_name,)
    )

    existing = cur.fetchone()

    db.close()

    if existing:

        if existing["active"]:

            await query.edit_message_text(
                "⚠️ هذا الفندق لديه حساب مسبقاً.\n\n"
                f"🏨 الفندق: {hotel_name}\n"
                f"👤 اسم المستخدم: {existing['username']}\n\n"
                "لا يمكن إنشاء حساب ثانٍ لنفس الفندق."
            )

        else:

            await query.edit_message_text(
                "⚠️ هذا الفندق لديه حساب موقوف مسبقاً.\n\n"
                f"🏨 {hotel_name}\n"
                f"👤 المستخدم: {existing['username']}\n\n"
                "يمكنك تفعيله من /hotels."
            )

        return ConversationHandler.END

    context.user_data[
        "new_hotel_name"
    ] = hotel_name

    await query.edit_message_text(
        "✅ تم اختيار الفندق\n\n"
        f"🏨 الفندق: {hotel_name}\n\n"
        "👤 الآن أرسل اسم المستخدم.\n\n"
        "مثال:\n"
        "hotel_qurtuba"
    )

    return ADD_HOTEL_USERNAME


# =========================================================
# إضافة حساب الفندق - اسم المستخدم
# =========================================================

async def add_hotel_username(
    update,
    context
):

    if not is_admin(update):

        return ConversationHandler.END

    username = update.message.text.strip().lower()

    if not re.fullmatch(
        r"[a-zA-Z0-9_.-]{3,50}",
        username
    ):

        await update.message.reply_text(
            "❌ اسم المستخدم غير صالح.\n\n"
            "استخدم الأحرف الإنجليزية والأرقام فقط.\n"
            "الطول من 3 إلى 50."
        )

        return ADD_HOTEL_USERNAME

    # فحص اسم المستخدم فوراً

    db = get_db()
    cur = db.cursor()

    cur.execute(
        """
        SELECT id
        FROM hotel_accounts
        WHERE username = ?
        """,
        (username,)
    )

    exists = cur.fetchone()

    db.close()

    if exists:

        await update.message.reply_text(
            "❌ اسم المستخدم مستخدم مسبقاً.\n\n"
            "أرسل اسم مستخدم آخر:"
        )

        return ADD_HOTEL_USERNAME

    context.user_data[
        "new_hotel_username"
    ] = username

    await update.message.reply_text(
        "✅ تم قبول اسم المستخدم.\n\n"
        "🔑 الآن أرسل كلمة المرور.\n\n"
        "يجب أن تكون 8 أحرف على الأقل."
    )

    return ADD_HOTEL_PASSWORD


# =========================================================
# إضافة حساب الفندق - كلمة المرور
# =========================================================

async def add_hotel_password(
    update,
    context
):

    if not is_admin(update):

        return ConversationHandler.END

    password = update.message.text.strip()

    hotel_name = context.user_data.get(
        "new_hotel_name"
    )

    username = context.user_data.get(
        "new_hotel_username"
    )

    if not hotel_name or not username:

        await update.message.reply_text(
            "❌ انتهت بيانات العملية.\n\n"
            "استخدم /add_hotel من جديد."
        )

        return ConversationHandler.END

    if len(password) < 8:

        await update.message.reply_text(
            "❌ كلمة المرور قصيرة.\n\n"
            "يجب أن تكون 8 أحرف على الأقل.\n"
            "أرسل كلمة مرور جديدة:"
        )

        return ADD_HOTEL_PASSWORD

    # -----------------------------------------------------
    # رسالة فورية للمدير
    # -----------------------------------------------------

    progress_message = await update.message.reply_text(
        "⏳ جارٍ إنشاء حساب الفندق..."
    )

    # -----------------------------------------------------
    # إنشاء الحساب خارج Event Loop
    # -----------------------------------------------------

    hotel_id, error = await asyncio.to_thread(
        create_hotel_account,
        hotel_name,
        username,
        password
    )

    # -----------------------------------------------------
    # نجاح
    # -----------------------------------------------------

    if hotel_id:

        context.user_data.pop(
            "new_hotel_name",
            None
        )

        context.user_data.pop(
            "new_hotel_username",
            None
        )

        context.user_data.pop(
            "new_hotel_password",
            None
        )

        await progress_message.edit_text(
            "✅ تم إنشاء حساب الفندق بنجاح!\n\n"
            "━━━━━━━━━━━━━━\n"
            f"🏨 الفندق: {hotel_name}\n"
            f"👤 اسم المستخدم: {username}\n"
            f"🔑 كلمة المرور: {password}\n"
            f"🆔 رقم الحساب: {hotel_id}\n"
            "━━━━━━━━━━━━━━\n\n"
            "📱 يمكن للفندق الآن استخدام:\n"
            "/login"
        )

        return ConversationHandler.END

    # -----------------------------------------------------
    # فشل
    # -----------------------------------------------------

    await progress_message.edit_text(
        "❌ لم يتم إنشاء حساب الفندق.\n\n"
        f"{error or 'حدث خطأ غير معروف.'}\n\n"
        "يمكنك المحاولة من جديد عبر:\n"
        "/add_hotel"
    )

    context.user_data.pop(
        "new_hotel_name",
        None
    )

    context.user_data.pop(
        "new_hotel_username",
        None
    )

    return ConversationHandler.END


# =========================================================
# إلغاء إنشاء الفندق
# =========================================================

async def cancel(
    update,
    context
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ تم إلغاء العملية."
    )

    return ConversationHandler.END


# =========================================================
# قائمة الفنادق
# =========================================================

async def hotels_list(
    update,
    context
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return

    hotels = await asyncio.to_thread(
        get_all_hotels
    )

    if not hotels:

        await update.message.reply_text(
            "📋 لا توجد حسابات فنادق."
        )

        return

    text = "🏨 حسابات الفنادق\n\n"
    keyboard = []

    for hotel in hotels:

        status = (
            "🟢 فعال"
            if hotel["active"]
            else "🔴 موقوف"
        )

        connected = (
            "🔗 مرتبط"
            if hotel["telegram_user_id"]
            else "⚪ غير مرتبط"
        )

        text += (
            f"🆔 {hotel['id']}\n"
            f"🏨 {hotel['hotel_name']}\n"
            f"👤 {hotel['username']}\n"
            f"📌 {status}\n"
            f"📱 {connected}\n\n"
        )

        if hotel["active"]:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🗑️ تعطيل {hotel['hotel_name']}",
                        callback_data=f"disable:{hotel['id']}"
                    )
                ]
            )

        else:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"♻️ تفعيل {hotel['hotel_name']}",
                        callback_data=f"enable:{hotel['id']}"
                    )
                ]
            )

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# تعطيل الفندق
# =========================================================

async def delete_hotel_command(
    update,
    context
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return

    hotels = await asyncio.to_thread(
        get_all_hotels
    )

    keyboard = []

    for hotel in hotels:

        if hotel["active"]:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🗑️ {hotel['hotel_name']}",
                        callback_data=f"disable:{hotel['id']}"
                    )
                ]
            )

    if not keyboard:

        await update.message.reply_text(
            "📋 لا توجد فنادق فعالة."
        )

        return

    await update.message.reply_text(
        "🗑️ اختر الفندق:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# Callback الإدارة
# =========================================================

async def admin_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not is_admin(update):

        await query.edit_message_text(
            "⛔ غير مصرح لك."
        )

        return

    data = query.data

    # تعطيل
    if data.startswith("disable:"):

        hotel_id = int(
            data.split(":")[1]
        )

        await query.edit_message_text(
            "⏳ جارٍ تعطيل الفندق..."
        )

        await asyncio.to_thread(
            disable_hotel,
            hotel_id
        )

        await query.edit_message_text(
            "🗑️ تم تعطيل الفندق بنجاح."
        )

        return

    # تفعيل
    if data.startswith("enable:"):

        hotel_id = int(
            data.split(":")[1]
        )

        await asyncio.to_thread(
            enable_hotel,
            hotel_id
        )

        await query.edit_message_text(
            "♻️ تم تفعيل الفندق بنجاح."
        )

        return


# =========================================================
# نموذج النزيل
# =========================================================

async def new_guest_start(
    update,
    context
):

    if is_admin(update):

        await update.message.reply_text(
            "👨‍💼 هذا النموذج مخصص لحسابات الفنادق."
        )

        return ConversationHandler.END

    hotel = await asyncio.to_thread(
        get_logged_hotel,
        update.effective_user.id
    )

    if not hotel:

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً.\n\n"
            "/login"
        )

        return ConversationHandler.END

    context.user_data["guest_form"] = {}
    context.user_data["guest_images"] = []
    context.user_data["guest_account_hotel"] = hotel["hotel_name"]
    context.user_data["guest_hotel_id"] = hotel["id"]

    await update.message.reply_text(
        "📋 تسجيل نزيل جديد\n\n"
        "1️⃣ الاسم الثلاثي:"
    )

    return GUEST_NAME


async def guest_name(update, context):

    text = update.message.text.strip()

    if not text:
        await update.message.reply_text(
            "❌ أدخل الاسم."
        )
        return GUEST_NAME

    context.user_data["guest_form"][
        "الاسم الثلاثي"
    ] = text

    await update.message.reply_text(
        "2️⃣ اسم الأم:"
    )

    return GUEST_MOTHER


async def guest_mother(update, context):

    context.user_data["guest_form"][
        "اسم الأم"
    ] = update.message.text.strip()

    await update.message.reply_text(
        "3️⃣ مكان وتاريخ الولادة:"
    )

    return GUEST_BIRTH


async def guest_birth(update, context):

    context.user_data["guest_form"][
        "مكان وتاريخ الولادة"
    ] = update.message.text.strip()

    await update.message.reply_text(
        "4️⃣ السكن الأصلي:"
    )

    return GUEST_HOME


async def guest_home(update, context):

    context.user_data["guest_form"][
        "السكن الأصلي"
    ] = update.message.text.strip()

    await update.message.reply_text(
        "5️⃣ اختر المحافظة / الدولة:",
        reply_markup=governorate_keyboard()
    )

    return GUEST_GOVERNORATE


async def guest_governorate(
    update,
    context
):

    query = update.callback_query
    await query.answer()

    try:
        index = int(
            query.data.split(":")[1]
        )
        governorate = GOVERNORATES[index]
    except Exception:
        return GUEST_GOVERNORATE

    context.user_data["guest_form"][
        "المحافظة"
    ] = governorate

    await query.edit_message_text(
        f"✅ المحافظة: {governorate}\n\n"
        "6️⃣ اختر الفندق:",
        reply_markup=hotel_keyboard(
            "guest_hotel"
        )
    )

    return GUEST_HOTEL


async def guest_hotel(
    update,
    context
):

    query = update.callback_query
    await query.answer()

    try:
        index = int(
            query.data.split(":")[1]
        )
        hotel_name = HOTELS[index]
    except Exception:
        return GUEST_HOTEL

    account_hotel = context.user_data.get(
        "guest_account_hotel"
    )

    if hotel_name != account_hotel:

        await query.answer(
            "لا يمكنك اختيار فندق آخر.",
            show_alert=True
        )

        return GUEST_HOTEL

    context.user_data["guest_form"][
        "اسم الفندق"
    ] = hotel_name

    await query.edit_message_text(
        f"✅ الفندق: {hotel_name}\n\n"
        "7️⃣ رقم الجناح:\n"
        "إذا لم يوجد اكتب: لا يوجد"
    )

    return GUEST_SUITE


async def guest_suite(update, context):

    context.user_data["guest_form"][
        "رقم الجناح"
    ] = update.message.text.strip()

    await update.message.reply_text(
        "8️⃣ رقم الغرفة:"
    )

    return GUEST_ROOM


async def guest_room(update, context):

    context.user_data["guest_form"][
        "رقم الغرفة"
    ] = update.message.text.strip()

    await update.message.reply_text(
        "9️⃣ تاريخ النزول:"
    )

    return GUEST_CHECKIN


async def guest_checkin(update, context):

    context.user_data["guest_form"][
        "تاريخ النزول"
    ] = update.message.text.strip()

    await update.message.reply_text(
        "🔟 مدة الإقامة:"
    )

    return GUEST_DURATION


async def guest_duration(update, context):

    context.user_data["guest_form"][
        "مدة الإقامة"
    ] = update.message.text.strip()

    await update.message.reply_text(
        "1️⃣1️⃣ سبب الإقامة:"
    )

    return GUEST_REASON


async def guest_reason(update, context):

    context.user_data["guest_form"][
        "سبب الإقامة"
    ] = update.message.text.strip()

    await update.message.reply_text(
        "📷 أرسل صورة الوجه الأمامي للبطاقة الشخصية:"
    )

    return GUEST_ID_FRONT


# =========================================================
# صور الهوية
# =========================================================

async def guest_id_front(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ يجب إرسال صورة."
        )

        return GUEST_ID_FRONT

    try:

        photo = update.message.photo[-1]
        telegram_file = await photo.get_file()

        image_buffer = BytesIO()

        await telegram_file.download_to_memory(
            image_buffer
        )

        image_buffer.seek(0)

        context.user_data[
            "guest_images"
        ].append(image_buffer)

        await update.message.reply_text(
            "✅ تم استلام الوجه الأمامي.\n\n"
            "📷 الآن أرسل الوجه الخلفي:"
        )

        return GUEST_ID_BACK

    except Exception as e:

        print("Front image:", e)

        await update.message.reply_text(
            "❌ حدث خطأ.\n"
            "أعد إرسال الصورة."
        )

        return GUEST_ID_FRONT


async def guest_id_back(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ يجب إرسال صورة."
        )

        return GUEST_ID_BACK

    try:

        photo = update.message.photo[-1]
        telegram_file = await photo.get_file()

        image_buffer = BytesIO()

        await telegram_file.download_to_memory(
            image_buffer
        )

        image_buffer.seek(0)

        context.user_data[
            "guest_images"
        ].append(image_buffer)

        await update.message.reply_text(
            "⏳ يتم تجهيز التقرير..."
        )

        return await finish_guest(
            update,
            context
        )

    except Exception as e:

        print("Back image:", e)

        await update.message.reply_text(
            "❌ حدث خطأ."
        )

        return GUEST_ID_BACK


# =========================================================
# PDF النزيل
# =========================================================

def safe_filename(name):

    name = re.sub(
        r'[\\/:*?"<>|]',
        "",
        str(name)
    )

    name = re.sub(
        r"\s+",
        "_",
        name.strip()
    )

    return (name or "تقرير_نزيل") + ".pdf"


def create_guest_pdf(
    guest,
    images
):

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    hotel = guest.get(
        "اسم الفندق",
        "الفندق"
    )

    pdf.setFillColor(
        colors.HexColor("#17365D")
    )

    pdf.roundRect(
        35,
        PAGE_HEIGHT - 105,
        PAGE_WIDTH - 70,
        70,
        8,
        fill=1,
        stroke=0
    )

    pdf.setFillColor(colors.white)

    pdf.setFont(
        PDF_FONT,
        18
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 63,
        arabic_text(hotel)
    )

    pdf.setFont(
        PDF_FONT,
        11
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 84,
        arabic_text(
            "تقرير بيانات نزيل"
        )
    )

    y = PAGE_HEIGHT - 135

    fields = [
        ("الاسم الثلاثي", guest.get("الاسم الثلاثي", "")),
        ("اسم الأم", guest.get("اسم الأم", "")),
        ("مكان وتاريخ الولادة", guest.get("مكان وتاريخ الولادة", "")),
        ("السكن الأصلي", guest.get("السكن الأصلي", "")),
        ("المحافظة", guest.get("المحافظة", "")),
        ("اسم الفندق", guest.get("اسم الفندق", "")),
        ("رقم الجناح", guest.get("رقم الجناح", "")),
        ("رقم الغرفة", guest.get("رقم الغرفة", "")),
        ("تاريخ النزول", guest.get("تاريخ النزول", "")),
        ("مدة الإقامة", guest.get("مدة الإقامة", "")),
        ("سبب الإقامة", guest.get("سبب الإقامة", "")),
    ]

    pdf.setFillColor(colors.black)
    pdf.setFont(PDF_FONT, 10)

    for label, value in fields:

        if y < 70:

            pdf.showPage()

            y = PAGE_HEIGHT - 70

        pdf.setFillColor(
            colors.HexColor("#F3F6F8")
        )

        pdf.roundRect(
            45,
            y - 25,
            PAGE_WIDTH - 90,
            30,
            4,
            fill=1,
            stroke=0
        )

        pdf.setFillColor(colors.black)

        pdf.drawRightString(
            PAGE_WIDTH - 60,
            y - 16,
            arabic_text(
                f"{label}: {value}"
            )
        )

        y -= 38

    # -----------------------------------------------------
    # صور البطاقة
    # -----------------------------------------------------

    for index, image_data in enumerate(images):

        pdf.showPage()

        title = (
            "الوجه الأمامي للبطاقة"
            if index == 0
            else "الوجه الخلفي للبطاقة"
        )

        pdf.setFont(
            PDF_FONT,
            15
        )

        pdf.setFillColor(
            colors.HexColor("#17365D")
        )

        pdf.drawCentredString(
            PAGE_WIDTH / 2,
            PAGE_HEIGHT - 60,
            arabic_text(title)
        )

        try:

            image_data.seek(0)

            image = ImageReader(
                image_data
            )

            iw, ih = image.getSize()

            max_w = PAGE_WIDTH - 80
            max_h = PAGE_HEIGHT - 150

            scale = min(
                max_w / iw,
                max_h / ih
            )

            width = iw * scale
            height = ih * scale

            pdf.drawImage(
                image,
                (PAGE_WIDTH - width) / 2,
                (PAGE_HEIGHT - height) / 2,
                width=width,
                height=height,
                preserveAspectRatio=True,
                mask="auto"
            )

        except Exception as e:

            print(
                "PDF image:",
                e
            )

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# مراجعة النزيل
# =========================================================

async def finish_guest(
    update,
    context
):

    form = context.user_data.get(
        "guest_form",
        {}
    )

    images = context.user_data.get(
        "guest_images",
        []
    )

    text = (
        "📋 مراجعة بيانات النزيل\n\n"
        f"👤 الاسم: {form.get('الاسم الثلاثي', '')}\n"
        f"👩 الأم: {form.get('اسم الأم', '')}\n"
        f"🎂 الولادة: {form.get('مكان وتاريخ الولادة', '')}\n"
        f"🏠 السكن: {form.get('السكن الأصلي', '')}\n"
        f"📍 المحافظة: {form.get('المحافظة', '')}\n"
        f"🏨 الفندق: {form.get('اسم الفندق', '')}\n"
        f"🏢 الجناح: {form.get('رقم الجناح', '')}\n"
        f"🚪 الغرفة: {form.get('رقم الغرفة', '')}\n"
        f"📅 النزول: {form.get('تاريخ النزول', '')}\n"
        f"⏱️ المدة: {form.get('مدة الإقامة', '')}\n"
        f"🎯 السبب: {form.get('سبب الإقامة', '')}\n\n"
        f"🪪 الصور: {len(images)}/2"
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

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    return ConversationHandler.END


# =========================================================
# إرسال النزيل للإدارة
# =========================================================

async def send_guest(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    hotel = await asyncio.to_thread(
        get_logged_hotel,
        update.effective_user.id
    )

    if not hotel:

        await query.edit_message_text(
            "🔐 انتهت جلسة الدخول."
        )

        return

    form = context.user_data.get(
        "guest_form"
    )

    images = context.user_data.get(
        "guest_images",
        []
    )

    if not form or len(images) != 2:

        await query.edit_message_text(
            "❌ البيانات أو الصور غير مكتملة."
        )

        return

    if form.get(
        "اسم الفندق"
    ) != hotel["hotel_name"]:

        await query.edit_message_text(
            "❌ الفندق لا يطابق الحساب."
        )

        return

    await query.edit_message_text(
        "⏳ جارٍ حفظ البيانات وإنشاء التقرير..."
    )

    guest_id = await asyncio.to_thread(
        save_guest,
        form,
        update,
        hotel["id"]
    )

    pdf_file = await asyncio.to_thread(
        create_guest_pdf,
        form,
        images
    )

    if not ADMIN_TELEGRAM_ID:

        await query.edit_message_text(
            "⚠️ تم حفظ البيانات، لكن لم يتم تحديد "
            "ADMIN_TELEGRAM_ID."
        )

        return

    filename = safe_filename(
        form.get(
            "الاسم الثلاثي",
            "نزيل"
        )
    )

    caption = (
        "📥 تم استلام بيانات نزيل جديد\n\n"
        f"🏨 الفندق: {hotel['hotel_name']}\n"
        f"👤 الاسم: {form.get('الاسم الثلاثي', '')}\n"
        f"📍 المحافظة: {form.get('المحافظة', '')}\n"
        f"🚪 الغرفة: {form.get('رقم الغرفة', '')}\n"
        f"🆔 رقم السجل: {guest_id}\n\n"
        "📎 التقرير الكامل مرفق."
    )

    try:

        pdf_file.seek(0)

        await context.bot.send_document(
            chat_id=int(ADMIN_TELEGRAM_ID),
            document=pdf_file,
            filename=filename,
            caption=caption
        )

    except Exception as e:

        print(
            "Admin send:",
            e
        )

        await query.edit_message_text(
            "⚠️ تم حفظ البيانات، لكن تعذر إرسال "
            "التقرير إلى الإدارة."
        )

        return

    context.user_data.clear()

    await query.edit_message_text(
        "✅ تم إرسال بيانات النزيل إلى الإدارة بنجاح.\n\n"
        "📎 تم إرسال التقرير مع صور البطاقة.\n\n"
        "/new_guest"
    )


# =========================================================
# إلغاء النزيل
# =========================================================

async def cancel_guest(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    context.user_data.clear()

    await query.edit_message_text(
        "❌ تم إلغاء تسجيل النزيل."
    )


# =========================================================
# التقرير PDF
# =========================================================

def create_report_pdf(
    rows,
    report_date,
    title
):

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

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

    pdf.setFillColor(
        colors.HexColor("#17365D")
    )

    pdf.roundRect(
        35,
        PAGE_HEIGHT - 105,
        PAGE_WIDTH - 70,
        70,
        8,
        fill=1,
        stroke=0
    )

    pdf.setFillColor(colors.white)

    pdf.setFont(
        PDF_FONT,
        16
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 63,
        arabic_text(title)
    )

    y = PAGE_HEIGHT - 135

    pdf.setFillColor(colors.black)

    pdf.setFont(
        PDF_FONT,
        12
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            f"التاريخ: {report_date}"
        )
    )

    y -= 35

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            f"إجمالي النزلاء: {total}"
        )
    )

    y -= 40

    sections = [
        ("حسب المحافظة", governorates),
        ("حسب الفندق", hotels),
        ("حسب سبب الإقامة", reasons),
    ]

    for section_title, counter in sections:

        if y < 100:

            pdf.showPage()
            y = PAGE_HEIGHT - 80

        pdf.setFillColor(
            colors.HexColor("#17365D")
        )

        pdf.setFont(
            PDF_FONT,
            13
        )

        pdf.drawRightString(
            PAGE_WIDTH - 50,
            y,
            arabic_text(section_title)
        )

        y -= 28

        pdf.setFillColor(colors.black)

        pdf.setFont(
            PDF_FONT,
            10
        )

        for name, count in counter.most_common():

            if y < 60:

                pdf.showPage()
                y = PAGE_HEIGHT - 80

            pdf.drawRightString(
                PAGE_WIDTH - 70,
                y,
                arabic_text(
                    f"{name}: {count}"
                )
            )

            y -= 22

        y -= 15

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# التقرير اليومي
# =========================================================

async def daily_report(
    update,
    context
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return

    target = date.today().isoformat()

    rows = await asyncio.to_thread(
        get_guests_by_date,
        target
    )

    if not rows:

        await update.message.reply_text(
            "📋 لا توجد بيانات مسجلة اليوم."
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
        "📊 التقرير اليومي\n\n"
        f"📅 {target}\n"
        f"👥 إجمالي النزلاء: {len(rows)}\n\n"
        "📍 المحافظات:\n"
    )

    for name, count in governorates.most_common():
        text += f"• {name}: {count}\n"

    text += "\n🏨 الفنادق:\n"

    for name, count in hotels.most_common():
        text += f"• {name}: {count}\n"

    await update.message.reply_text(text)

    pdf_file = await asyncio.to_thread(
        create_report_pdf,
        rows,
        target,
        "التقرير اليومي لقسم معلومات الفنادق"
    )

    await update.message.reply_document(
        document=pdf_file,
        filename=f"daily_{target}.pdf",
        caption="📎 التقرير اليومي PDF"
    )


# =========================================================
# تقرير أمس
# =========================================================

async def yesterday_report(
    update,
    context
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return

    target = (
        date.today()
        - timedelta(days=1)
    ).isoformat()

    rows = await asyncio.to_thread(
        get_guests_by_date,
        target
    )

    if not rows:

        await update.message.reply_text(
            f"📋 لا توجد بيانات بتاريخ {target}."
        )

        return

    pdf_file = await asyncio.to_thread(
        create_report_pdf,
        rows,
        target,
        "تقرير قسم معلومات الفنادق"
    )

    await update.message.reply_document(
        document=pdf_file,
        filename=f"yesterday_{target}.pdf",
        caption=f"📋 تقرير {target}"
    )


# =========================================================
# التقرير الشهري
# =========================================================

async def monthly_report(
    update,
    context
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return

    month = date.today().strftime(
        "%Y-%m"
    )

    rows = await asyncio.to_thread(
        get_guests_by_month,
        month
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
        "📈 التقرير الشهري\n\n"
        f"📅 الشهر: {month}\n"
        f"👥 إجمالي النزلاء: {len(rows)}\n\n"
        "📍 المحافظات:\n"
    )

    for name, count in governorates.most_common():
        text += f"• {name}: {count}\n"

    text += "\n🏨 الفنادق:\n"

    for name, count in hotels.most_common():
        text += f"• {name}: {count}\n"

    await update.message.reply_text(text)

    pdf_file = await asyncio.to_thread(
        create_report_pdf,
        rows,
        month,
        "التقرير الشهري لقسم معلومات الفنادق"
    )

    await update.message.reply_document(
        document=pdf_file,
        filename=f"monthly_{month}.pdf",
        caption="📎 التقرير الشهري PDF"
    )


# =========================================================
# Conversation تسجيل الدخول
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
# Conversation إنشاء حساب الفندق
#
# الإصلاح الأساسي هنا:
#
# اختيار الفندق أصبح داخل ConversationHandler
# وليس Handler خارج المحادثة.
# =========================================================

add_hotel_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "add_hotel",
            add_hotel_start
        )
    ],

    states={

        ADD_HOTEL_SELECT: [

            CallbackQueryHandler(
                add_hotel_select,
                pattern=r"^create_hotel:"
            )
        ],

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
# Conversation النزيل
# =========================================================

guest_form_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "new_guest",
            new_guest_start
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
            CallbackQueryHandler(
                guest_governorate,
                pattern=r"^governorate:"
            )
        ],

        GUEST_HOTEL: [
            CallbackQueryHandler(
                guest_hotel,
                pattern=r"^guest_hotel:"
            )
        ],

        GUEST_SUITE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_suite
            )
        ],

        GUEST_ROOM: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                guest_room
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

        GUEST_ID_FRONT: [
            MessageHandler(
                filters.PHOTO,
                guest_id_front
            )
        ],

        GUEST_ID_BACK: [
            MessageHandler(
                filters.PHOTO,
                guest_id_back
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
# إنشاء التطبيق
# =========================================================

if not TOKEN:

    print(
        "WARNING: BOT_TOKEN غير موجود."
    )


app = (
    ApplicationBuilder()
    .token(TOKEN)
    .build()
)


# =========================================================
# Handlers
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

# مهم جداً:
# يجب تسجيل Conversation إضافة الفندق
# قبل أي Callback عام.

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
        delete_hotel_command
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
# Callback الإدارة
#
# لاحظ أننا لا نضع create_hotel هنا.
# إنشاء الفندق أصبح داخل ConversationHandler.
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        admin_callback,
        pattern=r"^(disable:|enable:)"
    )
)


# =========================================================
# إرسال النزيل
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        send_guest,
        pattern=r"^send_guest$"
    )
)


# =========================================================
# إلغاء النزيل
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        cancel_guest,
        pattern=r"^cancel_guest$"
    )
)


# =========================================================
# الرسائل غير المعروفة
# =========================================================

async def unknown_message(
    update,
    context
):

    if not update.message:
        return

    if is_admin(update):

        await update.message.reply_text(
            "👨‍💼 أنت في حساب الإدارة.\n\n"
            "استخدم القائمة أو الأوامر."
        )

        return

    hotel = await asyncio.to_thread(
        get_logged_hotel,
        update.effective_user.id
    )

    if hotel:

        await update.message.reply_text(
            "🏨 الفندق:\n"
            f"{hotel['hotel_name']}\n\n"
            "👤 تسجيل نزيل:\n"
            "/new_guest"
        )

        return

    await update.message.reply_text(
        "🔐 يرجى تسجيل الدخول:\n"
        "/login"
    )


app.add_handler(
    MessageHandler(
        filters.ALL & ~filters.COMMAND,
        unknown_message
    )
)


# =========================================================
# خادم Render
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

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
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Web server running on port {port}"
    )

    server.serve_forever()


# =========================================================
# التشغيل
# =========================================================

async def main():

    # قاعدة البيانات
    await asyncio.to_thread(
        init_database
    )

    if not TOKEN:

        print(
            "ERROR: BOT_TOKEN غير موجود."
        )

        return

    if not ADMIN_TELEGRAM_ID:

        print(
            "WARNING: ADMIN_TELEGRAM_ID غير موجود."
        )

    # Render health server
    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    await app.initialize()

    await app.start()

    await app.updater.start_polling(
        drop_pending_updates=True
    )

    print(
        "================================="
    )

    print(
        "Hotel Report Bot Started"
    )

    print(
        "================================="
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
