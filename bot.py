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
# أسماء الفنادق
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
# حالات إضافة الفندق
# =========================================================

ADD_HOTEL_USERNAME = 10
ADD_HOTEL_PASSWORD = 11


# =========================================================
# حالات نموذج النزيل
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
# معالجة العربية
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
        check_same_thread=False,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_database():

    connection = get_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        PRAGMA journal_mode=WAL
        """
    )

    cursor.execute(
        """
        PRAGMA synchronous=NORMAL
        """
    )

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

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_guests_record_date
        ON guests(record_date)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_guests_hotel
        ON guests(hotel)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_guests_governorate
        ON guests(governorate)
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

    if hotel_name not in HOTELS:

        return None, "اسم الفندق غير موجود ضمن قائمة الفنادق المعتمدة."

    if not re.match(
        r"^[a-zA-Z0-9_.-]{3,50}$",
        username
    ):

        return None, (
            "اسم المستخدم غير صالح.\n"
            "استخدم الأحرف الإنجليزية والأرقام فقط "
            "مع _ أو - أو ."
        )

    if len(password) < 8:

        return None, "كلمة المرور يجب ألا تقل عن 8 أحرف."

    password_hash, salt = hash_password(
        password
    )

    connection = get_db()
    cursor = connection.cursor()

    # -----------------------------------------------------
    # التأكد من عدم وجود حساب لهذا الفندق مسبقاً
    # -----------------------------------------------------

    cursor.execute(
        """
        SELECT id, active
        FROM hotel_accounts
        WHERE hotel_name = ?
        LIMIT 1
        """,
        (hotel_name,)
    )

    existing_hotel = cursor.fetchone()

    if existing_hotel:

        connection.close()

        if existing_hotel["active"]:

            return None, (
                f"⚠️ يوجد حساب فعال مسبقاً للفندق:\n"
                f"🏨 {hotel_name}\n\n"
                "لا يمكن إنشاء حساب ثانٍ لنفس الفندق."
            )

        return None, (
            f"⚠️ يوجد حساب سابق للفندق:\n"
            f"🏨 {hotel_name}\n\n"
            "الحساب موقوف حالياً.\n"
            "يمكنك تفعيله من قائمة الفنادق بدلاً من إنشاء حساب جديد."
        )

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
                1,
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )
        )

        connection.commit()

        hotel_id = cursor.lastrowid

        connection.close()

        return hotel_id, None

    except sqlite3.IntegrityError as e:

        connection.close()

        error_text = str(e)

        if "username" in error_text.lower():

            return None, "اسم المستخدم مستخدم مسبقاً."

        return None, "تعذر إنشاء الحساب بسبب تعارض في قاعدة البيانات."


# =========================================================
# تسجيل الدخول
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
# PDF
# =========================================================

def draw_pdf_title(
    pdf,
    hotel_name,
    subtitle="تقرير بيانات نزيل"
):

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

        max_width = PAGE_WIDTH - 100
        max_height = 280

        iw, ih = image.getSize()

        scale = min(
            max_width / iw,
            max_height / ih
        )

        width = iw * scale
        height = ih * scale

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

    draw_pdf_title(
        pdf,
        hotel_name,
        "تقرير بيانات نزيل"
    )

    y = PAGE_HEIGHT - 130

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

    y = draw_pdf_section_title(
        pdf,
        y,
        "بيانات النزيل"
    )

    fields = [
        (
            "الاسم الثلاثي",
            guest.get(
                "الاسم الثلاثي",
                "غير مذكور"
            )
        ),
        (
            "اسم الأم",
            guest.get(
                "اسم الأم",
                "غير مذكور"
            )
        ),
        (
            "مكان وتاريخ الولادة",
            guest.get(
                "مكان وتاريخ الولادة",
                "غير مذكور"
            )
        ),
        (
            "السكن الأصلي",
            guest.get(
                "السكن الأصلي",
                "غير مذكور"
            )
        ),
        (
            "المحافظة",
            guest.get(
                "المحافظة",
                "غير مذكور"
            )
        ),
        (
            "اسم الفندق",
            hotel_name
        ),
        (
            "رقم الجناح",
            guest.get(
                "رقم الجناح",
                "غير مذكور"
            )
        ),
        (
            "رقم الغرفة",
            guest.get(
                "رقم الغرفة",
                "غير مذكور"
            )
        ),
        (
            "تاريخ النزول",
            guest.get(
                "تاريخ النزول",
                "غير مذكور"
            )
        ),
        (
            "مدة الإقامة",
            guest.get(
                "مدة الإقامة",
                "غير مذكور"
            )
        ),
        (
            "سبب الإقامة",
            guest.get(
                "سبب الإقامة",
                "غير مذكور"
            )
        ),
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

    if images and len(images) >= 2:

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

        draw_id_image(
            pdf,
            images[0],
            "الوجه الأمامي للبطاقة الشخصية",
            y
        )

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
# PDF التقارير
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
# إرسال صورة الترحيب
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

    if is_admin(update):

        await set_admin_commands(
            context.application,
            update.effective_chat.id
        )

        await send_welcome_image(update)

        await update.message.reply_text(

            "بسم الله الرحمن الرحيم 🌿\n\n"
            "السلام عليكم ورحمة الله وبركاته\n\n"
            "🏨 أهلاً وسهلاً بك في نظام معلومات الفنادق.\n\n"
            "👨‍💼 تم التعرف على حسابك كحساب مدير.\n\n"
            "يمكنك الآن إدارة حسابات الفنادق "
            "ومتابعة البيانات والتقارير من القائمة."
        )

        return

    hotel = await asyncio.to_thread(
        get_logged_hotel,
        user_id
    )

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
            "✅ تم تسجيل الدخول بنجاح.\n\n"
            "يمكنكم الآن تسجيل بيانات النزلاء من خلال:\n"
            "👤 /new_guest"
        )

        return

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
        "🔐 للبدء يرجى استخدام:\n"
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
            "👨‍💼 أنت المدير ولا تحتاج لتسجيل الدخول."
        )

        return ConversationHandler.END

    hotel = await asyncio.to_thread(
        get_logged_hotel,
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
        "أرسل اسم المستخدم:"
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
            "❌ حدث خطأ.\nاستخدم /login من جديد."
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
            "⚠️ هذا الحساب مرتبط بحساب Telegram آخر.\n\n"
            "يرجى التواصل مع الإدارة."
        )

        return ConversationHandler.END

    await asyncio.to_thread(
        create_session,
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

        "✅ تم تسجيل الدخول بنجاح.\n\n"
        f"🏨 الفندق: {account['hotel_name']}\n\n"
        "👤 لتسجيل نزيل جديد:\n"
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
            "👨‍💼 حساب المدير لا يحتاج إلى تسجيل خروج."
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
        "🚪 تم تسجيل الخروج بنجاح."
    )


# =========================================================
# لوحة اختيار الفندق
# =========================================================

def hotel_keyboard(
    prefix="hotel_select"
):

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

    return InlineKeyboardMarkup(
        keyboard
    )


# =========================================================
# لوحة المحافظات
# =========================================================

def governorate_keyboard():

    keyboard = []

    row = []

    for index, governorate in enumerate(
        GOVERNORATES
    ):

        row.append(
            InlineKeyboardButton(
                f"📍 {governorate}",
                callback_data=f"governorate_select:{index}"
            )
        )

        if len(row) == 2:

            keyboard.append(row)

            row = []

    if row:
        keyboard.append(row)

    return InlineKeyboardMarkup(
        keyboard
    )


# =========================================================
# إضافة فندق - البداية
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

    # تنظيف العملية السابقة
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
        "🏨 إضافة حساب فندق جديد\n\n"
        "اختر اسم الفندق من القائمة:",
        reply_markup=hotel_keyboard(
            "admin_hotel_select"
        )
    )

    return ADD_HOTEL_USERNAME


# =========================================================
# اختيار الفندق من زر الإدارة
# =========================================================

async def admin_hotel_select_button(
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

        await query.answer(
            "اختيار غير صالح",
            show_alert=True
        )

        return ADD_HOTEL_USERNAME

    # -----------------------------------------------------
    # التأكد من عدم وجود حساب للفندق
    # -----------------------------------------------------

    existing_accounts = await asyncio.to_thread(
        get_all_hotels
    )

    existing_account = None

    for account in existing_accounts:

        if account["hotel_name"] == hotel_name:

            existing_account = account
            break

    if existing_account:

        if existing_account["active"]:

            await query.edit_message_text(
                f"⚠️ الفندق لديه حساب فعال مسبقاً.\n\n"
                f"🏨 الفندق: {hotel_name}\n"
                f"👤 اسم المستخدم: {existing_account['username']}\n\n"
                "لا يمكن إنشاء حساب ثانٍ لهذا الفندق."
            )

        else:

            await query.edit_message_text(
                f"⚠️ يوجد حساب سابق لهذا الفندق لكنه موقوف.\n\n"
                f"🏨 الفندق: {hotel_name}\n"
                f"👤 اسم المستخدم: {existing_account['username']}\n\n"
                "استخدم /hotels ثم قم بتفعيل الحساب."
            )

        context.user_data.pop(
            "new_hotel_name",
            None
        )

        return ConversationHandler.END

    context.user_data[
        "new_hotel_name"
    ] = hotel_name

    context.user_data.pop(
        "new_hotel_username",
        None
    )

    context.user_data.pop(
        "new_hotel_password",
        None
    )

    await query.edit_message_text(

        f"🏨 الفندق المختار:\n"
        f"{hotel_name}\n\n"

        "👤 الآن أرسل اسم المستخدم للفندق.\n\n"

        "مثال:\n"
        "cordoba\n\n"

        "⚠️ اسم المستخدم يجب أن يكون باللغة "
        "الإنجليزية ويحتوي على أحرف أو أرقام "
        "أو _ أو - أو ."
    )

    return ADD_HOTEL_USERNAME


# =========================================================
# إدخال اسم المستخدم
# =========================================================

async def add_hotel_username(
    update,
    context
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ غير مصرح لك."
        )

        return ConversationHandler.END

    hotel_name = context.user_data.get(
        "new_hotel_name"
    )

    if not hotel_name:

        await update.message.reply_text(
            "❌ لم يتم اختيار الفندق.\n\n"
            "استخدم /add_hotel من جديد."
        )

        return ConversationHandler.END

    username = update.message.text.strip().lower()

    if not re.match(
        r"^[a-zA-Z0-9_.-]{3,50}$",
        username
    ):

        await update.message.reply_text(
            "❌ اسم المستخدم غير صالح.\n\n"
            "استخدم 3 إلى 50 محرفاً باللغة الإنجليزية "
            "والأرقام مع _ أو - أو ."
        )

        return ADD_HOTEL_USERNAME

    # -----------------------------------------------------
    # التأكد من عدم استخدام اسم المستخدم
    # -----------------------------------------------------

    existing_accounts = await asyncio.to_thread(
        get_all_hotels
    )

    for account in existing_accounts:

        if account["username"].lower() == username:

            await update.message.reply_text(
                "❌ اسم المستخدم مستخدم مسبقاً.\n\n"
                "أرسل اسم مستخدم آخر:"
            )

            return ADD_HOTEL_USERNAME

    context.user_data[
        "new_hotel_username"
    ] = username

    await update.message.reply_text(
        f"🏨 الفندق: {hotel_name}\n"
        f"👤 اسم المستخدم: {username}\n\n"
        "🔑 الآن أرسل كلمة المرور.\n\n"
        "⚠️ يجب ألا تقل عن 8 أحرف."
    )

    return ADD_HOTEL_PASSWORD


# =========================================================
# إدخال كلمة المرور وإنشاء الحساب
# =========================================================

async def add_hotel_password(
    update,
    context
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ غير مصرح لك."
        )

        return ConversationHandler.END

    password = update.message.text.strip()

    if len(password) < 8:

        await update.message.reply_text(
            "❌ كلمة المرور يجب أن تكون 8 أحرف على الأقل.\n\n"
            "أرسل كلمة المرور مرة أخرى:"
        )

        return ADD_HOTEL_PASSWORD

    hotel_name = context.user_data.get(
        "new_hotel_name"
    )

    username = context.user_data.get(
        "new_hotel_username"
    )

    if not hotel_name or not username:

        await update.message.reply_text(
            "❌ حدث خطأ في بيانات الحساب.\n\n"
            "استخدم /add_hotel من جديد."
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

    await update.message.reply_text(
        "⏳ جارٍ إنشاء حساب الفندق..."
    )

    hotel_id, error = await asyncio.to_thread(
        create_hotel_account,
        hotel_name,
        username,
        password
    )

    if error:

        await update.message.reply_text(
            f"❌ لم يتم إنشاء الحساب.\n\n"
            f"{error}"
        )

        return ConversationHandler.END

    # -----------------------------------------------------
    # حذف كلمة المرور من ذاكرة المحادثة
    # -----------------------------------------------------

    context.user_data.pop(
        "new_hotel_password",
        None
    )

    context.user_data.pop(
        "new_hotel_username",
        None
    )

    context.user_data.pop(
        "new_hotel_name",
        None
    )

    await update.message.reply_text(

        "✅ تم إنشاء حساب الفندق بنجاح.\n\n"

        f"🏨 الفندق: {hotel_name}\n"
        f"👤 اسم المستخدم: {username}\n"
        f"🔑 كلمة المرور: {password}\n\n"

        f"🆔 رقم الحساب: {hotel_id}\n\n"

        "📌 أرسل بيانات الدخول لمسؤول الفندق "
        "ليتمكن من تسجيل الدخول عبر /login."
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
            f"📱 {connected}\n\n"
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
# حذف / تعطيل الفندق
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
                        callback_data=f"delete_hotel:{hotel['id']}"
                    )
                ]
            )

    if not keyboard:

        await update.message.reply_text(
            "📋 لا توجد فنادق فعالة."
        )

        return

    await update.message.reply_text(
        "🗑️ اختر الفندق الذي تريد تعطيله:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
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

    context.user_data["guest_hotel_id"] = hotel["id"]

    context.user_data["guest_account_hotel"] = hotel[
        "hotel_name"
    ]

    context.user_data["guest_images"] = []

    await update.message.reply_text(
        "📋 نموذج تسجيل نزيل جديد\n\n"
        "1️⃣ الاسم الثلاثي:"
    )

    return GUEST_NAME


# =========================================================
# الاسم
# =========================================================

async def guest_name(update, context):

    text = update.message.text.strip()

    if not text:

        await update.message.reply_text(
            "❌ يرجى إدخال الاسم الثلاثي."
        )

        return GUEST_NAME

    context.user_data[
        "guest_form"
    ]["الاسم الثلاثي"] = text

    await update.message.reply_text(
        "2️⃣ اسم الأم:"
    )

    return GUEST_MOTHER


# =========================================================
# اسم الأم
# =========================================================

async def guest_mother(update, context):

    text = update.message.text.strip()

    if not text:

        await update.message.reply_text(
            "❌ يرجى إدخال اسم الأم."
        )

        return GUEST_MOTHER

    context.user_data[
        "guest_form"
    ]["اسم الأم"] = text

    await update.message.reply_text(
        "3️⃣ مكان وتاريخ الولادة:"
    )

    return GUEST_BIRTH


# =========================================================
# الولادة
# =========================================================

async def guest_birth(update, context):

    text = update.message.text.strip()

    if not text:

        await update.message.reply_text(
            "❌ يرجى إدخال مكان وتاريخ الولادة."
        )

        return GUEST_BIRTH

    context.user_data[
        "guest_form"
    ]["مكان وتاريخ الولادة"] = text

    await update.message.reply_text(
        "4️⃣ السكن الأصلي:"
    )

    return GUEST_HOME


# =========================================================
# السكن
# =========================================================

async def guest_home(update, context):

    text = update.message.text.strip()

    if not text:

        await update.message.reply_text(
            "❌ يرجى إدخال السكن الأصلي."
        )

        return GUEST_HOME

    context.user_data[
        "guest_form"
    ]["السكن الأصلي"] = text

    await update.message.reply_text(
        "5️⃣ اختر المحافظة / الدولة:",
        reply_markup=governorate_keyboard()
    )

    return GUEST_GOVERNORATE


# =========================================================
# المحافظة - زر
# =========================================================

async def guest_governorate_button(
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

        await query.answer(
            "اختيار غير صالح",
            show_alert=True
        )

        return GUEST_GOVERNORATE

    context.user_data[
        "guest_form"
    ]["المحافظة"] = governorate

    await query.edit_message_text(
        f"✅ المحافظة: {governorate}\n\n"
        "6️⃣ اختر اسم الفندق:",
        reply_markup=hotel_keyboard(
            "guest_hotel_select"
        )
    )

    return GUEST_HOTEL


# =========================================================
# الفندق - زر
# =========================================================

async def guest_hotel_button(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    try:

        index = int(
            query.data.split(":")[1]
        )

        selected_hotel = HOTELS[index]

    except Exception:

        await query.answer(
            "اختيار غير صالح",
            show_alert=True
        )

        return GUEST_HOTEL

    account_hotel = context.user_data.get(
        "guest_account_hotel"
    )

    # حماية مهمة:
    # الفندق لا يستطيع تسجيل بيانات باسم فندق آخر
    if (
        account_hotel
        and selected_hotel != account_hotel
    ):

        await query.answer(
            "لا يمكنك اختيار فندق آخر غير الفندق المرتبط بحسابك.",
            show_alert=True
        )

        return GUEST_HOTEL

    context.user_data[
        "guest_form"
    ]["اسم الفندق"] = selected_hotel

    await query.edit_message_text(
        f"✅ الفندق: {selected_hotel}\n\n"
        "7️⃣ رقم الجناح:\n\n"
        "إذا لم يوجد جناح اكتب: لا يوجد"
    )

    return GUEST_SUITE


# =========================================================
# الجناح
# =========================================================

async def guest_suite(update, context):

    text = update.message.text.strip()

    if not text:

        await update.message.reply_text(
            "❌ يرجى إدخال رقم الجناح أو كتابة: لا يوجد"
        )

        return GUEST_SUITE

    context.user_data[
        "guest_form"
    ]["رقم الجناح"] = text

    await update.message.reply_text(
        "8️⃣ رقم الغرفة:"
    )

    return GUEST_ROOM


# =========================================================
# الغرفة
# =========================================================

async def guest_room(update, context):

    text = update.message.text.strip()

    if not text:

        await update.message.reply_text(
            "❌ يرجى إدخال رقم الغرفة."
        )

        return GUEST_ROOM

    context.user_data[
        "guest_form"
    ]["رقم الغرفة"] = text

    await update.message.reply_text(
        "9️⃣ تاريخ النزول:"
    )

    return GUEST_CHECKIN


# =========================================================
# تاريخ النزول
# =========================================================

async def guest_checkin(update, context):

    text = update.message.text.strip()

    if not text:

        await update.message.reply_text(
            "❌ يرجى إدخال تاريخ النزول."
        )

        return GUEST_CHECKIN

    context.user_data[
        "guest_form"
    ]["تاريخ النزول"] = text

    await update.message.reply_text(
        "🔟 مدة الإقامة:"
    )

    return GUEST_DURATION


# =========================================================
# مدة الإقامة
# =========================================================

async def guest_duration(update, context):

    text = update.message.text.strip()

    if not text:

        await update.message.reply_text(
            "❌ يرجى إدخال مدة الإقامة."
        )

        return GUEST_DURATION

    context.user_data[
        "guest_form"
    ]["مدة الإقامة"] = text

    await update.message.reply_text(
        "1️⃣1️⃣ سبب الإقامة:"
    )

    return GUEST_REASON


# =========================================================
# سبب الإقامة
# =========================================================

async def guest_reason(update, context):

    text = update.message.text.strip()

    if not text:

        await update.message.reply_text(
            "❌ يرجى إدخال سبب الإقامة."
        )

        return GUEST_REASON

    context.user_data[
        "guest_form"
    ]["سبب الإقامة"] = text

    await update.message.reply_text(
        "📷 الآن ننتقل إلى صور البطاقة الشخصية.\n\n"
        "🪪 الصورة الأولى:\n"
        "الوجه الأمامي للبطاقة الشخصية."
    )

    return GUEST_ID_FRONT


# =========================================================
# صورة الهوية الأمامية
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
        ].append(
            image_buffer
        )

        await update.message.reply_text(
            "✅ تم استلام الوجه الأمامي.\n\n"
            "🪪 الآن أرسل الوجه الخلفي:"
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
# صورة الهوية الخلفية
# =========================================================

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
        ].append(
            image_buffer
        )

        await update.message.reply_text(
            "✅ تم استلام الوجه الخلفي.\n\n"
            "⏳ يتم تجهيز البيانات..."
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
            "❌ حدث خطأ أثناء استلام الصورة."
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
        f"{form.get('اسم الفندق', '')}\n\n"

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

        f"🪪 الوجه الأمامي: "
        f"{'✅' if len(images) >= 1 else '❌'}\n"

        f"🪪 الوجه الخلفي: "
        f"{'✅' if len(images) >= 2 else '❌'}\n\n"

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
# إرسال للإدارة
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

    if not form:

        await query.edit_message_text(
            "❌ لا توجد بيانات لإرسالها."
        )

        return

    if len(images) != 2:

        await query.edit_message_text(
            "❌ يجب إرفاق صورتي البطاقة."
        )

        return

    if form.get("اسم الفندق") != hotel["hotel_name"]:

        await query.edit_message_text(
            "❌ لا يمكن إرسال البيانات.\n"
            "اسم الفندق لا يطابق الحساب المسجل."
        )

        return

    # =====================================================
    # حفظ البيانات خارج event loop
    # =====================================================

    guest_id = await asyncio.to_thread(
        save_guest,
        form,
        update,
        hotel["id"]
    )

    # =====================================================
    # إنشاء PDF خارج event loop
    # =====================================================

    pdf_file = await asyncio.to_thread(
        create_guest_pdf,
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

        f"📍 المحافظة: "
        f"{form.get('المحافظة', 'غير مذكور')}\n"

        f"🚪 الغرفة: "
        f"{form.get('رقم الغرفة', 'غير مذكور')}\n"

        f"📅 تاريخ النزول: "
        f"{form.get('تاريخ النزول', 'غير مذكور')}\n"

        f"🪪 صور البطاقة: أمامي + خلفي\n"

        f"🆔 رقم السجل: {guest_id}\n\n"

        "📎 التقرير الكامل مرفق بصيغة PDF."
    )

    if not ADMIN_TELEGRAM_ID:

        await query.edit_message_text(
            "⚠️ تم حفظ المعلومات، "
            "لكن ADMIN_TELEGRAM_ID غير محدد."
        )

        return

    try:

        pdf_file.seek(0)

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
            "لكن حدث خطأ أثناء إرسالها للإدارة."
        )

        return

    await query.edit_message_text(

        "بسم الله الرحمن الرحيم 🌿\n\n"

        "✅ تم إرسال معلومات النزيل بنجاح.\n\n"

        "📤 وصلت المعلومات إلى الإدارة.\n\n"

        "🪪 تم إرفاق الوجه الأمامي والخلفي "
        "للبطاقة الشخصية ضمن ملف PDF.\n\n"

        "👤 يمكنك تسجيل نزيل آخر من خلال:\n"
        "/new_guest"
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
        "guest_account_hotel",
        None
    )


# =========================================================
# إلغاء النموذج
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
        "guest_account_hotel",
        None
    )

    await query.edit_message_text(
        "❌ تم إلغاء تسجيل النزيل.\n\n"
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

    rows = await asyncio.to_thread(
        get_guests_by_date,
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

    pdf_file = await asyncio.to_thread(
        create_daily_pdf,
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

    rows = await asyncio.to_thread(
        get_guests_by_date,
        yesterday
    )

    if not rows:

        await update.message.reply_text(
            f"📋 لا توجد بيانات بتاريخ {yesterday}."
        )

        return

    pdf_file = await asyncio.to_thread(
        create_daily_pdf,
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

    rows = await asyncio.to_thread(
        get_guests_by_month,
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

    pdf_file = await asyncio.to_thread(
        create_daily_pdf,
        rows,
        current_month,
        "التقرير الشهري لقسم معلومات الفنادق"
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
# Callback الإدارة
#
# ملاحظة:
# admin_hotel_select لم يعد هنا.
# أصبح داخل ConversationHandler الخاص بإضافة الفندق.
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

    # -----------------------------------------------------
    # تعطيل الفندق
    # -----------------------------------------------------

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
            "⚠️ هل أنت متأكد من تعطيل هذا الفندق؟",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    # -----------------------------------------------------
    # تأكيد التعطيل
    # -----------------------------------------------------

    if data.startswith("confirm_delete:"):

        try:

            hotel_id = int(
                data.split(
                    ":",
                    1
                )[1]
            )

        except Exception:

            await query.edit_message_text(
                "❌ رقم الفندق غير صالح."
            )

            return

        await asyncio.to_thread(
            disable_hotel,
            hotel_id
        )

        await query.edit_message_text(
            "🗑️ تم تعطيل الفندق بنجاح."
        )

        return

    # -----------------------------------------------------
    # إلغاء التعطيل
    # -----------------------------------------------------

    if data == "cancel_delete":

        await query.edit_message_text(
            "❌ تم إلغاء العملية."
        )

        return

    # -----------------------------------------------------
    # تفعيل الفندق
    # -----------------------------------------------------

    if data.startswith("enable_hotel:"):

        try:

            hotel_id = int(
                data.split(
                    ":",
                    1
                )[1]
            )

        except Exception:

            await query.edit_message_text(
                "❌ رقم الفندق غير صالح."
            )

            return

        await asyncio.to_thread(
            enable_hotel,
            hotel_id
        )

        await query.edit_message_text(
            "♻️ تم تفعيل الفندق بنجاح."
        )

        return


# =========================================================
# إلغاء Conversation
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
# Conversation إضافة الفندق
# =========================================================

add_hotel_handler = ConversationHandler(

    entry_points=[
        CommandHandler(
            "add_hotel",
            add_hotel_start
        )
    ],

    states={

        # -------------------------------------------------
        # اختيار الفندق + إدخال اسم المستخدم
        # -------------------------------------------------

        ADD_HOTEL_USERNAME: [

            # اختيار الفندق من الأزرار
            CallbackQueryHandler(
                admin_hotel_select_button,
                pattern=r"^admin_hotel_select:"
            ),

            # إدخال اسم المستخدم بعد اختيار الفندق
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                add_hotel_username
            ),
        ],

        # -------------------------------------------------
        # كلمة المرور
        # -------------------------------------------------

        ADD_HOTEL_PASSWORD: [

            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                add_hotel_password
            ),
        ],
    },

    fallbacks=[

        CommandHandler(
            "cancel",
            cancel
        ),

        CommandHandler(
            "start",
            start
        ),
    ],

    allow_reentry=True
)


# =========================================================
# Conversation نموذج النزيل
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
                guest_governorate_button,
                pattern=r"^governorate_select:"
            )
        ],

        GUEST_HOTEL: [
            CallbackQueryHandler(
                guest_hotel_button,
                pattern=r"^guest_hotel_select:"
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
# Handlers الأساسية
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
# Callback الإدارة
#
# لا نضع admin_hotel_select هنا
# لأنه أصبح تابعاً لـ add_hotel_handler
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
# إلغاء النزيل
# =========================================================

app.add_handler(
    CallbackQueryHandler(
        cancel_guest_callback,
        pattern=r"^cancel_guest_form$"
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
            "استخدم قائمة الأوامر."
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
            "لتسجيل نزيل جديد:\n"
            "/new_guest"
        )

        return

    await update.message.reply_text(
        "🔐 يرجى تسجيل الدخول أولاً.\n\n"
        "/login"
    )


app.add_handler(
    MessageHandler(
        filters.ALL & ~filters.COMMAND,
        unknown_message
    )
)


# =========================================================
# التشغيل
# =========================================================

async def main():

    await asyncio.to_thread(
        init_database
    )

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

    await app.updater.start_polling(
        drop_pending_updates=True
    )

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
