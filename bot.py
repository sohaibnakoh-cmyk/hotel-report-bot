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
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
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

TOKEN = os.getenv("BOT_TOKEN")

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

DEFAULT_MODE = "single"


# =========================================================
# حالات تسجيل الدخول الموحد
# =========================================================

LOGIN_USERNAME = 1
LOGIN_PASSWORD = 2

ADD_HOTEL_USERNAME = 3
ADD_HOTEL_PASSWORD = 4
ADD_HOTEL_NAME = 5

DELETE_HOTEL_ID = 6
DELETE_HOTEL_CONFIRM = 7


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
    # جدول حسابات الفنادق
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
    # جلسات المدير
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

    print(
        "Database initialized successfully"
    )


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
# التحقق من المدير
# =========================================================

def authenticate_admin(
    username,
    password
):

    if not ADMIN_USERNAME:
        return False

    if not ADMIN_PASSWORD:
        return False

    username = username.strip()

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
# الفنادق
# =========================================================

def get_hotel_by_id(
    hotel_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM hotel_accounts
        WHERE id = ?
        """,
        (hotel_id,)
    )

    account = cursor.fetchone()

    connection.close()

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
# حذف / تعطيل الفندق
# =========================================================

def delete_hotel_account(
    hotel_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM hotel_accounts
        WHERE id = ?
        """,
        (hotel_id,)
    )

    hotel = cursor.fetchone()

    if not hotel:

        connection.close()

        return False

    # تعطيل الحساب
    cursor.execute(
        """
        UPDATE hotel_accounts
        SET
            active = 0,
            telegram_user_id = NULL
        WHERE id = ?
        """,
        (hotel_id,)
    )

    # إنهاء أي جلسة حالية للفندق
    cursor.execute(
        """
        DELETE FROM sessions
        WHERE hotel_account_id = ?
        """,
        (hotel_id,)
    )

    connection.commit()

    connection.close()

    return True


# =========================================================
# جلسات الفندق
# =========================================================

def create_session(
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


def logout_admin_session(
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
# معرفة نوع الحساب
# =========================================================

def get_account_type(
    telegram_user_id
):

    if is_admin_logged(
        telegram_user_id
    ):
        return "admin"

    hotel = get_logged_hotel(
        telegram_user_id
    )

    if hotel:
        return "hotel"

    return None


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
# الحصول على بيانات يوم
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

        (
            target_date,
        )
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# =========================================================
# الحصول على بيانات شهر
# =========================================================

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

        (
            year_month,
        )
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# =========================================================
# تنظيف اسم الملف
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
# استخراج قيمة
# =========================================================

def extract_value(
    text,
    names
):

    for field in names:

        patterns = [

            rf"{re.escape(field)}\s*[:：]\s*(.+)",

            rf"{re.escape(field)}\s*[-–]\s*(.+)",
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            if match:

                value = match.group(1).strip()

                if value:
                    return value

    return "غير مذكور"


# =========================================================
# استخراج بيانات النزيل
# =========================================================

def parse_guest(
    text
):

    fields = {

        "الاسم الثلاثي": [
            "الاسم الثلاثي",
            "الاسم"
        ],

        "اسم الأم": [
            "اسم الأم",
            "اسم الام"
        ],

        "مكان وتاريخ الولادة": [
            "مكان وتاريخ الولادة",
            "مكان و تاريخ الولادة"
        ],

        "السكن الأصلي": [
            "السكن الأصلي",
            "السكن الاصلي"
        ],

        "المحافظة": [
            "المحافظة"
        ],

        "اسم الفندق": [
            "اسم الفندق",
            "الفندق"
        ],

        "رقم الجناح": [
            "رقم الجناح",
            "الجناح"
        ],

        "رقم الغرفة": [
            "رقم الغرفة",
            "الغرفة"
        ],

        "تاريخ النزول": [
            "تاريخ النزول",
            "تاريخ الدخول"
        ],

        "مدة الإقامة": [
            "مدة الإقامة",
            "مدة الاقامة"
        ],

        "سبب الإقامة": [
            "سبب الإقامة",
            "سبب الاقامة"
        ],
    }

    result = {}

    for key, names in fields.items():

        result[key] = extract_value(
            text,
            names
        )

    return result


# =========================================================
# تقسيم عدة نزلاء
# =========================================================

def split_guests(
    text
):

    parts = re.split(
        r"\n\s*(?:={3,}|-{3,}|\*{3,})\s*\n",
        text
    )

    result = []

    for part in parts:

        part = part.strip()

        if part:
            result.append(part)

    return result


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
# أوامر المستخدم غير المسجل
# =========================================================

async def set_logged_out_commands(
    application,
    chat_id
):

    commands = [

        BotCommand(
            "start",
            "🌙 إبدأ"
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
            "Logged out commands error:",
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
# أوامر الإدارة
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
            "single",
            "📄 كل نزيل في ملف"
        ),

        BotCommand(
            "all",
            "📚 جميع النزلاء في ملف"
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
# البداية
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    # -----------------------------------------
    # المدير المسجل
    # -----------------------------------------

    if is_admin_logged(user_id):

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(
            update
        )

        await update.message.reply_text(

            "﷽\n\n"

            "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

            "🤲 الحمد لله رب العالمين\n\n"

            "👨‍💼 أهلاً وسهلاً بك في لوحة الإدارة\n\n"

            "نسأل الله أن يوفقنا وإياكم لما فيه "
            "الخير، وأن يعيننا على أداء الأمانة "
            "وإتقان العمل.\n\n"

            "✅ تم التعرف على حسابك كحساب مدير.\n\n"

            "يمكنك الآن استخدام خيارات الإدارة "
            "من القائمة أسفل الشاشة."
        )

        return

    # -----------------------------------------
    # الفندق المسجل
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

            "﷽\n\n"

            "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

            "🤲 الحمد لله رب العالمين\n\n"

            f"🏨 أهلاً وسهلاً بكم في نظام معلومات "
            f"الفنادق\n\n"

            f"🏨 الفندق: {hotel['hotel_name']}\n\n"

            "بارك الله في جهودكم، ونسأل الله أن "
            "يوفقنا وإياكم لما فيه الخير، وأن "
            "يعيننا على أداء الأمانة وحسن العمل.\n\n"

            "✅ حسابكم فعال ويمكنكم إرسال بيانات "
            "النزلاء مباشرة.\n\n"

            "🚪 عند الانتهاء يمكنكم اختيار "
            "«تسجيل الخروج»."
        )

        return

    # -----------------------------------------
    # مستخدم غير مسجل
    # -----------------------------------------

    await set_logged_out_commands(
        context.application,
        update.effective_chat.id
    )

    await send_welcome_image(
        update
    )

    await update.message.reply_text(

        "﷽\n\n"

        "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

        "🤲 الحمد لله رب العالمين\n\n"

        "🏨 أهلاً وسهلاً ومرحباً بكم\n"
        "في نظام معلومات الفنادق\n\n"

        "قال الله تعالى:\n"
        "﴿إِنَّ اللَّهَ يَأْمُرُكُمْ أَنْ تُؤَدُّوا "
        "الْأَمَانَاتِ إِلَى أَهْلِهَا﴾\n\n"

        "نسأل الله أن يوفقنا جميعاً لأداء "
        "الأمانة، وأن يجعل هذا العمل نافعاً "
        "ومباركاً.\n\n"

        "🌙 للمتابعة اضغط «إبدأ» من القائمة "
        "أو أرسل /start."
    )


# =========================================================
# تسجيل الدخول الموحد
# =========================================================

async def unified_login_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    # إذا كان مديراً
    if is_admin_logged(user_id):

        await update.message.reply_text(
            "✅ أنت مسجل الدخول كمدير بالفعل."
        )

        return ConversationHandler.END

    # إذا كان فندقاً
    hotel = get_logged_hotel(
        user_id
    )

    if hotel:

        await update.message.reply_text(

            f"✅ أنت مسجل الدخول بالفعل.\n\n"
            f"🏨 الفندق: {hotel['hotel_name']}"
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "🔐 للمتابعة نحتاج إلى التحقق من الحساب.\n\n"

        "👤 أرسل اسم المستخدم:"
    )

    return LOGIN_USERNAME


async def login_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = update.message.text.strip()

    if not username:

        await update.message.reply_text(
            "❌ يرجى إرسال اسم مستخدم صحيح."
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
        "login_username"
    )

    if not username:

        await update.message.reply_text(
            "❌ انتهت عملية التحقق.\n\n"
            "اضغط /start للمحاولة من جديد."
        )

        return ConversationHandler.END

    current_telegram_id = str(
        update.effective_user.id
    )

    # =====================================================
    # أولاً: التحقق من المدير
    # =====================================================

    if authenticate_admin(
        username,
        password
    ):

        create_admin_session(
            current_telegram_id
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

            "﷽\n\n"

            "✅ تم التحقق من الحساب بنجاح.\n\n"

            "👨‍💼 تم التعرف على الحساب كحساب مدير.\n\n"

            "بارك الله فيكم ووفقكم لما فيه الخير.\n\n"

            "يمكنكم الآن استخدام لوحة الإدارة."
        )

        return ConversationHandler.END

    # =====================================================
    # ثانياً: التحقق من حساب الفندق
    # =====================================================

    account = authenticate_hotel(
        username,
        password
    )

    if account:

        old_telegram_id = account[
            "telegram_user_id"
        ]

        # الحساب مرتبط بحساب Telegram آخر
        if (
            old_telegram_id
            and old_telegram_id != current_telegram_id
        ):

            context.user_data.pop(
                "login_username",
                None
            )

            await update.message.reply_text(

                "⚠️ هذا الحساب مرتبط حالياً "
                "بحساب Telegram آخر.\n\n"

                "يرجى التواصل مع الإدارة."
            )

            return ConversationHandler.END

        create_session(
            current_telegram_id,
            account["id"]
        )

        context.user_data.pop(
            "login_username",
            None
        )

        context.user_data[
            "pdf_mode"
        ] = DEFAULT_MODE

        await set_hotel_commands(
            context.application,
            update.effective_chat.id
        )

        await update.message.reply_text(

            "﷽\n\n"

            "✅ تم التحقق من الحساب بنجاح.\n\n"

            f"🏨 الفندق: {account['hotel_name']}\n\n"

            "بارك الله فيكم، ونسأل الله أن يوفقكم "
            "في عملكم.\n\n"

            "يمكنكم الآن إرسال بيانات النزلاء "
            "مباشرة إلى البوت.\n\n"

            "🔐 لن نطلب اسم المستخدم وكلمة المرور "
            "مرة أخرى حتى تسجيل الخروج."
        )

        return ConversationHandler.END

    # =====================================================
    # فشل التحقق
    # =====================================================

    context.user_data.pop(
        "login_username",
        None
    )

    await update.message.reply_text(

        "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"

        "تأكد من البيانات وحاول من جديد "
        "بالضغط على /start."
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

    logout_admin_session(
        user_id
    )

    context.user_data.clear()

    await set_logged_out_commands(
        context.application,
        update.effective_chat.id
    )

    await update.message.reply_text(

        "﷽\n\n"

        "🚪 تم تسجيل الخروج بنجاح.\n\n"

        "جزاكم الله خيراً وبارك الله فيكم.\n\n"

        "🌙 للعودة إلى النظام استخدم /start."
    )


# =========================================================
# التحقق من المدير
# =========================================================

def admin_logged(
    update
):

    if not update.effective_user:
        return False

    return is_admin_logged(
        update.effective_user.id
    )


# =========================================================
# إضافة فندق
# =========================================================

async def add_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الخيار مخصص للإدارة."
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "🏨 إضافة حساب فندق جديد\n\n"

        "👤 أرسل اسم المستخدم للفندق:"
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
            "❌ يرجى إرسال اسم الفندق."
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

        "﷽\n\n"

        "✅ تم إنشاء حساب الفندق بنجاح.\n\n"

        f"🏨 الفندق: {hotel_name}\n"
        f"👤 اسم المستخدم: {username}\n\n"

        "🔐 تم حفظ كلمة المرور بشكل مشفر.\n\n"

        "⚠️ يرجى إرسال بيانات الدخول لصاحب "
        "الفندق بطريقة آمنة."
    )

    return ConversationHandler.END


# =========================================================
# حذف فندق - البداية
# =========================================================

async def delete_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الخيار مخصص للإدارة."
        )

        return ConversationHandler.END

    hotels = get_all_hotels()

    active_hotels = [
        hotel
        for hotel in hotels
        if hotel["active"]
    ]

    if not active_hotels:

        await update.message.reply_text(
            "📋 لا توجد فنادق فعالة حالياً."
        )

        return ConversationHandler.END

    text = (
        "🗑 حذف / إيقاف فندق\n\n"
        "⚠️ إيقاف الفندق سيمنعه من تسجيل الدخول، "
        "مع الاحتفاظ ببياناته السابقة.\n\n"
        "اختر رقم الفندق بإرسال رقم الحساب:\n\n"
    )

    for hotel in active_hotels:

        text += (
            f"#{hotel['id']} — "
            f"{hotel['hotel_name']} — "
            f"{hotel['username']}\n"
        )

    text += (
        "\nمثال: أرسل 3"
    )

    await update.message.reply_text(
        text
    )

    return DELETE_HOTEL_ID


async def delete_hotel_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    value = update.message.text.strip()

    if not value.isdigit():

        await update.message.reply_text(

            "❌ يرجى إرسال رقم الفندق فقط.\n\n"
            "مثال: 3"
        )

        return DELETE_HOTEL_ID

    hotel_id = int(value)

    hotel = get_hotel_by_id(
        hotel_id
    )

    if not hotel:

        await update.message.reply_text(
            "❌ لم يتم العثور على هذا الفندق."
        )

        return DELETE_HOTEL_ID

    if not hotel["active"]:

        await update.message.reply_text(

            "⚠️ هذا الفندق موقوف أصلاً.\n\n"
            "يمكنك استخدام قائمة الفنادق للتأكد."
        )

        return ConversationHandler.END

    context.user_data[
        "delete_hotel_id"
    ] = hotel_id

    await update.message.reply_text(

        "⚠️ تأكيد إيقاف الفندق\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n"
        f"👤 المستخدم: {hotel['username']}\n\n"

        "عند التأكيد:\n"
        "🔴 لن يستطيع صاحب الفندق تسجيل الدخول.\n"
        "🔴 سيتم إنهاء جلسة الدخول الحالية إن وجدت.\n"
        "🟢 ستبقى بيانات النزلاء القديمة محفوظة.\n\n"

        "للتأكيد أرسل:\n"
        "نعم\n\n"

        "وللإلغاء أرسل:\n"
        "لا"
    )

    return DELETE_HOTEL_CONFIRM


async def delete_hotel_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    answer = update.message.text.strip().lower()

    hotel_id = context.user_data.get(
        "delete_hotel_id"
    )

    if answer not in [
        "نعم",
        "نعم.",
        "yes",
        "y"
    ]:

        context.user_data.pop(
            "delete_hotel_id",
            None
        )

        await update.message.reply_text(
            "❌ تم إلغاء عملية حذف الفندق."
        )

        return ConversationHandler.END

    if not hotel_id:

        await update.message.reply_text(
            "❌ لم يتم تحديد الفندق."
        )

        return ConversationHandler.END

    hotel = get_hotel_by_id(
        hotel_id
    )

    if not hotel:

        await update.message.reply_text(
            "❌ الفندق غير موجود."
        )

        return ConversationHandler.END

    success = delete_hotel_account(
        hotel_id
    )

    context.user_data.pop(
        "delete_hotel_id",
        None
    )

    if not success:

        await update.message.reply_text(
            "❌ تعذر إيقاف الفندق."
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "🗑 تم إيقاف الفندق بنجاح.\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n"
        f"👤 اسم المستخدم: {hotel['username']}\n\n"

        "🔴 لم يعد بإمكان الحساب تسجيل الدخول.\n\n"

        "📁 بيانات النزلاء السابقة محفوظة "
        "في قاعدة البيانات."
    )

    return ConversationHandler.END


# =========================================================
# قائمة الفنادق
# =========================================================

async def hotels_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الخيار مخصص للإدارة."
        )

        return

    hotels = get_all_hotels()

    if not hotels:

        await update.message.reply_text(
            "📋 لا توجد حسابات فنادق حالياً."
        )

        return

    text = (
        "🏨 قائمة حسابات الفنادق\n\n"
    )

    for hotel in hotels:

        status = (
            "🟢 فعال"
            if hotel["active"]
            else "🔴 موقوف"
        )

        connected = (
            "🔗 مرتبط"
            if hotel["telegram_user_id"]
            else "⚪ غير مسجل"
        )

        text += (

            f"#{hotel['id']}\n"
            f"🏨 {hotel['hotel_name']}\n"
            f"👤 {hotel['username']}\n"
            f"📌 الحالة: {status}\n"
            f"📱 الحساب: {connected}\n"
            f"📅 الإنشاء: {hotel['created_at']}\n\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# وضع PDF مستقل
# =========================================================

async def single_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الخيار مخصص للإدارة."
        )

        return

    context.user_data[
        "pdf_mode"
    ] = "single"

    await update.message.reply_text(

        "📄 تم اختيار وضع الملفات المستقلة.\n\n"

        "كل نزيل سيتم إرساله في ملف PDF مستقل."
    )


# =========================================================
# وضع PDF موحد
# =========================================================

async def all_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الخيار مخصص للإدارة."
        )

        return

    context.user_data[
        "pdf_mode"
    ] = "all"

    await update.message.reply_text(

        "📚 تم اختيار وضع الملف الموحد.\n\n"

        "سيتم جمع النزلاء في ملف PDF واحد."
    )


# =========================================================
# رسم عنوان PDF
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
# رسم حقل PDF
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
# PDF نزيل واحد
# =========================================================

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
                "Image error:",
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
# PDF لجميع النزلاء
# =========================================================

def create_all_guests_pdf(
    guests,
    image_data=None,
    title="تقرير نزلاء الفنادق"
):

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    total = len(guests)

    for index, guest in enumerate(
        guests,
        start=1
    ):

        if index > 1:
            pdf.showPage()

        draw_pdf_header(
            pdf,
            title
        )

        y = PAGE_HEIGHT - 120

        pdf.setFont(
            PDF_FONT,
            13
        )

        pdf.drawRightString(
            PAGE_WIDTH - 50,
            y,
            arabic_text(
                f"النزيل رقم {index} من {total}"
            )
        )

        y -= 35

        for key, value in guest.items():

            if y < 100:

                pdf.showPage()

                draw_pdf_header(
                    pdf,
                    title
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
                    "Image error:",
                    e
                )

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# تحميل صورة Telegram
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
# معالجة رسالة النزيل
# =========================================================

async def process_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.message

    if not message:
        return

    user_id = update.effective_user.id

    # -----------------------------------------
    # معرفة نوع الحساب
    # -----------------------------------------

    account_type = get_account_type(
        user_id
    )

    hotel_account = None

    if account_type == "hotel":

        hotel_account = get_logged_hotel(
            user_id
        )

    elif account_type == "admin":

        hotel_account = None

    else:

        await message.reply_text(

            "🔐 يجب التحقق من الحساب أولاً.\n\n"

            "اضغط /start للمتابعة."
        )

        return

    # -----------------------------------------
    # النص
    # -----------------------------------------

    text = (

        message.text

        if message.text

        else message.caption

        if message.caption

        else ""
    )

    if not text.strip():

        await message.reply_text(

            "❌ لم أجد بيانات نصية.\n\n"

            "قم بإرسال رسالة تحتوي على بيانات النزيل."
        )

        return

    # -----------------------------------------
    # تقسيم النزلاء
    # -----------------------------------------

    guests_text = split_guests(
        text
    )

    if not guests_text:

        await message.reply_text(
            "❌ لم أتمكن من استخراج بيانات النزلاء."
        )

        return

    # -----------------------------------------
    # الصورة
    # -----------------------------------------

    image = await get_photo(
        update
    )

    # -----------------------------------------
    # الوضع
    # -----------------------------------------

    mode = context.user_data.get(
        "pdf_mode",
        DEFAULT_MODE
    )

    guests = []

    # -----------------------------------------
    # معالجة النزلاء
    # -----------------------------------------

    for guest_text in guests_text:

        guest = parse_guest(
            guest_text
        )

        if hotel_account:

            guest[
                "اسم الفندق"
            ] = hotel_account[
                "hotel_name"
            ]

            hotel_id = hotel_account[
                "id"
            ]

        else:

            hotel_id = None

        save_guest(
            guest,
            update,
            hotel_id
        )

        guests.append(
            guest
        )

    # -----------------------------------------
    # ملف مستقل
    # -----------------------------------------

    if mode == "single":

        for guest in guests:

            pdf_file = create_guest_pdf(
                guest,
                image
            )

            guest_name = guest.get(
                "الاسم الثلاثي",
                "تقرير_نزيل"
            )

            filename = safe_filename(
                guest_name
            )

            await message.reply_document(

                document=pdf_file,

                filename=filename,

                caption=(

                    "📋 تم تسجيل بيانات النزيل بنجاح\n\n"

                    f"👤 الاسم: {guest_name}\n"

                    f"🏨 الفندق: "
                    f"{guest.get('اسم الفندق', 'غير مذكور')}\n"

                    f"🚪 الغرفة: "
                    f"{guest.get('رقم الغرفة', 'غير مذكور')}\n\n"

                    "✅ تم حفظ البيانات."
                )
            )

            await asyncio.sleep(
                0.5
            )

    # -----------------------------------------
    # ملف موحد
    # -----------------------------------------

    elif mode == "all":

        pdf_file = create_all_guests_pdf(
            guests,
            image
        )

        filename = (
            f"نزلاء_الفنادق_"
            f"{date.today().isoformat()}.pdf"
        )

        await message.reply_document(

            document=pdf_file,

            filename=filename,

            caption=(

                "📚 تم إنشاء ملف موحد للنزلاء\n\n"

                f"👥 عدد النزلاء: {len(guests)}\n\n"

                "✅ تم حفظ جميع البيانات."
            )
        )

    # -----------------------------------------
    # رسالة النجاح
    # -----------------------------------------

    await message.reply_text(

        f"✅ تمت معالجة {len(guests)} نزيل بنجاح.\n\n"

        "يمكنك إرسال بيانات جديدة مباشرة."
    )


# =========================================================
# التقرير اليومي
# =========================================================

async def daily_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الخيار مخصص للإدارة."
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

    text += (
        "\n🏨 حسب الفندق:\n"
    )

    for name, count in hotels.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    text += (
        "\n🎯 أسباب الإقامة:\n"
    )

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

    filename = (
        f"تقرير_عمل_قسم_معلومات_الفنادق_"
        f"{target_date}.pdf"
    )

    await update.message.reply_document(

        document=pdf_file,

        filename=filename,

        caption=(
            "📋 تم إنشاء التقرير اليومي PDF."
        )
    )


# =========================================================
# التقرير اليومي PDF
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

    y = PAGE_HEIGHT - 120

    draw_pdf_header(
        pdf,
        title
    )

    pdf.setFont(
        PDF_FONT,
        12
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            f"التاريخ: {target_date}"
        )
    )

    y -= 40

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

    rooms = Counter(
        row["room"]
        for row in rows
        if row["room"]
        and row["room"] != "غير مذكور"
    )

    suites = Counter(
        row["suite"]
        for row in rows
        if row["suite"]
        and row["suite"] != "غير مذكور"
    )

    pdf.setFont(
        PDF_FONT,
        14
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            f"إجمالي النزلاء: {total}"
        )
    )

    y -= 40

    def draw_counter_section(
        section_title,
        counter
    ):

        nonlocal y

        if y < 120:

            pdf.showPage()

            draw_pdf_header(
                pdf,
                title
            )

            y = PAGE_HEIGHT - 125

        pdf.setFont(
            PDF_FONT,
            14
        )

        pdf.drawRightString(
            PAGE_WIDTH - 50,
            y,
            arabic_text(
                section_title
            )
        )

        y -= 30

        pdf.setFont(
            PDF_FONT,
            10
        )

        for name, count in counter.most_common():

            if y < 70:

                pdf.showPage()

                draw_pdf_header(
                    pdf,
                    title
                )

                y = PAGE_HEIGHT - 125

            line = f"• {name}: {count}"

            pdf.drawRightString(
                PAGE_WIDTH - 70,
                y,
                arabic_text(line)
            )

            y -= 22

        y -= 12

    draw_counter_section(
        "أولاً: التوزيع حسب المحافظة",
        governorates
    )

    draw_counter_section(
        "ثانياً: توزيع النزلاء على الفنادق",
        hotels
    )

    draw_counter_section(
        "ثالثاً: أسباب الإقامة",
        reasons
    )

    draw_counter_section(
        "رابعاً: أرقام الغرف",
        rooms
    )

    draw_counter_section(
        "خامساً: الأجنحة",
        suites
    )

    if y < 180:

        pdf.showPage()

        draw_pdf_header(
            pdf,
            title
        )

        y = PAGE_HEIGHT - 125

    pdf.setFont(
        PDF_FONT,
        14
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            "سادساً: التحليل والسرد"
        )
    )

    y -= 30

    top_governorate = (
        governorates.most_common(1)[0]
        if governorates
        else ("غير متوفر", 0)
    )

    top_hotel = (
        hotels.most_common(1)[0]
        if hotels
        else ("غير متوفر", 0)
    )

    top_reason = (
        reasons.most_common(1)[0]
        if reasons
        else ("غير متوفر", 0)
    )

    narrative = [

        f"بلغ إجمالي عدد النزلاء المسجلين "
        f"خلال يوم {target_date} "
        f"عدد {total} نزيلاً.",

        f"جاءت محافظة {top_governorate[0]} "
        f"في المرتبة الأولى بعدد "
        f"{top_governorate[1]} نزلاء.",

        f"سجل فندق {top_hotel[0]} "
        f"العدد الأكبر من النزلاء بواقع "
        f"{top_hotel[1]} نزلاء.",

        f"وكان سبب الإقامة الأكثر تكراراً "
        f"هو {top_reason[0]} بعدد "
        f"{top_reason[1]} نزلاء.",

        "وتعكس البيانات حركة النزلاء "
        "وتوزعهم على الفنادق والمحافظات "
        "وأسباب الإقامة."
    ]

    pdf.setFont(
        PDF_FONT,
        10
    )

    for paragraph in narrative:

        words = paragraph.split()

        lines = []

        current = ""

        for word in words:

            test = (
                current + " " + word
            ).strip()

            if len(test) > 75:

                if current:
                    lines.append(
                        current
                    )

                current = word

            else:

                current = test

        if current:
            lines.append(
                current
            )

        for line in lines:

            if y < 60:

                pdf.showPage()

                draw_pdf_header(
                    pdf,
                    title
                )

                y = PAGE_HEIGHT - 125

            pdf.drawRightString(
                PAGE_WIDTH - 50,
                y,
                arabic_text(line)
            )

            y -= 20

        y -= 10

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# تقرير أمس
# =========================================================

async def yesterday_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الخيار مخصص للإدارة."
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

            f"📋 لا توجد بيانات بتاريخ "
            f"{yesterday}."
        )

        return

    pdf_file = create_daily_pdf(
        rows,
        yesterday
    )

    filename = (
        f"تقرير_قسم_معلومات_الفنادق_"
        f"{yesterday}.pdf"
    )

    await update.message.reply_document(

        document=pdf_file,

        filename=filename,

        caption=(
            "📋 تقرير قسم معلومات الفنادق\n"
            f"📅 {yesterday}"
        )
    )


# =========================================================
# التقرير الشهري
# =========================================================

async def monthly_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not admin_logged(update):

        await update.message.reply_text(
            "⛔ هذا الخيار مخصص للإدارة."
        )

        return

    current_month = (
        date.today()
        .strftime("%Y-%m")
    )

    rows = get_guests_by_month(
        current_month
    )

    if not rows:

        await update.message.reply_text(

            "📋 لا توجد بيانات مسجلة "
            "خلال الشهر الحالي."
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

        f"📅 الشهر: {current_month}\n\n"

        f"👥 إجمالي النزلاء: {total}\n\n"

        "🏠 حسب المحافظة:\n"
    )

    for name, count in governorates.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    text += (
        "\n🏨 حسب الفندق:\n"
    )

    for name, count in hotels.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    text += (
        "\n🎯 حسب سبب الإقامة:\n"
    )

    for name, count in reasons.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    await update.message.reply_text(
        text
    )

    pdf_file = create_daily_pdf(
        rows,
        current_month,
        title="التقرير الشهري لقسم معلومات الفنادق"
    )

    filename = (
        f"تقرير_قسم_معلومات_الفنادق_"
        f"{current_month}.pdf"
    )

    await update.message.reply_document(

        document=pdf_file,

        filename=filename,

        caption=(
            "📊 تم إنشاء التقرير الشهري PDF."
        )
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
# إنشاء التطبيق
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
# تسجيل الدخول الموحد
# =========================================================

login_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "start",
            unified_login_start
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
# حذف الفندق
# =========================================================

delete_hotel_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "delete_hotel",
            delete_hotel_start
        )
    ],

    states={

        DELETE_HOTEL_ID: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                delete_hotel_id
            )
        ],

        DELETE_HOTEL_CONFIRM: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                delete_hotel_confirm
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
# الأوامر
# =========================================================

app.add_handler(
    login_handler
)

app.add_handler(
    add_hotel_handler
)

app.add_handler(
    delete_hotel_handler
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
        "single",
        single_mode
    )
)

app.add_handler(
    CommandHandler(
        "all",
        all_mode
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
# استقبال رسائل الفنادق والمدير
# =========================================================

app.add_handler(

    MessageHandler(

        (
            filters.TEXT
            |
            filters.PHOTO
        )
        &
        ~filters.COMMAND,

        process_message
    )
)


# =========================================================
# MAIN
# =========================================================

async def main():

    # -----------------------------------------
    # قاعدة البيانات
    # -----------------------------------------

    init_database()

    # -----------------------------------------
    # التحقق من التوكن
    # -----------------------------------------

    if not TOKEN:

        print(
            "ERROR: BOT_TOKEN is not set!"
        )

        return

    # -----------------------------------------
    # التحقق من بيانات المدير
    # -----------------------------------------

    if not ADMIN_USERNAME:

        print(
            "WARNING: ADMIN_USERNAME is empty!"
        )

    if not ADMIN_PASSWORD:

        print(
            "WARNING: ADMIN_PASSWORD is empty!"
        )

    # -----------------------------------------
    # خادم Render
    # -----------------------------------------

    threading.Thread(

        target=run_web_server,

        daemon=True

    ).start()

    # -----------------------------------------
    # Telegram
    # -----------------------------------------

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
# START PROGRAM
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
