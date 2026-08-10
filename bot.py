import os
import re
import sqlite3
import asyncio
import threading
import hashlib
import secrets
import logging

from io import BytesIO
from datetime import datetime, date, timedelta
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import (
    Update,
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
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
# إعدادات أساسية
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "").strip()

DATABASE_FILE = "hotel_reports.db"
IMAGE_FILE = "images.png"

PAGE_WIDTH, PAGE_HEIGHT = A4


# =========================================================
# قائمة الفنادق
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
# المحافظات / الدول
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
# حالات المحادثات
# =========================================================

LOGIN_USERNAME = 1
LOGIN_PASSWORD = 2

ADD_HOTEL_USERNAME = 10
ADD_HOTEL_PASSWORD = 11

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
GUEST_ID_FRONT = 30
GUEST_ID_BACK = 31


# =========================================================
# Logging
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


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
            TTFont("ArabicFont", ARABIC_FONT_PATH)
        )
        PDF_FONT = "ArabicFont"
    except Exception as e:
        logger.error("Arabic font error: %s", e)
        PDF_FONT = "Helvetica"
else:
    logger.warning("Arabic font not found")
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
    connection = sqlite3.connect(
        DATABASE_FILE,
        check_same_thread=False,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA cache_size=-20000")

    return connection


def init_database():
    connection = get_db()

    try:
        cursor = connection.cursor()

        cursor.execute("""
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

        cursor.execute("""
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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                telegram_user_id TEXT PRIMARY KEY,
                hotel_account_id INTEGER,
                login_time TEXT,
                active INTEGER DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_guests_record_date
            ON guests(record_date)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_guests_record_date_id
            ON guests(record_date, id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_guests_hotel
            ON guests(hotel)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_guests_governorate
            ON guests(governorate)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_guests_hotel_account
            ON guests(hotel_account_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotel_accounts_username
            ON hotel_accounts(username)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotel_accounts_hotel_active
            ON hotel_accounts(hotel_name, active)
        """)

        connection.commit()

        logger.info("Database initialized successfully")

    except Exception as e:
        logger.exception(
            "Database initialization error: %s",
            e,
        )

    finally:
        connection.close()


# =========================================================
# كلمات المرور
# =========================================================

def normalize_username(username):
    if username is None:
        return ""

    return str(username).strip().lower()


def hash_password(password, salt=None):
    password = str(password or "")

    if salt is None:
        salt = secrets.token_hex(16)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000,
    ).hex()

    return password_hash, salt


def verify_password(password, stored_hash, salt):
    if password is None:
        return False

    password_hash, _ = hash_password(
        str(password),
        salt,
    )

    return secrets.compare_digest(
        password_hash,
        str(stored_hash),
    )


# =========================================================
# حسابات الفنادق
# =========================================================

def create_hotel_account(
    hotel_name,
    username,
    password,
):
    hotel_name = str(hotel_name or "").strip()
    username = normalize_username(username)
    password = str(password or "")

    if hotel_name not in HOTELS:
        return False, None, "اسم الفندق غير موجود."

    if len(username) < 3:
        return False, None, "اسم المستخدم يجب أن يكون 3 أحرف على الأقل."

    if not re.match(
        r"^[a-z0-9_.-]+$",
        username,
    ):
        return (
            False,
            None,
            "اسم المستخدم يجب أن يحتوي على أحرف إنجليزية وأرقام فقط.",
        )

    if len(password) < 8:
        return (
            False,
            None,
            "كلمة المرور يجب أن تكون 8 أحرف على الأقل.",
        )

    password_hash, salt = hash_password(password)

    connection = get_db()

    try:
        cursor = connection.cursor()

        existing = cursor.execute("""
            SELECT id
            FROM hotel_accounts
            WHERE username = ? COLLATE NOCASE
            LIMIT 1
        """, (username,)).fetchone()

        if existing:
            return False, None, "اسم المستخدم مستخدم مسبقاً."

        existing_hotel = cursor.execute("""
            SELECT id
            FROM hotel_accounts
            WHERE hotel_name = ?
              AND active = 1
            LIMIT 1
        """, (hotel_name,)).fetchone()

        if existing_hotel:
            return (
                False,
                None,
                "يوجد حساب فعال لهذا الفندق مسبقاً.",
            )

        cursor.execute("""
            INSERT INTO hotel_accounts
            (
                hotel_name,
                username,
                password_hash,
                salt,
                active,
                telegram_user_id,
                created_at
            )
            VALUES (?, ?, ?, ?, 1, NULL, ?)
        """, (
            hotel_name,
            username,
            password_hash,
            salt,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        ))

        hotel_id = cursor.lastrowid

        connection.commit()

        return True, hotel_id, None

    except sqlite3.IntegrityError:
        connection.rollback()
        return False, None, "اسم المستخدم مستخدم مسبقاً."

    except Exception as e:
        connection.rollback()

        logger.exception(
            "Create hotel account error: %s",
            e,
        )

        return False, None, "حدث خطأ أثناء إنشاء الحساب."

    finally:
        connection.close()


def authenticate_hotel(username, password):
    username = normalize_username(username)

    if not username or not password:
        return None

    connection = get_db()

    try:
        account = connection.execute("""
            SELECT *
            FROM hotel_accounts
            WHERE username = ? COLLATE NOCASE
            LIMIT 1
        """, (username,)).fetchone()

        if not account:
            return None

        if int(account["active"] or 0) != 1:
            return None

        if not verify_password(
            password,
            account["password_hash"],
            account["salt"],
        ):
            return None

        return account

    except Exception as e:
        logger.exception(
            "Authentication error: %s",
            e,
        )

        return None

    finally:
        connection.close()


def get_all_hotels():
    connection = get_db()

    try:
        return connection.execute("""
            SELECT
                id,
                hotel_name,
                username,
                active,
                telegram_user_id,
                created_at
            FROM hotel_accounts
            ORDER BY id ASC
        """).fetchall()

    finally:
        connection.close()


def disable_hotel(hotel_id):
    connection = get_db()

    try:
        cursor = connection.cursor()

        cursor.execute("""
            UPDATE hotel_accounts
            SET active = 0
            WHERE id = ?
        """, (hotel_id,))

        cursor.execute("""
            DELETE FROM sessions
            WHERE hotel_account_id = ?
        """, (hotel_id,))

        cursor.execute("""
            UPDATE hotel_accounts
            SET telegram_user_id = NULL
            WHERE id = ?
        """, (hotel_id,))

        connection.commit()

    finally:
        connection.close()


def enable_hotel(hotel_id):
    connection = get_db()

    try:
        connection.execute("""
            UPDATE hotel_accounts
            SET active = 1
            WHERE id = ?
        """, (hotel_id,))

        connection.commit()

    finally:
        connection.close()


# =========================================================
# الجلسات
# =========================================================

def create_session(
    telegram_user_id,
    hotel_account_id,
):
    telegram_user_id = str(telegram_user_id)

    connection = get_db()

    try:
        cursor = connection.cursor()

        cursor.execute("""
            DELETE FROM sessions
            WHERE telegram_user_id = ?
        """, (telegram_user_id,))

        cursor.execute("""
            UPDATE hotel_accounts
            SET telegram_user_id = NULL
            WHERE id = ?
        """, (hotel_account_id,))

        cursor.execute("""
            INSERT INTO sessions
            (
                telegram_user_id,
                hotel_account_id,
                login_time,
                active
            )
            VALUES (?, ?, ?, 1)
        """, (
            telegram_user_id,
            hotel_account_id,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        ))

        cursor.execute("""
            UPDATE hotel_accounts
            SET telegram_user_id = ?
            WHERE id = ?
        """, (
            telegram_user_id,
            hotel_account_id,
        ))

        connection.commit()

    finally:
        connection.close()


def logout_session(telegram_user_id):
    telegram_user_id = str(telegram_user_id)

    connection = get_db()

    try:
        cursor = connection.cursor()

        session = cursor.execute("""
            SELECT hotel_account_id
            FROM sessions
            WHERE telegram_user_id = ?
        """, (telegram_user_id,)).fetchone()

        if session:
            cursor.execute("""
                UPDATE hotel_accounts
                SET telegram_user_id = NULL
                WHERE id = ?
                  AND telegram_user_id = ?
            """, (
                session["hotel_account_id"],
                telegram_user_id,
            ))

        cursor.execute("""
            DELETE FROM sessions
            WHERE telegram_user_id = ?
        """, (telegram_user_id,))

        connection.commit()

    finally:
        connection.close()


def get_logged_hotel(telegram_user_id):
    connection = get_db()

    try:
        return connection.execute("""
            SELECT h.*
            FROM sessions s
            JOIN hotel_accounts h
              ON h.id = s.hotel_account_id
            WHERE s.telegram_user_id = ?
              AND s.active = 1
              AND h.active = 1
            LIMIT 1
        """, (str(telegram_user_id),)).fetchone()

    finally:
        connection.close()


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
    hotel_account_id=None,
):
    now = datetime.now()

    user_id = ""
    username = ""

    if update.effective_user:
        user_id = str(update.effective_user.id)
        username = (
            update.effective_user.username
            or ""
        )

    connection = get_db()

    try:
        cursor = connection.cursor()

        cursor.execute("""
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
        """, (
            guest.get(
                "الاسم الثلاثي",
                "غير مذكور",
            ),
            guest.get(
                "اسم الأم",
                "غير مذكور",
            ),
            guest.get(
                "مكان وتاريخ الولادة",
                "غير مذكور",
            ),
            guest.get(
                "السكن الأصلي",
                "غير مذكور",
            ),
            guest.get(
                "المحافظة",
                "غير مذكور",
            ),
            guest.get(
                "اسم الفندق",
                "غير مذكور",
            ),
            guest.get(
                "رقم الجناح",
                "غير مذكور",
            ),
            guest.get(
                "رقم الغرفة",
                "غير مذكور",
            ),
            guest.get(
                "تاريخ النزول",
                "غير مذكور",
            ),
            guest.get(
                "مدة الإقامة",
                "غير مذكور",
            ),
            guest.get(
                "سبب الإقامة",
                "غير مذكور",
            ),
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            user_id,
            username,
            hotel_account_id,
        ))

        guest_id = cursor.lastrowid

        connection.commit()

        return guest_id

    finally:
        connection.close()


# =========================================================
# استعلامات التقارير
# =========================================================

def get_guests_by_date(target_date):
    connection = get_db()

    try:
        return connection.execute("""
            SELECT *
            FROM guests
            WHERE record_date = ?
            ORDER BY id ASC
        """, (target_date,)).fetchall()

    finally:
        connection.close()


def next_month(year_month):
    year, month = map(
        int,
        year_month.split("-"),
    )

    if month == 12:
        return f"{year + 1:04d}-01"

    return f"{year:04d}-{month + 1:02d}"


def get_guests_by_month(year_month):
    connection = get_db()

    try:
        return connection.execute("""
            SELECT *
            FROM guests
            WHERE record_date >= ?
              AND record_date < ?
            ORDER BY id ASC
        """, (
            f"{year_month}-01",
            f"{next_month(year_month)}-01",
        )).fetchall()

    finally:
        connection.close()


# =========================================================
# أسماء الملفات
# =========================================================

def safe_filename(name):
    if not name:
        name = "تقرير_نزيل"

    name = re.sub(
        r'[\\/:*?"<>|]',
        "",
        str(name),
    )

    name = re.sub(
        r"\s+",
        "_",
        name.strip(),
    )

    if not name:
        name = "تقرير_نزيل"

    return name + ".pdf"


# =========================================================
# PDF - أدوات التصميم
# =========================================================

PDF_BLUE = colors.HexColor("#17365D")
PDF_LIGHT_BLUE = colors.HexColor("#D9EAF7")
PDF_BORDER = colors.HexColor("#B7C9D6")
PDF_LIGHT = colors.HexColor("#F6F8FA")
PDF_TEXT = colors.HexColor("#333333")
PDF_GREY = colors.HexColor("#6B7280")


def draw_pdf_header(
    pdf,
    title,
    subtitle=None,
):
    pdf.setFillColor(PDF_BLUE)

    pdf.roundRect(
        35,
        PAGE_HEIGHT - 105,
        PAGE_WIDTH - 70,
        70,
        8,
        fill=1,
        stroke=0,
    )

    pdf.setFillColor(colors.white)

    pdf.setFont(
        PDF_FONT,
        17,
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 60,
        arabic_text(title),
    )

    if subtitle:
        pdf.setFont(
            PDF_FONT,
            9,
        )

        pdf.drawCentredString(
            PAGE_WIDTH / 2,
            PAGE_HEIGHT - 82,
            arabic_text(subtitle),
        )

    pdf.setFillColor(colors.black)


def draw_pdf_footer(
    pdf,
    hotel_name=None,
    page_number=None,
):
    pdf.setStrokeColor(PDF_BORDER)

    pdf.line(
        40,
        42,
        PAGE_WIDTH - 40,
        42,
    )

    pdf.setFont(
        PDF_FONT,
        7,
    )

    pdf.setFillColor(PDF_GREY)

    if hotel_name:
        pdf.drawRightString(
            PAGE_WIDTH - 45,
            27,
            arabic_text(
                f"الفندق: {hotel_name}"
            ),
        )

    if page_number is not None:
        pdf.drawCentredString(
            PAGE_WIDTH / 2,
            27,
            arabic_text(
                f"صفحة {page_number}"
            ),
        )

    pdf.setFillColor(colors.black)


def draw_pdf_section_title(
    pdf,
    y,
    title,
):
    pdf.setFillColor(PDF_LIGHT_BLUE)

    pdf.roundRect(
        45,
        y - 25,
        PAGE_WIDTH - 90,
        30,
        5,
        fill=1,
        stroke=0,
    )

    pdf.setFillColor(PDF_BLUE)

    pdf.setFont(
        PDF_FONT,
        12,
    )

    pdf.drawRightString(
        PAGE_WIDTH - 60,
        y - 15,
        arabic_text(title),
    )

    pdf.setFillColor(colors.black)

    return y - 42


def draw_pdf_field(
    pdf,
    y,
    number,
    label,
    value,
):
    value = str(
        value
        if value is not None
        else ""
    )

    # رقم الحقل
    pdf.setFillColor(PDF_BLUE)

    pdf.roundRect(
        47,
        y - 25,
        28,
        24,
        4,
        fill=1,
        stroke=0,
    )

    pdf.setFillColor(colors.white)

    pdf.setFont(
        PDF_FONT,
        9,
    )

    pdf.drawCentredString(
        61,
        y - 17,
        str(number),
    )

    # خلفية البيانات
    pdf.setFillColor(PDF_LIGHT)

    pdf.roundRect(
        82,
        y - 27,
        PAGE_WIDTH - 127,
        28,
        4,
        fill=1,
        stroke=0,
    )

    # اسم الحقل
    pdf.setFillColor(PDF_BLUE)

    pdf.setFont(
        PDF_FONT,
        9,
    )

    pdf.drawRightString(
        PAGE_WIDTH - 60,
        y - 16,
        arabic_text(
            f"{label}:"
        ),
    )

    # خط تحت الحقل
    pdf.setStrokeColor(PDF_BORDER)

    pdf.line(
        95,
        y - 30,
        PAGE_WIDTH - 60,
        y - 30,
    )

    # القيمة
    pdf.setFillColor(PDF_TEXT)

    pdf.setFont(
        PDF_FONT,
        9,
    )

    value_text = arabic_text(value)

    pdf.drawString(
        95,
        y - 16,
        value_text,
    )

    pdf.setFillColor(colors.black)

    return y - 38


def draw_id_image(
    pdf,
    image_data,
    title,
    y,
):
    try:
        image_data.seek(0)

        image = ImageReader(image_data)

        max_width = PAGE_WIDTH - 100
        max_height = 410

        iw, ih = image.getSize()

        if iw <= 0 or ih <= 0:
            return y - 30

        scale = min(
            max_width / iw,
            max_height / ih,
        )

        width = iw * scale
        height = ih * scale

        pdf.setFillColor(PDF_BLUE)

        pdf.setFont(
            PDF_FONT,
            12,
        )

        pdf.drawCentredString(
            PAGE_WIDTH / 2,
            y,
            arabic_text(title),
        )

        y -= 25

        # إطار الصورة
        pdf.setStrokeColor(PDF_BORDER)

        pdf.setLineWidth(1.2)

        pdf.roundRect(
            (PAGE_WIDTH - width) / 2 - 8,
            y - height - 8,
            width + 16,
            height + 16,
            6,
            fill=0,
            stroke=1,
        )

        pdf.drawImage(
            image,
            (PAGE_WIDTH - width) / 2,
            y - height,
            width=width,
            height=height,
            preserveAspectRatio=True,
            anchor="sw",
            mask="auto",
        )

        return y - height - 35

    except Exception as e:
        logger.exception(
            "PDF image error: %s",
            e,
        )

        return y - 30


# =========================================================
# PDF - تقرير بيانات النزيل
# =========================================================

def create_guest_pdf(
    guest,
    images=None,
):
    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4,
    )

    hotel_name = guest.get(
        "اسم الفندق",
        "الفندق",
    )

    # -----------------------------------------------------
    # الصفحة الأولى
    # -----------------------------------------------------

    draw_pdf_header(
        pdf,
        hotel_name,
        "تقرير بيانات نزيل",
    )

    y = PAGE_HEIGHT - 130

    pdf.setFont(
        PDF_FONT,
        8,
    )

    pdf.setFillColor(PDF_GREY)

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M"
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            f"تاريخ إنشاء التقرير: {now}"
        ),
    )

    y -= 35

    y = draw_pdf_section_title(
        pdf,
        y,
        "بيانات النزيل",
    )

    fields = [
        (
            "الاسم الثلاثي",
            guest.get(
                "الاسم الثلاثي",
                "غير مذكور",
            ),
        ),
        (
            "اسم الأم",
            guest.get(
                "اسم الأم",
                "غير مذكور",
            ),
        ),
        (
            "مكان وتاريخ الولادة",
            guest.get(
                "مكان وتاريخ الولادة",
                "غير مذكور",
            ),
        ),
        (
            "السكن الأصلي",
            guest.get(
                "السكن الأصلي",
                "غير مذكور",
            ),
        ),
        (
            "المحافظة",
            guest.get(
                "المحافظة",
                "غير مذكور",
            ),
        ),
        (
            "اسم الفندق",
            hotel_name,
        ),
        (
            "رقم الجناح",
            guest.get(
                "رقم الجناح",
                "غير مذكور",
            ),
        ),
        (
            "رقم الغرفة",
            guest.get(
                "رقم الغرفة",
                "غير مذكور",
            ),
        ),
        (
            "تاريخ النزول",
            guest.get(
                "تاريخ النزول",
                "غير مذكور",
            ),
        ),
        (
            "مدة الإقامة",
            guest.get(
                "مدة الإقامة",
                "غير مذكور",
            ),
        ),
        (
            "سبب الإقامة",
            guest.get(
                "سبب الإقامة",
                "غير مذكور",
            ),
        ),
    ]

    for number, (label, value) in enumerate(
        fields,
        start=1,
    ):
        if y < 80:
            draw_pdf_footer(
                pdf,
                hotel_name,
                1,
            )

            pdf.showPage()

            draw_pdf_header(
                pdf,
                hotel_name,
                "تقرير بيانات نزيل",
            )

            y = PAGE_HEIGHT - 130

        y = draw_pdf_field(
            pdf,
            y,
            number,
            label,
            value,
        )

    # -----------------------------------------------------
    # صور البطاقة
    # -----------------------------------------------------

    if images and len(images) >= 2:

        # الوجه الأمامي
        pdf.showPage()

        draw_pdf_header(
            pdf,
            hotel_name,
            "صور البطاقة الشخصية",
        )

        y = PAGE_HEIGHT - 135

        y = draw_pdf_section_title(
            pdf,
            y,
            "البطاقة الشخصية - الوجه الأمامي",
        )

        draw_id_image(
            pdf,
            images[0],
            "الوجه الأمامي للبطاقة الشخصية",
            y - 15,
        )

        draw_pdf_footer(
            pdf,
            hotel_name,
            2,
        )

        # الوجه الخلفي
        pdf.showPage()

        draw_pdf_header(
            pdf,
            hotel_name,
            "صور البطاقة الشخصية",
        )

        y = PAGE_HEIGHT - 135

        y = draw_pdf_section_title(
            pdf,
            y,
            "البطاقة الشخصية - الوجه الخلفي",
        )

        draw_id_image(
            pdf,
            images[1],
            "الوجه الخلفي للبطاقة الشخصية",
            y - 15,
        )

        draw_pdf_footer(
            pdf,
            hotel_name,
            3,
        )

    else:
        draw_pdf_footer(
            pdf,
            hotel_name,
            1,
        )

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# PDF - التقرير اليومي / الشهري
# =========================================================

def create_daily_pdf(
    rows,
    target_date,
    title="تقرير عمل قسم معلومات الفنادق",
):
    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4,
    )

    page_number = 1

    # -----------------------------------------------------
    # الترويسة
    # -----------------------------------------------------

    pdf.setFillColor(PDF_BLUE)

    pdf.roundRect(
        35,
        PAGE_HEIGHT - 105,
        PAGE_WIDTH - 70,
        70,
        8,
        fill=1,
        stroke=0,
    )

    pdf.setFillColor(colors.white)

    pdf.setFont(
        PDF_FONT,
        16,
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 60,
        arabic_text(title),
    )

    pdf.setFillColor(colors.black)

    y = PAGE_HEIGHT - 120

    pdf.setFont(
        PDF_FONT,
        11,
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            f"التاريخ: {target_date}"
        ),
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

    # -----------------------------------------------------
    # إجمالي النزلاء
    # -----------------------------------------------------

    pdf.setFillColor(PDF_LIGHT_BLUE)

    pdf.roundRect(
        45,
        y - 35,
        PAGE_WIDTH - 90,
        40,
        6,
        fill=1,
        stroke=0,
    )

    pdf.setFillColor(PDF_BLUE)

    pdf.setFont(
        PDF_FONT,
        14,
    )

    pdf.drawRightString(
        PAGE_WIDTH - 60,
        y - 21,
        arabic_text(
            f"إجمالي النزلاء: {total}"
        ),
    )

    y -= 60

    # -----------------------------------------------------
    # دالة أقسام الإحصائيات
    # -----------------------------------------------------

    def draw_counter_section(
        section_number,
        section_title,
        counter,
    ):
        nonlocal y
        nonlocal page_number

        if y < 150:
            draw_pdf_footer(
                pdf,
                None,
                page_number,
            )

            pdf.showPage()

            page_number += 1

            y = PAGE_HEIGHT - 120

        # عنوان القسم
        pdf.setFillColor(PDF_LIGHT_BLUE)

        pdf.roundRect(
            45,
            y - 30,
            PAGE_WIDTH - 90,
            35,
            6,
            fill=1,
            stroke=0,
        )

        pdf.setFillColor(PDF_BLUE)

        pdf.setFont(
            PDF_FONT,
            12,
        )

        pdf.drawRightString(
            PAGE_WIDTH - 60,
            y - 19,
            arabic_text(
                f"{section_number}. {section_title}"
            ),
        )

        y -= 50

        pdf.setFont(
            PDF_FONT,
            10,
        )

        for item_number, (
            name,
            count,
        ) in enumerate(
            counter.most_common(),
            start=1,
        ):

            if y < 70:
                draw_pdf_footer(
                    pdf,
                    None,
                    page_number,
                )

                pdf.showPage()

                page_number += 1

                y = PAGE_HEIGHT - 120

            # رقم
            pdf.setFillColor(PDF_BLUE)

            pdf.roundRect(
                55,
                y - 20,
                23,
                21,
                4,
                fill=1,
                stroke=0,
            )

            pdf.setFillColor(colors.white)

            pdf.setFont(
                PDF_FONT,
                8,
            )

            pdf.drawCentredString(
                66.5,
                y - 14,
                str(item_number),
            )

            # النص
            pdf.setFillColor(PDF_TEXT)

            pdf.setFont(
                PDF_FONT,
                10,
            )

            pdf.drawRightString(
                PAGE_WIDTH - 70,
                y - 14,
                arabic_text(
                    f"{name}: {count}"
                ),
            )

            # تسطير
            pdf.setStrokeColor(PDF_BORDER)

            pdf.line(
                90,
                y - 25,
                PAGE_WIDTH - 70,
                y - 25,
            )

            y -= 30

        y -= 15

    # -----------------------------------------------------
    # الأقسام
    # -----------------------------------------------------

    draw_counter_section(
        1,
        "التوزيع حسب المحافظة",
        governorates,
    )

    draw_counter_section(
        2,
        "توزيع النزلاء على الفنادق",
        hotels,
    )

    draw_counter_section(
        3,
        "أسباب الإقامة",
        reasons,
    )

    # -----------------------------------------------------
    # التذييل
    # -----------------------------------------------------

    draw_pdf_footer(
        pdf,
        None,
        page_number,
    )

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# رسالة الترحيب والصورة
# =========================================================

async def send_welcome_image(update: Update):
    if not update.message:
        return

    if not os.path.exists(IMAGE_FILE):
        return

    try:
        with open(
            IMAGE_FILE,
            "rb",
        ) as photo:
            await update.message.reply_photo(
                photo=photo
            )

    except Exception as e:
        logger.exception(
            "Welcome image error: %s",
            e,
        )


# =========================================================
# أوامر الإدارة
# =========================================================

async def set_admin_commands(
    application,
    chat_id,
):
    commands = [
        BotCommand(
            "start",
            "🏠 الرئيسية",
        ),
        BotCommand(
            "add_hotel",
            "🏨 إنشاء حساب فندق",
        ),
        BotCommand(
            "hotels",
            "📋 حسابات الفنادق",
        ),
        BotCommand(
            "delete_hotel",
            "🗑️ تعطيل فندق",
        ),
        BotCommand(
            "daily",
            "📊 التقرير اليومي",
        ),
        BotCommand(
            "yesterday",
            "📅 تقرير أمس",
        ),
        BotCommand(
            "monthly",
            "📈 التقرير الشهري",
        ),
        BotCommand(
            "logout",
            "🚪 تسجيل الخروج",
        ),
    ]

    try:
        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(
                chat_id
            ),
        )

    except Exception as e:
        logger.error(
            "Admin commands error: %s",
            e,
        )


async def set_hotel_commands(
    application,
    chat_id,
):
    commands = [
        BotCommand(
            "start",
            "🏠 بدء",
        ),
        BotCommand(
            "login",
            "🔐 تسجيل الدخول",
        ),
    ]

    try:
        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(
                chat_id
            ),
        )

    except Exception as e:
        logger.error(
            "Hotel commands error: %s",
            e,
        )


async def set_logged_hotel_commands(
    application,
    chat_id,
):
    commands = [
        BotCommand(
            "start",
            "🏠 الرئيسية",
        ),
        BotCommand(
            "new_guest",
            "👤 تسجيل نزيل",
        ),
        BotCommand(
            "logout",
            "🚪 تسجيل الخروج",
        ),
    ]

    try:
        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(
                chat_id
            ),
        )

    except Exception as e:
        logger.error(
            "Logged hotel commands error: %s",
            e,
        )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        if not update.effective_user:
            return

        if not update.message:
            return

        user_id = update.effective_user.id

        # -------------------------------------------------
        # المدير
        # -------------------------------------------------

        if is_admin(update):

            await set_admin_commands(
                context.application,
                update.effective_chat.id,
            )

            await send_welcome_image(update)

            await update.message.reply_text(
                "بسم الله الرحمن الرحيم 🌿\n\n"
                "﴿ وَقُلْ رَبِّ زِدْنِي عِلْمًا ﴾\n"
                "سورة طه - الآية 114\n\n"
                "السلام عليكم ورحمة الله وبركاته\n\n"
                "🏨 أهلاً وسهلاً بك في نظام "
                "معلومات الفنادق.\n\n"
                "👨‍💼 تم التعرف على حسابك "
                "كحساب مدير.\n\n"
                "يمكنك إدارة حسابات الفنادق "
                "ومتابعة النزلاء والتقارير.\n\n"
                "🏨 إنشاء حساب فندق:\n"
                "/add_hotel\n\n"
                "📋 حسابات الفنادق:\n"
                "/hotels\n\n"
                "📊 التقرير اليومي:\n"
                "/daily"
            )

            return

        # -------------------------------------------------
        # الفندق المسجل
        # -------------------------------------------------

        hotel = await asyncio.to_thread(
            get_logged_hotel,
            user_id,
        )

        if hotel:

            await set_logged_hotel_commands(
                context.application,
                update.effective_chat.id,
            )

            await send_welcome_image(update)

            await update.message.reply_text(
                "بسم الله الرحمن الرحيم 🌿\n\n"
                "﴿ وَقُلْ رَبِّ زِدْنِي عِلْمًا ﴾\n"
                "سورة طه - الآية 114\n\n"
                "السلام عليكم ورحمة الله وبركاته\n\n"
                "🏨 أهلاً وسهلاً بكم\n\n"
                f"🏨 الفندق: {hotel['hotel_name']}\n\n"
                "✅ تم تسجيل الدخول بنجاح.\n\n"
                "👤 لتسجيل نزيل جديد:\n"
                "/new_guest"
            )

            return

        # -------------------------------------------------
        # مستخدم غير مسجل
        # -------------------------------------------------

        await set_hotel_commands(
            context.application,
            update.effective_chat.id,
        )

        await send_welcome_image(update)

        await update.message.reply_text(
            "بسم الله الرحمن الرحيم 🌿\n\n"
            "﴿ وَقُلْ رَبِّ زِدْنِي عِلْمًا ﴾\n"
            "سورة طه - الآية 114\n\n"
            "السلام عليكم ورحمة الله وبركاته\n\n"
            "🏨 أهلاً وسهلاً ومرحباً بكم\n"
            "في نظام معلومات الفنادق.\n\n"
            "🔐 للبدء يرجى استخدام:\n"
            "/login"
        )

    except Exception as e:
        logger.exception(
            "START ERROR: %s",
            e,
        )

        try:
            if update.message:
                await update.message.reply_text(
                    "⚠️ حدث خطأ مؤقت.\n\n"
                    "يرجى المحاولة مرة أخرى."
                )
        except Exception:
            pass


# =========================================================
# تسجيل الدخول
# =========================================================

async def login_start(
    update,
    context,
):
    if not update.message:
        return ConversationHandler.END

    if is_admin(update):
        await update.message.reply_text(
            "👨‍💼 أنت المدير ولا تحتاج إلى تسجيل الدخول."
        )

        return ConversationHandler.END

    hotel = await asyncio.to_thread(
        get_logged_hotel,
        update.effective_user.id,
    )

    if hotel:
        await update.message.reply_text(
            "✅ أنت مسجل الدخول بالفعل.\n\n"
            f"🏨 الفندق: {hotel['hotel_name']}"
        )

        return ConversationHandler.END

    context.user_data.pop(
        "login_username",
        None,
    )

    await update.message.reply_text(
        "🔐 تسجيل دخول الفندق\n\n"
        "👤 أرسل اسم المستخدم:"
    )

    return LOGIN_USERNAME


async def login_username(
    update,
    context,
):
    username = normalize_username(
        update.message.text
    )

    if not username:
        await update.message.reply_text(
            "❌ اسم المستخدم فارغ.\n"
            "أرسل اسم المستخدم من جديد:"
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
    update,
    context,
):
    password = update.message.text

    username = context.user_data.get(
        "login_username"
    )

    if not username:
        await update.message.reply_text(
            "❌ انتهت عملية تسجيل الدخول.\n\n"
            "استخدم /login من جديد."
        )

        return ConversationHandler.END

    account = await asyncio.to_thread(
        authenticate_hotel,
        username,
        password,
    )

    if not account:
        context.user_data.pop(
            "login_username",
            None,
        )

        await update.message.reply_text(
            "❌ اسم المستخدم أو كلمة المرور "
            "غير صحيحة.\n\n"
            "حاول مرة أخرى باستخدام:\n"
            "/login"
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
        and str(old_telegram_id)
        != current_telegram_id
    ):
        await update.message.reply_text(
            "⚠️ هذا الحساب مرتبط مسبقاً "
            "بحساب Telegram آخر.\n\n"
            "يرجى التواصل مع الإدارة."
        )

        return ConversationHandler.END

    await asyncio.to_thread(
        create_session,
        current_telegram_id,
        account["id"],
    )

    context.user_data.pop(
        "login_username",
        None,
    )

    await set_logged_hotel_commands(
        context.application,
        update.effective_chat.id,
    )

    await update.message.reply_text(
        "✅ تم تسجيل الدخول بنجاح.\n\n"
        f"🏨 الفندق: {account['hotel_name']}\n"
        f"👤 اسم المستخدم: {account['username']}\n\n"
        "يمكنك الآن تسجيل بيانات النزلاء.\n\n"
        "👤 تسجيل نزيل جديد:\n"
        "/new_guest"
    )

    return ConversationHandler.END


# =========================================================
# تسجيل الخروج
# =========================================================

async def logout(
    update,
    context,
):
    if is_admin(update):
        await update.message.reply_text(
            "👨‍💼 حساب المدير لا يحتاج إلى تسجيل خروج."
        )

        return

    await asyncio.to_thread(
        logout_session,
        update.effective_user.id,
    )

    context.user_data.clear()

    await set_hotel_commands(
        context.application,
        update.effective_chat.id,
    )

    await update.message.reply_text(
        "🚪 تم تسجيل الخروج بنجاح.\n\n"
        "يمكنك تسجيل الدخول من جديد عبر:\n"
        "/login"
    )


# =========================================================
# لوحة الفنادق
# =========================================================

def hotel_keyboard(prefix):
    keyboard = []
    row = []

    for index, hotel in enumerate(HOTELS):
        row.append(
            InlineKeyboardButton(
                f"🏨 {hotel}",
                callback_data=f"{prefix}:{index}",
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
                callback_data=(
                    f"governorate_select:{index}"
                ),
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
# إنشاء حساب فندق
# =========================================================

async def add_hotel_start(
    update,
    context,
):
    if not is_admin(update):
        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return ConversationHandler.END

    for key in (
        "new_hotel_name",
        "new_hotel_username",
        "new_hotel_password",
    ):
        context.user_data.pop(
            key,
            None,
        )

    await update.message.reply_text(
        "🏨 إنشاء حساب فندق جديد\n\n"
        "اختر الفندق:",
        reply_markup=hotel_keyboard(
            "admin_create_hotel"
        ),
    )

    return ADD_HOTEL_USERNAME


async def add_hotel_username(
    update,
    context,
):
    if not is_admin(update):
        return ConversationHandler.END

    hotel_name = context.user_data.get(
        "new_hotel_name"
    )

    if not hotel_name:
        await update.message.reply_text(
            "❌ لم يتم اختيار الفندق.\n\n"
            "ابدأ من جديد:\n"
            "/add_hotel"
        )

        return ConversationHandler.END

    username = normalize_username(
        update.message.text
    )

    if not re.match(
        r"^[a-z0-9_.-]{3,50}$",
        username,
    ):
        await update.message.reply_text(
            "❌ اسم المستخدم غير صالح.\n\n"
            "استخدم أحرفاً إنجليزية صغيرة "
            "وأرقاماً فقط.\n\n"
            "مثال:\n"
            "hotel_qurtuba"
        )

        return ADD_HOTEL_USERNAME

    context.user_data[
        "new_hotel_username"
    ] = username

    await update.message.reply_text(
        "🔑 أرسل كلمة المرور.\n\n"
        "يجب ألا تقل عن 8 أحرف."
    )

    return ADD_HOTEL_PASSWORD


async def add_hotel_password(
    update,
    context,
):
    if not is_admin(update):
        return ConversationHandler.END

    password = update.message.text

    hotel_name = context.user_data.get(
        "new_hotel_name"
    )

    username = context.user_data.get(
        "new_hotel_username"
    )

    if not hotel_name:
        await update.message.reply_text(
            "❌ لم يتم اختيار الفندق.\n\n"
            "/add_hotel"
        )

        return ConversationHandler.END

    if not username:
        await update.message.reply_text(
            "❌ لم يتم تسجيل اسم المستخدم.\n\n"
            "/add_hotel"
        )

        return ConversationHandler.END

    if len(password) < 8:
        await update.message.reply_text(
            "❌ كلمة المرور يجب أن تكون "
            "8 أحرف على الأقل.\n\n"
            "أرسل كلمة مرور جديدة:"
        )

        return ADD_HOTEL_PASSWORD

    await update.message.reply_text(
        "⏳ جارٍ إنشاء حساب الفندق..."
    )

    success, hotel_id, error = (
        await asyncio.to_thread(
            create_hotel_account,
            hotel_name,
            username,
            password,
        )
    )

    if not success:
        await update.message.reply_text(
            "❌ لم يتم إنشاء الحساب.\n\n"
            f"السبب:\n{error}\n\n"
            "يمكنك المحاولة من جديد عبر:\n"
            "/add_hotel"
        )

        return ConversationHandler.END

    for key in (
        "new_hotel_name",
        "new_hotel_username",
        "new_hotel_password",
    ):
        context.user_data.pop(
            key,
            None,
        )

    await update.message.reply_text(
        "✅ تم إنشاء حساب الفندق بنجاح.\n\n"
        f"🏨 الفندق: {hotel_name}\n"
        f"👤 اسم المستخدم: {username}\n"
        f"🆔 رقم الحساب: {hotel_id}\n\n"
        "🔐 تم حفظ كلمة المرور بشكل مشفر.\n\n"
        "📱 يمكن لصاحب الفندق استخدام:\n"
        "/login"
    )

    return ConversationHandler.END


# =========================================================
# قائمة الفنادق
# =========================================================

async def hotels_list(
    update,
    context,
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
            f"🆔 الحساب: {hotel['id']}\n"
            f"🏨 الفندق: {hotel['hotel_name']}\n"
            f"👤 المستخدم: {hotel['username']}\n"
            f"📌 الحالة: {status}\n"
            f"📱 Telegram: {connected}\n"
            f"🕐 الإنشاء: {hotel['created_at']}\n\n"
        )

        if hotel["active"]:
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑️ تعطيل {hotel['hotel_name']}",
                    callback_data=(
                        f"delete_hotel:{hotel['id']}"
                    ),
                )
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(
                    f"♻️ تفعيل {hotel['hotel_name']}",
                    callback_data=(
                        f"enable_hotel:{hotel['id']}"
                    ),
                )
            ])

    await update.message.reply_text(
        text,
        reply_markup=(
            InlineKeyboardMarkup(keyboard)
            if keyboard
            else None
        ),
    )


# =========================================================
# تعطيل فندق
# =========================================================

async def delete_hotel_command(
    update,
    context,
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
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑️ {hotel['hotel_name']}",
                    callback_data=(
                        f"delete_hotel:{hotel['id']}"
                    ),
                )
            ])

    if not keyboard:
        await update.message.reply_text(
            "📋 لا توجد فنادق فعالة."
        )

        return

    await update.message.reply_text(
        "🗑️ اختر الفندق الذي تريد تعطيل حسابه:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# =========================================================
# تسجيل نزيل
# =========================================================

async def new_guest_start(
    update,
    context,
):
    if is_admin(update):
        await update.message.reply_text(
            "👨‍💼 نموذج النزلاء مخصص "
            "لحسابات الفنادق."
        )

        return ConversationHandler.END

    hotel = await asyncio.to_thread(
        get_logged_hotel,
        update.effective_user.id,
    )

    if not hotel:
        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً.\n\n"
            "/login"
        )

        return ConversationHandler.END

    context.user_data[
        "guest_form"
    ] = {}

    context.user_data[
        "guest_hotel_id"
    ] = hotel["id"]

    context.user_data[
        "guest_account_hotel"
    ] = hotel["hotel_name"]

    context.user_data[
        "guest_images"
    ] = []

    await update.message.reply_text(
        "📋 نموذج تسجيل نزيل جديد\n\n"
        "1️⃣ الاسم الثلاثي:"
    )

    return GUEST_NAME


async def guest_name(
    update,
    context,
):
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


async def guest_mother(
    update,
    context,
):
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


async def guest_birth(
    update,
    context,
):
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


async def guest_home(
    update,
    context,
):
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
        reply_markup=governorate_keyboard(),
    )

    return GUEST_GOVERNORATE


async def guest_governorate_button(
    update,
    context,
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
            show_alert=True,
        )

        return GUEST_GOVERNORATE

    context.user_data[
        "guest_form"
    ]["المحافظة"] = governorate

    account_hotel = context.user_data.get(
        "guest_account_hotel"
    )

    context.user_data[
        "guest_form"
    ]["اسم الفندق"] = account_hotel

    await query.edit_message_text(
        f"✅ المحافظة: {governorate}\n\n"
        "6️⃣ الفندق المرتبط بالحساب:\n"
        f"🏨 {account_hotel}"
    )

    await query.message.reply_text(
        "7️⃣ رقم الجناح:\n\n"
        "إذا لم يوجد جناح اكتب: لا يوجد"
    )

    return GUEST_SUITE


async def guest_suite(
    update,
    context,
):
    text = update.message.text.strip()

    if not text:
        await update.message.reply_text(
            "❌ أدخل رقم الجناح أو اكتب: لا يوجد"
        )

        return GUEST_SUITE

    context.user_data[
        "guest_form"
    ]["رقم الجناح"] = text

    await update.message.reply_text(
        "8️⃣ رقم الغرفة:"
    )

    return GUEST_ROOM


async def guest_room(
    update,
    context,
):
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


async def guest_checkin(
    update,
    context,
):
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


async def guest_duration(
    update,
    context,
):
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


async def guest_reason(
    update,
    context,
):
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
        "🪪 أرسل الوجه الأمامي للبطاقة:"
    )

    return GUEST_ID_FRONT


# =========================================================
# صورة البطاقة الأمامية
# =========================================================

async def guest_id_front(
    update,
    context,
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
            "🪪 أرسل الوجه الخلفي:"
        )

        return GUEST_ID_BACK

    except Exception as e:
        logger.exception(
            "ID front error: %s",
            e,
        )

        await update.message.reply_text(
            "❌ حدث خطأ أثناء استلام الصورة.\n"
            "أعد إرسالها."
        )

        return GUEST_ID_FRONT


# =========================================================
# صورة البطاقة الخلفية
# =========================================================

async def guest_id_back(
    update,
    context,
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
            "✅ تم استلام الوجه الخلفي.\n\n"
            "⏳ يتم تجهيز البيانات..."
        )

        await finish_guest_form(
            update,
            context,
        )

        return ConversationHandler.END

    except Exception as e:
        logger.exception(
            "ID back error: %s",
            e,
        )

        await update.message.reply_text(
            "❌ حدث خطأ أثناء استلام الصورة."
        )

        return GUEST_ID_BACK


# =========================================================
# مراجعة النزيل
# =========================================================

async def finish_guest_form(
    update,
    context,
):
    form = context.user_data.get(
        "guest_form",
        {},
    )

    images = context.user_data.get(
        "guest_images",
        [],
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
                callback_data=(
                    "send_guest_to_admin"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data=(
                    "cancel_guest_form"
                ),
            )
        ],
    ]

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# =========================================================
# إرسال النزيل للإدارة
# =========================================================

async def send_guest_to_admin(
    update,
    context,
):
    query = update.callback_query

    await query.answer(
        "⏳ جاري الإرسال..."
    )

    if is_admin(update):
        await query.edit_message_text(
            "⛔ هذا الزر مخصص لحسابات الفنادق."
        )

        return

    hotel = await asyncio.to_thread(
        get_logged_hotel,
        update.effective_user.id,
    )

    if not hotel:
        await query.edit_message_text(
            "🔐 انتهت جلسة الدخول.\n\n"
            "/login"
        )

        return

    form = context.user_data.get(
        "guest_form"
    )

    images = context.user_data.get(
        "guest_images",
        [],
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

    if (
        form.get("اسم الفندق")
        != hotel["hotel_name"]
    ):
        await query.edit_message_text(
            "❌ اسم الفندق لا يطابق الحساب."
        )

        return

    if not ADMIN_TELEGRAM_ID:
        await query.edit_message_text(
            "⚠️ تم حفظ المعلومات، "
            "لكن ADMIN_TELEGRAM_ID غير محدد."
        )

        return

    try:
        guest_id = await asyncio.to_thread(
            save_guest,
            form,
            update,
            hotel["id"],
        )

        pdf_file = await asyncio.to_thread(
            create_guest_pdf,
            form,
            images,
        )

        guest_name = form.get(
            "الاسم الثلاثي",
            "نزيل",
        )

        filename = safe_filename(
            guest_name
        )

        admin_caption = (
            "📥 تم استلام بيانات نزيل جديد\n\n"
            f"🏨 الفندق: "
            f"{hotel['hotel_name']}\n"
            f"👤 الاسم: "
            f"{guest_name}\n"
            f"📍 المحافظة: "
            f"{form.get('المحافظة', 'غير مذكور')}\n"
            f"🚪 الغرفة: "
            f"{form.get('رقم الغرفة', 'غير مذكور')}\n"
            f"📅 تاريخ النزول: "
            f"{form.get('تاريخ النزول', 'غير مذكور')}\n"
            "🪪 صور البطاقة: أمامي + خلفي\n"
            f"🆔 رقم السجل: {guest_id}\n\n"
            "📎 التقرير الكامل مرفق بصيغة PDF."
        )

        pdf_file.seek(0)

        await context.bot.send_document(
            chat_id=int(ADMIN_TELEGRAM_ID),
            document=pdf_file,
            filename=filename,
            caption=admin_caption,
        )

        await query.edit_message_text(
            "بسم الله الرحمن الرحيم 🌿\n\n"
            "✅ تم إرسال معلومات النزيل بنجاح.\n\n"
            "📤 وصلت المعلومات إلى الإدارة.\n\n"
            "🪪 تم إرفاق الوجه الأمامي "
            "والخلفي للبطاقة ضمن PDF.\n\n"
            "👤 تسجيل نزيل آخر:\n"
            "/new_guest"
        )

        for key in (
            "guest_form",
            "guest_images",
            "guest_hotel_id",
            "guest_account_hotel",
        ):
            context.user_data.pop(
                key,
                None,
            )

    except Exception as e:
        logger.exception(
            "Send guest error: %s",
            e,
        )

        await query.edit_message_text(
            "⚠️ حدث خطأ أثناء إرسال المعلومات "
            "للإدارة.\n\n"
            "يرجى المحاولة مرة أخرى."
        )


# =========================================================
# إلغاء نموذج النزيل
# =========================================================

async def cancel_guest_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    for key in (
        "guest_form",
        "guest_images",
        "guest_hotel_id",
        "guest_account_hotel",
    ):
        context.user_data.pop(
            key,
            None,
        )

    await query.edit_message_text(
        "❌ تم إلغاء تسجيل النزيل.\n\n"
        "/new_guest"
    )


# =========================================================
# التقارير
# =========================================================

def build_report_text(
    rows,
    title,
    period,
):
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
        f"{title}\n\n"
        f"📅 {period}\n\n"
        f"👥 إجمالي النزلاء: {total}\n\n"
        "🏠 حسب المحافظة:\n"
    )

    for name, count in (
        governorates.most_common()
    ):
        text += (
            f"• {name}: {count}\n"
        )

    text += "\n🏨 حسب الفندق:\n"

    for name, count in (
        hotels.most_common()
    ):
        text += (
            f"• {name}: {count}\n"
        )

    text += "\n🎯 أسباب الإقامة:\n"

    for name, count in (
        reasons.most_common()
    ):
        text += (
            f"• {name}: {count}\n"
        )

    return text


async def daily_report(
    update,
    context,
):
    if not is_admin(update):
        await update.message.reply_text(
            "⛔ هذا الأمر مخصص للإدارة."
        )

        return

    target_date = date.today().isoformat()

    rows = await asyncio.to_thread(
        get_guests_by_date,
        target_date,
    )

    if not rows:
        await update.message.reply_text(
            "📋 لا توجد بيانات مسجلة اليوم."
        )

        return

    await update.message.reply_text(
        build_report_text(
            rows,
            "📋 تقرير عمل قسم معلومات الفنادق",
            f"التاريخ: {target_date}",
        )
    )

    pdf_file = await asyncio.to_thread(
        create_daily_pdf,
        rows,
        target_date,
    )

    await update.message.reply_document(
        document=pdf_file,
        filename=(
            f"تقرير_عمل_قسم_معلومات_الفنادق_"
            f"{target_date}.pdf"
        ),
        caption=(
            "📋 تم إنشاء التقرير اليومي PDF."
        ),
    )


async def yesterday_report(
    update,
    context,
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
        yesterday,
    )

    if not rows:
        await update.message.reply_text(
            f"📋 لا توجد بيانات بتاريخ "
            f"{yesterday}."
        )

        return

    pdf_file = await asyncio.to_thread(
        create_daily_pdf,
        rows,
        yesterday,
    )

    await update.message.reply_document(
        document=pdf_file,
        filename=(
            f"تقرير_قسم_معلومات_الفنادق_"
            f"{yesterday}.pdf"
        ),
        caption=(
            "📋 تقرير قسم معلومات الفنادق\n"
            f"📅 {yesterday}"
        ),
    )


async def monthly_report(
    update,
    context,
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
        current_month,
    )

    if not rows:
        await update.message.reply_text(
            "📋 لا توجد بيانات مسجلة "
            "خلال الشهر الحالي."
        )

        return

    await update.message.reply_text(
        build_report_text(
            rows,
            "📊 التقرير الشهري",
            f"الشهر: {current_month}",
        )
    )

    pdf_file = await asyncio.to_thread(
        create_daily_pdf,
        rows,
        current_month,
        "التقرير الشهري لقسم معلومات الفنادق",
    )

    await update.message.reply_document(
        document=pdf_file,
        filename=(
            f"تقرير_قسم_معلومات_الفنادق_"
            f"{current_month}.pdf"
        ),
        caption=(
            "📊 تم إنشاء التقرير الشهري PDF."
        ),
    )


# =========================================================
# Callbacks الإدارة
# =========================================================

async def admin_callback(
    update,
    context,
):
    query = update.callback_query

    if not is_admin(update):
        await query.answer(
            "⛔ غير مصرح لك.",
            show_alert=True,
        )

        return

    data = query.data or ""

    # -----------------------------------------------------
    # اختيار فندق لإنشاء الحساب
    # -----------------------------------------------------

    if data.startswith(
        "admin_create_hotel:"
    ):
        try:
            index = int(
                data.split(":")[1]
            )

            hotel_name = HOTELS[index]

        except Exception:
            await query.answer(
                "اختيار غير صالح.",
                show_alert=True,
            )

            return ADD_HOTEL_USERNAME

        accounts = await asyncio.to_thread(
            get_all_hotels
        )

        existing_active = next(
            (
                account
                for account in accounts
                if account["hotel_name"]
                == hotel_name
                and account["active"]
            ),
            None,
        )

        if existing_active:
            await query.answer(
                "يوجد حساب فعال لهذا الفندق.",
                show_alert=True,
            )

            await query.edit_message_text(
                "⚠️ يوجد حساب فعال لهذا الفندق "
                "مسبقاً.\n\n"
                f"🏨 الفندق: {hotel_name}\n"
                f"👤 المستخدم: "
                f"{existing_active['username']}"
            )

            return ADD_HOTEL_USERNAME

        context.user_data[
            "new_hotel_name"
        ] = hotel_name

        await query.answer()

        await query.edit_message_text(
            f"🏨 الفندق المختار:\n"
            f"{hotel_name}\n\n"
            "👤 أرسل اسم المستخدم.\n\n"
            "مثال:\n"
            "hotel_qurtuba"
        )

        return ADD_HOTEL_USERNAME

    # -----------------------------------------------------
    # حذف
    # -----------------------------------------------------

    if data.startswith(
        "delete_hotel:"
    ):
        try:
            hotel_id = int(
                data.split(":", 1)[1]
            )

        except Exception:
            await query.answer(
                "رقم الحساب غير صحيح.",
                show_alert=True,
            )

            return

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ نعم، تعطيل الفندق",
                    callback_data=(
                        f"confirm_delete:{hotel_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data=(
                        "cancel_delete"
                    ),
                )
            ],
        ]

        await query.answer()

        await query.edit_message_text(
            "⚠️ هل أنت متأكد من تعطيل هذا الفندق؟",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

        return

    # -----------------------------------------------------
    # تأكيد الحذف
    # -----------------------------------------------------

    if data.startswith(
        "confirm_delete:"
    ):
        try:
            hotel_id = int(
                data.split(":", 1)[1]
            )

        except Exception:
            await query.answer(
                "رقم الحساب غير صحيح.",
                show_alert=True,
            )

            return

        await asyncio.to_thread(
            disable_hotel,
            hotel_id,
        )

        await query.answer(
            "تم تعطيل الحساب."
        )

        await query.edit_message_text(
            "🗑️ تم تعطيل حساب الفندق بنجاح."
        )

        return

    # -----------------------------------------------------
    # إلغاء
    # -----------------------------------------------------

    if data == "cancel_delete":
        await query.answer()

        await query.edit_message_text(
            "❌ تم إلغاء العملية."
        )

        return

    # -----------------------------------------------------
    # تفعيل
    # -----------------------------------------------------

    if data.startswith(
        "enable_hotel:"
    ):
        try:
            hotel_id = int(
                data.split(":", 1)[1]
            )

        except Exception:
            await query.answer(
                "رقم الحساب غير صحيح.",
                show_alert=True,
            )

            return

        await asyncio.to_thread(
            enable_hotel,
            hotel_id,
        )

        await query.answer(
            "تم تفعيل الحساب."
        )

        await query.edit_message_text(
            "♻️ تم تفعيل حساب الفندق بنجاح."
        )


# =========================================================
# إلغاء Conversation
# =========================================================

async def cancel(
    update,
    context,
):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ تم إلغاء العملية."
    )

    return ConversationHandler.END


# =========================================================
# Health Server - Render
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):
    def do_GET(self):
        self.send_response(200)

        self.send_header(
            "Content-type",
            "text/plain; charset=utf-8",
        )

        self.end_headers()

        self.wfile.write(
            b"Hotel Report Bot is running"
        )

    def log_message(
        self,
        format,
        *args,
    ):
        return


def run_web_server():
    port = int(
        os.environ.get(
            "PORT",
            "10000",
        )
    )

    server = ThreadingHTTPServer(
        (
            "0.0.0.0",
            port,
        ),
        HealthHandler,
    )

    logger.info(
        "Web server running on port %s",
        port,
    )

    server.serve_forever()


# =========================================================
# بناء التطبيق
# =========================================================

def build_application():
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    # -----------------------------------------------------
    # تسجيل الدخول
    # -----------------------------------------------------

    hotel_login_handler = ConversationHandler(
        entry_points=[
            CommandHandler(
                "login",
                login_start,
            )
        ],

        states={
            LOGIN_USERNAME: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    login_username,
                )
            ],

            LOGIN_PASSWORD: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    login_password,
                )
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],

        allow_reentry=True,
    )

    # -----------------------------------------------------
    # إنشاء حساب الفندق
    # -----------------------------------------------------

    add_hotel_handler = ConversationHandler(
        entry_points=[
            CommandHandler(
                "add_hotel",
                add_hotel_start,
            )
        ],

        states={
            ADD_HOTEL_USERNAME: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    add_hotel_username,
                )
            ],

            ADD_HOTEL_PASSWORD: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    add_hotel_password,
                )
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],

        allow_reentry=True,
    )

    # -----------------------------------------------------
    # تسجيل النزيل
    # -----------------------------------------------------

    guest_form_handler = ConversationHandler(
        entry_points=[
            CommandHandler(
                "new_guest",
                new_guest_start,
            )
        ],

        states={
            GUEST_NAME: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    guest_name,
                )
            ],

            GUEST_MOTHER: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    guest_mother,
                )
            ],

            GUEST_BIRTH: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    guest_birth,
                )
            ],

            GUEST_HOME: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    guest_home,
                )
            ],

            GUEST_GOVERNORATE: [
                CallbackQueryHandler(
                    guest_governorate_button,
                    pattern=(
                        r"^governorate_select:"
                    ),
                )
            ],

            GUEST_SUITE: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    guest_suite,
                )
            ],

            GUEST_ROOM: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    guest_room,
                )
            ],

            GUEST_CHECKIN: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    guest_checkin,
                )
            ],

            GUEST_DURATION: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    guest_duration,
                )
            ],

            GUEST_REASON: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    guest_reason,
                )
            ],

            GUEST_ID_FRONT: [
                MessageHandler(
                    filters.PHOTO,
                    guest_id_front,
                )
            ],

            GUEST_ID_BACK: [
                MessageHandler(
                    filters.PHOTO,
                    guest_id_back,
                )
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],

        allow_reentry=True,
    )

    # =====================================================
    # ترتيب الـ Handlers
    # =====================================================

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        hotel_login_handler
    )

    application.add_handler(
        add_hotel_handler
    )

    application.add_handler(
        guest_form_handler
    )

    application.add_handler(
        CommandHandler(
            "logout",
            logout,
        )
    )

    application.add_handler(
        CommandHandler(
            "hotels",
            hotels_list,
        )
    )

    application.add_handler(
        CommandHandler(
            "delete_hotel",
            delete_hotel_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "daily",
            daily_report,
        )
    )

    application.add_handler(
        CommandHandler(
            "yesterday",
            yesterday_report,
        )
    )

    application.add_handler(
        CommandHandler(
            "monthly",
            monthly_report,
        )
    )

    # =====================================================
    # Callbacks الإدارة
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=(
                r"^(admin_create_hotel:"
                r"|delete_hotel:"
                r"|confirm_delete:"
                r"|cancel_delete$"
                r"|enable_hotel:)"
            ),
        )
    )

    # =====================================================
    # إرسال النزيل
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            send_guest_to_admin,
            pattern=(
                r"^send_guest_to_admin$"
            ),
        )
    )

    # =====================================================
    # إلغاء النزيل
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            cancel_guest_callback,
            pattern=(
                r"^cancel_guest_form$"
            ),
        )
    )

    return application


# =========================================================
# الرسائل غير المعروفة
# =========================================================

async def unknown_message(
    update,
    context,
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
        update.effective_user.id,
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


# =========================================================
# الأخطاء
# =========================================================

async def error_handler(
    update,
    context,
):
    logger.exception(
        "BOT ERROR",
        exc_info=context.error,
    )


# =========================================================
# التشغيل الرئيسي
# =========================================================

def main():
    if not TOKEN:
        logger.error(
            "BOT_TOKEN is not set!"
        )

        return

    if not ADMIN_TELEGRAM_ID:
        logger.warning(
            "ADMIN_TELEGRAM_ID is not set!"
        )

    # -----------------------------------------------------
    # قاعدة البيانات
    # -----------------------------------------------------

    init_database()

    # -----------------------------------------------------
    # Health Server
    # -----------------------------------------------------

    web_thread = threading.Thread(
        target=run_web_server,
        daemon=True,
    )

    web_thread.start()

    # -----------------------------------------------------
    # بناء التطبيق
    # -----------------------------------------------------

    application = build_application()

    application.add_handler(
        MessageHandler(
            filters.ALL
            & ~filters.COMMAND,
            unknown_message,
        )
    )

    application.add_error_handler(
        error_handler
    )

    # -----------------------------------------------------
    # تشغيل مستقر
    # -----------------------------------------------------

    logger.info(
        "Starting Telegram Bot..."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        poll_interval=1.0,
        timeout=30,
        bootstrap_retries=-1,
        close_loop=True,
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
