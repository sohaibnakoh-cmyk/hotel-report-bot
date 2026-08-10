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

# ضع Telegram User ID الخاص بالمدير هنا في Environment Variables
ADMIN_TELEGRAM_ID = os.getenv(
    "ADMIN_TELEGRAM_ID",
    ""
).strip()

# أبقينا هذين المتغيرين للتوافق مع الإصدارات السابقة
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


# =========================================================
# حالات إضافة الفندق
# =========================================================

ADD_HOTEL_USERNAME = 10
ADD_HOTEL_PASSWORD = 11
ADD_HOTEL_NAME = 12


# =========================================================
# حالات نموذج النزيل
# =========================================================

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

# صورتان فقط
GUEST_ID_FRONT = 30
GUEST_ID_BACK = 31


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
# معالجة اللغة العربية
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
# تسجيل دخول الفندق
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


# =========================================================
# تفعيل الفندق
# =========================================================

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
# جلسة الفندق
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
# التقارير
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
# PDF احترافي للنزيل
# =========================================================

def draw_pdf_title(
    pdf,
    hotel_name,
    subtitle="تقرير بيانات نزيل"
):

    # إطار علوي
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

    pdf.setFillColor(
        colors.white
    )

    pdf.setFont(
        PDF_FONT,
        18
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 63,
        arabic_text(hotel_name)
    )

    pdf.setFont(
        PDF_FONT,
        11
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 84,
        arabic_text(subtitle)
    )

    pdf.setFillColor(
        colors.black
    )


def draw_pdf_section_title(
    pdf,
    y,
    title
):

    pdf.setFillColor(
        colors.HexColor("#D9EAF7")
    )

    pdf.roundRect(
        45,
        y - 25,
        PAGE_WIDTH - 90,
        30,
        5,
        fill=1,
        stroke=0
    )

    pdf.setFillColor(
        colors.HexColor("#17365D")
    )

    pdf.setFont(
        PDF_FONT,
        12
    )

    pdf.drawRightString(
        PAGE_WIDTH - 60,
        y - 15,
        arabic_text(title)
    )

    pdf.setFillColor(
        colors.black
    )

    return y - 42


def draw_pdf_field(
    pdf,
    y,
    label,
    value
):

    value = str(
        value if value is not None else ""
    )

    # خلفية الحقل
    pdf.setFillColor(
        colors.HexColor("#F6F8FA")
    )

    pdf.roundRect(
        45,
        y - 27,
        PAGE_WIDTH - 90,
        31,
        4,
        fill=1,
        stroke=0
    )

    pdf.setFillColor(
        colors.HexColor("#333333")
    )

    pdf.setFont(
        PDF_FONT,
        9
    )

    text = f"{label}: {value}"

    pdf.drawRightString(
        PAGE_WIDTH - 60,
        y - 17,
        arabic_text(text)
    )

    return y - 38


def draw_id_image(
    pdf,
    image_data,
    title,
    y
):

    try:

        image_data.seek(0)

        image = ImageReader(
            image_data
        )

        # مساحة الصورة
        max_width = PAGE_WIDTH - 100
        max_height = 280

        # الحصول على أبعاد الصورة
        iw, ih = image.getSize()

        scale = min(
            max_width / iw,
            max_height / ih
        )

        width = iw * scale
        height = ih * scale

        # عنوان الصورة
        pdf.setFillColor(
            colors.HexColor("#17365D")
        )

        pdf.setFont(
            PDF_FONT,
            12
        )

        pdf.drawCentredString(
            PAGE_WIDTH / 2,
            y,
            arabic_text(title)
        )

        y -= 20

        # إطار الصورة
        pdf.setStrokeColor(
            colors.HexColor("#B7C9D6")
        )

        pdf.rect(
            (PAGE_WIDTH - width) / 2 - 5,
            y - height - 5,
            width + 10,
            height + 10,
            fill=0,
            stroke=1
        )

        pdf.drawImage(
            image,
            (PAGE_WIDTH - width) / 2,
            y - height,
            width=width,
            height=height,
            preserveAspectRatio=True,
            anchor="sw",
            mask="auto"
        )

        return y - height - 35

    except Exception as e:

        print(
            "PDF image error:",
            e
        )

        return y - 30


def create_guest_pdf(
    guest,
    images=None
):

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    hotel_name = guest.get(
        "اسم الفندق",
        "الفندق"
    )

    # =====================================================
    # الصفحة الأولى - البيانات
    # =====================================================

    draw_pdf_title(
        pdf,
        hotel_name,
        "تقرير بيانات نزيل"
    )

    y = PAGE_HEIGHT - 130

    # التاريخ والوقت
    pdf.setFont(
        PDF_FONT,
        8
    )

    pdf.setFillColor(
        colors.grey
    )

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M"
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            f"تاريخ إنشاء التقرير: {now}"
        )
    )

    y -= 35

    # قسم بيانات النزيل
    y = draw_pdf_section_title(
        pdf,
        y,
        "بيانات النزيل"
    )

    fields = [
        ("الاسم الثلاثي", guest.get(
            "الاسم الثلاثي",
            "غير مذكور"
        )),
        ("اسم الأم", guest.get(
            "اسم الأم",
            "غير مذكور"
        )),
        ("مكان وتاريخ الولادة", guest.get(
            "مكان وتاريخ الولادة",
            "غير مذكور"
        )),
        ("السكن الأصلي", guest.get(
            "السكن الأصلي",
            "غير مذكور"
        )),
        ("المحافظة", guest.get(
            "المحافظة",
            "غير مذكور"
        )),
        ("اسم الفندق", hotel_name),
        ("رقم الجناح", guest.get(
            "رقم الجناح",
            "غير مذكور"
        )),
        ("رقم الغرفة", guest.get(
            "رقم الغرفة",
            "غير مذكور"
        )),
        ("تاريخ النزول", guest.get(
            "تاريخ النزول",
            "غير مذكور"
        )),
        ("مدة الإقامة", guest.get(
            "مدة الإقامة",
            "غير مذكور"
        )),
        ("سبب الإقامة", guest.get(
            "سبب الإقامة",
            "غير مذكور"
        )),
    ]

    for label, value in fields:

        if y < 80:

            pdf.showPage()

            draw_pdf_title(
                pdf,
                hotel_name,
                "تقرير بيانات نزيل"
            )

            y = PAGE_HEIGHT - 130

        y = draw_pdf_field(
            pdf,
            y,
            label,
            value
        )

    # =====================================================
    # الصفحات الخاصة بالبطاقات
    # =====================================================

    if images and len(images) >= 2:

        # الوجه الأمامي
        pdf.showPage()

        draw_pdf_title(
            pdf,
            hotel_name,
            "صور البطاقة الشخصية"
        )

        y = PAGE_HEIGHT - 135

        y = draw_pdf_section_title(
            pdf,
            y,
            "البطاقة الشخصية - الوجه الأمامي"
        )

        y -= 15

        y = draw_id_image(
            pdf,
            images[0],
            "الوجه الأمامي للبطاقة الشخصية",
            y
        )

        # الوجه الخلفي
        pdf.showPage()

        draw_pdf_title(
            pdf,
            hotel_name,
            "صور البطاقة الشخصية"
        )

        y = PAGE_HEIGHT - 135

        y = draw_pdf_section_title(
            pdf,
            y,
            "البطاقة الشخصية - الوجه الخلفي"
        )

        y -= 15

        draw_id_image(
            pdf,
            images[1],
            "الوجه الخلفي للبطاقة الشخصية",
            y
        )

    # =====================================================
    # تذييل الصفحات
    # =====================================================

    pdf.setFont(
        PDF_FONT,
        7
    )

    pdf.setFillColor(
        colors.grey
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        25,
        arabic_text(
            f"الفندق: {hotel_name}"
        )
    )

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# PDF التقارير اليومية والشهرية
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

    pdf.setFillColor(
        colors.white
    )

    pdf.setFont(
        PDF_FONT,
        16
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 63,
        arabic_text(title)
    )

    pdf.setFillColor(
        colors.black
    )

    pdf.setFont(
        PDF_FONT,
        11
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

            y = PAGE_HEIGHT - 120

        pdf.setFont(
            PDF_FONT,
            13
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

                y = PAGE_HEIGHT - 120

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

    pdf.save()

    buffer.seek(0)

    return buffer


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
            "🗑️ حذف فندق"
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
            scope=BotCommandScopeChat(chat_id)
        )

    except Exception as e:

        print(
            "Admin commands error:",
            e
        )


# =========================================================
# أوامر الفندق قبل الدخول
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
            scope=BotCommandScopeChat(chat_id)
        )

    except Exception as e:

        print(
            "Logged hotel commands error:",
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

    # المدير
    if is_admin(update):

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(update)

        await update.message.reply_text(

            "بسم الله الرحمن الرحيم 🌿\n\n"
            "السلام عليكم ورحمة الله وبركاته\n\n"
            "﴿ وَقُلْ رَبِّ زِدْنِي عِلْمًا ﴾\n\n"
            "🏨 أهلاً وسهلاً بك في نظام معلومات الفنادق.\n\n"
            "نسأل الله أن يوفقنا وإياكم لما فيه الخير "
            "وأن يعيننا على أداء الأمانة وحفظ المعلومات.\n\n"
            "👨‍💼 تم التعرف على حسابك كحساب مدير.\n\n"
            "يمكنك الآن إدارة حسابات الفنادق "
            "ومتابعة البيانات والتقارير من القائمة."
        )

        return

    # الفندق المسجل
    hotel = get_logged_hotel(user_id)

    if hotel:

        await set_logged_hotel_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(update)

        await update.message.reply_text(

            "بسم الله الرحمن الرحيم 🌿\n\n"
            "السلام عليكم ورحمة الله وبركاته\n\n"
            f"🏨 أهلاً وسهلاً بكم\n"
            f"فندق: {hotel['hotel_name']}\n\n"
            "نسأل الله لكم التوفيق وأن يعيننا "
            "على التعاون بما يخدم العمل ويحفظ الأمانة.\n\n"
            "✅ تم تسجيل الدخول بنجاح.\n\n"
            "يمكنكم الآن تسجيل بيانات النزلاء من خلال:\n"
            "👤 /new_guest"
        )

        return

    # زائر
    await set_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await send_welcome_image(update)

    await update.message.reply_text(

        "بسم الله الرحمن الرحيم 🌿\n\n"
        "السلام عليكم ورحمة الله وبركاته\n\n"
        "🏨 أهلاً وسهلاً ومرحباً بكم\n"
        "في نظام معلومات الفنادق.\n\n"
        "نسأل الله أن يوفقنا جميعاً لما فيه الخير "
        "وأن يعيننا على أداء الأمانة وحفظ المعلومات.\n\n"
        "🔐 للبدء يرجى استخدام:\n"
        "/login\n\n"
        "ثم إدخال اسم المستخدم وكلمة المرور "
        "الخاصة بالفندق."
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
            "👨‍💼 أهلاً بك مدير النظام.\n\n"
            "تم التعرف على حسابك تلقائياً.\n"
            "لا تحتاج إلى تسجيل دخول."
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
        "🔐 تسجيل دخول الفندق\n\n"
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
            "❌ حدث خطأ، استخدم /login من جديد."
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
            "إذا كان الحساب جديداً، تأكد من كتابة "
            "بيانات الدخول التي أعطاك إياها المدير."
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

            "⚠️ هذا الحساب مرتبط حالياً بحساب Telegram آخر.\n\n"
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

    await set_logged_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await update.message.reply_text(

        "بسم الله الرحمن الرحيم 🌿\n\n"
        "✅ تم تسجيل الدخول بنجاح.\n\n"
        f"🏨 الفندق: {account['hotel_name']}\n\n"
        "يمكنك الآن تسجيل بيانات النزلاء من خلال:\n"
        "👤 /new_guest"
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
            "👨‍💼 حساب المدير مرتبط بحساب Telegram "
            "ولا يحتاج إلى تسجيل دخول أو خروج."
        )

        return

    logout_session(
        update.effective_user.id
    )

    context.user_data.clear()

    await set_hotel_commands(
        context.application,
        update.effective_chat.id
    )

    await update.message.reply_text(
        "🚪 تم تسجيل الخروج بنجاح."
    )


# =========================================================
# إضافة فندق
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

    await update.message.reply_text(

        "🏨 إضافة حساب فندق جديد\n\n"
        "الخطوة 1 من 3\n\n"
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
            "استخدم الأحرف الإنجليزية والأرقام "
            "والنقطة أو الشرطة فقط."
        )

        return ADD_HOTEL_USERNAME

    context.user_data[
        "new_hotel_username"
    ] = username

    await update.message.reply_text(
        "🔑 الخطوة 2 من 3\n\n"
        "أرسل كلمة المرور للحساب.\n\n"
        "يجب ألا تقل عن 8 أحرف."
    )

    return ADD_HOTEL_PASSWORD


async def add_hotel_password(
    update,
    context
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
        "🏨 الخطوة 3 من 3\n\n"
        "أرسل اسم الفندق:"
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

        "✅ تم إنشاء حساب الفندق بنجاح.\n\n"
        f"🏨 الفندق: {hotel_name}\n"
        f"👤 اسم المستخدم: {username}\n\n"
        "🔐 تم حفظ كلمة المرور بشكل مشفر.\n\n"
        "قم بإرسال بيانات الدخول لصاحب الفندق "
        "بطريقة آمنة."
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

    hotels = get_all_hotels()

    if not hotels:

        await update.message.reply_text(
            "📋 لا توجد حسابات فنادق حالياً."
        )

        return

    text = "🏨 قائمة حسابات الفنادق\n\n"
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
            f"#{hotel['id']}\n"
            f"🏨 {hotel['hotel_name']}\n"
            f"👤 {hotel['username']}\n"
            f"📌 {status}\n"
            f"📱 {connected}\n"
            f"📅 {hotel['created_at']}\n\n"
        )

        if hotel["active"]:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🗑️ تعطيل {hotel['hotel_name']}",
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
# حذف فندق
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

    hotels = get_all_hotels()

    if not hotels:

        await update.message.reply_text(
            "📋 لا توجد فنادق."
        )

        return

    keyboard = []

    for hotel in hotels:

        if hotel["active"]:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🗑️ {hotel['hotel_name']}",
                        callback_data=f"delete_hotel:{hotel['id']}"
                    )
                ]
            )

    if not keyboard:

        await update.message.reply_text(
            "📋 جميع الفنادق موقوفة حالياً."
        )

        return

    await update.message.reply_text(

        "🗑️ اختر الفندق الذي تريد تعطيله:\n\n"
        "⚠️ بعد التعطيل لن يستطيع صاحب الفندق "
        "تسجيل الدخول.",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# أزرار المدير
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

    if data.startswith("delete_hotel:"):

        hotel_id = data.split(
            ":",
            1
        )[1]

        keyboard = [

            [
                InlineKeyboardButton(
                    "✅ نعم، تعطيل الفندق",
                    callback_data=f"confirm_delete:{hotel_id}"
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

            "⚠️ هل أنت متأكد من تعطيل هذا الفندق؟\n\n"
            "لن يستطيع صاحب الفندق تسجيل الدخول "
            "بعد التعطيل.",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    if data.startswith("confirm_delete:"):

        hotel_id = int(
            data.split(
                ":",
                1
            )[1]
        )

        disable_hotel(hotel_id)

        await query.edit_message_text(
            "🗑️ تم تعطيل الفندق بنجاح.\n\n"
            "لن يستطيع صاحب الفندق تسجيل الدخول "
            "حتى يقوم المدير بإعادة تفعيله."
        )

        return

    if data == "cancel_delete":

        await query.edit_message_text(
            "❌ تم إلغاء العملية."
        )

        return

    if data.startswith("enable_hotel:"):

        hotel_id = int(
            data.split(
                ":",
                1
            )[1]
        )

        enable_hotel(hotel_id)

        await query.edit_message_text(
            "♻️ تم تفعيل الفندق بنجاح."
        )


# =========================================================
# بدء نموذج النزيل
# =========================================================

async def new_guest_start(
    update,
    context
):

    if is_admin(update):

        await update.message.reply_text(
            "👨‍💼 نموذج النزلاء مخصص لحسابات الفنادق."
        )

        return ConversationHandler.END

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if not hotel:

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً.\n\n"
            "استخدم /login"
        )

        return ConversationHandler.END

    context.user_data["guest_form"] = {}

    context.user_data["guest_hotel_id"] = hotel["id"]

    context.user_data["guest_hotel_name"] = hotel["hotel_name"]

    # صورتان فقط
    context.user_data["guest_images"] = []

    await update.message.reply_text(

        "📋 نموذج تسجيل نزيل جديد\n\n"
        f"🏨 الفندق: {hotel['hotel_name']}\n\n"
        "سيتم طرح الأسئلة واحداً تلو الآخر.\n"
        "يرجى إدخال البيانات بدقة.\n\n"
        "1️⃣ الاسم الثلاثي:"
    )

    return GUEST_NAME


# =========================================================
# بيانات النزيل
# =========================================================

async def guest_name(update, context):

    context.user_data[
        "guest_form"
    ]["الاسم الثلاثي"] = update.message.text.strip()

    await update.message.reply_text(
        "2️⃣ اسم الأم:"
    )

    return GUEST_MOTHER


async def guest_mother(update, context):

    context.user_data[
        "guest_form"
    ]["اسم الأم"] = update.message.text.strip()

    await update.message.reply_text(
        "3️⃣ مكان وتاريخ الولادة:"
    )

    return GUEST_BIRTH


async def guest_birth(update, context):

    context.user_data[
        "guest_form"
    ]["مكان وتاريخ الولادة"] = update.message.text.strip()

    await update.message.reply_text(
        "4️⃣ السكن الأصلي:"
    )

    return GUEST_HOME


async def guest_home(update, context):

    context.user_data[
        "guest_form"
    ]["السكن الأصلي"] = update.message.text.strip()

    await update.message.reply_text(
        "5️⃣ المحافظة:"
    )

    return GUEST_GOVERNORATE


async def guest_governorate(update, context):

    context.user_data[
        "guest_form"
    ]["المحافظة"] = update.message.text.strip()

    await update.message.reply_text(
        "6️⃣ رقم الجناح:\n\n"
        "إذا لم يوجد جناح، اكتب: لا يوجد"
    )

    return GUEST_SUITE


async def guest_suite(update, context):

    context.user_data[
        "guest_form"
    ]["رقم الجناح"] = update.message.text.strip()

    await update.message.reply_text(
        "7️⃣ رقم الغرفة:"
    )

    return GUEST_ROOM


async def guest_room(update, context):

    context.user_data[
        "guest_form"
    ]["رقم الغرفة"] = update.message.text.strip()

    await update.message.reply_text(
        "8️⃣ تاريخ النزول:"
    )

    return GUEST_CHECKIN


async def guest_checkin(update, context):

    context.user_data[
        "guest_form"
    ]["تاريخ النزول"] = update.message.text.strip()

    await update.message.reply_text(
        "9️⃣ مدة الإقامة:"
    )

    return GUEST_DURATION


async def guest_duration(update, context):

    context.user_data[
        "guest_form"
    ]["مدة الإقامة"] = update.message.text.strip()

    await update.message.reply_text(
        "🔟 سبب الإقامة:"
    )

    return GUEST_REASON


async def guest_reason(update, context):

    context.user_data[
        "guest_form"
    ]["سبب الإقامة"] = update.message.text.strip()

    await update.message.reply_text(

        "📷 الآن ننتقل إلى صور البطاقة الشخصية.\n\n"
        "🪪 الصورة الأولى إلزامية:\n"
        "الوجه الأمامي للبطاقة الشخصية.\n\n"
        "أرسل صورة الوجه الأمامي الآن:"
    )

    return GUEST_ID_FRONT


# =========================================================
# صورة البطاقة - الوجه الأمامي
# =========================================================

async def guest_id_front(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ يجب إرسال صورة.\n\n"
            "الصورة المطلوبة هي الوجه الأمامي "
            "للبطاقة الشخصية."
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
        ].append(
            image_buffer
        )

        await update.message.reply_text(

            "✅ تم استلام الوجه الأمامي.\n\n"
            "🪪 الصورة الثانية إلزامية:\n"
            "الوجه الخلفي للبطاقة الشخصية.\n\n"
            "أرسل صورة الوجه الخلفي الآن:"
        )

        return GUEST_ID_BACK

    except Exception as e:

        print(
            "ID front error:",
            e
        )

        await update.message.reply_text(
            "❌ حدث خطأ أثناء استلام الصورة.\n"
            "أعد إرسالها."
        )

        return GUEST_ID_FRONT


# =========================================================
# صورة البطاقة - الوجه الخلفي
# =========================================================

async def guest_id_back(
    update,
    context
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ يجب إرسال صورة.\n\n"
            "الصورة المطلوبة هي الوجه الخلفي "
            "للبطاقة الشخصية."
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
        ].append(
            image_buffer
        )

        await update.message.reply_text(
            "✅ تم استلام الوجه الخلفي.\n\n"
            "⏳ يتم تجهيز البيانات للمراجعة..."
        )

        return await finish_guest_form(
            update,
            context
        )

    except Exception as e:

        print(
            "ID back error:",
            e
        )

        await update.message.reply_text(
            "❌ حدث خطأ أثناء استلام الصورة.\n"
            "أعد إرسالها."
        )

        return GUEST_ID_BACK


# =========================================================
# مراجعة البيانات
# =========================================================

async def finish_guest_form(
    update,
    context
):

    form = context.user_data.get(
        "guest_form",
        {}
    )

    hotel_name = context.user_data.get(
        "guest_hotel_name",
        "غير مذكور"
    )

    form["اسم الفندق"] = hotel_name

    context.user_data[
        "guest_form"
    ] = form

    images = context.user_data.get(
        "guest_images",
        []
    )

    text = (

        "📋 مراجعة بيانات النزيل\n\n"

        f"👤 الاسم الثلاثي: "
        f"{form.get('الاسم الثلاثي', '')}\n\n"

        f"👩 اسم الأم: "
        f"{form.get('اسم الأم', '')}\n\n"

        f"🎂 مكان وتاريخ الولادة: "
        f"{form.get('مكان وتاريخ الولادة', '')}\n\n"

        f"🏠 السكن الأصلي: "
        f"{form.get('السكن الأصلي', '')}\n\n"

        f"📍 المحافظة: "
        f"{form.get('المحافظة', '')}\n\n"

        f"🏨 الفندق: "
        f"{hotel_name}\n\n"

        f"🏢 الجناح: "
        f"{form.get('رقم الجناح', '')}\n\n"

        f"🚪 الغرفة: "
        f"{form.get('رقم الغرفة', '')}\n\n"

        f"📅 تاريخ النزول: "
        f"{form.get('تاريخ النزول', '')}\n\n"

        f"⏱️ مدة الإقامة: "
        f"{form.get('مدة الإقامة', '')}\n\n"

        f"🎯 سبب الإقامة: "
        f"{form.get('سبب الإقامة', '')}\n\n"

        "🪪 الوجه الأمامي: ✅\n"
        "🪪 الوجه الخلفي: ✅\n\n"

        "يرجى مراجعة المعلومات قبل الإرسال."
    )

    keyboard = [

        [
            InlineKeyboardButton(
                "📤 إرسال المعلومات للإدارة",
                callback_data="send_guest_to_admin"
            )
        ],

        [
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="cancel_guest_form"
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
# إرسال البيانات للإدارة
# =========================================================

async def send_guest_to_admin(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if is_admin(update):

        await query.edit_message_text(
            "⛔ هذا الزر مخصص لحساب الفندق."
        )

        return

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if not hotel:

        await query.edit_message_text(
            "🔐 انتهت جلسة الدخول.\n\n"
            "يرجى تسجيل الدخول من جديد."
        )

        return

    form = context.user_data.get(
        "guest_form"
    )

    images = context.user_data.get(
        "guest_images",
        []
    )

    if not form:

        await query.edit_message_text(
            "❌ لا توجد بيانات لإرسالها."
        )

        return

    # يجب وجود صورتين
    if len(images) != 2:

        await query.edit_message_text(
            "❌ لا يمكن إرسال المعلومات.\n\n"
            "يجب إرفاق صورتين للبطاقة الشخصية:\n"
            "1️⃣ الوجه الأمامي\n"
            "2️⃣ الوجه الخلفي"
        )

        return

    # حفظ البيانات
    guest_id = save_guest(
        form,
        update,
        hotel["id"]
    )

    # إنشاء PDF
    pdf_file = create_guest_pdf(
        form,
        images
    )

    guest_name = form.get(
        "الاسم الثلاثي",
        "نزيل"
    )

    filename = safe_filename(
        guest_name
    )

    admin_caption = (

        "📥 تم استلام بيانات نزيل جديد\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n"

        f"👤 الاسم: {guest_name}\n"

        f"🚪 الغرفة: "
        f"{form.get('رقم الغرفة', 'غير مذكور')}\n"

        f"📅 تاريخ النزول: "
        f"{form.get('تاريخ النزول', 'غير مذكور')}\n"

        f"🪪 صور البطاقة: وجه أمامي + وجه خلفي\n"

        f"🆔 رقم السجل: {guest_id}\n\n"

        "📎 تم إرفاق التقرير الكامل بصيغة PDF."
    )

    if not ADMIN_TELEGRAM_ID:

        await query.edit_message_text(
            "⚠️ تم حفظ المعلومات، "
            "لكن ADMIN_TELEGRAM_ID غير محدد."
        )

        return

    try:

        await context.bot.send_document(

            chat_id=int(
                ADMIN_TELEGRAM_ID
            ),

            document=pdf_file,

            filename=filename,

            caption=admin_caption
        )

    except Exception as e:

        print(
            "Admin send error:",
            e
        )

        await query.edit_message_text(

            "⚠️ تم حفظ المعلومات، "
            "لكن حدث خطأ أثناء إرسالها للإدارة.\n\n"
            "يرجى إبلاغ الإدارة."
        )

        return

    # نجاح
    await query.edit_message_text(

        "بسم الله الرحمن الرحيم 🌿\n\n"

        "✅ تم إرسال معلومات النزيل بنجاح.\n\n"

        "📤 وصلت المعلومات إلى الإدارة.\n\n"

        "🪪 تم إرفاق الوجه الأمامي والخلفي "
        "للبطاقة الشخصية ضمن ملف PDF.\n\n"

        "يمكنك الآن تسجيل نزيل آخر من خلال:\n"
        "👤 /new_guest"
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
        "guest_hotel_id",
        None
    )

    context.user_data.pop(
        "guest_hotel_name",
        None
    )


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
        "guest_images",
        None
    )

    context.user_data.pop(
        "guest_hotel_id",
        None
    )

    context.user_data.pop(
        "guest_hotel_name",
        None
    )

    await query.edit_message_text(
        "❌ تم إلغاء تسجيل النزيل.\n\n"
        "يمكنك البدء من جديد من خلال:\n"
        "/new_guest"
    )


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
        caption="📋 تم إنشاء التقرير اليومي PDF."
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
    update,
    context
):

    if not is_admin(update):

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
        caption="📊 تم إنشاء التقرير الشهري PDF."
    )


# =========================================================
# إلغاء المحادثة
# =========================================================

async def cancel(
    update,
    context
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
# التحقق من التوكن
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

        # الوجه الأمامي
        GUEST_ID_FRONT: [
            MessageHandler(
                filters.PHOTO,
                guest_id_front
            )
        ],

        # الوجه الخلفي
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
# إضافة Handlers
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
# أزرار الإدارة
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        admin_callback,
        pattern=r"^(delete_hotel:|confirm_delete:|cancel_delete|enable_hotel:)"
    )
)


# =========================================================
# إرسال النزيل للإدارة
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        send_guest_to_admin,
        pattern=r"^send_guest_to_admin$"
    )
)


# =========================================================
# إلغاء نموذج النزيل
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        cancel_guest_callback,
        pattern=r"^cancel_guest_form$"
    )
)


# =========================================================
# رسائل غير الأوامر
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
            "استخدم قائمة الأوامر الموجودة "
            "للوصول إلى وظائف الإدارة."
        )

        return

    hotel = get_logged_hotel(
        update.effective_user.id
    )

    if hotel:

        await update.message.reply_text(

            "🏨 أنت مسجل الدخول باسم:\n"
            f"{hotel['hotel_name']}\n\n"
            "لتسجيل نزيل جديد استخدم:\n"
            "/new_guest"
        )

        return

    await update.message.reply_text(
        "🔐 يرجى تسجيل الدخول أولاً.\n\n"
        "استخدم:\n"
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
# التشغيل
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
