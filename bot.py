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

DEFAULT_MODE = "single"


# =========================================================
# حالات تسجيل دخول الفندق
# =========================================================

LOGIN_USERNAME = 1
LOGIN_PASSWORD = 2


# =========================================================
# حالات نموذج النزيل
# =========================================================

GUEST_NAME = 10
GUEST_MOTHER = 11
GUEST_BIRTH = 12
GUEST_HOME = 13
GUEST_GOVERNORATE = 14
GUEST_ROOM = 15
GUEST_SUITE = 16
GUEST_CHECKIN = 17
GUEST_DURATION = 18
GUEST_REASON = 19

GUEST_PHOTO_1 = 20
GUEST_PHOTO_2 = 21
GUEST_PHOTO_3 = 22


# =========================================================
# حالات إنشاء حساب الفندق
# =========================================================

ADD_HOTEL_USERNAME = 30
ADD_HOTEL_PASSWORD = 31
ADD_HOTEL_NAME = 32


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

        print("Arabic font error:", e)

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

    print("Database initialized successfully")


# =========================================================
# تشفير كلمات المرور
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


def verify_password(password, stored_hash, salt):

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


def delete_hotel(hotel_id):

    connection = get_db()

    cursor = connection.cursor()

    # حذف الجلسات المرتبطة
    cursor.execute(
        """
        DELETE FROM sessions
        WHERE hotel_account_id = ?
        """,
        (hotel_id,)
    )

    # تعطيل الحساب بدلاً من حذف السجلات نهائياً
    cursor.execute(
        """
        UPDATE hotel_accounts
        SET active = 0,
            telegram_user_id = NULL
        WHERE id = ?
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
# جلسات الفنادق
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


def logout_session(telegram_user_id):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT hotel_account_id
        FROM sessions
        WHERE telegram_user_id = ?
        """,
        (str(telegram_user_id),)
    )

    row = cursor.fetchone()

    if row:

        cursor.execute(
            """
            UPDATE hotel_accounts
            SET telegram_user_id = NULL
            WHERE id = ?
            """,
            (row["hotel_account_id"],)
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


def get_logged_hotel(telegram_user_id):

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
        (str(telegram_user_id),)
    )

    account = cursor.fetchone()

    connection.close()

    return account


# =========================================================
# المدير
# =========================================================

def verify_admin_credentials(
    username,
    password
):

    if not ADMIN_USERNAME:
        return False

    if not ADMIN_PASSWORD:
        return False

    return (
        username.strip().lower()
        == ADMIN_USERNAME.strip().lower()
        and
        secrets.compare_digest(
            password.strip(),
            ADMIN_PASSWORD
        )
    )


def is_admin_logged(
    update,
    context
):

    return bool(
        context.user_data.get(
            "admin_logged",
            False
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
# اسم الملف
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

    if not name:
        name = "تقرير_نزيل"

    return name + ".pdf"


# =========================================================
# PDF
# =========================================================

def draw_pdf_header(pdf, title):

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

    pdf.setFillColor(colors.white)

    pdf.setFont(
        PDF_FONT,
        18
    )

    pdf.drawRightString(
        PAGE_WIDTH - 45,
        PAGE_HEIGHT - 45,
        arabic_text(title)
    )

    pdf.setFillColor(colors.black)


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

    pdf.setFillColor(colors.black)

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
    images=None
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

    # الصور
    if images:

        for index, image_data in enumerate(
            images,
            start=1
        ):

            if not image_data:
                continue

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
                        f"صورة النزيل رقم {index}"
                    )

                    y = PAGE_HEIGHT - 125

                pdf.setFont(
                    PDF_FONT,
                    11
                )

                pdf.drawRightString(
                    PAGE_WIDTH - 50,
                    y,
                    arabic_text(
                        f"صورة النزيل رقم {index}"
                    )
                )

                y -= 20

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

                y -= img_height + 30

            except Exception as e:

                print(
                    "Image error:",
                    e
                )

    pdf.setFont(
        PDF_FONT,
        8
    )

    pdf.setFillColor(colors.grey)

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


def create_all_guests_pdf(
    guests,
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

    pdf.save()

    buffer.seek(0)

    return buffer


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
            scope=BotCommandScopeChat(chat_id)
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
            "👤 نزيل جديد"
        ),

        BotCommand(
            "logout",
            "🚪 تسجيل الخروج"
        ),
    ]

    try:

        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(chat_id)
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
            "🗑 حذف/إيقاف فندق"
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
            scope=BotCommandScopeChat(chat_id)
        )

    except Exception as e:

        print(
            "Admin commands error:",
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

    # =====================================================
    # المدير المسجل
    # =====================================================

    if is_admin_logged(
        update,
        context
    ):

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(update)

        await update.message.reply_text(

            "﷽\n\n"

            "السلام عليكم ورحمة الله وبركاته 🌿\n\n"

            "أهلاً وسهلاً بكم في نظام معلومات الفنادق.\n\n"

            "نسأل الله أن يوفقنا وإياكم لما فيه "
            "الخير، وأن يجعل هذا العمل نافعاً ومباركاً.\n\n"

            "👨‍💼 مرحباً بك في لوحة الإدارة.\n\n"

            "يمكنك الآن إدارة حسابات الفنادق "
            "ومتابعة البيانات وإصدار التقارير."
        )

        return

    # =====================================================
    # فحص هل هو مدير بواسطة جلسة سابقة
    # =====================================================

    if context.user_data.get(
        "admin_logged",
        False
    ):

        return

    # =====================================================
    # صاحب الفندق
    # =====================================================

    hotel = get_logged_hotel(user_id)

    if hotel:

        await set_logged_hotel_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(update)

        await update.message.reply_text(

            "﷽\n\n"

            "السلام عليكم ورحمة الله وبركاته 🌿\n\n"

            f"🏨 أهلاً وسهلاً بك\n"
            f"فندق: {hotel['hotel_name']}\n\n"

            "نسعد بتعاونكم معنا في تنظيم بيانات "
            "النزلاء وتسهيل عملية تسجيلها.\n\n"

            "يمكنك الآن اختيار:\n\n"

            "👤 نزيل جديد\n\n"

            "ثم تعبئة النموذج خطوة بخطوة.\n\n"

            "🚪 وعند الانتهاء يمكنك تسجيل الخروج "
            "من خلال /logout."
        )

        return

    # =====================================================
    # مستخدم غير مسجل
    # =====================================================

    await set_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await send_welcome_image(update)

    await update.message.reply_text(

        "﷽\n\n"

        "السلام عليكم ورحمة الله وبركاته 🌿\n\n"

        "أهلاً وسهلاً ومرحباً بكم في\n"
        "🏨 نظام معلومات الفنادق\n\n"

        "قال الله تعالى:\n"
        "﴿وَقُلِ اعْمَلُوا فَسَيَرَى اللَّهُ "
        "عَمَلَكُمْ وَرَسُولُهُ وَالْمُؤْمِنُونَ﴾\n\n"

        "هذا النظام مخصص لتنظيم بيانات النزلاء "
        "وتسهيل إرسالها بطريقة واضحة ومنظمة.\n\n"

        "🔐 للبدء يرجى اختيار:\n"
        "/login"
    )


# =========================================================
# تسجيل دخول الفندق
# =========================================================

async def login_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # إذا كان مديراً مسجلاً
    if is_admin_logged(
        update,
        context
    ):

        await update.message.reply_text(
            "👨‍💼 أنت تستخدم حساب الإدارة."
        )

        return ConversationHandler.END

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if hotel:

        await update.message.reply_text(

            "✅ أنت مسجل الدخول بالفعل.\n\n"
            f"🏨 الفندق: {hotel['hotel_name']}"
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

    context.user_data[
        "login_username"
    ] = username

    await update.message.reply_text(
        "🔑 الآن أرسل كلمة المرور:"
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
            "❌ حدث خطأ. استخدم /login من جديد."
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

            "يرجى استخدام /login للمحاولة من جديد."
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

        "✅ تم تسجيل الدخول بنجاح\n\n"

        f"🏨 الفندق: {account['hotel_name']}\n\n"

        "بارك الله في تعاونكم 🌿\n\n"

        "يمكنك الآن البدء بتسجيل بيانات النزيل "
        "من خلال:\n\n"

        "👤 /new_guest\n\n"

        "وسأطلب منك البيانات سؤالاً سؤالاً."
    )

    return ConversationHandler.END


# =========================================================
# تسجيل الخروج
# =========================================================

async def logout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # إذا كان مديراً
    if context.user_data.get(
        "admin_logged",
        False
    ):

        context.user_data.clear()

        await set_hotel_commands(
            context.application,
            update.effective_chat.id
        )

        await update.message.reply_text(

            "🚪 تم تسجيل خروج المدير بنجاح.\n\n"
            "للدخول مجدداً استخدم /start."
        )

        return

    # الفندق
    logout_session(
        update.effective_user.id
    )

    context.user_data.clear()

    await set_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await update.message.reply_text(

        "🚪 تم تسجيل الخروج بنجاح.\n\n"

        "🔐 عند العودة يمكنك استخدام /login."
    )


# =========================================================
# إضافة فندق
# =========================================================

async def add_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(
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

            "استخدم أحرفاً إنجليزية وأرقاماً "
            "أو النقطة أو الشرطة فقط."
        )

        return ADD_HOTEL_USERNAME

    context.user_data[
        "new_hotel_username"
    ] = username

    await update.message.reply_text(

        "🔑 أرسل كلمة المرور للحساب:\n\n"

        "يفضل أن تكون قوية ولا تقل عن 8 أحرف."
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

    if not username or not password:

        await update.message.reply_text(
            "❌ انتهت جلسة إضافة الفندق. حاول من جديد."
        )

        return ConversationHandler.END

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

        "يمكنك الآن إعطاء بيانات الدخول لصاحب الفندق."
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
            "📋 لا توجد حسابات فنادق حالياً."
        )

        return

    text = "🏨 قائمة حسابات الفنادق\n\n"

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
            f"📌 {status}\n"
            f"📱 {connected}\n"
            f"📅 {hotel['created_at']}\n\n"
        )

    await update.message.reply_text(text)


# =========================================================
# حذف/إيقاف فندق
# =========================================================

async def delete_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(
        update,
        context
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return

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

        return

    buttons = []

    for hotel in active_hotels:

        buttons.append(
            [
                InlineKeyboardButton(
                    f"🗑 {hotel['hotel_name']} "
                    f"({hotel['username']})",
                    callback_data=f"delete_hotel:{hotel['id']}"
                )
            ]
        )

    keyboard = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(

        "🗑 حذف / إيقاف فندق\n\n"

        "اختر الفندق الذي تريد إيقاف حسابه:\n\n"

        "⚠️ بعد الإيقاف لن يستطيع صاحب الفندق "
        "تسجيل الدخول بالحساب.",

        reply_markup=keyboard
    )


async def delete_hotel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if not is_admin_logged(
        update,
        context
    ):

        await query.edit_message_text(
            "⛔ غير مصرح."
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

    delete_hotel(hotel_id)

    await query.edit_message_text(

        "✅ تم إيقاف حساب الفندق بنجاح.\n\n"

        "🔐 لن يستطيع صاحب الفندق تسجيل الدخول "
        "إلى هذا الحساب."
    )


# =========================================================
# بدء نموذج النزيل
# =========================================================

async def new_guest_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if not hotel:

        await update.message.reply_text(

            "🔐 يجب تسجيل الدخول أولاً.\n\n"
            "استخدم /login"
        )

        return ConversationHandler.END

    # تنظيف بيانات النموذج السابق
    context.user_data[
        "guest_form"
    ] = {}

    context.user_data[
        "guest_images"
    ] = []

    await update.message.reply_text(

        "🌿 بسم الله نبدأ\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n\n"

        "سيتم الآن تسجيل بيانات النزيل "
        "خطوة بخطوة.\n\n"

        "يرجى الإجابة عن كل سؤال بدقة.\n\n"

        "👤 السؤال الأول:\n\n"
        "ما هو الاسم الثلاثي للنزيل؟"
    )

    return GUEST_NAME


async def guest_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "guest_form"
    ]["الاسم الثلاثي"] = update.message.text.strip()

    await update.message.reply_text(
        "👩 السؤال الثاني:\n\n"
        "ما اسم الأم؟"
    )

    return GUEST_MOTHER


async def guest_mother(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "guest_form"
    ]["اسم الأم"] = update.message.text.strip()

    await update.message.reply_text(

        "📅 السؤال الثالث:\n\n"
        "ما مكان وتاريخ الولادة؟"
    )

    return GUEST_BIRTH


async def guest_birth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "guest_form"
    ]["مكان وتاريخ الولادة"] = update.message.text.strip()

    await update.message.reply_text(

        "🏠 السؤال الرابع:\n\n"
        "ما هو السكن الأصلي؟"
    )

    return GUEST_HOME


async def guest_home(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "guest_form"
    ]["السكن الأصلي"] = update.message.text.strip()

    await update.message.reply_text(

        "🗺 السؤال الخامس:\n\n"
        "ما هي المحافظة؟"
    )

    return GUEST_GOVERNORATE


async def guest_governorate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "guest_form"
    ]["المحافظة"] = update.message.text.strip()

    await update.message.reply_text(

        "🚪 السؤال السادس:\n\n"
        "ما رقم الغرفة؟"
    )

    return GUEST_ROOM


async def guest_room(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "guest_form"
    ]["رقم الغرفة"] = update.message.text.strip()

    await update.message.reply_text(

        "🏢 السؤال السابع:\n\n"
        "ما رقم الجناح؟\n\n"
        "إذا لم يوجد جناح اكتب: لا يوجد"
    )

    return GUEST_SUITE


async def guest_suite(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "guest_form"
    ]["رقم الجناح"] = update.message.text.strip()

    await update.message.reply_text(

        "📅 السؤال الثامن:\n\n"
        "ما تاريخ النزول؟"
    )

    return GUEST_CHECKIN


async def guest_checkin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "guest_form"
    ]["تاريخ النزول"] = update.message.text.strip()

    await update.message.reply_text(

        "⏱ السؤال التاسع:\n\n"
        "ما مدة الإقامة؟"
    )

    return GUEST_DURATION


async def guest_duration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "guest_form"
    ]["مدة الإقامة"] = update.message.text.strip()

    await update.message.reply_text(

        "🎯 السؤال العاشر:\n\n"
        "ما سبب الإقامة؟"
    )

    return GUEST_REASON


async def guest_reason(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "guest_form"
    ]["سبب الإقامة"] = update.message.text.strip()

    context.user_data[
        "guest_images"
    ] = []

    await update.message.reply_text(

        "📷 الآن ننتقل إلى صور النزيل.\n\n"

        "الصورة الأولى **إلزامية**.\n\n"

        "يرجى إرسال الصورة الأولى."
    )

    return GUEST_PHOTO_1


# =========================================================
# الصورة الأولى - إلزامية
# =========================================================

async def guest_photo_1(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.photo:

        await update.message.reply_text(

            "❌ هذه الخطوة تتطلب صورة.\n\n"
            "يرجى إرسال الصورة الأولى."
        )

        return GUEST_PHOTO_1

    image = await download_photo(update)

    if not image:

        await update.message.reply_text(
            "❌ تعذر تحميل الصورة. حاول مرة أخرى."
        )

        return GUEST_PHOTO_1

    context.user_data[
        "guest_images"
    ].append(image)

    await update.message.reply_text(

        "✅ تم استلام الصورة الأولى.\n\n"

        "📷 الصورة الثانية **إلزامية**.\n\n"

        "يرجى إرسال الصورة الثانية."
    )

    return GUEST_PHOTO_2


# =========================================================
# الصورة الثانية - إلزامية
# =========================================================

async def guest_photo_2(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.photo:

        await update.message.reply_text(

            "❌ هذه الخطوة تتطلب صورة.\n\n"
            "يرجى إرسال الصورة الثانية."
        )

        return GUEST_PHOTO_2

    image = await download_photo(update)

    if not image:

        await update.message.reply_text(
            "❌ تعذر تحميل الصورة. حاول مرة أخرى."
        )

        return GUEST_PHOTO_2

    context.user_data[
        "guest_images"
    ].append(image)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📷 إضافة الصورة الثالثة",
                    callback_data="photo3_yes"
                )
            ],
            [
                InlineKeyboardButton(
                    "➡️ تخطي الصورة الثالثة",
                    callback_data="photo3_skip"
                )
            ]
        ]
    )

    await update.message.reply_text(

        "✅ تم استلام الصورة الثانية.\n\n"

        "📷 الصورة الثالثة اختيارية.\n\n"

        "هل تريد إضافة صورة ثالثة؟",

        reply_markup=keyboard
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

            "📷 أرسل الصورة الثالثة "
            "أو استخدم زر التخطي."
        )

        return GUEST_PHOTO_3

    image = await download_photo(update)

    if image:

        context.user_data[
            "guest_images"
        ].append(image)

    await show_guest_confirmation(
        update,
        context
    )

    return ConversationHandler.END


# =========================================================
# اختيار الصورة الثالثة
# =========================================================

async def photo3_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if query.data == "photo3_yes":

        await query.edit_message_text(

            "📷 أرسل الصورة الثالثة الآن.\n\n"
            "هذه الصورة اختيارية."
        )

        return GUEST_PHOTO_3

    if query.data == "photo3_skip":

        await query.edit_message_text(
            "⏭ تم تخطي الصورة الثالثة."
        )

        await show_guest_confirmation(
            update,
            context
        )

        return ConversationHandler.END

    return GUEST_PHOTO_3


# =========================================================
# تحميل صورة
# =========================================================

async def download_photo(update):

    try:

        photo = update.message.photo[-1]

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
# تأكيد البيانات قبل الإرسال
# =========================================================

async def show_guest_confirmation(
    update,
    context
):

    guest = context.user_data.get(
        "guest_form",
        {}
    )

    images = context.user_data.get(
        "guest_images",
        []
    )

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if not hotel:

        # قد تكون callback
        if hasattr(update, "callback_query") and update.callback_query:

            await update.callback_query.message.reply_text(
                "❌ انتهت جلسة تسجيل الدخول. يرجى تسجيل الدخول من جديد."
            )

        return

    guest["اسم الفندق"] = hotel["hotel_name"]

    context.user_data[
        "guest_form"
    ] = guest

    text = (

        "📋 مراجعة بيانات النزيل\n\n"

        f"👤 الاسم الثلاثي: "
        f"{guest.get('الاسم الثلاثي', '-')}\n\n"

        f"👩 اسم الأم: "
        f"{guest.get('اسم الأم', '-')}\n\n"

        f"📅 مكان وتاريخ الولادة: "
        f"{guest.get('مكان وتاريخ الولادة', '-')}\n\n"

        f"🏠 السكن الأصلي: "
        f"{guest.get('السكن الأصلي', '-')}\n\n"

        f"🗺 المحافظة: "
        f"{guest.get('المحافظة', '-')}\n\n"

        f"🏨 الفندق: "
        f"{hotel['hotel_name']}\n\n"

        f"🚪 رقم الغرفة: "
        f"{guest.get('رقم الغرفة', '-')}\n\n"

        f"🏢 رقم الجناح: "
        f"{guest.get('رقم الجناح', '-')}\n\n"

        f"📅 تاريخ النزول: "
        f"{guest.get('تاريخ النزول', '-')}\n\n"

        f"⏱ مدة الإقامة: "
        f"{guest.get('مدة الإقامة', '-')}\n\n"

        f"🎯 سبب الإقامة: "
        f"{guest.get('سبب الإقامة', '-')}\n\n"

        f"📷 عدد الصور: {len(images)}\n\n"

        "⚠️ يرجى التأكد من صحة جميع المعلومات "
        "قبل إرسالها للإدارة."
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ إرسال المعلومات للإدارة",
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
    )

    if update.callback_query:

        await update.callback_query.message.reply_text(
            text,
            reply_markup=keyboard
        )

    else:

        await update.message.reply_text(
            text,
            reply_markup=keyboard
        )


# =========================================================
# إرسال المعلومات للإدارة
# =========================================================

async def submit_guest_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if query.data == "cancel_guest":

        context.user_data.pop(
            "guest_form",
            None
        )

        context.user_data.pop(
            "guest_images",
            None
        )

        await query.edit_message_text(

            "❌ تم إلغاء تسجيل النزيل.\n\n"

            "يمكنك البدء بنزيل جديد من خلال:\n"
            "/new_guest"
        )

        return

    if query.data != "submit_guest":
        return

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if not hotel:

        await query.edit_message_text(
            "❌ انتهت جلسة تسجيل الدخول."
        )

        return

    guest = context.user_data.get(
        "guest_form"
    )

    images = context.user_data.get(
        "guest_images",
        []
    )

    if not guest:

        await query.edit_message_text(
            "❌ لا توجد بيانات للنزيل."
        )

        return

    # إجبار اسم الفندق
    guest["اسم الفندق"] = hotel[
        "hotel_name"
    ]

    # حفظ البيانات
    save_guest(
        guest,
        update,
        hotel["id"]
    )

    # إنشاء PDF
    pdf_file = create_guest_pdf(
        guest,
        images
    )

    guest_name = guest.get(
        "الاسم الثلاثي",
        "تقرير_نزيل"
    )

    filename = safe_filename(
        guest_name
    )

    # إرسال التقرير للمستخدم
    await query.edit_message_text(

        "✅ تم إرسال معلومات النزيل بنجاح.\n\n"

        "📋 تم حفظ البيانات وإعداد التقرير.\n\n"

        "يمكنك الآن تسجيل نزيل جديد."
    )

    await query.message.reply_document(

        document=pdf_file,

        filename=filename,

        caption=(

            "📋 تقرير نزيل\n\n"

            f"👤 الاسم: {guest_name}\n"
            f"🏨 الفندق: {hotel['hotel_name']}\n\n"

            "✅ تم إرسال المعلومات للإدارة."
        )
    )

    # تنظيف النموذج
    context.user_data.pop(
        "guest_form",
        None
    )

    context.user_data.pop(
        "guest_images",
        None
    )


# =========================================================
# وضع PDF مستقل
# =========================================================

async def single_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(
        update,
        context
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return

    context.user_data[
        "pdf_mode"
    ] = "single"

    await update.message.reply_text(

        "📄 تم اختيار وضع الملفات المستقلة."
    )


# =========================================================
# وضع PDF موحد
# =========================================================

async def all_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(
        update,
        context
    ):

        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return

    context.user_data[
        "pdf_mode"
    ] = "all"

    await update.message.reply_text(

        "📚 تم اختيار وضع الملف الموحد."
    )


# =========================================================
# التقرير اليومي
# =========================================================

async def daily_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(
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

    await update.message.reply_text(text)


# =========================================================
# تقرير أمس
# =========================================================

async def yesterday_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin_logged(
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

    if not is_admin_logged(
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
            "📋 لا توجد بيانات مسجلة خلال الشهر الحالي."
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

        text += f"• {name}: {count}\n"

    text += "\n🏨 حسب الفندق:\n"

    for name, count in hotels.most_common():

        text += f"• {name}: {count}\n"

    text += "\n🎯 حسب سبب الإقامة:\n"

    for name, count in reasons.most_common():

        text += f"• {name}: {count}\n"

    await update.message.reply_text(text)

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

        caption="📊 تم إنشاء التقرير الشهري PDF."
    )


# =========================================================
# PDF التقرير
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
            arabic_text(section_title)
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

            pdf.drawRightString(
                PAGE_WIDTH - 70,
                y,
                arabic_text(
                    f"• {name}: {count}"
                )
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

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# إلغاء
# =========================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.pop(
        "login_username",
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

    context.user_data.pop(
        "guest_form",
        None
    )

    context.user_data.pop(
        "guest_images",
        None
    )

    await update.message.reply_text(
        "❌ تم إلغاء العملية."
    )

    return ConversationHandler.END


# =========================================================
# بناء التطبيق
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
        )
    ],

    allow_reentry=True
)


# =========================================================
# الأوامر الأساسية
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
# أزرار النموذج
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        photo3_callback,
        pattern=r"^photo3_(yes|skip)$"
    )
)

app.add_handler(
    CallbackQueryHandler(
        submit_guest_callback,
        pattern=r"^(submit_guest|cancel_guest)$"
    )
)

app.add_handler(
    CallbackQueryHandler(
        delete_hotel_callback,
        pattern=r"^delete_hotel:\d+$"
    )
)


# =========================================================
# استقبال الرسائل العادية
# =========================================================

async def general_message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # المدير لا يستقبل بيانات النزلاء
    if is_admin_logged(
        update,
        context
    ):

        await update.message.reply_text(

            "👨‍💼 أنت في حساب الإدارة.\n\n"

            "استخدم الأوامر الموجودة في القائمة "
            "للوصول إلى وظائف الإدارة."
        )

        return

    # الفندق
    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if not hotel:

        await update.message.reply_text(

            "🔐 يرجى تسجيل الدخول أولاً.\n\n"
            "استخدم /login"
        )

        return

    await update.message.reply_text(

        "📋 لإدخال بيانات نزيل جديد استخدم:\n\n"
        "/new_guest"
    )


app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        general_message_handler
    )
)


# =========================================================
# دخول المدير السري عبر /start
# =========================================================

# ملاحظة:
# لا نضع ADMIN_LOGIN_USERNAME/PASSWORD في ConversationHandler.
# المدير يستخدم /start ثم يتم التعرف عليه من بيانات Telegram
# فقط إذا كان قد تم ربطه سابقاً؟
#
# لذلك نستخدم هنا أمر /admin.
#
# ولكن بما أنك تريد ADMIN_USERNAME و ADMIN_PASSWORD،
# سنجعل /start يطلب بيانات المدير عند استخدام /admin.
#
# =========================================================


ADMIN_USERNAME_STATE = 100
ADMIN_PASSWORD_STATE = 101


async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if is_admin_logged(
        update,
        context
    ):

        await update.message.reply_text(
            "✅ أنت مسجل دخول الإدارة بالفعل."
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "👨‍💼 تسجيل دخول الإدارة\n\n"

        "أرسل اسم مستخدم الإدارة:"
    )

    return ADMIN_USERNAME_STATE


async def admin_username_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "admin_username_temp"
    ] = update.message.text.strip()

    await update.message.reply_text(
        "🔑 أرسل كلمة مرور الإدارة:"
    )

    return ADMIN_PASSWORD_STATE


async def admin_password_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = context.user_data.get(
        "admin_username_temp",
        ""
    )

    password = update.message.text.strip()

    if verify_admin_credentials(
        username,
        password
    ):

        context.user_data[
            "admin_logged"
        ] = True

        context.user_data.pop(
            "admin_username_temp",
            None
        )

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await update.message.reply_text(

            "✅ تم تسجيل دخول المدير بنجاح.\n\n"

            "👨‍💼 أهلاً بك في لوحة الإدارة.\n\n"

            "يمكنك الآن إدارة الفنادق "
            "ومتابعة التقارير."
        )

    else:

        context.user_data.pop(
            "admin_username_temp",
            None
        )

        await update.message.reply_text(

            "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"

            "تأكد من أن القيم في Environment Variables "
            "مطابقة تماماً:\n\n"

            "ADMIN_USERNAME\n"
            "ADMIN_PASSWORD"
        )

    return ConversationHandler.END


admin_login_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "admin",
            admin_command
        )
    ],

    states={

        ADMIN_USERNAME_STATE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                admin_username_step
            )
        ],

        ADMIN_PASSWORD_STATE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                admin_password_step
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


app.add_handler(
    admin_login_handler
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
