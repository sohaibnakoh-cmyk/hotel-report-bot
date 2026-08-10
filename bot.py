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
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
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
)

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD",
    ""
)

DATABASE_FILE = "hotel_reports.db"

WELCOME_IMAGE = "images.png"

PAGE_WIDTH, PAGE_HEIGHT = A4

DEFAULT_MODE = "single"


# =========================================================
# حالات المحادثة
# =========================================================

LOGIN_USERNAME = 1
LOGIN_PASSWORD = 2

ADD_HOTEL_NAME = 10
ADD_HOTEL_USERNAME = 11
ADD_HOTEL_PASSWORD = 12


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

        reshaped = arabic_reshaper.reshape(
            text
        )

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

    # -----------------------------------------------------
    # جدول النزلاء
    # -----------------------------------------------------

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

            hotel_id INTEGER

        )
        """
    )

    # -----------------------------------------------------
    # جدول الفنادق
    # -----------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS hotels (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            hotel_name TEXT NOT NULL,

            username TEXT UNIQUE NOT NULL,

            password_hash TEXT NOT NULL,

            password_salt TEXT NOT NULL,

            active INTEGER DEFAULT 1,

            created_date TEXT,

            created_time TEXT

        )
        """
    )

    # -----------------------------------------------------
    # إضافة hotel_id للقاعدة القديمة إن لم يكن موجوداً
    # -----------------------------------------------------

    try:

        cursor.execute(
            "ALTER TABLE guests ADD COLUMN hotel_id INTEGER"
        )

    except sqlite3.OperationalError:

        pass

    connection.commit()

    connection.close()

    print(
        "Database initialized successfully"
    )


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
        200000
    )

    return (
        password_hash.hex(),
        salt
    )


def verify_password(
    password,
    password_hash,
    salt
):

    new_hash, _ = hash_password(
        password,
        salt
    )

    return secrets.compare_digest(
        new_hash,
        password_hash
    )


# =========================================================
# حساب المدير
# =========================================================

def is_admin(update):

    if not update.effective_user:
        return False

    username = (
        update.effective_user.username
        or ""
    )

    return (
        username.lower()
        == ADMIN_USERNAME.lower()
    )


def is_admin_logged_in(
    context
):

    return context.user_data.get(
        "role"
    ) == "admin"


# =========================================================
# حساب الفندق
# =========================================================

def get_hotel_by_username(
    username
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM hotels
        WHERE username = ?
        LIMIT 1
        """,
        (
            username,
        )
    )

    hotel = cursor.fetchone()

    connection.close()

    return hotel


def get_hotel_by_id(
    hotel_id
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM hotels
        WHERE id = ?
        LIMIT 1
        """,
        (
            hotel_id,
        )
    )

    hotel = cursor.fetchone()

    connection.close()

    return hotel


def create_hotel_account(
    hotel_name,
    username,
    password
):

    password_hash, salt = hash_password(
        password
    )

    now = datetime.now()

    connection = get_db()

    cursor = connection.cursor()

    try:

        cursor.execute(
            """
            INSERT INTO hotels (

                hotel_name,
                username,
                password_hash,
                password_salt,
                active,
                created_date,
                created_time

            )

            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,

            (
                hotel_name,
                username,
                password_hash,
                salt,
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
            )
        )

        connection.commit()

        hotel_id = cursor.lastrowid

        connection.close()

        return hotel_id

    except sqlite3.IntegrityError:

        connection.close()

        return None


# =========================================================
# الحصول على الفنادق
# =========================================================

def get_all_hotels():

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM hotels
        ORDER BY id ASC
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# =========================================================
# حالة تسجيل الدخول
# =========================================================

def is_logged_in(
    context
):

    return context.user_data.get(
        "logged_in",
        False
    )


def get_current_hotel_id(
    context
):

    return context.user_data.get(
        "hotel_id"
    )


# =========================================================
# حفظ النزيل
# =========================================================

def save_guest(
    guest,
    update,
    hotel_id=None,
    forced_hotel_name=None
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

    hotel_name = (
        forced_hotel_name
        or guest.get(
            "اسم الفندق",
            "غير مذكور"
        )
    )

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO guests (

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
            hotel_id

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

            hotel_name,

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

            hotel_id
        )
    )

    connection.commit()

    guest_id = cursor.lastrowid

    connection.close()

    return guest_id


# =========================================================
# بيانات يوم
# =========================================================

def get_guests_by_date(
    target_date,
    hotel_id=None
):

    connection = get_db()

    cursor = connection.cursor()

    if hotel_id is None:

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

    else:

        cursor.execute(
            """
            SELECT *
            FROM guests
            WHERE record_date = ?
            AND hotel_id = ?
            ORDER BY id ASC
            """,
            (
                target_date,
                hotel_id
            )
        )

    rows = cursor.fetchall()

    connection.close()

    return rows


# =========================================================
# بيانات شهر
# =========================================================

def get_guests_by_month(
    year_month,
    hotel_id=None
):

    connection = get_db()

    cursor = connection.cursor()

    if hotel_id is None:

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

    else:

        cursor.execute(
            """
            SELECT *
            FROM guests
            WHERE substr(record_date, 1, 7) = ?
            AND hotel_id = ?
            ORDER BY id ASC
            """,
            (
                year_month,
                hotel_id
            )
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

def parse_guest(text):

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

def split_guests(text):

    parts = re.split(
        r"\n\s*(?:={3,}|-{3,}|\*{3,})\s*\n",
        text
    )

    result = []

    for part in parts:

        part = part.strip()

        if part:

            result.append(
                part
            )

    return result


# =========================================================
# خادم Render
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

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
# PDF HEADER
# =========================================================

def draw_pdf_header(
    pdf,
    title
):

    pdf.setFillColor(
        colors.HexColor(
            "#17365D"
        )
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
        arabic_text(
            title
        )
    )

    pdf.setFillColor(
        colors.black
    )


# =========================================================
# PDF FIELD
# =========================================================

def draw_field(
    pdf,
    y,
    key,
    value
):

    pdf.setFillColor(
        colors.HexColor(
            "#F2F4F7"
        )
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
        arabic_text(
            line
        )
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
            "تم إنشاء التقرير آلياً بواسطة بوت تقارير الفنادق"
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

        if not counter:

            pdf.drawRightString(
                PAGE_WIDTH - 70,
                y,
                arabic_text(
                    "لا توجد بيانات"
                )
            )

            y -= 25

            return

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
                arabic_text(
                    line
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

    if governorates:

        top_governorate, gov_count = (
            governorates.most_common(1)[0]
        )

    else:

        top_governorate = "غير متوفر"
        gov_count = 0

    if hotels:

        top_hotel, hotel_count = (
            hotels.most_common(1)[0]
        )

    else:

        top_hotel = "غير متوفر"
        hotel_count = 0

    if reasons:

        top_reason, reason_count = (
            reasons.most_common(1)[0]
        )

    else:

        top_reason = "غير متوفر"
        reason_count = 0

    narrative = [

        f"بلغ إجمالي عدد النزلاء الذين تم تسجيل "
        f"بياناتهم خلال يوم {target_date} "
        f"عدد {total} نزيلاً.",

        f"وبحسب التوزيع الجغرافي، جاءت محافظة "
        f"{top_governorate} في المرتبة الأولى "
        f"بعدد {gov_count} نزلاء.",

        f"أما على مستوى الفنادق، فقد سجل فندق "
        f"{top_hotel} العدد الأكبر من النزلاء "
        f"بواقع {hotel_count} نزلاء.",

        f"وكان سبب الإقامة الأكثر تكراراً هو "
        f"{top_reason} بعدد {reason_count} نزلاء.",

        "وتعكس البيانات المسجلة خلال اليوم حركة "
        "النزلاء وتوزعهم على الفنادق والمحافظات "
        "وأسباب الإقامة، بما يساهم في متابعة "
        "العمل وتنظيم المعلومات اليومية لقسم "
        "معلومات الفنادق."
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
                arabic_text(
                    line
                )
            )

            y -= 20

        y -= 10

    pdf.save()

    buffer.seek(0)

    return buffer


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
# رسالة الترحيب
# =========================================================

WELCOME_TEXT = (

    "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

    "أهلاً وسهلاً ومرحباً بك في\n"

    "🏨 قسم معلومات الفنادق 🏨\n\n"

    "نسعد بخدمتك، وقد تم تجهيز هذا البوت "
    "لتسهيل تنظيم وتوثيق بيانات النزلاء "
    "وإعداد التقارير بصورة آلية ومنظمة.\n\n"

    "🤝 أهلاً بك، ونسأل الله أن يوفقنا "
    "وإياكم لما فيه الخير."
)


async def send_welcome(
    update
):

    message = update.message

    if not message:
        return

    if os.path.exists(
        WELCOME_IMAGE
    ):

        try:

            with open(
                WELCOME_IMAGE,
                "rb"
            ) as photo:

                await message.reply_photo(
                    photo=photo,
                    caption=WELCOME_TEXT
                )

                return

        except Exception as e:

            print(
                "Welcome image error:",
                e
            )

    await message.reply_text(
        WELCOME_TEXT
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data["pdf_mode"] = DEFAULT_MODE

    await send_welcome(
        update
    )


# =========================================================
# LOGIN
# =========================================================

async def login_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.pop(
        "login_username",
        None
    )

    await update.message.reply_text(

        "🔐 تسجيل الدخول\n\n"

        "أرسل اسم المستخدم الخاص بك:"
    )

    return LOGIN_USERNAME


async def login_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = (
        update.message.text
        or ""
    ).strip()

    if not username:

        await update.message.reply_text(
            "❌ اسم المستخدم غير صحيح."
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

    password = (
        update.message.text
        or ""
    ).strip()

    username = context.user_data.get(
        "login_username"
    )

    if not username:

        await update.message.reply_text(
            "❌ انتهت عملية تسجيل الدخول. استخدم /login من جديد."
        )

        return ConversationHandler.END

    # -----------------------------------------------------
    # دخول المدير
    # -----------------------------------------------------

    if (
        username.lower()
        == ADMIN_USERNAME.lower()
    ):

        if password == ADMIN_PASSWORD:

            context.user_data[
                "logged_in"
            ] = True

            context.user_data[
                "role"
            ] = "admin"

            context.user_data[
                "hotel_id"
            ] = None

            context.user_data.pop(
                "login_username",
                None
            )

            await update.message.reply_text(

                "👑 تم تسجيل الدخول بنجاح.\n\n"

                "مرحباً بك في لوحة المدير.\n\n"

                "يمكنك الآن إدارة الفنادق "
                "والاطلاع على التقارير."
            )

            return ConversationHandler.END

    # -----------------------------------------------------
    # دخول الفندق
    # -----------------------------------------------------

    hotel = get_hotel_by_username(
        username
    )

    if hotel:

        if not hotel["active"]:

            await update.message.reply_text(

                "🚫 هذا الحساب موقوف حالياً.\n\n"
                "يرجى التواصل مع المدير."
            )

            return ConversationHandler.END

        if verify_password(
            password,
            hotel["password_hash"],
            hotel["password_salt"]
        ):

            context.user_data[
                "logged_in"
            ] = True

            context.user_data[
                "role"
            ] = "hotel"

            context.user_data[
                "hotel_id"
            ] = hotel["id"]

            context.user_data[
                "hotel_name"
            ] = hotel["hotel_name"]

            context.user_data.pop(
                "login_username",
                None
            )

            await update.message.reply_text(

                "✅ تم تسجيل الدخول بنجاح.\n\n"

                f"🏨 مرحباً بك في {hotel['hotel_name']}\n\n"

                "يمكنك الآن إرسال بيانات النزلاء "
                "إلى البوت."
            )

            return ConversationHandler.END

    await update.message.reply_text(

        "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"

        "حاول مرة أخرى باستخدام /login"
    )

    return ConversationHandler.END


# =========================================================
# LOGOUT
# =========================================================

async def logout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    await update.message.reply_text(

        "🚪 تم تسجيل الخروج بنجاح.\n\n"

        "يمكنك تسجيل الدخول مرة أخرى باستخدام:\n"
        "/login"
    )


# =========================================================
# ADMIN CHECK
# =========================================================

async def require_admin(
    update,
    context
):

    if not is_admin_logged_in(
        context
    ):

        await update.message.reply_text(

            "🔒 هذا الأمر خاص بالمدير.\n\n"

            "يرجى تسجيل الدخول أولاً باستخدام:\n"
            "/login"
        )

        return False

    return True


# =========================================================
# إضافة فندق - البداية
# =========================================================

async def add_hotel_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(
        update,
        context
    ):

        return ConversationHandler.END

    await update.message.reply_text(

        "🏨 إنشاء حساب فندق جديد\n\n"

        "أرسل اسم الفندق:"
    )

    return ADD_HOTEL_NAME


async def add_hotel_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    hotel_name = (
        update.message.text
        or ""
    ).strip()

    if not hotel_name:

        await update.message.reply_text(
            "❌ اسم الفندق غير صالح."
        )

        return ADD_HOTEL_NAME

    context.user_data[
        "new_hotel_name"
    ] = hotel_name

    await update.message.reply_text(

        "👤 الآن أرسل اسم المستخدم للفندق:"
    )

    return ADD_HOTEL_USERNAME


async def add_hotel_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    username = (
        update.message.text
        or ""
    ).strip()

    if not re.match(
        r"^[A-Za-z0-9_.-]{3,32}$",
        username
    ):

        await update.message.reply_text(

            "❌ اسم المستخدم غير صالح.\n\n"

            "استخدم أحرفاً إنكليزية أو أرقاماً "
            "أو _ أو - فقط."
        )

        return ADD_HOTEL_USERNAME

    existing = get_hotel_by_username(
        username
    )

    if existing:

        await update.message.reply_text(

            "❌ اسم المستخدم موجود مسبقاً.\n\n"

            "اختر اسماً آخر:"
        )

        return ADD_HOTEL_USERNAME

    context.user_data[
        "new_hotel_username"
    ] = username

    await update.message.reply_text(

        "🔑 الآن أرسل كلمة مرور الفندق:\n\n"

        "يفضل أن تكون قوية ولا تقل عن 8 أحرف."
    )

    return ADD_HOTEL_PASSWORD


async def add_hotel_password(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    password = (
        update.message.text
        or ""
    ).strip()

    if len(password) < 8:

        await update.message.reply_text(

            "❌ كلمة المرور قصيرة.\n\n"

            "يجب ألا تقل عن 8 أحرف.\n\n"

            "أرسل كلمة مرور جديدة:"
        )

        return ADD_HOTEL_PASSWORD

    hotel_name = context.user_data.get(
        "new_hotel_name"
    )

    username = context.user_data.get(
        "new_hotel_username"
    )

    hotel_id = create_hotel_account(
        hotel_name,
        username,
        password
    )

    context.user_data.pop(
        "new_hotel_name",
        None
    )

    context.user_data.pop(
        "new_hotel_username",
        None
    )

    if hotel_id is None:

        await update.message.reply_text(

            "❌ تعذر إنشاء الحساب.\n\n"

            "قد يكون اسم المستخدم مستخدماً مسبقاً."
        )

        return ConversationHandler.END

    await update.message.reply_text(

        "✅ تم إنشاء حساب الفندق بنجاح.\n\n"

        "🏨 اسم الفندق:\n"
        f"{hotel_name}\n\n"

        "👤 اسم المستخدم:\n"
        f"{username}\n\n"

        "🔑 كلمة المرور:\n"
        f"{password}\n\n"

        "⚠️ احتفظ ببيانات الدخول بشكل آمن "
        "وسلمها لصاحب الفندق.\n\n"

        "يمكن لصاحب الفندق الآن الدخول باستخدام:\n"
        "/login"
    )

    return ConversationHandler.END


# =========================================================
# قائمة الفنادق للمدير
# =========================================================

async def hotels_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await require_admin(
        update,
        context
    ):

        return

    hotels = get_all_hotels()

    if not hotels:

        await update.message.reply_text(

            "🏨 لا توجد حسابات فنادق حتى الآن.\n\n"

            "استخدم /addhotel لإنشاء حساب."
        )

        return

    text = (
        "🏨 قائمة الفنادق\n\n"
    )

    for index, hotel in enumerate(
        hotels,
        start=1
    ):

        status = (
            "🟢 فعال"
            if hotel["active"]
            else "🔴 موقوف"
        )

        text += (
            f"{index}. {hotel['hotel_name']}\n"
            f"   👤 {hotel['username']}\n"
            f"   {status}\n\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# SINGLE
# =========================================================

async def single_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "pdf_mode"
    ] = "single"

    await update.message.reply_text(

        "📄 تم اختيار وضع الملفات المستقلة.\n\n"

        "سيتم إنشاء ملف PDF مستقل لكل نزيل."
    )


# =========================================================
# ALL
# =========================================================

async def all_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "pdf_mode"
    ] = "all"

    await update.message.reply_text(

        "📚 تم اختيار وضع الملف الموحد.\n\n"

        "سيتم جمع جميع النزلاء في ملف PDF واحد."
    )


# =========================================================
# CANCEL
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
        "new_hotel_name",
        None
    )

    context.user_data.pop(
        "new_hotel_username",
        None
    )

    await update.message.reply_text(

        "❌ تم إلغاء العملية."
    )

    return ConversationHandler.END


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

    # -----------------------------------------------------
    # لا يسمح بإرسال البيانات بدون تسجيل دخول
    # -----------------------------------------------------

    if not is_logged_in(
        context
    ):

        await message.reply_text(

            "🔒 يجب تسجيل الدخول أولاً.\n\n"

            "استخدم:\n"
            "/login"
        )

        return

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

            "قم بتحويل رسالة النزيل "
            "التي تحتوي على البيانات."
        )

        return

    guests_text = split_guests(
        text
    )

    if not guests_text:

        await message.reply_text(
            "❌ لم أتمكن من استخراج بيانات النزلاء."
        )

        return

    image = await get_photo(
        update
    )

    mode = context.user_data.get(
        "pdf_mode",
        DEFAULT_MODE
    )

    guests = []

    # -----------------------------------------------------
    # تحديد الفندق
    # -----------------------------------------------------

    role = context.user_data.get(
        "role"
    )

    hotel_id = None

    forced_hotel_name = None

    if role == "hotel":

        hotel_id = context.user_data.get(
            "hotel_id"
        )

        forced_hotel_name = context.user_data.get(
            "hotel_name"
        )

    # -----------------------------------------------------
    # معالجة النزلاء
    # -----------------------------------------------------

    for guest_text in guests_text:

        guest = parse_guest(
            guest_text
        )

        # الفندق لا يستطيع تغيير اسم الفندق
        if forced_hotel_name:

            guest[
                "اسم الفندق"
            ] = forced_hotel_name

        save_guest(
            guest,
            update,
            hotel_id=hotel_id,
            forced_hotel_name=forced_hotel_name
        )

        guests.append(
            guest
        )

    # -----------------------------------------------------
    # ملف مستقل
    # -----------------------------------------------------

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

                    "📋 تم تسجيل النزيل بنجاح\n\n"

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

    # -----------------------------------------------------
    # ملف موحد
    # -----------------------------------------------------

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

                "✅ تم حفظ البيانات."
            )
        )

    await message.reply_text(

        f"✅ تمت معالجة {len(guests)} نزيل بنجاح.\n\n"

        "📊 يمكنك طلب التقرير باستخدام:\n"
        "/daily"
    )


# =========================================================
# التقرير اليومي
# =========================================================

async def daily_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(
        context
    ):

        await update.message.reply_text(
            "🔒 سجل الدخول أولاً باستخدام /login"
        )

        return

    role = context.user_data.get(
        "role"
    )

    hotel_id = None

    hotel_title = ""

    if role == "hotel":

        hotel_id = context.user_data.get(
            "hotel_id"
        )

        hotel_title = context.user_data.get(
            "hotel_name",
            ""
        )

    target_date = date.today().isoformat()

    rows = get_guests_by_date(
        target_date,
        hotel_id
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

        f"📅 التاريخ: {target_date}\n"
    )

    if hotel_title:

        text += (
            f"🏨 الفندق: {hotel_title}\n"
        )

    text += (
        f"\n👥 إجمالي النزلاء: {total}\n\n"

        "🏠 التوزيع حسب المحافظة:\n"
    )

    for name, count in governorates.most_common():

        text += (
            f"• {name}: {count}\n"
        )

    text += (
        "\n🏨 توزيع النزلاء على الفنادق:\n"
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

    top_gov = (
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

    text += (

        "\n📝 التحليل والسرد:\n\n"

        f"بلغ عدد النزلاء المسجلين خلال اليوم "
        f"{total} نزيلاً. "

        f"وسجلت محافظة {top_gov[0]} أعلى عدد "
        f"من النزلاء بواقع {top_gov[1]} نزلاء. "

        f"وكان الفندق الأعلى تسجيلاً هو "
        f"{top_hotel[0]} بعدد {top_hotel[1]} نزلاء. "

        f"وكان سبب الإقامة الأكثر تكراراً هو "
        f"{top_reason[0]} بعدد {top_reason[1]} نزلاء."
    )

    await update.message.reply_text(
        text
    )

    pdf_title = (
        "تقرير عمل قسم معلومات الفنادق"
    )

    if hotel_title:

        pdf_title += (
            f" - {hotel_title}"
        )

    pdf_file = create_daily_pdf(
        rows,
        target_date,
        title=pdf_title
    )

    filename = (
        f"تقرير_الفنادق_"
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
# تقرير أمس
# =========================================================

async def yesterday_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(
        context
    ):

        await update.message.reply_text(
            "🔒 سجل الدخول أولاً باستخدام /login"
        )

        return

    role = context.user_data.get(
        "role"
    )

    hotel_id = None

    if role == "hotel":

        hotel_id = context.user_data.get(
            "hotel_id"
        )

    yesterday = (
        date.today()
        - timedelta(days=1)
    ).isoformat()

    rows = get_guests_by_date(
        yesterday,
        hotel_id
    )

    if not rows:

        await update.message.reply_text(

            f"📋 لا توجد بيانات مسجلة بتاريخ {yesterday}."
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

    if not is_logged_in(
        context
    ):

        await update.message.reply_text(
            "🔒 سجل الدخول أولاً باستخدام /login"
        )

        return

    role = context.user_data.get(
        "role"
    )

    hotel_id = None

    if role == "hotel":

        hotel_id = context.user_data.get(
            "hotel_id"
        )

    current_month = date.today().strftime(
        "%Y-%m"
    )

    rows = get_guests_by_month(
        current_month,
        hotel_id
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
# قائمة أوامر Telegram
# =========================================================

async def set_bot_commands(
    application
):

    commands = [

        BotCommand(
            "start",
            "🏠 بدء استخدام البوت"
        ),

        BotCommand(
            "login",
            "🔐 تسجيل الدخول"
        ),

        BotCommand(
            "logout",
            "🚪 تسجيل الخروج"
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
            "📄 ملف مستقل لكل نزيل"
        ),

        BotCommand(
            "all",
            "📚 جميع النزلاء في ملف واحد"
        ),

        BotCommand(
            "addhotel",
            "🏨 إنشاء حساب فندق - للمدير"
        ),

        BotCommand(
            "hotels",
            "🏨 قائمة الفنادق - للمدير"
        ),

        BotCommand(
            "cancel",
            "❌ إلغاء العملية"
        ),
    ]

    await application.bot.set_my_commands(
        commands
    )

    print(
        "Telegram commands registered successfully"
    )


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
# محادثة تسجيل الدخول
# =========================================================

login_conversation = ConversationHandler(

    entry_points=[
        CommandHandler(
            "login",
            login_start
        )
    ],

    states={

        LOGIN_USERNAME: [
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                login_username
            )
        ],

        LOGIN_PASSWORD: [
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
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
# محادثة إضافة فندق
# =========================================================

add_hotel_conversation = ConversationHandler(

    entry_points=[
        CommandHandler(
            "addhotel",
            add_hotel_start
        )
    ],

    states={

        ADD_HOTEL_NAME: [
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                add_hotel_name
            )
        ],

        ADD_HOTEL_USERNAME: [
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                add_hotel_username
            )
        ],

        ADD_HOTEL_PASSWORD: [
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
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
# إضافة المحادثات
# =========================================================

app.add_handler(
    login_conversation
)

app.add_handler(
    add_hotel_conversation
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
    CommandHandler(
        "logout",
        logout
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

app.add_handler(
    CommandHandler(
        "hotels",
        hotels_command
    )
)


# =========================================================
# استقبال رسائل النزلاء
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

    # -----------------------------------------------------
    # قاعدة البيانات
    # -----------------------------------------------------

    init_database()

    # -----------------------------------------------------
    # التحقق من المتغيرات
    # -----------------------------------------------------

    if not TOKEN:

        print(
            "ERROR: BOT_TOKEN is not set!"
        )

        return

    if not ADMIN_PASSWORD:

        print(
            "WARNING: ADMIN_PASSWORD is not set!"
        )

    # -----------------------------------------------------
    # تشغيل Render Web Server
    # -----------------------------------------------------

    threading.Thread(

        target=run_web_server,

        daemon=True

    ).start()

    # -----------------------------------------------------
    # Telegram
    # -----------------------------------------------------

    await app.initialize()

    # -----------------------------------------------------
    # قائمة الأوامر
    # -----------------------------------------------------

    await set_bot_commands(
        app
    )

    # -----------------------------------------------------
    # تشغيل البوت
    # -----------------------------------------------------

    await app.start()

    await app.updater.start_polling()

    print(
        "Hotel Report Bot is running successfully!"
    )

    # -----------------------------------------------------
    # إبقاء البوت يعمل
    # -----------------------------------------------------

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
