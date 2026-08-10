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
# حالات المحادثة
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
GUEST_ROOM = 25
GUEST_CHECKIN = 26
GUEST_DURATION = 27
GUEST_REASON = 28
GUEST_IMAGE_1 = 29
GUEST_IMAGE_2 = 30
GUEST_IMAGE_3 = 31
GUEST_CONFIRM = 32


# =========================================================
# الخط العربي
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

    try:
        reshaped = arabic_reshaper.reshape(
            str(text)
        )

        return get_display(
            reshaped
        )

    except Exception:

        return str(text)


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

    # =====================================================
    # النزلاء
    # =====================================================

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

    # =====================================================
    # الفنادق
    # =====================================================

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

    # =====================================================
    # جلسات المستخدمين
    # =====================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (

            telegram_user_id TEXT PRIMARY KEY,

            account_type TEXT,

            hotel_account_id INTEGER,

            login_time TEXT,

            active INTEGER DEFAULT 1
        )
        """
    )

    connection.commit()
    connection.close()

    print("Database initialized successfully")


# =========================================================
# تشفير كلمة المرور
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
# حذف / إيقاف الفندق
# =========================================================

def delete_hotel(hotel_id):

    connection = get_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM sessions
        WHERE hotel_account_id = ?
        """,
        (hotel_id,)
    )

    cursor.execute(
        """
        DELETE FROM hotel_accounts
        WHERE id = ?
        """,
        (hotel_id,)
    )

    connection.commit()

    deleted = cursor.rowcount

    connection.close()

    return deleted > 0


def disable_hotel(hotel_id):

    connection = get_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE hotel_accounts
        SET active = 0
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


def enable_hotel(hotel_id):

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
    hotel_account_id
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
    update
):

    if not update.effective_user:
        return False

    session = get_session(
        update.effective_user.id
    )

    if not session:
        return False

    return session["account_type"] == "admin"


# =========================================================
# التحقق من المدير
# =========================================================

def authenticate_admin(
    username,
    password
):

    username = username.strip()

    password = password.strip()

    if not ADMIN_USERNAME:
        return False

    if not ADMIN_PASSWORD:
        return False

    return (
        secrets.compare_digest(
            username,
            ADMIN_USERNAME
        )
        and
        secrets.compare_digest(
            password,
            ADMIN_PASSWORD
        )
    )


# =========================================================
# حفظ النزيل
# =========================================================

def save_guest(
    guest,
    update,
    hotel_account_id=None
):

    now = datetime.now()

    user_id = ""
    username = ""

    if update.effective_user:

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
# بيانات التقارير
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
# تنظيف اسم الملف
# =========================================================

def safe_filename(name):

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

    return (name or "تقرير_نزيل") + ".pdf"


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
            "Hotel commands error:",
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
            "Logged hotel commands error:",
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
            "📋 قائمة الفنادق"
        ),

        BotCommand(
            "delete_hotel",
            "🗑 حذف فندق"
        ),

        BotCommand(
            "disable_hotel",
            "⛔ إيقاف فندق"
        ),

        BotCommand(
            "enable_hotel",
            "✅ تفعيل فندق"
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
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    session = get_session(
        user_id
    )

    # -----------------------------------------------------
    # المدير
    # -----------------------------------------------------

    if session and session["account_type"] == "admin":

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(
            update
        )

        await update.message.reply_text(

            "بسم الله الرحمن الرحيم 🌿\n\n"

            "السلام عليكم ورحمة الله وبركاته\n\n"

            "الحمد لله الذي بنعمته تتم الصالحات، "
            "نسأل الله التوفيق والسداد في أداء الأمانة "
            "وحسن العمل.\n\n"

            "🏢 أهلاً وسهلاً بك في نظام معلومات الفنادق\n\n"

            "👨‍💼 تم التعرف على حسابك كحساب مدير.\n\n"

            "يمكنك الآن إدارة حسابات الفنادق "
            "ومتابعة التقارير والبيانات من القائمة."
        )

        return

    # -----------------------------------------------------
    # الفندق
    # -----------------------------------------------------

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

        await update.message.reply_text(

            "بسم الله الرحمن الرحيم 🌿\n\n"

            "السلام عليكم ورحمة الله وبركاته\n\n"

            "أهلاً وسهلاً ومرحباً بك في "
            "نظام معلومات الفنادق.\n\n"

            f"🏨 الفندق: {hotel['hotel_name']}\n\n"

            "نسأل الله أن يوفقنا وإياكم لما فيه "
            "الخير وحسن الأمانة.\n\n"

            "يمكنك الآن الضغط على /start_form "
            "لبدء تسجيل بيانات نزيل جديد.\n\n"

            "🚪 وعند الانتهاء استخدم /logout."
        )

        return

    # -----------------------------------------------------
    # غير مسجل
    # -----------------------------------------------------

    await set_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await send_welcome_image(
        update
    )

    await update.message.reply_text(

        "بسم الله الرحمن الرحيم 🌿\n\n"

        "السلام عليكم ورحمة الله وبركاته\n\n"

        "أهلاً وسهلاً ومرحباً بكم في "
        "نظام معلومات الفنادق.\n\n"

        "نسأل الله أن يوفقنا جميعاً لأداء الأمانة "
        "وحسن العمل وخدمة الناس بما يرضيه سبحانه.\n\n"

        "🔐 للمتابعة استخدم:\n"
        "/login\n\n"

        "سيقوم النظام تلقائياً بالتعرف على نوع الحساب "
        "بعد إدخال اسم المستخدم وكلمة المرور."
    )


# =========================================================
# LOGIN
# =========================================================

async def login_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    session = get_session(
        user_id
    )

    if session:

        if session["account_type"] == "admin":

            await update.message.reply_text(
                "✅ أنت مسجل الدخول بالفعل كمدير."
            )

        else:

            hotel = get_logged_hotel(
                user_id
            )

            await update.message.reply_text(
                f"✅ أنت مسجل الدخول بالفعل.\n\n"
                f"🏨 الفندق: "
                f"{hotel['hotel_name'] if hotel else 'غير معروف'}"
            )

        return ConversationHandler.END

    await update.message.reply_text(

        "🔐 تسجيل الدخول\n\n"

        "يرجى إرسال اسم المستخدم:"
    )

    return LOGIN_USERNAME


async def login_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = update.message.text.strip()

    if not username:

        await update.message.reply_text(
            "❌ اسم المستخدم فارغ.\n\n"
            "أرسل اسم المستخدم من جديد."
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
    )

    if not username:

        await update.message.reply_text(
            "❌ انتهت عملية تسجيل الدخول.\n\n"
            "استخدم /login من جديد."
        )

        return ConversationHandler.END

    user_id = update.effective_user.id

    # =====================================================
    # أولاً: تجربة حساب المدير
    # =====================================================

    if authenticate_admin(
        username,
        password
    ):

        create_admin_session(
            user_id
        )

        context.user_data.pop(
            "login_username",
            None
        )

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await update.message.reply_text(

            "بسم الله الرحمن الرحيم 🌿\n\n"

            "✅ تم تسجيل الدخول بنجاح.\n\n"

            "👨‍💼 تم التعرف على الحساب كحساب مدير.\n\n"

            "أهلاً وسهلاً بك، ويمكنك الآن إدارة "
            "الفنادق والتقارير من قائمة الأوامر."
        )

        return ConversationHandler.END

    # =====================================================
    # ثانياً: تجربة حساب الفندق
    # =====================================================

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

            "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"

            "تأكد من البيانات الموجودة في الحساب "
            "ثم استخدم /login للمحاولة مرة أخرى."
        )

        return ConversationHandler.END

    # =====================================================
    # منع ربط الحساب بشخص آخر
    # =====================================================

    old_telegram_id = account[
        "telegram_user_id"
    ]

    current_telegram_id = str(
        user_id
    )

    if (
        old_telegram_id
        and old_telegram_id != current_telegram_id
    ):

        await update.message.reply_text(

            "⚠️ هذا الحساب مرتبط حالياً بحساب "
            "Telegram آخر.\n\n"

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

        "بسم الله الرحمن الرحيم 🌿\n\n"

        "✅ تم تسجيل الدخول بنجاح.\n\n"

        f"🏨 الفندق: {account['hotel_name']}\n\n"

        "أهلاً وسهلاً بك، ويمكنك الآن تسجيل "
        "بيانات النزلاء خطوة بخطوة.\n\n"

        "اضغط على الزر التالي للبدء:"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "📝 تسجيل نزيل جديد",
                callback_data="start_guest_form"
            )
        ]
    ]

    await update.message.reply_text(
        "اختر العملية:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    return ConversationHandler.END


# =========================================================
# LOGOUT
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

    await set_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await update.message.reply_text(

        "🚪 تم تسجيل الخروج بنجاح.\n\n"

        "نسأل الله لكم التوفيق.\n\n"

        "يمكنك تسجيل الدخول مجدداً عند الحاجة "
        "باستخدام /login."
    )


# =========================================================
# إضافة فندق
# =========================================================

async def add_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "🏨 إضافة حساب فندق جديد\n\n"

        "أرسل اسم المستخدم للفندق:"
    )

    return ADD_HOTEL_USERNAME


async def add_hotel_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = (
        update.message.text
        .strip()
        .lower()
    )

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

        "🔑 أرسل كلمة المرور للحساب:\n\n"

        "يفضل ألا تقل عن 8 أحرف."
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
            "❌ اسم الفندق لا يمكن أن يكون فارغاً."
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

        "🔐 تم حفظ كلمة المرور بشكل مشفر.\n\n"

        "يمكن الآن إعطاء بيانات الدخول لصاحب الفندق."
    )

    return ConversationHandler.END


# =========================================================
# قائمة الفنادق
# =========================================================

async def hotels_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    hotels = get_all_hotels()

    if not hotels:

        await update.message.reply_text(
            "📋 لا توجد حسابات فنادق."
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

            f"🆔 رقم الحساب: {hotel['id']}\n"
            f"🏨 الفندق: {hotel['hotel_name']}\n"
            f"👤 المستخدم: {hotel['username']}\n"
            f"📌 الحالة: {status}\n"
            f"📱 Telegram: {connected}\n"
            f"📅 الإنشاء: {hotel['created_at']}\n\n"
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

    if not is_admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    hotels = get_all_hotels()

    if not hotels:

        await update.message.reply_text(
            "📋 لا توجد فنادق لحذفها."
        )

        return

    keyboard = []

    for hotel in hotels:

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🗑 {hotel['hotel_name']} "
                    f"(#{hotel['id']})",
                    callback_data=f"delete:{hotel['id']}"
                )
            ]
        )

    await update.message.reply_text(

        "🗑 اختر الفندق الذي تريد حذفه:\n\n"

        "⚠️ بعد الحذف لن يستطيع صاحب الفندق "
        "تسجيل الدخول بهذا الحساب.",

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

    if not is_admin_logged(update):

        await query.edit_message_text(
            "⛔ غير مصرح لك."
        )

        return

    try:

        hotel_id = int(
            query.data.split(":")[1]
        )

    except Exception:

        await query.edit_message_text(
            "❌ رقم الفندق غير صحيح."
        )

        return

    deleted = delete_hotel(
        hotel_id
    )

    if deleted:

        await query.edit_message_text(

            "🗑 تم حذف الفندق بنجاح.\n\n"

            "🚫 لن يستطيع صاحب الحساب "
            "تسجيل الدخول بعد الآن."
        )

    else:

        await query.edit_message_text(
            "❌ لم يتم العثور على الفندق."
        )


# =========================================================
# إيقاف فندق
# =========================================================

async def disable_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    hotels = get_all_hotels()

    keyboard = []

    for hotel in hotels:

        if hotel["active"]:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"⛔ {hotel['hotel_name']}",
                        callback_data=f"disable:{hotel['id']}"
                    )
                ]
            )

    if not keyboard:

        await update.message.reply_text(
            "لا توجد فنادق فعالة حالياً."
        )

        return

    await update.message.reply_text(

        "⛔ اختر الفندق الذي تريد إيقافه:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def disable_hotel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if not is_admin_logged(update):

        await query.edit_message_text(
            "⛔ غير مصرح لك."
        )

        return

    hotel_id = int(
        query.data.split(":")[1]
    )

    disable_hotel(
        hotel_id
    )

    await query.edit_message_text(
        "⛔ تم إيقاف حساب الفندق.\n\n"
        "لن يستطيع تسجيل الدخول حالياً."
    )


# =========================================================
# تفعيل فندق
# =========================================================

async def enable_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للمدير."
        )

        return

    hotels = get_all_hotels()

    keyboard = []

    for hotel in hotels:

        if not hotel["active"]:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"✅ {hotel['hotel_name']}",
                        callback_data=f"enable:{hotel['id']}"
                    )
                ]
            )

    if not keyboard:

        await update.message.reply_text(
            "لا توجد فنادق موقوفة."
        )

        return

    await update.message.reply_text(

        "✅ اختر الفندق الذي تريد تفعيله:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def enable_hotel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if not is_admin_logged(update):

        await query.edit_message_text(
            "⛔ غير مصرح لك."
        )

        return

    hotel_id = int(
        query.data.split(":")[1]
    )

    enable_hotel(
        hotel_id
    )

    await query.edit_message_text(
        "✅ تم تفعيل حساب الفندق."
    )


# =========================================================
# بدء نموذج النزيل
# =========================================================

async def start_guest_form(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    hotel = get_logged_hotel(
        user_id
    )

    if not hotel:

        await update.message.reply_text(
            "⛔ يجب تسجيل الدخول كفندق أولاً."
        )

        return ConversationHandler.END

    context.user_data[
        "guest_form"
    ] = {}

    await update.message.reply_text(

        "📝 نموذج تسجيل نزيل جديد\n\n"

        "سيتم إدخال البيانات خطوة بخطوة.\n"
        "يرجى الإجابة عن كل سؤال بدقة.\n\n"

        "نبدأ الآن:\n\n"

        "1️⃣ ما الاسم الثلاثي للنزيل؟"
    )

    return GUEST_NAME


# =========================================================
# أسئلة النموذج
# =========================================================

async def guest_name(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["الاسم الثلاثي"] = update.message.text.strip()

    await update.message.reply_text(
        "2️⃣ ما اسم الأم؟"
    )

    return GUEST_MOTHER


async def guest_mother(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["اسم الأم"] = update.message.text.strip()

    await update.message.reply_text(
        "3️⃣ مكان وتاريخ الولادة؟"
    )

    return GUEST_BIRTH


async def guest_birth(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["مكان وتاريخ الولادة"] = update.message.text.strip()

    await update.message.reply_text(
        "4️⃣ ما السكن الأصلي؟"
    )

    return GUEST_HOME


async def guest_home(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["السكن الأصلي"] = update.message.text.strip()

    await update.message.reply_text(
        "5️⃣ ما المحافظة؟"
    )

    return GUEST_GOVERNORATE


async def guest_governorate(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["المحافظة"] = update.message.text.strip()

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    await update.message.reply_text(

        f"🏨 الفندق:\n"
        f"{hotel['hotel_name']}\n\n"

        "6️⃣ ما رقم الغرفة؟"
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
        "7️⃣ ما تاريخ النزول؟"
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
        "8️⃣ ما مدة الإقامة؟"
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
        "9️⃣ ما سبب الإقامة؟"
    )

    return GUEST_REASON


async def guest_reason(
    update,
    context
):

    context.user_data[
        "guest_form"
    ]["سبب الإقامة"] = update.message.text.strip()

    context.user_data[
        "guest_images"
    ] = []

    await update.message.reply_text(

        "📷 الآن نحتاج إلى 3 صور للنزيل.\n\n"

        "1️⃣ الصورة الأولى **إلزامية**.\n\n"

        "أرسل الصورة الأولى الآن."
    )

    return GUEST_IMAGE_1


# =========================================================
# الصور
# =========================================================

async def guest_image_1(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ يجب إرسال صورة.\n\n"
            "الصورة الأولى إلزامية."
        )

        return GUEST_IMAGE_1

    image = await get_photo(
        update
    )

    if not image:

        await update.message.reply_text(
            "❌ تعذر استلام الصورة.\n"
            "أرسلها مرة أخرى."
        )

        return GUEST_IMAGE_1

    context.user_data[
        "guest_images"
    ].append(image)

    await update.message.reply_text(

        "✅ تم استلام الصورة الأولى.\n\n"

        "2️⃣ الصورة الثانية **إلزامية**.\n\n"

        "أرسل الصورة الثانية الآن."
    )

    return GUEST_IMAGE_2


async def guest_image_2(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ يجب إرسال صورة.\n\n"
            "الصورة الثانية إلزامية."
        )

        return GUEST_IMAGE_2

    image = await get_photo(
        update
    )

    if not image:

        await update.message.reply_text(
            "❌ تعذر استلام الصورة.\n"
            "أرسلها مرة أخرى."
        )

        return GUEST_IMAGE_2

    context.user_data[
        "guest_images"
    ].append(image)

    keyboard = [

        [
            InlineKeyboardButton(
                "📷 إضافة صورة ثالثة",
                callback_data="add_image_3"
            )
        ],

        [
            InlineKeyboardButton(
                "➡️ المتابعة بدون صورة ثالثة",
                callback_data="skip_image_3"
            )
        ]
    ]

    await update.message.reply_text(

        "✅ تم استلام الصورة الثانية.\n\n"

        "3️⃣ الصورة الثالثة اختيارية.\n\n"

        "يمكنك إضافة صورة ثالثة أو المتابعة.",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    return GUEST_IMAGE_3


async def guest_image_3(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if query.data == "add_image_3":

        await query.edit_message_text(
            "📷 أرسل الصورة الثالثة الآن."
        )

        context.user_data[
            "waiting_image_3"
        ] = True

        return GUEST_IMAGE_3

    if query.data == "skip_image_3":

        context.user_data.pop(
            "waiting_image_3",
            None
        )

        await show_guest_confirmation(
            query,
            context
        )

        return GUEST_CONFIRM

    return GUEST_IMAGE_3


async def guest_image_3_message(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ أرسل صورة فقط."
        )

        return GUEST_IMAGE_3

    image = await get_photo(
        update
    )

    if not image:

        await update.message.reply_text(
            "❌ تعذر استلام الصورة."
        )

        return GUEST_IMAGE_3

    images = context.user_data.get(
        "guest_images",
        []
    )

    if len(images) >= 3:

        await update.message.reply_text(
            "تم استلام الصور الثلاث بالفعل."
        )

        return GUEST_CONFIRM

    images.append(image)

    context.user_data[
        "guest_images"
    ] = images

    context.user_data.pop(
        "waiting_image_3",
        None
    )

    await show_guest_confirmation(
        update,
        context
    )

    return GUEST_CONFIRM


# =========================================================
# تأكيد البيانات
# =========================================================

async def show_guest_confirmation(
    target,
    context
):

    guest = context.user_data.get(
        "guest_form",
        {}
    )

    hotel = get_logged_hotel(
        target.from_user.id
    )

    hotel_name = (
        hotel["hotel_name"]
        if hotel
        else "غير معروف"
    )

    text = (

        "📋 مراجعة بيانات النزيل\n\n"

        f"👤 الاسم: {guest.get('الاسم الثلاثي')}\n"
        f"👩 اسم الأم: {guest.get('اسم الأم')}\n"
        f"🎂 الولادة: {guest.get('مكان وتاريخ الولادة')}\n"
        f"🏠 السكن: {guest.get('السكن الأصلي')}\n"
        f"📍 المحافظة: {guest.get('المحافظة')}\n"
        f"🏨 الفندق: {hotel_name}\n"
        f"🚪 الغرفة: {guest.get('رقم الغرفة')}\n"
        f"📅 تاريخ النزول: {guest.get('تاريخ النزول')}\n"
        f"⏱ مدة الإقامة: {guest.get('مدة الإقامة')}\n"
        f"🎯 السبب: {guest.get('سبب الإقامة')}\n\n"

        f"📷 عدد الصور: "
        f"{len(context.user_data.get('guest_images', []))}\n\n"

        "إذا كانت البيانات صحيحة اضغط "
        "«إرسال المعلومات للإدارة»."
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

    if hasattr(
        target,
        "edit_message_text"
    ):

        await target.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    else:

        await target.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )


# =========================================================
# إرسال المعلومات
# =========================================================

async def submit_guest(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    hotel = get_logged_hotel(
        query.from_user.id
    )

    if not hotel:

        await query.edit_message_text(
            "⛔ انتهت جلسة تسجيل الدخول."
        )

        return ConversationHandler.END

    guest = dict(
        context.user_data.get(
            "guest_form",
            {}
        )
    )

    guest[
        "اسم الفندق"
    ] = hotel["hotel_name"]

    images = context.user_data.get(
        "guest_images",
        []
    )

    if len(images) < 2:

        await query.edit_message_text(
            "❌ يجب إرسال صورتين على الأقل."
        )

        return GUEST_IMAGE_2

    save_guest(
        guest,
        update,
        hotel["id"]
    )

    # -----------------------------------------------------
    # إرسال PDF للإدارة
    # -----------------------------------------------------

    pdf_file = create_guest_pdf(
        guest,
        images[0] if images else None
    )

    filename = safe_filename(
        guest.get(
            "الاسم الثلاثي",
            "تقرير_نزيل"
        )
    )

    # إرسال التقرير إلى صاحب الفندق نفسه كنسخة
    await context.bot.send_document(
        chat_id=query.from_user.id,
        document=pdf_file,
        filename=filename,
        caption=(
            "✅ تم إرسال معلومات النزيل بنجاح "
            "وحفظها في النظام."
        )
    )

    # -----------------------------------------------------
    # إرسال الصور والتقرير للمديرين
    # -----------------------------------------------------
    # سيتم الإرسال لأي مستخدم مسجل كمدير في قاعدة الجلسات.

    admin_ids = get_admin_ids()

    for admin_id in admin_ids:

        try:

            pdf_file.seek(0)

            await context.bot.send_document(
                chat_id=int(admin_id),
                document=pdf_file,
                filename=filename,
                caption=(
                    "📥 معلومات نزيل جديدة\n\n"
                    f"🏨 الفندق: {hotel['hotel_name']}\n"
                    f"👤 النزيل: "
                    f"{guest.get('الاسم الثلاثي', 'غير مذكور')}"
                )
            )

            for index, image in enumerate(
                images,
                start=1
            ):

                image.seek(0)

                await context.bot.send_photo(
                    chat_id=int(admin_id),
                    photo=image,
                    caption=(
                        f"📷 صورة {index}\n"
                        f"🏨 الفندق: {hotel['hotel_name']}"
                    )
                )

        except Exception as e:

            print(
                "Admin notification error:",
                e
            )

    context.user_data.pop(
        "guest_form",
        None
    )

    context.user_data.pop(
        "guest_images",
        None
    )

    context.user_data.pop(
        "waiting_image_3",
        None
    )

    await query.edit_message_text(

        "✅ تم إرسال المعلومات بنجاح.\n\n"

        "📤 تم إرسال البيانات للإدارة.\n"
        "📷 تم إرفاق الصور.\n\n"

        "يمكنك الآن تسجيل نزيل جديد."
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
        "guest_images",
        None
    )

    await query.edit_message_text(
        "❌ تم إلغاء تسجيل النزيل."
    )

    return ConversationHandler.END


# =========================================================
# الحصول على المديرين المسجلين
# =========================================================

def get_admin_ids():

    connection = get_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT telegram_user_id
        FROM sessions
        WHERE account_type = 'admin'
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
# صورة Telegram
# =========================================================

async def get_photo(
    update
):

    message = update.message

    if not message:
        return None

    if not message.photo:
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
    image_data=None
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

    if image_data:

        try:

            image_data.seek(0)

            image = ImageReader(
                image_data
            )

            img_width = 300
            img_height = 220

            if y < img_height + 70:

                pdf.showPage()

                draw_pdf_header(
                    pdf,
                    "صورة النزيل"
                )

                y = PAGE_HEIGHT - 125

            pdf.drawImage(
                image,
                50,
                y - img_height,
                width=img_width,
                height=img_height,
                preserveAspectRatio=True,
                anchor="sw",
                mask="auto"
            )

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
# التقرير اليومي
# =========================================================

async def daily_report(
    update,
    context
):

    if not is_admin_logged(update):

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

        text += f"• {name}: {count}\n"

    text += "\n🏨 حسب الفندق:\n"

    for name, count in hotels.most_common():

        text += f"• {name}: {count}\n"

    text += "\n🎯 أسباب الإقامة:\n"

    for name, count in reasons.most_common():

        text += f"• {name}: {count}\n"

    await update.message.reply_text(
        text
    )


# =========================================================
# تقرير أمس
# =========================================================

async def yesterday_report(
    update,
    context
):

    if not is_admin_logged(update):

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

        f"📋 تقرير أمس\n\n"
        f"📅 التاريخ: {yesterday}\n"
        f"👥 عدد النزلاء: {len(rows)}"
    )


# =========================================================
# التقرير الشهري
# =========================================================

async def monthly_report(
    update,
    context
):

    if not is_admin_logged(update):

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
            "📋 لا توجد بيانات في الشهر الحالي."
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
# Conversation تسجيل الدخول
# =========================================================

login_handler = ConversationHandler(

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
# Conversation إضافة فندق
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
# Conversation نموذج النزيل
# =========================================================

guest_form_handler = ConversationHandler(

    entry_points=[

        CommandHandler(
            "start_form",
            start_guest_form
        ),

        CallbackQueryHandler(
            start_guest_form,
            pattern="^start_guest_form$"
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

        GUEST_IMAGE_1: [
            MessageHandler(
                filters.PHOTO,
                guest_image_1
            )
        ],

        GUEST_IMAGE_2: [
            MessageHandler(
                filters.PHOTO,
                guest_image_2
            )
        ],

        GUEST_IMAGE_3: [

            CallbackQueryHandler(
                guest_image_3,
                pattern="^(add_image_3|skip_image_3)$"
            ),

            MessageHandler(
                filters.PHOTO,
                guest_image_3_message
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
        )
    ],

    allow_reentry=True
)


# =========================================================
# التطبيق
# =========================================================

if not TOKEN:

    print(
        "WARNING: BOT_TOKEN is not set!"
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
        "delete_hotel",
        delete_hotel_start
    )
)

app.add_handler(
    CommandHandler(
        "disable_hotel",
        disable_hotel_start
    )
)

app.add_handler(
    CommandHandler(
        "enable_hotel",
        enable_hotel_start
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
    CallbackQueryHandler(
        delete_hotel_callback,
        pattern=r"^delete:"
    )
)

app.add_handler(
    CallbackQueryHandler(
        disable_hotel_callback,
        pattern=r"^disable:"
    )
)

app.add_handler(
    CallbackQueryHandler(
        enable_hotel_callback,
        pattern=r"^enable:"
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
            "ERROR: ADMIN_USERNAME is not set!"
        )

        return

    if not ADMIN_PASSWORD:

        print(
            "ERROR: ADMIN_PASSWORD is not set!"
        )

        return

    print(
        "ADMIN_USERNAME:",
        ADMIN_USERNAME
    )

    print(
        "ADMIN_PASSWORD is configured:",
        bool(ADMIN_PASSWORD)
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
