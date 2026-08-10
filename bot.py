# ============================================================
# 🏨 BOT نظام معلومات الفنادق
# ============================================================
#
# الوظائف:
# 🔐 تسجيل الدخول
# 👨‍💼 حساب المدير
# 🏨 حساب مستقل لكل فندق
# 📝 استقبال بيانات النزلاء
# 🗄️ SQLite Database
# 📄 PDF لكل نزيل
# 📚 PDF لجميع النزلاء
# 📊 التقرير اليومي
# 📅 تقرير أمس
# 📈 التقرير الشهري
# 🔎 البحث عن نزيل
# 🏨 إدارة الفنادق
# 🔑 تغيير كلمة المرور
# 🖼️ صورة الترحيب images.png
# 📋 قائمة أوامر Telegram
#
# ============================================================

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


# ============================================================
# 1. الإعدادات
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

DATABASE_FILE = "hotel_reports.db"

WELCOME_IMAGE = "images.png"

PAGE_WIDTH, PAGE_HEIGHT = A4

DEFAULT_MODE = "single"

ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    "admin"
)

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD",
    "ChangeThisPassword123!"
)


# ============================================================
# 2. الخط العربي
# ============================================================

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

    print("WARNING: Arabic font not found")

    PDF_FONT = "Helvetica"


# ============================================================
# 3. معالجة العربية
# ============================================================

def arabic_text(text):

    if text is None:
        return ""

    text = str(text)

    try:

        reshaped = arabic_reshaper.reshape(text)

        return get_display(reshaped)

    except Exception:

        return text


# ============================================================
# 4. قاعدة البيانات
# ============================================================

def get_db():

    connection = sqlite3.connect(
        DATABASE_FILE,
        check_same_thread=False
    )

    connection.row_factory = sqlite3.Row

    return connection


def column_exists(
    connection,
    table_name,
    column_name
):

    cursor = connection.cursor()

    cursor.execute(
        f"PRAGMA table_info({table_name})"
    )

    columns = cursor.fetchall()

    return any(
        column["name"] == column_name
        for column in columns
    )


def init_database():

    connection = get_db()

    cursor = connection.cursor()

    # --------------------------------------------------------
    # الفنادق
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS hotels (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            hotel_name TEXT NOT NULL,

            username TEXT UNIQUE NOT NULL,

            password_hash TEXT NOT NULL,

            password_salt TEXT NOT NULL,

            governorate TEXT DEFAULT '',

            address TEXT DEFAULT '',

            phone TEXT DEFAULT '',

            active INTEGER DEFAULT 1,

            created_at TEXT NOT NULL

        )
        """
    )

    # --------------------------------------------------------
    # المستخدمون
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            username TEXT UNIQUE NOT NULL,

            password_hash TEXT NOT NULL,

            password_salt TEXT NOT NULL,

            role TEXT NOT NULL,

            hotel_id INTEGER,

            active INTEGER DEFAULT 1,

            created_at TEXT NOT NULL,

            FOREIGN KEY(hotel_id)
            REFERENCES hotels(id)

        )
        """
    )

    # --------------------------------------------------------
    # النزلاء
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS guests (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            hotel_id INTEGER,

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

            FOREIGN KEY(hotel_id)
            REFERENCES hotels(id)

        )
        """
    )

    # --------------------------------------------------------
    # ترقية قاعدة البيانات القديمة
    # --------------------------------------------------------

    if not column_exists(
        connection,
        "guests",
        "hotel_id"
    ):

        cursor.execute(
            """
            ALTER TABLE guests
            ADD COLUMN hotel_id INTEGER
            """
        )

    # --------------------------------------------------------
    # إنشاء حساب المدير
    # --------------------------------------------------------

    cursor.execute(
        """
        SELECT id
        FROM users
        WHERE username = ?
        """,
        (
            ADMIN_USERNAME,
        )
    )

    admin = cursor.fetchone()

    if not admin:

        password_hash, salt = hash_password(
            ADMIN_PASSWORD
        )

        cursor.execute(
            """
            INSERT INTO users (
                username,
                password_hash,
                password_salt,
                role,
                hotel_id,
                active,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ADMIN_USERNAME,
                password_hash,
                salt,
                "admin",
                None,
                1,
                datetime.now().isoformat()
            )
        )

        print(
            "Admin account created:",
            ADMIN_USERNAME
        )

    connection.commit()

    connection.close()

    print(
        "Database initialized successfully"
    )


# ============================================================
# 5. تشفير كلمات المرور
# ============================================================

def hash_password(password):

    salt = secrets.token_hex(16)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200000
    ).hex()

    return password_hash, salt


def verify_password(
    password,
    password_hash,
    salt
):

    test_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200000
    ).hex()

    return secrets.compare_digest(
        test_hash,
        password_hash
    )


# ============================================================
# 6. البحث عن المستخدم
# ============================================================

def get_user_by_username(username):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM users
        WHERE username = ?
        """,
        (
            username.strip(),
        )
    )

    user = cursor.fetchone()

    connection.close()

    return user


def get_hotel_by_id(hotel_id):

    if not hotel_id:
        return None

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM hotels
        WHERE id = ?
        """,
        (
            hotel_id,
        )
    )

    hotel = cursor.fetchone()

    connection.close()

    return hotel


# ============================================================
# 7. جلسة المستخدم
# ============================================================

def is_logged_in(context):

    return bool(
        context.user_data.get(
            "logged_in",
            False
        )
    )


def is_admin(context):

    return (
        context.user_data.get(
            "role"
        ) == "admin"
    )


def get_hotel_id(context):

    return context.user_data.get(
        "hotel_id"
    )


# ============================================================
# 8. حفظ النزيل
# ============================================================

def save_guest(
    guest,
    update,
    hotel_id
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

    hotel_name = guest.get(
        "اسم الفندق",
        "غير مذكور"
    )

    if hotel_id:

        hotel = get_hotel_by_id(
            hotel_id
        )

        if hotel:

            hotel_name = hotel["hotel_name"]

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO guests (

            hotel_id,

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
            telegram_username

        )

        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )
        """,

        (

            hotel_id,

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

            username
        )
    )

    connection.commit()

    guest_id = cursor.lastrowid

    connection.close()

    return guest_id


# ============================================================
# 9. الحصول على سجلات يوم
# ============================================================

def get_guests_by_date(
    target_date,
    hotel_id=None
):

    connection = get_db()

    cursor = connection.cursor()

    if hotel_id:

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

    else:

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


# ============================================================
# 10. الحصول على سجلات شهر
# ============================================================

def get_guests_by_month(
    year_month,
    hotel_id=None
):

    connection = get_db()

    cursor = connection.cursor()

    if hotel_id:

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

    else:

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


# ============================================================
# 11. تنظيف اسم الملف
# ============================================================

def safe_filename(name):

    if not name:

        name = "تقرير_نزيل"

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

    if not name:

        name = "تقرير_نزيل"

    return name + ".pdf"


# ============================================================
# 12. استخراج قيمة من النص
# ============================================================

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


# ============================================================
# 13. استخراج بيانات النزيل
# ============================================================

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


# ============================================================
# 14. تقسيم عدة نزلاء
# ============================================================

def split_guests(text):

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


# ============================================================
# 15. خادم Render
# ============================================================

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

    print(
        f"Web server running on port {port}"
    )

    server.serve_forever()


# ============================================================
# 16. عنوان PDF
# ============================================================

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


# ============================================================
# 17. حقل PDF
# ============================================================

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


# ============================================================
# 18. PDF نزيل واحد
# ============================================================

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


# ============================================================
# 19. PDF لجميع النزلاء
# ============================================================

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


# ============================================================
# 20. التقرير اليومي PDF
# ============================================================

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

    def new_page():

        nonlocal y

        pdf.showPage()

        draw_pdf_header(
            pdf,
            title
        )

        y = PAGE_HEIGHT - 125

    def draw_counter_section(
        section_title,
        counter
    ):

        nonlocal y

        if y < 120:

            new_page()

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

                new_page()

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

        new_page()

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

        f"بلغ إجمالي عدد النزلاء الذين تم تسجيل "
        f"بياناتهم خلال يوم {target_date} "
        f"عدد {total} نزيلاً.",

        f"وبحسب التوزيع الجغرافي، جاءت محافظة "
        f"{top_governorate[0]} في المرتبة الأولى "
        f"بعدد {top_governorate[1]} نزلاء.",

        f"أما على مستوى الفنادق، فقد سجل فندق "
        f"{top_hotel[0]} العدد الأكبر من النزلاء "
        f"بواقع {top_hotel[1]} نزلاء.",

        f"وكان سبب الإقامة الأكثر تكراراً هو "
        f"{top_reason[0]} بعدد {top_reason[1]} نزلاء.",

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

                new_page()

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


# ============================================================
# 21. تحميل صورة Telegram
# ============================================================

async def get_photo(update):

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


# ============================================================
# 22. قائمة أوامر المستخدم
# ============================================================

USER_COMMANDS = [

    BotCommand(
        "start",
        "🏠 الصفحة الرئيسية"
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
        "📄 كل نزيل في ملف"
    ),

    BotCommand(
        "all",
        "📚 جميع النزلاء في ملف واحد"
    ),

    BotCommand(
        "guests",
        "👥 نزلاء الفندق"
    ),

    BotCommand(
        "search",
        "🔎 البحث عن نزيل"
    ),

    BotCommand(
        "hotel",
        "🏨 معلومات الفندق"
    ),

    BotCommand(
        "password",
        "🔑 تغيير كلمة المرور"
    ),

    BotCommand(
        "cancel",
        "❌ إلغاء"
    ),
]


# ============================================================
# 23. قائمة أوامر المدير
# ============================================================

ADMIN_COMMANDS = [

    BotCommand(
        "start",
        "🏠 الصفحة الرئيسية"
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
        "addhotel",
        "➕ إضافة فندق"
    ),

    BotCommand(
        "hotels",
        "🏨 قائمة الفنادق"
    ),

    BotCommand(
        "daily",
        "📊 التقرير اليومي العام"
    ),

    BotCommand(
        "yesterday",
        "📅 تقرير أمس"
    ),

    BotCommand(
        "monthly",
        "📈 التقرير الشهري العام"
    ),

    BotCommand(
        "guests",
        "👥 جميع النزلاء"
    ),

    BotCommand(
        "search",
        "🔎 البحث عن نزيل"
    ),

    BotCommand(
        "password",
        "🔑 تغيير كلمة المرور"
    ),

    BotCommand(
        "cancel",
        "❌ إلغاء"
    ),
]


# ============================================================
# 24. تحديث قائمة أوامر المستخدم
# ============================================================

async def set_user_commands(
    application,
    chat_id,
    admin=False
):

    commands = (
        ADMIN_COMMANDS
        if admin
        else USER_COMMANDS
    )

    try:

        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(
                chat_id
            )
        )

    except Exception as e:

        print(
            "Command menu error:",
            e
        )


# ============================================================
# 25. START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    context.user_data[
        "pdf_mode"
    ] = DEFAULT_MODE

    chat_id = update.effective_chat.id

    await set_user_commands(
        context.application,
        chat_id,
        False
    )

    if os.path.exists(
        WELCOME_IMAGE
    ):

        try:

            with open(
                WELCOME_IMAGE,
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

    await update.message.reply_text(

        "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

        "أهلاً وسهلاً ومرحباً بك\n"

        "🏨 في نظام معلومات الفنادق 🏨\n\n"

        "نسعد بخدمتك، وقد تم تصميم هذا النظام "
        "لتسهيل استقبال وتنظيم بيانات النزلاء "
        "وإعداد التقارير بصورة منظمة وسريعة.\n\n"

        "🔐 لاستخدام النظام، يرجى تسجيل الدخول "
        "باستخدام اسم المستخدم وكلمة المرور الخاصة بك.\n\n"

        "بعد تسجيل الدخول ستظهر لك الخدمات "
        "المتاحة حسب صلاحية حسابك.\n\n"

        "📋 يمكنك في أي وقت كتابة / "
        "لإظهار قائمة الأوامر المتاحة.\n\n"

        "🌷 أهلاً وسهلاً بك، ونتمنى لك "
        "التوفيق في عملك."
    )


# ============================================================
# 26. LOGIN
# ============================================================

async def login(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    context.user_data[
        "login_state"
    ] = "username"

    await update.message.reply_text(

        "🔐 تسجيل الدخول\n\n"

        "يرجى إرسال اسم المستخدم:"
    )


# ============================================================
# 27. معالجة تسجيل الدخول
# ============================================================

async def handle_login_message(
    update,
    context
):

    state = context.user_data.get(
        "login_state"
    )

    if state not in (
        "username",
        "password"
    ):

        return False

    text = (
        update.message.text
        or ""
    ).strip()

    if not text:

        return True

    if state == "username":

        user = get_user_by_username(
            text
        )

        if not user:

            await update.message.reply_text(
                "❌ اسم المستخدم غير موجود.\n\n"
                "حاول مرة أخرى."
            )

            return True

        if not user["active"]:

            await update.message.reply_text(
                "🚫 هذا الحساب غير مفعل حالياً."
            )

            context.user_data.clear()

            return True

        context.user_data[
            "login_username"
        ] = text

        context.user_data[
            "login_user_id"
        ] = user["id"]

        context.user_data[
            "login_state"
        ] = "password"

        await update.message.reply_text(

            "🔑 تم العثور على الحساب.\n\n"

            "الآن أرسل كلمة المرور:"
        )

        return True

    if state == "password":

        username = context.user_data.get(
            "login_username"
        )

        user = get_user_by_username(
            username
        )

        if not user:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ حدث خطأ، يرجى البدء من جديد."
            )

            return True

        if not verify_password(
            text,
            user["password_hash"],
            user["password_salt"]
        ):

            await update.message.reply_text(
                "❌ كلمة المرور غير صحيحة.\n\n"
                "حاول مرة أخرى."
            )

            return True

        context.user_data.clear()

        context.user_data[
            "logged_in"
        ] = True

        context.user_data[
            "user_id"
        ] = user["id"]

        context.user_data[
            "username"
        ] = user["username"]

        context.user_data[
            "role"
        ] = user["role"]

        context.user_data[
            "hotel_id"
        ] = user["hotel_id"]

        context.user_data[
            "pdf_mode"
        ] = DEFAULT_MODE

        if user["role"] == "admin":

            await set_user_commands(
                context.application,
                update.effective_chat.id,
                True
            )

            await update.message.reply_text(

                "✅ تم تسجيل الدخول بنجاح.\n\n"

                "👨‍💼 أهلاً بك مدير النظام.\n\n"

                "يمكنك الآن إدارة الفنادق "
                "والاطلاع على التقارير والبيانات."
            )

        else:

            hotel = get_hotel_by_id(
                user["hotel_id"]
            )

            hotel_name = (
                hotel["hotel_name"]
                if hotel
                else "الفندق"
            )

            await set_user_commands(
                context.application,
                update.effective_chat.id,
                False
            )

            await update.message.reply_text(

                "✅ تم تسجيل الدخول بنجاح.\n\n"

                f"🏨 الفندق: {hotel_name}\n\n"

                "أهلاً بك في نظام معلومات الفنادق.\n\n"

                "يمكنك الآن إرسال رسالة النزيل "
                "المحولة من المجموعة، وسيتم حفظها "
                "تلقائياً ضمن بيانات فندقك."
            )

        return True

    return True


# ============================================================
# 28. LOGOUT
# ============================================================

async def logout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    await set_user_commands(
        context.application,
        update.effective_chat.id,
        False
    )

    await update.message.reply_text(

        "🚪 تم تسجيل الخروج بنجاح.\n\n"

        "للدخول من جديد استخدم:\n"
        "/login"
    )


# ============================================================
# 29. SINGLE
# ============================================================

async def single_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(context):

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً.\n\n"
            "/login"
        )

        return

    context.user_data[
        "pdf_mode"
    ] = "single"

    await update.message.reply_text(

        "📄 تم اختيار وضع الملفات المستقلة.\n\n"

        "سيتم إنشاء ملف PDF مستقل لكل نزيل."
    )


# ============================================================
# 30. ALL
# ============================================================

async def all_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(context):

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً.\n\n"
            "/login"
        )

        return

    context.user_data[
        "pdf_mode"
    ] = "all"

    await update.message.reply_text(

        "📚 تم اختيار وضع الملف الموحد.\n\n"

        "سيتم جمع جميع النزلاء الموجودين "
        "في الرسالة داخل ملف PDF واحد."
    )


# ============================================================
# 31. CANCEL
# ============================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    logged = context.user_data.get(
        "logged_in"
    )

    role = context.user_data.get(
        "role"
    )

    hotel_id = context.user_data.get(
        "hotel_id"
    )

    username = context.user_data.get(
        "username"
    )

    context.user_data.clear()

    if logged:

        context.user_data[
            "logged_in"
        ] = True

        context.user_data[
            "role"
        ] = role

        context.user_data[
            "hotel_id"
        ] = hotel_id

        context.user_data[
            "username"
        ] = username

        context.user_data[
            "pdf_mode"
        ] = DEFAULT_MODE

    await update.message.reply_text(

        "❌ تم إلغاء العملية.\n\n"

        "يمكنك متابعة استخدام النظام."
    )


# ============================================================
# 32. إضافة فندق - البداية
# ============================================================

async def add_hotel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(context):

        await update.message.reply_text(
            "🚫 هذا الأمر متاح للمدير فقط."
        )

        return

    context.user_data[
        "add_hotel_state"
    ] = "name"

    await update.message.reply_text(

        "➕ إضافة فندق جديد\n\n"

        "أرسل اسم الفندق:"
    )


# ============================================================
# 33. معالجة إضافة الفندق
# ============================================================

async def handle_add_hotel_message(
    update,
    context
):

    state = context.user_data.get(
        "add_hotel_state"
    )

    if not state:

        return False

    text = (
        update.message.text
        or ""
    ).strip()

    if not text:

        return True

    if state == "name":

        context.user_data[
            "new_hotel_name"
        ] = text

        context.user_data[
            "add_hotel_state"
        ] = "username"

        await update.message.reply_text(
            "👤 أرسل اسم المستخدم الخاص بالفندق:"
        )

        return True

    if state == "username":

        username = text

        existing = get_user_by_username(
            username
        )

        if existing:

            await update.message.reply_text(
                "❌ اسم المستخدم مستخدم مسبقاً.\n\n"
                "أرسل اسماً آخر:"
            )

            return True

        context.user_data[
            "new_hotel_username"
        ] = username

        context.user_data[
            "add_hotel_state"
        ] = "password"

        await update.message.reply_text(
            "🔑 أرسل كلمة المرور الخاصة بالفندق:"
        )

        return True

    if state == "password":

        password = text

        if len(password) < 6:

            await update.message.reply_text(
                "❌ كلمة المرور يجب أن تكون "
                "6 أحرف أو أرقام على الأقل."
            )

            return True

        hotel_name = context.user_data[
            "new_hotel_name"
        ]

        username = context.user_data[
            "new_hotel_username"
        ]

        password_hash, salt = hash_password(
            password
        )

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
                    governorate,
                    address,
                    phone,
                    active,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hotel_name,
                    username,
                    password_hash,
                    salt,
                    "",
                    "",
                    "",
                    1,
                    datetime.now().isoformat()
                )
            )

            hotel_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO users (
                    username,
                    password_hash,
                    password_salt,
                    role,
                    hotel_id,
                    active,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    password_hash,
                    salt,
                    "hotel",
                    hotel_id,
                    1,
                    datetime.now().isoformat()
                )
            )

            connection.commit()

        except sqlite3.IntegrityError:

            connection.close()

            await update.message.reply_text(
                "❌ تعذر إنشاء الحساب، "
                "قد يكون اسم المستخدم مستخدماً."
            )

            return True

        connection.close()

        context.user_data.pop(
            "add_hotel_state",
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

            "✅ تم إنشاء الفندق بنجاح.\n\n"

            f"🏨 الفندق: {hotel_name}\n"
            f"👤 اسم المستخدم: {username}\n\n"

            "🔐 تم حفظ كلمة المرور بشكل آمن.\n\n"

            "يمكن لصاحب الفندق الآن استخدام "
            "/login لتسجيل الدخول."
        )

        return True

    return True


# ============================================================
# 34. قائمة الفنادق
# ============================================================

async def hotels_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(context):

        await update.message.reply_text(
            "🚫 هذا الأمر متاح للمدير فقط."
        )

        return

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM hotels
        ORDER BY id ASC
        """
    )

    hotels = cursor.fetchall()

    connection.close()

    if not hotels:

        await update.message.reply_text(
            "🏨 لا توجد فنادق مسجلة حتى الآن."
        )

        return

    text = "🏨 قائمة الفنادق\n\n"

    for hotel in hotels:

        status = (
            "🟢 فعال"
            if hotel["active"]
            else "🔴 متوقف"
        )

        text += (

            f"#{hotel['id']}\n"
            f"🏨 {hotel['hotel_name']}\n"
            f"👤 {hotel['username']}\n"
            f"{status}\n\n"
        )

    await update.message.reply_text(
        text
    )


# ============================================================
# 35. معلومات الفندق
# ============================================================

async def hotel_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(context):

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً."
        )

        return

    hotel_id = get_hotel_id(
        context
    )

    if is_admin(context):

        await update.message.reply_text(
            "👨‍💼 أنت مدير النظام.\n\n"
            "استخدم /hotels لعرض قائمة الفنادق."
        )

        return

    hotel = get_hotel_by_id(
        hotel_id
    )

    if not hotel:

        await update.message.reply_text(
            "❌ لم يتم العثور على بيانات الفندق."
        )

        return

    await update.message.reply_text(

        "🏨 معلومات الفندق\n\n"

        f"اسم الفندق: {hotel['hotel_name']}\n"
        f"اسم المستخدم: {hotel['username']}\n"
        f"المحافظة: {hotel['governorate'] or 'غير مذكور'}\n"
        f"العنوان: {hotel['address'] or 'غير مذكور'}\n"
        f"الهاتف: {hotel['phone'] or 'غير مذكور'}\n"
        f"الحالة: {'فعال' if hotel['active'] else 'متوقف'}"
    )


# ============================================================
# 36. تغيير كلمة المرور
# ============================================================

async def change_password(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(context):

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً."
        )

        return

    context.user_data[
        "password_state"
    ] = "new"

    await update.message.reply_text(

        "🔑 تغيير كلمة المرور\n\n"

        "أرسل كلمة المرور الجديدة:"
    )


# ============================================================
# 37. معالجة كلمة المرور
# ============================================================

async def handle_password_message(
    update,
    context
):

    state = context.user_data.get(
        "password_state"
    )

    if state != "new":

        return False

    password = (
        update.message.text
        or ""
    ).strip()

    if len(password) < 6:

        await update.message.reply_text(
            "❌ كلمة المرور يجب أن تكون "
            "6 أحرف أو أرقام على الأقل."
        )

        return True

    password_hash, salt = hash_password(
        password
    )

    user_id = context.user_data.get(
        "user_id"
    )

    if not user_id:

        context.user_data.pop(
            "password_state",
            None
        )

        await update.message.reply_text(
            "❌ تعذر تحديد الحساب."
        )

        return True

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE users
        SET password_hash = ?,
            password_salt = ?
        WHERE id = ?
        """,
        (
            password_hash,
            salt,
            user_id
        )
    )

    # إذا كان الحساب فندقاً
    # نحدث كلمة مرور سجل الفندق أيضاً
    if context.user_data.get(
        "role"
    ) == "hotel":

        hotel_id = context.user_data.get(
            "hotel_id"
        )

        cursor.execute(
            """
            UPDATE hotels
            SET password_hash = ?,
                password_salt = ?
            WHERE id = ?
            """,
            (
                password_hash,
                salt,
                hotel_id
            )
        )

    connection.commit()

    connection.close()

    context.user_data.pop(
        "password_state",
        None
    )

    await update.message.reply_text(
        "✅ تم تغيير كلمة المرور بنجاح."
    )

    return True


# ============================================================
# 38. البحث
# ============================================================

async def search_guest(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(context):

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً."
        )

        return

    context.user_data[
        "search_state"
    ] = True

    await update.message.reply_text(

        "🔎 البحث عن نزيل\n\n"

        "أرسل اسم النزيل أو جزءاً منه:"
    )


# ============================================================
# 39. معالجة البحث
# ============================================================

async def handle_search_message(
    update,
    context
):

    if not context.user_data.get(
        "search_state"
    ):

        return False

    query = (
        update.message.text
        or ""
    ).strip()

    hotel_id = None

    if not is_admin(context):

        hotel_id = get_hotel_id(
            context
        )

    connection = get_db()

    cursor = connection.cursor()

    if hotel_id:

        cursor.execute(
            """
            SELECT *
            FROM guests
            WHERE hotel_id = ?
            AND guest_name LIKE ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (
                hotel_id,
                f"%{query}%"
            )
        )

    else:

        cursor.execute(
            """
            SELECT *
            FROM guests
            WHERE guest_name LIKE ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (
                f"%{query}%",
            )
        )

    rows = cursor.fetchall()

    connection.close()

    context.user_data.pop(
        "search_state",
        None
    )

    if not rows:

        await update.message.reply_text(
            "❌ لم يتم العثور على نتائج."
        )

        return True

    text = (
        "🔎 نتائج البحث\n\n"
    )

    for row in rows:

        text += (

            f"👤 {row['guest_name']}\n"
            f"🏨 {row['hotel']}\n"
            f"🚪 الغرفة: {row['room']}\n"
            f"📅 {row['record_date']}\n"
            f"🎯 {row['reason']}\n"
            "────────────\n"
        )

    await update.message.reply_text(
        text
    )

    return True


# ============================================================
# 40. قائمة النزلاء
# ============================================================

async def guests_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(context):

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً."
        )

        return

    hotel_id = None

    if not is_admin(context):

        hotel_id = get_hotel_id(
            context
        )

    rows = get_guests_by_date(
        date.today().isoformat(),
        hotel_id
    )

    if not rows:

        await update.message.reply_text(
            "👥 لا توجد بيانات نزلاء مسجلة اليوم."
        )

        return

    text = (
        "👥 نزلاء اليوم\n\n"
    )

    for row in rows:

        text += (

            f"👤 {row['guest_name']}\n"
            f"🏨 {row['hotel']}\n"
            f"🚪 الغرفة: {row['room']}\n"
            f"🎯 {row['reason']}\n"
            "────────────\n"
        )

    await update.message.reply_text(
        text
    )


# ============================================================
# 41. معالجة رسالة النزيل
# ============================================================

async def process_guest_message(
    update,
    context
):

    if not is_logged_in(context):

        await update.message.reply_text(

            "🔐 يجب تسجيل الدخول أولاً.\n\n"

            "استخدم:\n"
            "/login"
        )

        return

    if is_admin(context):

        await update.message.reply_text(

            "👨‍💼 أنت داخل حساب المدير.\n\n"

            "لاستقبال بيانات نزيل يجب استخدام "
            "حساب الفندق المخصص له."
        )

        return

    message = update.message

    if not message:

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

            "قم بتحويل رسالة النزيل التي "
            "تحتوي على البيانات إلى البوت."
        )

        return

    guests_text = split_guests(
        text
    )

    if not guests_text:

        await message.reply_text(
            "❌ لم أتمكن من استخراج بيانات النزيل."
        )

        return

    image = await get_photo(
        update
    )

    hotel_id = get_hotel_id(
        context
    )

    hotel = get_hotel_by_id(
        hotel_id
    )

    if not hotel:

        await message.reply_text(
            "❌ تعذر تحديد الفندق المرتبط بالحساب."
        )

        return

    guests = []

    for guest_text in guests_text:

        guest = parse_guest(
            guest_text
        )

        # إجبار اسم الفندق على الفندق
        # المرتبط بالحساب
        guest[
            "اسم الفندق"
        ] = hotel["hotel_name"]

        save_guest(
            guest,
            update,
            hotel_id
        )

        guests.append(
            guest
        )

    mode = context.user_data.get(
        "pdf_mode",
        DEFAULT_MODE
    )

    # --------------------------------------------------------
    # ملف مستقل
    # --------------------------------------------------------

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

                    f"🏨 الفندق: {hotel['hotel_name']}\n"

                    f"🚪 الغرفة: "
                    f"{guest.get('رقم الغرفة', 'غير مذكور')}\n"

                    f"🛏 الجناح: "
                    f"{guest.get('رقم الجناح', 'غير مذكور')}\n"

                    f"🎯 سبب الإقامة: "
                    f"{guest.get('سبب الإقامة', 'غير مذكور')}\n\n"

                    "✅ تم حفظ البيانات في قاعدة البيانات."
                )
            )

            await asyncio.sleep(
                0.5
            )

    # --------------------------------------------------------
    # ملف موحد
    # --------------------------------------------------------

    else:

        pdf_file = create_all_guests_pdf(
            guests,
            title=(
                f"نزلاء {hotel['hotel_name']}"
            )
        )

        filename = (
            f"نزلاء_"
            f"{safe_filename(hotel['hotel_name']).replace('.pdf', '')}"
            f"_"
            f"{date.today().isoformat()}.pdf"
        )

        await message.reply_document(

            document=pdf_file,

            filename=filename,

            caption=(

                "📚 تم إنشاء ملف موحد للنزلاء\n\n"

                f"🏨 الفندق: {hotel['hotel_name']}\n"

                f"👥 عدد النزلاء: {len(guests)}\n\n"

                "✅ تم حفظ جميع البيانات."
            )
        )

    await message.reply_text(

        f"✅ تمت معالجة {len(guests)} نزيل بنجاح.\n\n"

        f"🏨 الفندق: {hotel['hotel_name']}\n\n"

        "📊 يمكنك الآن استخدام /daily "
        "للحصول على تقرير اليوم."
    )


# ============================================================
# 42. التقرير اليومي
# ============================================================

async def daily_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(context):

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً."
        )

        return

    hotel_id = None

    if not is_admin(context):

        hotel_id = get_hotel_id(
            context
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

    await send_report(
        update,
        rows,
        target_date,
        "تقرير عمل قسم معلومات الفنادق"
    )


# ============================================================
# 43. تقرير أمس
# ============================================================

async def yesterday_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(context):

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً."
        )

        return

    hotel_id = None

    if not is_admin(context):

        hotel_id = get_hotel_id(
            context
        )

    target_date = (
        date.today()
        - timedelta(days=1)
    ).isoformat()

    rows = get_guests_by_date(
        target_date,
        hotel_id
    )

    if not rows:

        await update.message.reply_text(

            f"📋 لا توجد بيانات مسجلة بتاريخ "
            f"{target_date}."
        )

        return

    await send_report(
        update,
        rows,
        target_date,
        "تقرير قسم معلومات الفنادق"
    )


# ============================================================
# 44. التقرير الشهري
# ============================================================

async def monthly_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_logged_in(context):

        await update.message.reply_text(
            "🔐 يجب تسجيل الدخول أولاً."
        )

        return

    hotel_id = None

    if not is_admin(context):

        hotel_id = get_hotel_id(
            context
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
            "📋 لا توجد بيانات خلال الشهر الحالي."
        )

        return

    await send_report(
        update,
        rows,
        current_month,
        "التقرير الشهري لقسم معلومات الفنادق"
    )


# ============================================================
# 45. إرسال التقرير
# ============================================================

async def send_report(
    update,
    rows,
    report_date,
    title
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

        f"📋 {title}\n\n"

        f"📅 التاريخ: {report_date}\n\n"

        f"👥 إجمالي النزلاء: {total}\n\n"

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

        f"بلغ عدد النزلاء المسجلين خلال الفترة "
        f"{report_date} {total} نزيلاً. "

        f"وسجلت محافظة {top_gov[0]} أعلى عدد "
        f"من النزلاء بواقع {top_gov[1]} نزلاء. "

        f"كما سجل فندق {top_hotel[0]} العدد الأكبر "
        f"من النزلاء بواقع {top_hotel[1]} نزلاء. "

        f"وكان سبب الإقامة الأكثر تكراراً هو "
        f"{top_reason[0]} بعدد {top_reason[1]} نزلاء."
    )

    await update.message.reply_text(
        text
    )

    pdf_file = create_daily_pdf(
        rows,
        report_date,
        title=title
    )

    filename = (
        "تقرير_معلومات_الفنادق_"
        f"{report_date}.pdf"
    )

    await update.message.reply_document(

        document=pdf_file,

        filename=filename,

        caption=(
            f"📋 {title}\n"
            f"📅 {report_date}\n\n"
            "✅ تم إنشاء التقرير بصيغة PDF."
        )
    )


# ============================================================
# 46. التحقق من الأوامر
# ============================================================

async def protected_command(
    update,
    context,
    function
):

    if not is_logged_in(context):

        await update.message.reply_text(

            "🔐 يجب تسجيل الدخول أولاً.\n\n"

            "استخدم:\n"
            "/login"
        )

        return

    await function(
        update,
        context
    )


# ============================================================
# 47. استقبال الرسائل النصية
# ============================================================

async def text_router(
    update,
    context
):

    # تسجيل الدخول
    if context.user_data.get(
        "login_state"
    ):

        handled = await handle_login_message(
            update,
            context
        )

        if handled:
            return

    # إضافة فندق
    if context.user_data.get(
        "add_hotel_state"
    ):

        handled = await handle_add_hotel_message(
            update,
            context
        )

        if handled:
            return

    # تغيير كلمة المرور
    if context.user_data.get(
        "password_state"
    ):

        handled = await handle_password_message(
            update,
            context
        )

        if handled:
            return

    # البحث
    if context.user_data.get(
        "search_state"
    ):

        handled = await handle_search_message(
            update,
            context
        )

        if handled:
            return

    # رسالة نزيل
    await process_guest_message(
        update,
        context
    )


# ============================================================
# 48. إنشاء التطبيق
# ============================================================

if not TOKEN:

    print(
        "WARNING: BOT_TOKEN is not set!"
    )

app = (
    ApplicationBuilder()
    .token(TOKEN)
    .build()
)


# ============================================================
# 49. تسجيل الأوامر
# ============================================================

app.add_handler(
    CommandHandler(
        "start",
        start
    )
)

app.add_handler(
    CommandHandler(
        "login",
        login
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
        "guests",
        guests_list
    )
)

app.add_handler(
    CommandHandler(
        "search",
        search_guest
    )
)

app.add_handler(
    CommandHandler(
        "hotel",
        hotel_info
    )
)

app.add_handler(
    CommandHandler(
        "password",
        change_password
    )
)

app.add_handler(
    CommandHandler(
        "addhotel",
        add_hotel
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
        "cancel",
        cancel
    )
)


# ============================================================
# 50. استقبال النصوص والصور
# ============================================================

app.add_handler(

    MessageHandler(

        (
            filters.TEXT
            |
            filters.PHOTO
        )
        &
        ~filters.COMMAND,

        text_router
    )
)


# ============================================================
# 51. MAIN
# ============================================================

async def main():

    # --------------------------------------------------------
    # إنشاء قاعدة البيانات
    # --------------------------------------------------------

    init_database()

    # --------------------------------------------------------
    # التأكد من التوكن
    # --------------------------------------------------------

    if not TOKEN:

        print(
            "ERROR: BOT_TOKEN is not set!"
        )

        return

    # --------------------------------------------------------
    # تشغيل خادم Render
    # --------------------------------------------------------

    threading.Thread(

        target=run_web_server,

        daemon=True

    ).start()

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    await app.initialize()

    # --------------------------------------------------------
    # قائمة الأوامر العامة
    # تظهر للمستخدم قبل تسجيل الدخول
    # --------------------------------------------------------

    await app.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "🏠 الصفحة الرئيسية"
            ),
            BotCommand(
                "login",
                "🔐 تسجيل الدخول"
            ),
        ]
    )

    # --------------------------------------------------------
    # تشغيل التطبيق
    # --------------------------------------------------------

    await app.start()

    await app.updater.start_polling()

    print(
        "===================================="
    )

    print(
        "🏨 Hotel Information Bot"
    )

    print(
        "✅ Telegram Bot is running"
    )

    print(
        "===================================="
    )

    try:

        await asyncio.Event().wait()

    finally:

        await app.updater.stop()

        await app.stop()

        await app.shutdown()


# ============================================================
# 52. تشغيل البرنامج
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
        )
