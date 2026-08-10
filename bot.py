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
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import arabic_reshaper
from bidi.algorithm import get_display


# =========================================================
# الإعدادات
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "").strip()

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
# حالات الدخول
# =========================================================

LOGIN_USERNAME = 1
LOGIN_PASSWORD = 2

ADD_HOTEL_USERNAME = 10
ADD_HOTEL_PASSWORD = 11
ADD_HOTEL_NAME = 12

GUEST_NAME = 20
GUEST_MOTHER = 21
GUEST_BIRTH = 22
GUEST_HOME = 23
GUEST_GOVERNORATE = 24
GUEST_SUITE = 25
GUEST_ROOM = 26
GUEST_CHECKIN = 27
GUEST_DURATION = 28
GUEST_REASON = 29
GUEST_PHOTO1 = 30
GUEST_PHOTO2 = 31
GUEST_PHOTO3 = 32
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

    except Exception as e:

        print("Arabic font error:", e)

        PDF_FONT = "Helvetica"

else:

    PDF_FONT = "Helvetica"


# =========================================================
# معالجة العربية
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
    # النزلاء
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

            photo1 BLOB,
            photo2 BLOB,
            photo3 BLOB,

            record_date TEXT,
            record_time TEXT,

            telegram_user_id TEXT,
            telegram_username TEXT,

            hotel_account_id INTEGER
        )
        """
    )

    # -----------------------------------------
    # حسابات الفنادق
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

            telegram_user_id TEXT,

            created_at TEXT
        )
        """
    )

    # -----------------------------------------
    # جلسات الدخول
    # -----------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (

            telegram_user_id TEXT PRIMARY KEY,

            account_type TEXT NOT NULL,

            hotel_account_id INTEGER,

            login_time TEXT,

            active INTEGER DEFAULT 1
        )
        """
    )

    # -----------------------------------------
    # إضافة أعمدة الصور إن كانت القاعدة قديمة
    # -----------------------------------------

    existing_columns = []

    try:

        cursor.execute(
            "PRAGMA table_info(guests)"
        )

        existing_columns = [
            row["name"]
            for row in cursor.fetchall()
        ]

    except Exception:
        pass

    for column in ["photo1", "photo2", "photo3"]:

        if column not in existing_columns:

            try:

                cursor.execute(
                    f"ALTER TABLE guests ADD COLUMN {column} BLOB"
                )

            except Exception:
                pass

    connection.commit()

    connection.close()

    print("Database initialized successfully.")


# =========================================================
# تشفير كلمة المرور
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
# حسابات الفنادق
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


def get_all_hotels():

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
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

    # تعطيل الحساب ومنع الدخول
    cursor.execute(
        """
        UPDATE hotel_accounts
        SET active = 0
        WHERE id = ?
        """,
        (hotel_id,)
    )

    # حذف جلسات الحساب
    cursor.execute(
        """
        DELETE FROM sessions
        WHERE hotel_account_id = ?
        """,
        (hotel_id,)
    )

    connection.commit()

    connection.close()


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
# الجلسات
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
            account_type,
            hotel_account_id,
            login_time,
            active
        )
        VALUES (?, 'admin', NULL, ?, 1)
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


def create_hotel_session(
    telegram_user_id,
    hotel_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO sessions
        (
            telegram_user_id,
            account_type,
            hotel_account_id,
            login_time,
            active
        )
        VALUES (?, 'hotel', ?, ?, 1)
        """,
        (
            str(telegram_user_id),
            hotel_id,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
    )

    # ربط الحساب بجهاز Telegram الحالي
    cursor.execute(
        """
        UPDATE hotel_accounts
        SET telegram_user_id = ?
        WHERE id = ?
        """,
        (
            str(telegram_user_id),
            hotel_id
        )
    )

    connection.commit()

    connection.close()


def logout_session(
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
        (str(telegram_user_id),)
    )

    session = cursor.fetchone()

    connection.close()

    return session


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
          AND s.account_type = 'hotel'
          AND s.active = 1
          AND h.active = 1
        """,
        (str(telegram_user_id),)
    )

    account = cursor.fetchone()

    connection.close()

    return account


def is_admin_logged(
    telegram_user_id
):

    session = get_session(
        telegram_user_id
    )

    if not session:
        return False

    return session["account_type"] == "admin"


# =========================================================
# حفظ النزيل
# =========================================================

def save_guest(
    guest,
    update,
    hotel_account_id,
    photos=None
):

    if photos is None:
        photos = [None, None, None]

    while len(photos) < 3:
        photos.append(None)

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

            photo1,
            photo2,
            photo3,

            record_date,
            record_time,

            telegram_user_id,
            telegram_username,

            hotel_account_id
        )

        VALUES
        (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?
        )
        """,
        (
            guest.get("الاسم الثلاثي", "غير مذكور"),
            guest.get("اسم الأم", "غير مذكور"),
            guest.get("مكان وتاريخ الولادة", "غير مذكور"),
            guest.get("السكن الأصلي", "غير مذكور"),
            guest.get("المحافظة", "غير مذكور"),
            guest.get("اسم الفندق", "غير مذكور"),
            guest.get("رقم الجناح", "غير مذكور"),
            guest.get("رقم الغرفة", "غير مذكور"),
            guest.get("تاريخ النزول", "غير مذكور"),
            guest.get("مدة الإقامة", "غير مذكور"),
            guest.get("سبب الإقامة", "غير مذكور"),

            photos[0],
            photos[1],
            photos[2],

            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),

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
# تقارير
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
# Render
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
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Web server running on port {port}"
    )

    server.serve_forever()


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
# أوامر موحدة
# =========================================================

async def set_commands(
    application,
    chat_id,
    logged=False
):

    if logged:

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

    else:

        commands = [
            BotCommand(
                "start",
                "🏠 البدء"
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
            "Commands error:",
            e
        )


# =========================================================
# /start
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    session = get_session(
        user_id
    )

    await send_welcome_image(
        update
    )

    if session:

        if session["account_type"] == "admin":

            await update.message.reply_text(

                "السلام عليكم ورحمة الله وبركاته 🌿\n\n"

                "بسم الله الرحمن الرحيم\n"
                "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"

                "🤍 أهلاً وسهلاً بك في نظام "
                "معلومات الفنادق والعقارات.\n\n"

                "👨‍💼 تم تسجيل دخولك بصلاحيات الإدارة.\n\n"

                "يمكنك إدارة حسابات الفنادق "
                "ومتابعة التقارير والبيانات."
            )

            return

        hotel = get_logged_hotel(
            user_id
        )

        if hotel:

            await update.message.reply_text(

                "السلام عليكم ورحمة الله وبركاته 🌿\n\n"

                "بسم الله الرحمن الرحيم\n"
                "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"

                f"🏨 أهلاً وسهلاً بكم في نظام "
                f"معلومات الفنادق.\n\n"

                f"🏨 الفندق: {hotel['hotel_name']}\n\n"

                "نسأل الله أن يوفقكم ويسدد خطاكم.\n\n"

                "يمكنكم الآن البدء بتسجيل بيانات النزيل "
                "من خلال إرسال /start مرة أخرى."
            )

            # زر بدء النموذج
            keyboard = [
                [
                    InlineKeyboardButton(
                        "📝 تسجيل نزيل جديد",
                        callback_data="new_guest"
                    )
                ]
            ]

            await update.message.reply_text(
                "اختر العملية المطلوبة:",
                reply_markup=InlineKeyboardMarkup(
                    keyboard
                )
            )

            return

    # غير مسجل
    await update.message.reply_text(

        "السلام عليكم ورحمة الله وبركاته 🌿\n\n"

        "بسم الله الرحمن الرحيم\n"
        "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"

        "🤍 أهلاً وسهلاً ومرحباً بكم.\n\n"

        "هذا النظام مخصص لتنظيم واستقبال "
        "بيانات الفنادق والنزلاء بطريقة سهلة "
        "ومنظمة.\n\n"

        "🔐 للبدء اضغط الزر التالي:"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🔐 تسجيل الدخول",
                callback_data="login"
            )
        ]
    ]

    await update.message.reply_text(
        "👇",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# زر تسجيل الدخول
# =========================================================

async def login_button(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    await query.message.reply_text(

        "🔐 تسجيل الدخول\n\n"

        "أرسل اسم المستخدم:"
    )

    return LOGIN_USERNAME


# =========================================================
# تسجيل الدخول
# =========================================================

async def login_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if get_session(user_id):

        await update.message.reply_text(
            "✅ أنت مسجل الدخول بالفعل."
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "🔐 تسجيل الدخول\n\n"
        "أرسل اسم المستخدم:"
    )

    return LOGIN_USERNAME


async def login_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = update.message.text.strip()

    if not username:

        await update.message.reply_text(
            "❌ أرسل اسم مستخدم صحيح."
        )

        return LOGIN_USERNAME

    context.user_data[
        "login_username"
    ] = username

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
        "login_username",
        ""
    ).strip()

    user_id = update.effective_user.id

    # =====================================================
    # أولاً: تجربة حساب المدير
    # =====================================================

    if (
        username == ADMIN_USERNAME
        and password == ADMIN_PASSWORD
    ):

        create_admin_session(
            user_id
        )

        context.user_data.clear()

        await set_commands(
            context.application,
            update.effective_chat.id,
            logged=True
        )

        await update.message.reply_text(

            "✅ تم تسجيل دخول الإدارة بنجاح.\n\n"

            "👨‍💼 مرحباً بك مدير النظام.\n\n"

            "يمكنك الآن استخدام وظائف الإدارة."
        )

        return ConversationHandler.END

    # =====================================================
    # ثانياً: تجربة حساب الفندق
    # =====================================================

    account = authenticate_hotel(
        username,
        password
    )

    if account:

        # ---------------------------------------------
        # إذا كان الحساب مرتبطاً بحساب Telegram آخر
        # ---------------------------------------------

        old_id = account[
            "telegram_user_id"
        ]

        current_id = str(
            user_id
        )

        if (
            old_id
            and old_id != current_id
        ):

            await update.message.reply_text(

                "⚠️ هذا الحساب مرتبط حالياً "
                "بحساب Telegram آخر.\n\n"

                "إذا كان هذا الحساب يعود إليك، "
                "يرجى التواصل مع الإدارة لإعادة ربطه."
            )

            return ConversationHandler.END

        create_hotel_session(
            current_id,
            account["id"]
        )

        context.user_data.clear()

        await set_commands(
            context.application,
            update.effective_chat.id,
            logged=True
        )

        await update.message.reply_text(

            "✅ تم تسجيل الدخول بنجاح.\n\n"

            f"🏨 الفندق: {account['hotel_name']}\n\n"

            "أهلاً وسهلاً بكم، ونسأل الله "
            "التوفيق والسداد.\n\n"

            "سيبقى تسجيل الدخول فعالاً حتى "
            "تقوموا بتسجيل الخروج."
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "📝 تسجيل نزيل جديد",
                    callback_data="new_guest"
                )
            ]
        ]

        await update.message.reply_text(
            "يمكنك الآن البدء:",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return ConversationHandler.END

    # =====================================================
    # فشل الدخول
    # =====================================================

    context.user_data.pop(
        "login_username",
        None
    )

    await update.message.reply_text(

        "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"

        "تأكد من كتابة البيانات كما أنشأتها الإدارة "
        "ثم اضغط /start للمحاولة من جديد."
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

    await set_commands(
        context.application,
        update.effective_chat.id,
        logged=False
    )

    await update.message.reply_text(

        "🚪 تم تسجيل الخروج بنجاح.\n\n"

        "نسأل الله لكم التوفيق.\n\n"

        "يمكنك تسجيل الدخول مرة أخرى من خلال /start."
    )


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
            "⛔ هذه العملية مخصصة للمدير."
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
            "والنقطة أو الشرطة فقط."
        )

        return ADD_HOTEL_USERNAME

    context.user_data[
        "new_hotel_username"
    ] = username

    await update.message.reply_text(
        "🔑 أرسل كلمة المرور للفندق:"
    )

    return ADD_HOTEL_PASSWORD


async def add_hotel_password(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    password = update.message.text.strip()

    if len(password) < 8:

        await update.message.reply_text(
            "❌ كلمة المرور يجب أن تكون 8 أحرف على الأقل."
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
            "❌ يجب إدخال اسم الفندق."
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

        f"🏨 الفندق: {hotel_name}\n"
        f"👤 اسم المستخدم: {username}\n\n"

        "🔐 تم حفظ كلمة المرور بشكل آمن.\n\n"

        "أرسل بيانات الدخول لصاحب الفندق "
        "بطريقة آمنة."
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
            "⛔ هذه العملية مخصصة للمدير."
        )

        return

    hotels = get_all_hotels()

    if not hotels:

        await update.message.reply_text(
            "📋 لا توجد فنادق مسجلة."
        )

        return

    text = "🏨 قائمة الفنادق\n\n"

    keyboard = []

    for hotel in hotels:

        status = (
            "🟢 فعال"
            if hotel["active"]
            else "🔴 محذوف/موقوف"
        )

        text += (
            f"#{hotel['id']}\n"
            f"🏨 {hotel['hotel_name']}\n"
            f"👤 {hotel['username']}\n"
            f"📌 {status}\n\n"
        )

        if hotel["active"]:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🗑 حذف {hotel['hotel_name']}",
                        callback_data=f"delete_hotel:{hotel['id']}"
                    )
                ]
            )

        else:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"♻️ تفعيل {hotel['hotel_name']}",
                        callback_data=f"enable_hotel:{hotel['id']}"
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
# حذف/تفعيل الفندق
# =========================================================

async def hotel_management_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if not is_admin_logged(
        query.from_user.id
    ):

        await query.message.reply_text(
            "⛔ غير مصرح لك."
        )

        return

    data = query.data

    if data.startswith(
        "delete_hotel:"
    ):

        hotel_id = int(
            data.split(":")[1]
        )

        delete_hotel(
            hotel_id
        )

        await query.message.reply_text(

            "🗑 تم حذف/تعطيل حساب الفندق.\n\n"

            "لن يستطيع صاحب الفندق تسجيل الدخول "
            "بهذا الحساب."
        )

    elif data.startswith(
        "enable_hotel:"
    ):

        hotel_id = int(
            data.split(":")[1]
        )

        enable_hotel(
            hotel_id
        )

        await query.message.reply_text(
            "♻️ تم تفعيل حساب الفندق."
        )


# =========================================================
# بدء نموذج النزيل
# =========================================================

async def new_guest_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    hotel = get_logged_hotel(
        query.from_user.id
    )

    if not hotel:

        await query.message.reply_text(
            "⛔ يجب تسجيل الدخول أولاً."
        )

        return ConversationHandler.END

    context.user_data[
        "guest_form"
    ] = {}

    context.user_data[
        "guest_photos"
    ] = []

    await query.message.reply_text(

        "📝 نموذج تسجيل نزيل جديد\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n\n"

        "سيتم طلب بيانات النزيل سؤالاً سؤالاً.\n"
        "يرجى إدخال البيانات بدقة.\n\n"

        "سنطلب أيضاً 3 صور، "
        "والصورتان الأولى والثانية إلزاميتان.\n\n"

        "نبدأ الآن 👇"
    )

    await query.message.reply_text(
        "1️⃣ الاسم الثلاثي:"
    )

    return GUEST_NAME


async def guest_name(
    update,
    context
):

    value = update.message.text.strip()

    if not value:

        await update.message.reply_text(
            "❌ يرجى إدخال الاسم."
        )

        return GUEST_NAME

    context.user_data[
        "guest_form"
    ]["الاسم الثلاثي"] = value

    await update.message.reply_text(
        "2️⃣ اسم الأم:"
    )

    return GUEST_MOTHER


async def guest_mother(
    update,
    context
):

    value = update.message.text.strip()

    context.user_data[
        "guest_form"
    ]["اسم الأم"] = value

    await update.message.reply_text(
        "3️⃣ مكان وتاريخ الولادة:"
    )

    return GUEST_BIRTH


async def guest_birth(
    update,
    context
):

    value = update.message.text.strip()

    context.user_data[
        "guest_form"
    ]["مكان وتاريخ الولادة"] = value

    await update.message.reply_text(
        "4️⃣ السكن الأصلي:"
    )

    return GUEST_HOME


async def guest_home(
    update,
    context
):

    value = update.message.text.strip()

    context.user_data[
        "guest_form"
    ]["السكن الأصلي"] = value

    await update.message.reply_text(
        "5️⃣ المحافظة:"
    )

    return GUEST_GOVERNORATE


async def guest_governorate(
    update,
    context
):

    value = update.message.text.strip()

    context.user_data[
        "guest_form"
    ]["المحافظة"] = value

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    context.user_data[
        "guest_form"
    ]["اسم الفندق"] = (
        hotel["hotel_name"]
        if hotel
        else "غير مذكور"
    )

    await update.message.reply_text(
        "6️⃣ رقم الجناح:\n\n"
        "إذا لم يوجد جناح اكتب: لا يوجد"
    )

    return GUEST_SUITE


async def guest_suite(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["رقم الجناح"] = update.message.text.strip()

    await update.message.reply_text(
        "7️⃣ رقم الغرفة:"
    )

    return GUEST_ROOM


async def guest_room(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["رقم الغرفة"] = update.message.text.strip()

    await update.message.reply_text(
        "8️⃣ تاريخ النزول:"
    )

    return GUEST_CHECKIN


async def guest_checkin(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["تاريخ النزول"] = update.message.text.strip()

    await update.message.reply_text(
        "9️⃣ مدة الإقامة:"
    )

    return GUEST_DURATION


async def guest_duration(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["مدة الإقامة"] = update.message.text.strip()

    await update.message.reply_text(
        "🔟 سبب الإقامة:"
    )

    return GUEST_REASON


async def guest_reason(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["سبب الإقامة"] = update.message.text.strip()

    await update.message.reply_text(

        "📷 الصورة الأولى\n\n"

        "⚠️ هذه الصورة إلزامية.\n"
        "أرسل صورة واضحة للنزيل:"
    )

    return GUEST_PHOTO1


# =========================================================
# الصور
# =========================================================

async def guest_photo1(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "⚠️ الصورة الأولى إلزامية.\n"
            "يرجى إرسال صورة وليس نصاً."
        )

        return GUEST_PHOTO1

    image = await get_photo(
        update
    )

    if not image:

        await update.message.reply_text(
            "❌ تعذر استلام الصورة. أعد إرسالها."
        )

        return GUEST_PHOTO1

    context.user_data[
        "guest_photos"
    ].append(
        image.getvalue()
    )

    await update.message.reply_text(

        "✅ تم استلام الصورة الأولى.\n\n"

        "📷 الصورة الثانية\n\n"

        "⚠️ هذه الصورة إلزامية.\n"
        "أرسل الصورة الثانية:"
    )

    return GUEST_PHOTO2


async def guest_photo2(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "⚠️ الصورة الثانية إلزامية.\n"
            "يرجى إرسال صورة."
        )

        return GUEST_PHOTO2

    image = await get_photo(
        update
    )

    if not image:

        await update.message.reply_text(
            "❌ تعذر استلام الصورة."
        )

        return GUEST_PHOTO2

    context.user_data[
        "guest_photos"
    ].append(
        image.getvalue()
    )

    await update.message.reply_text(

        "✅ تم استلام الصورة الثانية.\n\n"

        "📷 الصورة الثالثة\n\n"

        "هذه الصورة اختيارية، "
        "لكن يمكنك إرسالها إذا كانت متوفرة.\n\n"

        "أرسل الصورة الثالثة أو اضغط:\n"
        " /skip"
    )

    return GUEST_PHOTO3


async def guest_photo3(
    update,
    context
):

    if update.message.photo:

        image = await get_photo(
            update
        )

        if image:

            context.user_data[
                "guest_photos"
            ].append(
                image.getvalue()
            )

    else:

        await update.message.reply_text(
            "يمكنك إرسال صورة ثالثة أو استخدام /skip."
        )

        return GUEST_PHOTO3

    return await show_guest_confirmation(
        update,
        context
    )


async def skip_photo3(
    update,
    context
):

    return await show_guest_confirmation(
        update,
        context
    )


# =========================================================
# معاينة البيانات
# =========================================================

async def show_guest_confirmation(
    update,
    context
):

    form = context.user_data.get(
        "guest_form",
        {}
    )

    photos = context.user_data.get(
        "guest_photos",
        []
    )

    while len(photos) < 3:
        photos.append(None)

    text = (

        "📋 مراجعة بيانات النزيل\n\n"

        f"👤 الاسم: {form.get('الاسم الثلاثي')}\n"
        f"👩 اسم الأم: {form.get('اسم الأم')}\n"
        f"🎂 الولادة: {form.get('مكان وتاريخ الولادة')}\n"
        f"🏠 السكن: {form.get('السكن الأصلي')}\n"
        f"📍 المحافظة: {form.get('المحافظة')}\n"
        f"🏨 الفندق: {form.get('اسم الفندق')}\n"
        f"🚪 الجناح: {form.get('رقم الجناح')}\n"
        f"🚪 الغرفة: {form.get('رقم الغرفة')}\n"
        f"📅 تاريخ النزول: {form.get('تاريخ النزول')}\n"
        f"⏱ مدة الإقامة: {form.get('مدة الإقامة')}\n"
        f"🎯 السبب: {form.get('سبب الإقامة')}\n\n"

        f"📷 الصور: "
        f"{sum(1 for p in photos if p)} / 3\n\n"

        "إذا كانت البيانات صحيحة اضغط:\n"
        "📤 إرسال المعلومات للإدارة"
    )

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

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    return GUEST_CONFIRM


# =========================================================
# إرسال النزيل
# =========================================================

async def submit_guest(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    hotel = get_logged_hotel(
        user_id
    )

    if not hotel:

        await query.message.reply_text(
            "⛔ انتهت جلسة الدخول."
        )

        return ConversationHandler.END

    form = context.user_data.get(
        "guest_form"
    )

    photos = context.user_data.get(
        "guest_photos",
        []
    )

    if not form:

        await query.message.reply_text(
            "❌ لا توجد بيانات جاهزة."
        )

        return ConversationHandler.END

    if len(photos) < 2:

        await query.message.reply_text(
            "❌ يجب إرسال الصورتين الإلزاميتين."
        )

        return GUEST_PHOTO2

    while len(photos) < 3:
        photos.append(None)

    form["اسم الفندق"] = hotel[
        "hotel_name"
    ]

    save_guest(
        form,
        update,
        hotel["id"],
        photos
    )

    await query.message.reply_text(

        "✅ تم إرسال معلومات النزيل بنجاح.\n\n"

        "📤 وصلت البيانات إلى النظام.\n"
        "🏨 الفندق: "
        f"{hotel['hotel_name']}\n\n"

        "يمكنك الآن تسجيل نزيل جديد."
    )

    context.user_data.pop(
        "guest_form",
        None
    )

    context.user_data.pop(
        "guest_photos",
        None
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "📝 تسجيل نزيل جديد",
                callback_data="new_guest"
            )
        ]
    ]

    await query.message.reply_text(
        "👇",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    return ConversationHandler.END


# =========================================================
# إلغاء نموذج النزيل
# =========================================================

async def cancel_guest(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    context.user_data.pop(
        "guest_form",
        None
    )

    context.user_data.pop(
        "guest_photos",
        None
    )

    await query.message.reply_text(
        "❌ تم إلغاء تسجيل النزيل."
    )

    return ConversationHandler.END


# =========================================================
# الصورة من Telegram
# =========================================================

async def get_photo(
    update
):

    message = update.message

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
            "Photo error:",
            e
        )

        return None


# =========================================================
# PDF
# =========================================================

def safe_filename(
    name
):

    name = name or "تقرير_نزيل"

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

    return (
        name
        if name.endswith(".pdf")
        else name + ".pdf"
    )


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

    pdf.drawRightString(
        PAGE_WIDTH - 60,
        y - 13,
        arabic_text(
            f"{key}: {value}"
        )
    )

    return y - 38


def create_guest_pdf(
    guest
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

        if key in [
            "photo1",
            "photo2",
            "photo3"
        ]:
            continue

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
# التقارير اليومية
# =========================================================

def create_daily_pdf(
    rows,
    target_date,
    title="تقرير عمل قسم معلومات الفنادق"
):

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    draw_pdf_header(
        pdf,
        title
    )

    y = PAGE_HEIGHT - 120

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

    pdf.setFont(
        PDF_FONT,
        13
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            f"التاريخ: {target_date}"
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
        (
            "أولاً: حسب المحافظة",
            governorates
        ),
        (
            "ثانياً: حسب الفندق",
            hotels
        ),
        (
            "ثالثاً: حسب سبب الإقامة",
            reasons
        )
    ]

    for title_text, counter in sections:

        if y < 100:

            pdf.showPage()

            draw_pdf_header(
                pdf,
                title
            )

            y = PAGE_HEIGHT - 125

        pdf.setFont(
            PDF_FONT,
            13
        )

        pdf.drawRightString(
            PAGE_WIDTH - 50,
            y,
            arabic_text(title_text)
        )

        y -= 30

        pdf.setFont(
            PDF_FONT,
            10
        )

        for name, count in counter.most_common():

            if y < 60:

                pdf.showPage()

                draw_pdf_header(
                    pdf,
                    title
                )

                y = PAGE_HEIGHT - 125

            pdf.drawRightString(
                PAGE_WIDTH - 70,
                y,
                arabic_text(
                    f"• {name}: {count}"
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

    if not is_admin_logged(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ هذه العملية مخصصة للمدير."
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

        text += (
            f"• {name}: {count}\n"
        )

    text += "\n🏨 حسب الفندق:\n"

    for name, count in hotels.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    text += "\n🎯 حسب سبب الإقامة:\n"

    for name, count in reasons.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    await update.message.reply_text(
        text
    )

    pdf_file = create_daily_pdf(
        rows,
        target_date
    )

    await update.message.reply_document(
        document=pdf_file,
        filename=(
            f"تقرير_الفنادق_{target_date}.pdf"
        ),
        caption="📋 التقرير اليومي PDF."
    )


# =========================================================
# تقرير أمس
# =========================================================

async def yesterday_report(
    update,
    context
):

    if not is_admin_logged(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ هذه العملية مخصصة للمدير."
        )

        return

    target_date = (
        date.today()
        - timedelta(days=1)
    ).isoformat()

    rows = get_guests_by_date(
        target_date
    )

    if not rows:

        await update.message.reply_text(
            f"📋 لا توجد بيانات بتاريخ {target_date}."
        )

        return

    pdf_file = create_daily_pdf(
        rows,
        target_date
    )

    await update.message.reply_document(
        document=pdf_file,
        filename=(
            f"تقرير_الفنادق_{target_date}.pdf"
        ),
        caption=(
            f"📋 تقرير يوم {target_date}"
        )
    )


# =========================================================
# التقرير الشهري
# =========================================================

async def monthly_report(
    update,
    context
):

    if not is_admin_logged(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ هذه العملية مخصصة للمدير."
        )

        return

    month = date.today().strftime(
        "%Y-%m"
    )

    rows = get_guests_by_month(
        month
    )

    if not rows:

        await update.message.reply_text(
            "📋 لا توجد بيانات للشهر الحالي."
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

        "📊 التقرير الشهري\n\n"

        f"📅 الشهر: {month}\n"
        f"👥 إجمالي النزلاء: {total}\n\n"

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

    pdf_file = create_daily_pdf(
        rows,
        month,
        title="التقرير الشهري لقسم معلومات الفنادق"
    )

    await update.message.reply_document(
        document=pdf_file,
        filename=(
            f"التقرير_الشهري_{month}.pdf"
        ),
        caption="📊 التقرير الشهري PDF."
    )


# =========================================================
# أوامر المدير
# =========================================================

async def admin_add_hotel_command(
    update,
    context
):

    return await add_hotel_start(
        update,
        context
    )


# =========================================================
# أوامر المدير المخفية
# =========================================================

async def admin_help(
    update,
    context
):

    if not is_admin_logged(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ هذه العملية مخصصة للمدير."
        )

        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🏨 إضافة فندق",
                callback_data="admin_add_hotel"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 قائمة الفنادق",
                callback_data="admin_hotels"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 التقرير اليومي",
                callback_data="admin_daily"
            )
        ],
        [
            InlineKeyboardButton(
                "📅 تقرير أمس",
                callback_data="admin_yesterday"
            )
        ],
        [
            InlineKeyboardButton(
                "📈 التقرير الشهري",
                callback_data="admin_monthly"
            )
        ]
    ]

    await update.message.reply_text(
        "👨‍💼 لوحة الإدارة:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not is_admin_logged(
        query.from_user.id
    ):

        await query.message.reply_text(
            "⛔ غير مصرح لك."
        )

        return

    data = query.data

    if data == "admin_add_hotel":

        await query.message.reply_text(
            "🏨 إضافة فندق\n\n"
            "أرسل اسم المستخدم للفندق:"
        )

        # لا يمكن تحويل callback مباشرة إلى state
        # لذلك نستخدم flag مؤقت
        context.user_data[
            "admin_add_hotel_manual"
        ] = True

    elif data == "admin_hotels":

        fake_update = update

        await hotels_list(
            fake_update,
            context
        )

    elif data == "admin_daily":

        await daily_report(
            update,
            context
        )

    elif data == "admin_yesterday":

        await yesterday_report(
            update,
            context
        )

    elif data == "admin_monthly":

        await monthly_report(
            update,
            context
        )


# =========================================================
# إلغاء
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
# تجاهل /skip إذا لم تكن داخل النموذج
# =========================================================

async def skip_command(
    update,
    context
):

    await update.message.reply_text(
        "ℹ️ لا توجد عملية حالية."
    )


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
# تسجيل الدخول
# =========================================================

login_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "login",
            login_start
        ),
        CallbackQueryHandler(
            login_button,
            pattern="^login$"
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
        ]
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
        ]
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
            pattern="^new_guest$"
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

        GUEST_PHOTO1: [
            MessageHandler(
                filters.PHOTO,
                guest_photo1
            )
        ],

        GUEST_PHOTO2: [
            MessageHandler(
                filters.PHOTO,
                guest_photo2
            )
        ],

        GUEST_PHOTO3: [
            MessageHandler(
                filters.PHOTO,
                guest_photo3
            )
        ],

        GUEST_CONFIRM: [
            CallbackQueryHandler(
                submit_guest,
                pattern="^submit_guest$"
            ),
            CallbackQueryHandler(
                cancel_guest,
                pattern="^cancel_guest$"
            )
        ]
    },

    fallbacks=[
        CommandHandler(
            "cancel",
            cancel
        ),
        CommandHandler(
            "skip",
            skip_photo3
        )
    ],

    allow_reentry=True
)


# =========================================================
# الأوامر
# =========================================================

app.add_handler(
    CommandHandler(
        "start",
        start
    )
)

app.add_handler(
    login_handler
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

app.add_handler(
    CommandHandler(
        "admin",
        admin_help
    )
)

app.add_handler(
    CommandHandler(
        "skip",
        skip_command
    )
)

# =========================================================
# أزرار الإدارة والفندق
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        hotel_management_callback,
        pattern=r"^(delete_hotel|enable_hotel):"
    )
)

app.add_handler(
    CallbackQueryHandler(
        new_guest_start,
        pattern="^new_guest$"
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

    if not ADMIN_USERNAME:

        print(
            "ERROR: ADMIN_USERNAME is empty!"
        )

        return

    if not ADMIN_PASSWORD:

        print(
            "ERROR: ADMIN_PASSWORD is empty!"
        )

        return

    print(
        "ADMIN_USERNAME:",
        ADMIN_USERNAME
    )

    print(
        "ADMIN_PASSWORD is configured: YES"
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


if __name__ == "__main__":

    asyncio.run(
        main()
    )
