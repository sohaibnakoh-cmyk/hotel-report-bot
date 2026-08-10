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

TOKEN = os.getenv("BOT_TOKEN")

# رقم Telegram ID للمدير
ADMIN_TELEGRAM_ID = os.getenv(
    "ADMIN_TELEGRAM_ID",
    ""
).strip()

DATABASE_FILE = "hotel_reports.db"
IMAGE_FILE = "images.png"

PAGE_WIDTH, PAGE_HEIGHT = A4

DEFAULT_MODE = "single"


# =========================================================
# حالات تسجيل الدخول
# =========================================================

LOGIN_USERNAME = 1
LOGIN_PASSWORD = 2

ADD_HOTEL_USERNAME = 3
ADD_HOTEL_PASSWORD = 4
ADD_HOTEL_NAME = 5


# =========================================================
# حالات نموذج النزيل
# =========================================================

GUEST_NAME = 10
GUEST_MOTHER = 11
GUEST_BIRTH = 12
GUEST_HOME = 13
GUEST_GOVERNORATE = 14
GUEST_SUITE = 15
GUEST_ROOM = 16
GUEST_CHECKIN = 17
GUEST_DURATION = 18
GUEST_REASON = 19

GUEST_PHOTO_1 = 20
GUEST_PHOTO_2 = 21
GUEST_PHOTO_3 = 22


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

        print("Arabic font error:", e)

        PDF_FONT = "Helvetica"

else:

    PDF_FONT = "Helvetica"


# =========================================================
# النص العربي
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

    connection.commit()

    connection.close()

    print("Database initialized successfully.")


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
# التحقق من المدير
# =========================================================

def is_admin(update):

    if not update.effective_user:
        return False

    if not ADMIN_TELEGRAM_ID:
        return False

    return str(
        update.effective_user.id
    ) == ADMIN_TELEGRAM_ID


# =========================================================
# حالة المدير
# =========================================================

def admin_logged(update, context):

    return is_admin(update)


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
            photo1,
            photo2,
            photo3,
            record_date,
            record_time,
            telegram_user_id,
            telegram_username,
            hotel_account_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            guest.get("photo1"),
            guest.get("photo2"),
            guest.get("photo3"),
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

def get_guests_by_date(target_date):

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


def get_guests_by_month(year_month):

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
# حذف/تعطيل فندق
# =========================================================

async def delete_hotel_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not is_admin(update):
        return

    data = query.data

    if data.startswith("delete_hotel_"):

        hotel_id = int(
            data.replace(
                "delete_hotel_",
                ""
            )
        )

        disable_hotel(
            hotel_id
        )

        await query.edit_message_text(
            "✅ تم تعطيل حساب الفندق.\n\n"
            "لن يستطيع صاحب الفندق تسجيل الدخول "
            "حتى يتم إعادة تفعيله."
        )


    elif data.startswith("enable_hotel_"):

        hotel_id = int(
            data.replace(
                "enable_hotel_",
                ""
            )
        )

        enable_hotel(
            hotel_id
        )

        await query.edit_message_text(
            "✅ تم إعادة تفعيل حساب الفندق."
        )


# =========================================================
# الخادم
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
        (
            "0.0.0.0",
            port
        ),
        HealthHandler
    )

    server.serve_forever()


# =========================================================
# صورة الترحيب
# =========================================================

async def send_welcome_image(update):

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
            "🏠 بدء"
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
            "new_guest",
            "👤 تسجيل نزيل"
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
            "single",
            "📄 ملف مستقل"
        ),

        BotCommand(
            "all",
            "📚 ملف موحد"
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

    # -----------------------------------------------------
    # المدير
    # -----------------------------------------------------

    if is_admin(update):

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(
            update
        )

        await update.message.reply_text(

            "السلام عليكم ورحمة الله وبركاته 🌿\n\n"

            "بسم الله الرحمن الرحيم\n"
            "﴿ وَقُلْ رَبِّ زِدْنِي عِلْمًا ﴾\n\n"

            "🤍 أهلاً وسهلاً بك في\n"
            "🏨 نظام معلومات الفنادق\n\n"

            "نسأل الله أن يوفقنا جميعاً لما فيه "
            "الخير، وأن يجعل هذا العمل عوناً على "
            "تنظيم المعلومات وحفظها بدقة.\n\n"

            "👨‍💼 تم التعرف على حسابك كحساب إدارة.\n\n"

            "يمكنك الآن استخدام أدوات الإدارة "
            "من قائمة الأوامر."
        )

        return

    # -----------------------------------------------------
    # الفندق المسجل
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

            "السلام عليكم ورحمة الله وبركاته 🌿\n\n"

            "بسم الله نبدأ، وعلى الله نتوكل 🤍\n\n"

            f"🏨 أهلاً وسهلاً بك\n"
            f"فندق: {hotel['hotel_name']}\n\n"

            "نسأل الله أن يوفقكم ويسدد خطاكم.\n\n"

            "يمكنك الآن تسجيل بيانات النزيل "
            "من خلال:\n"
            "👤 /new_guest\n\n"

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

        "السلام عليكم ورحمة الله وبركاته 🌿\n\n"

        "بسم الله الرحمن الرحيم\n"
        "﴿ وَتَعَاوَنُوا عَلَى الْبِرِّ وَالتَّقْوَى ﴾\n\n"

        "🏨 أهلاً وسهلاً ومرحباً بكم\n"
        "في نظام معلومات الفنادق.\n\n"

        "نسأل الله أن يبارك في هذا العمل "
        "وأن يجعله نافعاً وميسراً للجميع.\n\n"

        "🔐 للبدء يرجى اختيار:\n"
        "/login\n\n"

        "وبعد تسجيل الدخول لن يطلب منك "
        "اسم المستخدم وكلمة المرور مرة أخرى "
        "حتى تقوم بتسجيل الخروج."
    )


# =========================================================
# تسجيل دخول الفندق
# =========================================================

async def login_start(
    update,
    context
):

    if is_admin(update):

        await update.message.reply_text(
            "👨‍💼 تم التعرف على حساب الإدارة تلقائياً."
        )

        return ConversationHandler.END

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if hotel:

        await update.message.reply_text(

            f"✅ أنت مسجل الدخول بالفعل.\n\n"
            f"🏨 الفندق: {hotel['hotel_name']}\n\n"
            "يمكنك تسجيل نزيل جديد."
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "🔐 تسجيل الدخول\n\n"
        "يرجى إرسال اسم المستخدم:"
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
            "❌ حدث خطأ.\n"
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

            "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"
            "يرجى المحاولة من جديد باستخدام /login."
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

    await set_logged_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await update.message.reply_text(

        "🌿 الحمد لله\n\n"

        "✅ تم تسجيل الدخول بنجاح.\n\n"

        f"🏨 الفندق: {account['hotel_name']}\n\n"

        "نسأل الله أن يوفقكم.\n\n"

        "يمكنك الآن البدء بتسجيل بيانات النزيل "
        "من خلال:\n\n"

        "👤 /new_guest\n\n"

        "وسيظهر لك النموذج سؤالاً سؤالاً."
    )

    return ConversationHandler.END


# =========================================================
# تسجيل الخروج
# =========================================================

async def logout(
    update,
    context
):

    user_id = update.effective_user.id

    if is_admin(update):

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await update.message.reply_text(
            "🚪 تم تسجيل الخروج من جلسة الإدارة."
        )

        return

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

        "جزاكم الله خيراً وبارك الله في عملكم.\n\n"

        "🔐 عند العودة استخدم /login."
    )


# =========================================================
# إضافة فندق
# =========================================================

async def add_hotel_start(
    update,
    context
):

    if not admin_logged(
        update,
        context
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "🏨 إضافة حساب فندق جديد\n\n"

        "أرسل اسم المستخدم للفندق:"
    )

    return ADD_HOTEL_USERNAME


async def add_hotel_username(
    update,
    context
):

    username = update.message.text.strip().lower()

    if not re.match(
        r"^[a-zA-Z0-9_.-]{3,50}$",
        username
    ):

        await update.message.reply_text(

            "❌ اسم المستخدم غير صالح.\n\n"
            "استخدم أحرفاً إنجليزية وأرقاماً "
            "أو النقطة أو الشرطة."
        )

        return ADD_HOTEL_USERNAME

    context.user_data[
        "new_hotel_username"
    ] = username

    await update.message.reply_text(
        "🔑 أرسل كلمة المرور:\n\n"
        "يفضل أن تكون 8 أحرف على الأقل."
    )

    return ADD_HOTEL_PASSWORD


async def add_hotel_password(
    update,
    context
):

    password = update.message.text.strip()

    if len(password) < 8:

        await update.message.reply_text(
            "❌ كلمة المرور قصيرة.\n"
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
    update,
    context
):

    hotel_name = update.message.text.strip()

    username = context.user_data.get(
        "new_hotel_username"
    )

    password = context.user_data.get(
        "new_hotel_password"
    )

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

        "🔐 كلمة المرور محفوظة بشكل مشفر.\n\n"

        "يمكنك إعطاء بيانات الدخول لصاحب الفندق."
    )

    return ConversationHandler.END


# =========================================================
# قائمة الفنادق
# =========================================================

async def hotels_list(
    update,
    context
):

    if not admin_logged(
        update,
        context
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return

    hotels = get_all_hotels()

    if not hotels:

        await update.message.reply_text(
            "📋 لا توجد فنادق."
        )

        return

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

        keyboard = []

        if hotel["active"]:

            keyboard.append([
                InlineKeyboardButton(
                    "🗑 تعطيل الفندق",
                    callback_data=(
                        f"delete_hotel_{hotel['id']}"
                    )
                )
            ])

        else:

            keyboard.append([
                InlineKeyboardButton(
                    "♻️ إعادة تفعيل",
                    callback_data=(
                        f"enable_hotel_{hotel['id']}"
                    )
                )
            ])

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
# نموذج النزيل
# =========================================================

async def new_guest_start(
    update,
    context
):

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if not hotel:

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً باستخدام /login."
        )

        return ConversationHandler.END

    context.user_data[
        "guest_form"
    ] = {
        "اسم الفندق": hotel["hotel_name"]
    }

    context.user_data[
        "guest_photos"
    ] = {}

    await update.message.reply_text(

        "🌿 بسم الله نبدأ\n\n"

        "👤 تسجيل بيانات نزيل جديد\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n\n"

        "سأرسل لك الأسئلة واحداً تلو الآخر.\n"
        "يرجى تعبئة كل سؤال بدقة.\n\n"

        "نبدأ الآن 👇\n\n"

        "1️⃣ ما الاسم الثلاثي للنزيل؟"
    )

    return GUEST_NAME


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
        "3️⃣ ما مكان وتاريخ الولادة؟"
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

    await update.message.reply_text(
        "6️⃣ ما رقم الجناح؟\n\n"
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
        "7️⃣ ما رقم الغرفة؟"
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
        "8️⃣ ما تاريخ النزول؟"
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
        "9️⃣ ما مدة الإقامة؟"
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
        "🔟 ما سبب الإقامة؟"
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

        "📷 الآن ننتقل إلى صور النزيل.\n\n"

        "الصورة الأولى **إلزامية**.\n\n"
        "أرسل الصورة الأولى الآن."
    )

    return GUEST_PHOTO_1


# =========================================================
# الصورة الأولى
# =========================================================

async def guest_photo_1(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ يجب إرسال صورة.\n\n"
            "الصورة الأولى إلزامية."
        )

        return GUEST_PHOTO_1

    photo = update.message.photo[-1]

    telegram_file = await photo.get_file()

    image_buffer = BytesIO()

    await telegram_file.download_to_memory(
        image_buffer
    )

    context.user_data[
        "guest_photos"
    ]["photo1"] = image_buffer.getvalue()

    await update.message.reply_text(

        "✅ تم استلام الصورة الأولى.\n\n"

        "📷 أرسل الصورة الثانية.\n"
        "الصورة الثانية **إلزامية**."
    )

    return GUEST_PHOTO_2


# =========================================================
# الصورة الثانية
# =========================================================

async def guest_photo_2(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ يجب إرسال صورة.\n\n"
            "الصورة الثانية إلزامية."
        )

        return GUEST_PHOTO_2

    photo = update.message.photo[-1]

    telegram_file = await photo.get_file()

    image_buffer = BytesIO()

    await telegram_file.download_to_memory(
        image_buffer
    )

    context.user_data[
        "guest_photos"
    ]["photo2"] = image_buffer.getvalue()

    keyboard = [

        [
            InlineKeyboardButton(
                "📷 إرسال الصورة الثالثة",
                callback_data="photo3_yes"
            )
        ],

        [
            InlineKeyboardButton(
                "⏭ تخطي الصورة الثالثة",
                callback_data="photo3_skip"
            )
        ]
    ]

    await update.message.reply_text(

        "✅ تم استلام الصورة الثانية.\n\n"

        "📷 الصورة الثالثة اختيارية.\n\n"

        "يمكنك إرسالها أو تخطيها.",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    return GUEST_PHOTO_3


# =========================================================
# الصورة الثالثة
# =========================================================

async def guest_photo_3(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ أرسل صورة ثالثة أو استخدم زر التخطي."
        )

        return GUEST_PHOTO_3

    photo = update.message.photo[-1]

    telegram_file = await photo.get_file()

    image_buffer = BytesIO()

    await telegram_file.download_to_memory(
        image_buffer
    )

    context.user_data[
        "guest_photos"
    ]["photo3"] = image_buffer.getvalue()

    await show_guest_confirmation(
        update,
        context
    )

    return GUEST_PHOTO_3


# =========================================================
# تخطي الصورة الثالثة
# =========================================================

async def photo3_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not get_logged_hotel(
        update.effective_user.id
    ):
        return

    if query.data == "photo3_skip":

        context.user_data[
            "guest_photos"
        ]["photo3"] = None

        await query.edit_message_text(
            "⏭ تم تخطي الصورة الثالثة."
        )

        await show_guest_confirmation(
            update,
            context,
            from_callback=True
        )


    elif query.data == "photo3_yes":

        await query.edit_message_text(
            "📷 أرسل الصورة الثالثة الآن.\n\n"
            "هذه الصورة اختيارية."
        )


# =========================================================
# عرض البيانات قبل الإرسال
# =========================================================

async def show_guest_confirmation(
    update,
    context,
    from_callback=False
):

    guest = context.user_data.get(
        "guest_form",
        {}
    )

    photos = context.user_data.get(
        "guest_photos",
        {}
    )

    text = (

        "📋 **مراجعة بيانات النزيل**\n\n"

        f"👤 الاسم: {guest.get('الاسم الثلاثي', '')}\n"
        f"👩 اسم الأم: {guest.get('اسم الأم', '')}\n"
        f"🎂 الولادة: {guest.get('مكان وتاريخ الولادة', '')}\n"
        f"🏠 السكن: {guest.get('السكن الأصلي', '')}\n"
        f"📍 المحافظة: {guest.get('المحافظة', '')}\n"
        f"🏨 الفندق: {guest.get('اسم الفندق', '')}\n"
        f"🚪 الجناح: {guest.get('رقم الجناح', '')}\n"
        f"🚪 الغرفة: {guest.get('رقم الغرفة', '')}\n"
        f"📅 تاريخ النزول: {guest.get('تاريخ النزول', '')}\n"
        f"⏱ مدة الإقامة: {guest.get('مدة الإقامة', '')}\n"
        f"🎯 سبب الإقامة: {guest.get('سبب الإقامة', '')}\n\n"

        f"📷 الصورة الأولى: "
        f"{'✅' if photos.get('photo1') else '❌'}\n"

        f"📷 الصورة الثانية: "
        f"{'✅' if photos.get('photo2') else '❌'}\n"

        f"📷 الصورة الثالثة: "
        f"{'✅' if photos.get('photo3') else '⚪ اختيارية'}\n\n"

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

    markup = InlineKeyboardMarkup(
        keyboard
    )

    if from_callback:

        await update.callback_query.message.reply_text(
            text,
            reply_markup=markup
        )

    else:

        await update.message.reply_text(
            text,
            reply_markup=markup
        )


# =========================================================
# إرسال المعلومات للإدارة
# =========================================================

async def submit_guest_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if not hotel:

        await query.message.reply_text(
            "🔐 انتهت جلسة الدخول.\n"
            "يرجى تسجيل الدخول من جديد."
        )

        return ConversationHandler.END

    guest = context.user_data.get(
        "guest_form"
    )

    photos = context.user_data.get(
        "guest_photos",
        {}
    )

    if not guest:

        await query.message.reply_text(
            "❌ لا توجد بيانات نزيل."
        )

        return ConversationHandler.END

    if not photos.get("photo1"):

        await query.message.reply_text(
            "❌ الصورة الأولى إلزامية."
        )

        return GUEST_PHOTO_1

    if not photos.get("photo2"):

        await query.message.reply_text(
            "❌ الصورة الثانية إلزامية."
        )

        return GUEST_PHOTO_2

    guest["photo1"] = photos.get(
        "photo1"
    )

    guest["photo2"] = photos.get(
        "photo2"
    )

    guest["photo3"] = photos.get(
        "photo3"
    )

    guest_id = save_guest(
        guest,
        update,
        hotel["id"]
    )

    # -----------------------------------------------------
    # إرسال نسخة إلى المدير
    # -----------------------------------------------------

    if ADMIN_TELEGRAM_ID:

        try:

            admin_id = int(
                ADMIN_TELEGRAM_ID
            )

            admin_text = (

                "📥 **وصلت معلومات نزيل جديدة**\n\n"

                f"🏨 الفندق: {hotel['hotel_name']}\n"
                f"👤 الاسم: {guest.get('الاسم الثلاثي')}\n"
                f"👩 اسم الأم: {guest.get('اسم الأم')}\n"
                f"🎂 الولادة: {guest.get('مكان وتاريخ الولادة')}\n"
                f"🏠 السكن: {guest.get('السكن الأصلي')}\n"
                f"📍 المحافظة: {guest.get('المحافظة')}\n"
                f"🚪 الجناح: {guest.get('رقم الجناح')}\n"
                f"🚪 الغرفة: {guest.get('رقم الغرفة')}\n"
                f"📅 تاريخ النزول: {guest.get('تاريخ النزول')}\n"
                f"⏱ مدة الإقامة: {guest.get('مدة الإقامة')}\n"
                f"🎯 سبب الإقامة: {guest.get('سبب الإقامة')}\n\n"

                f"🆔 رقم السجل: {guest_id}"
            )

            await context.application.bot.send_message(
                chat_id=admin_id,
                text=admin_text
            )

            # الصورة الأولى
            if guest.get("photo1"):

                await context.application.bot.send_photo(
                    chat_id=admin_id,
                    photo=BytesIO(
                        guest["photo1"]
                    ),
                    caption=(
                        f"📷 الصورة الأولى\n"
                        f"🏨 {hotel['hotel_name']}\n"
                        f"👤 {guest.get('الاسم الثلاثي')}"
                    )
                )

            # الصورة الثانية
            if guest.get("photo2"):

                await context.application.bot.send_photo(
                    chat_id=admin_id,
                    photo=BytesIO(
                        guest["photo2"]
                    ),
                    caption=(
                        f"📷 الصورة الثانية\n"
                        f"🏨 {hotel['hotel_name']}\n"
                        f"👤 {guest.get('الاسم الثلاثي')}"
                    )
                )

            # الصورة الثالثة
            if guest.get("photo3"):

                await context.application.bot.send_photo(
                    chat_id=admin_id,
                    photo=BytesIO(
                        guest["photo3"]
                    ),
                    caption=(
                        f"📷 الصورة الثالثة\n"
                        f"🏨 {hotel['hotel_name']}\n"
                        f"👤 {guest.get('الاسم الثلاثي')}"
                    )
                )

        except Exception as e:

            print(
                "Admin notification error:",
                e
            )

    # -----------------------------------------------------
    # إنهاء النموذج
    # -----------------------------------------------------

    context.user_data.pop(
        "guest_form",
        None
    )

    context.user_data.pop(
        "guest_photos",
        None
    )

    await query.message.reply_text(

        "الحمد لله 🤍\n\n"

        "✅ تم إرسال معلومات النزيل إلى الإدارة بنجاح.\n\n"

        f"🆔 رقم السجل: {guest_id}\n\n"

        "يمكنك الآن تسجيل نزيل جديد من خلال:\n"
        "/new_guest"
    )

    return ConversationHandler.END


# =========================================================
# إلغاء نموذج النزيل
# =========================================================

async def cancel_guest_callback(
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

    await query.edit_message_text(
        "❌ تم إلغاء تسجيل بيانات النزيل."
    )

    return ConversationHandler.END


# =========================================================
# التقرير اليومي
# =========================================================

async def daily_report(
    update,
    context
):

    if not admin_logged(
        update,
        context
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
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

        "🏠 المحافظات:\n"
    )

    for name, count in governorates.most_common():

        text += f"• {name}: {count}\n"

    text += "\n🏨 الفنادق:\n"

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

    if not admin_logged(
        update,
        context
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
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
        f"📅 {yesterday}\n"
        f"👥 عدد النزلاء: {len(rows)}"
    )


# =========================================================
# التقرير الشهري
# =========================================================

async def monthly_report(
    update,
    context
):

    if not admin_logged(
        update,
        context
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
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

    hotels = Counter(
        row["hotel"]
        for row in rows
    )

    governorates = Counter(
        row["governorate"]
        for row in rows
    )

    text = (

        "📊 التقرير الشهري\n\n"

        f"📅 الشهر: {current_month}\n"
        f"👥 إجمالي النزلاء: {len(rows)}\n\n"

        "🏨 حسب الفندق:\n"
    )

    for name, count in hotels.most_common():

        text += f"• {name}: {count}\n"

    text += "\n📍 حسب المحافظة:\n"

    for name, count in governorates.most_common():

        text += f"• {name}: {count}\n"

    await update.message.reply_text(
        text
    )


# =========================================================
# وضع الملفات
# =========================================================

async def single_mode(
    update,
    context
):

    if not admin_logged(
        update,
        context
    ):
        return

    context.user_data[
        "pdf_mode"
    ] = "single"

    await update.message.reply_text(
        "📄 تم اختيار وضع الملفات المستقلة."
    )


async def all_mode(
    update,
    context
):

    if not admin_logged(
        update,
        context
    ):
        return

    context.user_data[
        "pdf_mode"
    ] = "all"

    await update.message.reply_text(
        "📚 تم اختيار وضع الملف الموحد."
    )


# =========================================================
# إلغاء المحادثة
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
# تسجيل دخول الفندق
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
    hotel_login_handler
)

app.add_handler(
    add_hotel_handler
)

app.add_handler(
    guest_form_handler
)

app.add_handler(
    CallbackQueryHandler(
        submit_guest_callback,
        pattern="^submit_guest$"
    )
)

app.add_handler(
    CallbackQueryHandler(
        cancel_guest_callback,
        pattern="^cancel_guest$"
    )
)

app.add_handler(
    CallbackQueryHandler(
        photo3_callback,
        pattern="^photo3_"
    )
)

app.add_handler(
    CallbackQueryHandler(
        delete_hotel_callback,
        pattern="^(delete_hotel_|enable_hotel_)"
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
        "new_guest",
        new_guest_start
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


# =========================================================
# استقبال الرسائل غير المرتبطة بالنموذج
# =========================================================

async def unknown_message(
    update,
    context
):

    if not update.message:
        return

    if is_admin(update):

        await update.message.reply_text(
            "👨‍💼 استخدم قائمة أوامر الإدارة."
        )

        return

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if hotel:

        await update.message.reply_text(

            "🏨 أنت مسجل الدخول.\n\n"

            "لتسجيل نزيل جديد استخدم:\n"
            "/new_guest"
        )

    else:

        await update.message.reply_text(

            "🔐 يرجى تسجيل الدخول أولاً:\n"
            "/login"
        )


app.add_handler(
    MessageHandler(
        filters.ALL & ~filters.COMMAND,
        unknown_message
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

    if not ADMIN_TELEGRAM_ID:

        print(
            "WARNING: ADMIN_TELEGRAM_ID is not set!"
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
