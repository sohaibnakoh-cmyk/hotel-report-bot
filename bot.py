import os
import re
import sqlite3
import asyncio
import threading
from io import BytesIO
from datetime import datetime, date, timedelta
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import arabic_reshaper
from bidi.algorithm import get_display


# ============================================================
# الإعدادات
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

DATABASE_FILE = "hotel_reports.db"

IMAGE_FILE = "images.png"

PAGE_WIDTH, PAGE_HEIGHT = A4

# الوضع الافتراضي:
# all    = جميع النزلاء في ملف واحد
# single = كل نزيل في ملف مستقل
PDF_MODE = "all"


# ============================================================
# البحث عن الخط العربي
# ============================================================

def find_arabic_font():

    fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",

        "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",

        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",

        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    for font in fonts:
        if os.path.exists(font):
            return font

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


# ============================================================
# معالجة النص العربي
# ============================================================

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


# ============================================================
# اسم ملف آمن
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
# قاعدة البيانات
# ============================================================

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
            telegram_username TEXT

        )
        """
    )

    connection.commit()

    connection.close()

    print(
        "Database initialized successfully"
    )


# ============================================================
# حفظ نزيل
# ============================================================

def save_guest(
    guest,
    update
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
            telegram_username

        )

        VALUES (
            ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?
        )
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

            username
        )
    )

    connection.commit()

    guest_id = cursor.lastrowid

    connection.close()

    return guest_id


# ============================================================
# جلب بيانات اليوم
# ============================================================

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

        (
            target_date,
        )
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# ============================================================
# جلب بيانات الشهر
# ============================================================

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

        (
            year_month,
        )
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# ============================================================
# استخراج قيمة من النص
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
# تحليل بيانات النزيل
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
# تقسيم عدة نزلاء داخل رسالة واحدة
# ============================================================

def split_guests(text):

    # يسمح بفصل النزلاء بواسطة:
    #
    # ====================
    #
    # --------------------
    #
    # ********************
    #
    # أو كلمة نزيل / النزيل

    parts = re.split(
        r"\n\s*(?:={3,}|-{3,}|\*{3,})\s*\n",
        text
    )

    parts = [
        part.strip()
        for part in parts
        if part.strip()
    ]

    return parts


# ============================================================
# خادم Render
# ============================================================

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


# ============================================================
# تحميل صورة Telegram
# ============================================================

async def get_photo(update):

    message = update.message

    if not message:
        return None

    if not message.photo:
        return None

    try:

        photo = message.photo[-1]

        telegram_file = (
            await photo.get_file()
        )

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
# رسم عنوان PDF
# ============================================================

def draw_pdf_header(
    pdf,
    title
):

    pdf.setFont(
        PDF_FONT,
        18
    )

    pdf.drawRightString(
        PAGE_WIDTH - 45,
        PAGE_HEIGHT - 45,
        arabic_text(
            "مكتب معلومات الفنادق"
        )
    )

    pdf.setFont(
        PDF_FONT,
        15
    )

    pdf.drawRightString(
        PAGE_WIDTH - 45,
        PAGE_HEIGHT - 78,
        arabic_text(
            title
        )
    )

    pdf.setStrokeColor(
        colors.grey
    )

    pdf.line(
        45,
        PAGE_HEIGHT - 95,
        PAGE_WIDTH - 45,
        PAGE_HEIGHT - 95
    )


# ============================================================
# إنشاء PDF لنزيل واحد
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
        "تقرير نزيل فندق"
    )

    y = PAGE_HEIGHT - 125

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
            guest.get(
                "اسم الفندق",
                "غير مذكور"
            )
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

    for key, value in fields:

        if y < 100:

            pdf.showPage()

            draw_pdf_header(
                pdf,
                "تقرير نزيل فندق"
            )

            y = PAGE_HEIGHT - 125

        # صندوق الحقل

        pdf.setStrokeColor(
            colors.lightgrey
        )

        pdf.roundRect(
            45,
            y - 25,
            PAGE_WIDTH - 90,
            32,
            5,
            stroke=1,
            fill=0
        )

        pdf.setFont(
            PDF_FONT,
            10
        )

        pdf.drawRightString(
            PAGE_WIDTH - 60,
            y - 14,
            arabic_text(
                f"{key}: {value}"
            )
        )

        y -= 43

    # --------------------------------------------------------
    # الصورة
    # --------------------------------------------------------

    if image_data:

        try:

            image_data.seek(0)

            image = ImageReader(
                image_data
            )

            img_width = 240
            img_height = 180

            if y < 230:

                pdf.showPage()

                draw_pdf_header(
                    pdf,
                    "صورة النزيل"
                )

                y = PAGE_HEIGHT - 125

            pdf.drawImage(
                image,
                55,
                y - img_height,
                width=img_width,
                height=img_height,
                preserveAspectRatio=True,
                anchor="sw"
            )

        except Exception as e:

            print(
                "Image error:",
                e
            )

    pdf.save()

    buffer.seek(0)

    return buffer


# ============================================================
# PDF لجميع النزلاء في ملف واحد
# ============================================================

def create_all_guests_pdf(
    guests,
    image_data=None
):

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    total = len(guests)

    # --------------------------------------------------------
    # الغلاف
    # --------------------------------------------------------

    pdf.setFont(
        PDF_FONT,
        22
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 100,
        arabic_text(
            "مكتب معلومات الفنادق"
        )
    )

    pdf.setFont(
        PDF_FONT,
        18
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 145,
        arabic_text(
            "تقرير بيانات النزلاء"
        )
    )

    pdf.setFont(
        PDF_FONT,
        13
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 185,
        arabic_text(
            f"عدد النزلاء: {total}"
        )
    )

    pdf.setFont(
        PDF_FONT,
        11
    )

    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        PAGE_HEIGHT - 215,
        arabic_text(
            f"تاريخ التقرير: {date.today().isoformat()}"
        )
    )

    pdf.setStrokeColor(
        colors.grey
    )

    pdf.line(
        60,
        PAGE_HEIGHT - 240,
        PAGE_WIDTH - 60,
        PAGE_HEIGHT - 240
    )

    # --------------------------------------------------------
    # كل نزيل في قسم
    # --------------------------------------------------------

    for index, guest in enumerate(guests, start=1):

        pdf.showPage()

        draw_pdf_header(
            pdf,
            f"بيانات النزيل رقم {index}"
        )

        y = PAGE_HEIGHT - 125

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
                guest.get(
                    "اسم الفندق",
                    "غير مذكور"
                )
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

        for key, value in fields:

            pdf.setStrokeColor(
                colors.lightgrey
            )

            pdf.roundRect(
                45,
                y - 25,
                PAGE_WIDTH - 90,
                32,
                5,
                stroke=1,
                fill=0
            )

            pdf.setFont(
                PDF_FONT,
                10
            )

            pdf.drawRightString(
                PAGE_WIDTH - 60,
                y - 14,
                arabic_text(
                    f"{key}: {value}"
                )
            )

            y -= 43

        # الصورة المرتبطة بالرسالة
        if image_data:

            try:

                image_data.seek(0)

                image = ImageReader(
                    image_data
                )

                img_width = 220
                img_height = 165

                if y < 220:

                    pdf.showPage()

                    draw_pdf_header(
                        pdf,
                        f"صورة النزيل رقم {index}"
                    )

                    y = PAGE_HEIGHT - 125

                pdf.drawImage(
                    image,
                    55,
                    y - img_height,
                    width=img_width,
                    height=img_height,
                    preserveAspectRatio=True,
                    anchor="sw"
                )

            except Exception as e:

                print(
                    "Image error:",
                    e
                )

    pdf.save()

    buffer.seek(0)

    return buffer


# ============================================================
# التقرير اليومي PDF
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

    y = PAGE_HEIGHT - 50

    pdf.setFont(
        PDF_FONT,
        20
    )

    pdf.drawRightString(
        PAGE_WIDTH - 45,
        y,
        arabic_text(
            title
        )
    )

    y -= 35

    pdf.setFont(
        PDF_FONT,
        12
    )

    pdf.drawRightString(
        PAGE_WIDTH - 45,
        y,
        arabic_text(
            f"التاريخ: {target_date}"
        )
    )

    y -= 45

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

    suites = Counter(
        row["suite"]
        for row in rows
        if row["suite"]
        and row["suite"] != "غير مذكور"
    )

    rooms = Counter(
        row["room"]
        for row in rows
        if row["room"]
        and row["room"] != "غير مذكور"
    )

    # --------------------------------------------------------
    # دالة الأقسام
    # --------------------------------------------------------

    def section(
        title,
        counter
    ):

        nonlocal y

        if y < 100:

            pdf.showPage()

            y = PAGE_HEIGHT - 50

        pdf.setFont(
            PDF_FONT,
            14
        )

        pdf.drawRightString(
            PAGE_WIDTH - 45,
            y,
            arabic_text(
                title
            )
        )

        y -= 25

        pdf.setFont(
            PDF_FONT,
            10
        )

        for name, count in counter.most_common():

            if y < 60:

                pdf.showPage()

                y = PAGE_HEIGHT - 50

                pdf.setFont(
                    PDF_FONT,
                    10
                )

            pdf.drawRightString(
                PAGE_WIDTH - 60,
                y,
                arabic_text(
                    f"{name}: {count}"
                )
            )

            y -= 20

        y -= 15

    # --------------------------------------------------------
    # الإجمالي
    # --------------------------------------------------------

    pdf.setFont(
        PDF_FONT,
        15
    )

    pdf.drawRightString(
        PAGE_WIDTH - 45,
        y,
        arabic_text(
            f"إجمالي النزلاء: {total}"
        )
    )

    y -= 40

    section(
        "أولاً: التوزيع حسب المحافظة",
        governorates
    )

    section(
        "ثانياً: توزيع النزلاء على الفنادق",
        hotels
    )

    section(
        "ثالثاً: أسباب الإقامة",
        reasons
    )

    section(
        "رابعاً: أرقام الغرف",
        rooms
    )

    section(
        "خامساً: أرقام الأجنحة",
        suites
    )

    # --------------------------------------------------------
    # التحليل السردي
    # --------------------------------------------------------

    if y < 180:

        pdf.showPage()

        y = PAGE_HEIGHT - 50

    pdf.setFont(
        PDF_FONT,
        15
    )

    pdf.drawRightString(
        PAGE_WIDTH - 45,
        y,
        arabic_text(
            "سادساً: التحليل والسرد"
        )
    )

    y -= 30

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

    paragraphs = [

        f"بلغ إجمالي عدد النزلاء الذين تم تسجيل بياناتهم خلال يوم {target_date} عدد {total} نزيلاً.",

        f"وبحسب التوزيع الجغرافي، جاءت محافظة {top_gov[0]} في المرتبة الأولى بعدد {top_gov[1]} نزلاء.",

        f"أما على مستوى الفنادق، فقد سجل فندق {top_hotel[0]} العدد الأكبر من النزلاء بواقع {top_hotel[1]} نزلاء.",

        f"وكان سبب الإقامة الأكثر تكراراً هو {top_reason[0]} بعدد {top_reason[1]} نزلاء.",

        "وتوضح البيانات المسجلة خلال اليوم حركة النزلاء وتوزعهم على المحافظات والفنادق وأسباب الإقامة، بما يساعد على تنظيم البيانات وإعداد المتابعة اليومية لقسم معلومات الفنادق."
    ]

    pdf.setFont(
        PDF_FONT,
        10
    )

    for paragraph in paragraphs:

        # تقسيم الفقرة إلى أسطر
        words = paragraph.split()

        current = ""

        lines = []

        for word in words:

            test = (
                current + " " + word
            ).strip()

            if len(test) > 80:

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

                y = PAGE_HEIGHT - 50

                pdf.setFont(
                    PDF_FONT,
                    10
                )

            pdf.drawRightString(
                PAGE_WIDTH - 45,
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


# ============================================================
# رسالة الترحيب
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    welcome = (
        "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

        "أهلاً وسهلاً ومرحباً بك في\n"
        "🏨 قسم معلومات الفنادق\n\n"

        "يسعدنا خدمتك، وقد تم تجهيز هذا البوت "
        "لتسهيل تسجيل بيانات النزلاء وإعداد التقارير "
        "بشكل منظم وسريع.\n\n"

        "📋 يمكنك تحويل رسالة النزيل من المجموعة "
        "إلى البوت، سواء كانت تحتوي على نص فقط "
        "أو نصاً مع صورة.\n\n"

        "سيقوم البوت تلقائياً بـ:\n"
        "✅ استخراج بيانات النزيل\n"
        "✅ حفظ البيانات في قاعدة البيانات\n"
        "✅ إنشاء ملف PDF منسق\n"
        "✅ تسمية الملف باسم النزيل\n"
        "✅ إدراج الصورة ضمن التقرير عند وجودها\n"
        "✅ إعداد التقارير اليومية والشهرية\n\n"

        "📌 طريقة العمل:\n"
        "1️⃣ حوّل رسالة النزيل إلى البوت.\n"
        "2️⃣ انتظر معالجة البيانات.\n"
        "3️⃣ سيصلك ملف PDF جاهز.\n\n"

        "📊 ويمكنك استخدام الأوامر من القائمة "
        "بالضغط على زر / أسفل المحادثة.\n\n"

        "مع تمنياتنا لكم بالتوفيق والنجاح 🌷"
    )

    # --------------------------------------------------------
    # إرسال الصورة إذا كانت موجودة
    # --------------------------------------------------------

    if os.path.exists(IMAGE_FILE):

        try:

            with open(
                IMAGE_FILE,
                "rb"
            ) as photo:

                await update.message.reply_photo(
                    photo=photo,
                    caption=welcome
                )

            return

        except Exception as e:

            print(
                "Welcome image error:",
                e
            )

    await update.message.reply_text(
        welcome
    )


# ============================================================
# أمر المساعدة
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "📋 أوامر بوت قسم معلومات الفنادق\n\n"

        "/start\n"
        "رسالة الترحيب وشرح طريقة العمل.\n\n"

        "/daily\n"
        "إنشاء التقرير اليومي.\n\n"

        "/yesterday\n"
        "إنشاء تقرير أمس.\n\n"

        "/monthly\n"
        "إنشاء التقرير الشهري.\n\n"

        "/mode\n"
        "اختيار طريقة ملفات PDF.\n\n"

        "/all\n"
        "إرسال جميع النزلاء في ملف PDF واحد.\n\n"

        "/single\n"
        "إرسال كل نزيل في ملف PDF مستقل.\n\n"

        "/help\n"
        "عرض هذه القائمة."
    )

    await update.message.reply_text(
        text
    )


# ============================================================
# اختيار وضع PDF
# ============================================================

async def mode_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global PDF_MODE

    if PDF_MODE == "all":

        current = (
            "📚 الوضع الحالي:\n"
            "جميع النزلاء في ملف PDF واحد."
        )

    else:

        current = (
            "📄 الوضع الحالي:\n"
            "كل نزيل في ملف PDF مستقل."
        )

    text = (
        f"{current}\n\n"

        "لتغيير الوضع:\n\n"

        "/all\n"
        "جميع النزلاء في ملف واحد.\n\n"

        "/single\n"
        "كل نزيل في ملف مستقل."
    )

    await update.message.reply_text(
        text
    )


# ============================================================
# وضع ملف واحد
# ============================================================

async def all_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global PDF_MODE

    PDF_MODE = "all"

    await update.message.reply_text(
        "✅ تم اختيار وضع:\n\n"
        "📚 جميع النزلاء في ملف PDF واحد."
    )


# ============================================================
# وضع ملفات منفصلة
# ============================================================

async def single_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global PDF_MODE

    PDF_MODE = "single"

    await update.message.reply_text(
        "✅ تم اختيار وضع:\n\n"
        "📄 كل نزيل في ملف PDF مستقل."
    )


# ============================================================
# معالجة رسالة النزيل
# ============================================================

async def process_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.message

    if not message:
        return

    # --------------------------------------------------------
    # النص أو Caption
    # --------------------------------------------------------

    text = (
        message.text
        if message.text
        else message.caption
        if message.caption
        else ""
    )

    if not text.strip():

        await message.reply_text(
            "❌ لم أجد بيانات نصية في الرسالة.\n\n"
            "يرجى تحويل رسالة تحتوي على بيانات النزيل."
        )

        return

    # --------------------------------------------------------
    # تقسيم النزلاء
    # --------------------------------------------------------

    guests_text = split_guests(
        text
    )

    guests = []

    # --------------------------------------------------------
    # الصورة
    # --------------------------------------------------------

    image = await get_photo(
        update
    )

    # --------------------------------------------------------
    # استخراج وحفظ البيانات
    # --------------------------------------------------------

    for guest_text in guests_text:

        guest = parse_guest(
            guest_text
        )

        save_guest(
            guest,
            update
        )

        guests.append(
            guest
        )

    # --------------------------------------------------------
    # لا يوجد نزلاء
    # --------------------------------------------------------

    if not guests:

        await message.reply_text(
            "❌ لم يتم العثور على بيانات نزيل."
        )

        return

    # ========================================================
    # وضع ملف واحد
    # ========================================================

    if PDF_MODE == "all":

        pdf_file = create_all_guests_pdf(
            guests,
            image
        )

        today = date.today().isoformat()

        filename = (
            f"تقرير_النزلاء_{today}.pdf"
        )

        await message.reply_document(

            document=pdf_file,

            filename=filename,

            caption=(
                "📚 تم إنشاء التقرير بنجاح\n\n"

                f"👥 عدد النزلاء: {len(guests)}\n"

                "📄 جميع البيانات موجودة في ملف PDF واحد.\n\n"

                "✅ تم حفظ البيانات في قاعدة البيانات."
            )
        )

    # ========================================================
    # وضع الملفات المنفصلة
    # ========================================================

    else:

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
                    "📋 تقرير نزيل\n\n"

                    f"👤 الاسم: {guest_name}\n"

                    f"🏨 الفندق: "
                    f"{guest.get('اسم الفندق', 'غير مذكور')}\n"

                    f"🚪 الغرفة: "
                    f"{guest.get('رقم الغرفة', 'غير مذكور')}\n"

                    f"🛏 الجناح: "
                    f"{guest.get('رقم الجناح', 'غير مذكور')}\n"

                    f"📅 تاريخ النزول: "
                    f"{guest.get('تاريخ النزول', 'غير مذكور')}\n\n"

                    "✅ تم حفظ البيانات."
                )
            )

            await asyncio.sleep(
                0.5
            )

    # --------------------------------------------------------
    # رسالة نهائية
    # --------------------------------------------------------

    await message.reply_text(
        f"✅ تمت معالجة {len(guests)} نزيل بنجاح.\n\n"

        "📊 تمت إضافة البيانات إلى قاعدة البيانات.\n\n"

        "لإنشاء التقرير اليومي استخدم:\n"
        "/daily"
    )


# ============================================================
# التقرير اليومي
# ============================================================

async def daily_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    target_date = date.today().isoformat()

    rows = get_guests_by_date(
        target_date
    )

    if not rows:

        await update.message.reply_text(
            "📋 لا توجد بيانات مسجلة اليوم حتى الآن."
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

        f"كما سجل فندق {top_hotel[0]} العدد الأكبر "
        f"من النزلاء بواقع {top_hotel[1]} نزلاء. "

        f"وكان سبب الإقامة الأكثر تكراراً هو "
        f"{top_reason[0]} بعدد {top_reason[1]} نزلاء.\n\n"

        "وتوضح البيانات اليومية حركة النزلاء "
        "وتوزعهم على المحافظات والفنادق، إضافة "
        "إلى أسباب الإقامة المسجلة خلال اليوم."
    )

    # --------------------------------------------------------
    # إرسال التقرير النصي
    # --------------------------------------------------------

    await update.message.reply_text(
        text
    )

    # --------------------------------------------------------
    # إنشاء PDF
    # --------------------------------------------------------

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
            "📋 تقرير عمل قسم معلومات الفنادق\n"
            f"📅 {target_date}\n\n"
            "✅ تم إنشاء التقرير اليومي PDF."
        )
    )


# ============================================================
# تقرير أمس
# ============================================================

async def yesterday_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    yesterday = (
        date.today()
        - timedelta(days=1)
    ).isoformat()

    rows = get_guests_by_date(
        yesterday
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
            f"📅 {yesterday}\n\n"
            "✅ تم إنشاء التقرير."
        )
    )


# ============================================================
# التقرير الشهري
# ============================================================

async def monthly_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

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
            "📊 التقرير الشهري\n"
            f"📅 {current_month}\n\n"
            "✅ تم إنشاء التقرير PDF."
        )
    )


# ============================================================
# إعداد قائمة أوامر Telegram
# ============================================================

async def setup_commands(application):

    commands = [

        BotCommand(
            "start",
            "رسالة الترحيب"
        ),

        BotCommand(
            "daily",
            "التقرير اليومي"
        ),

        BotCommand(
            "yesterday",
            "تقرير أمس"
        ),

        BotCommand(
            "monthly",
            "التقرير الشهري"
        ),

        BotCommand(
            "mode",
            "اختيار وضع PDF"
        ),

        BotCommand(
            "all",
            "جميع النزلاء في ملف واحد"
        ),

        BotCommand(
            "single",
            "كل نزيل في ملف مستقل"
        ),

        BotCommand(
            "help",
            "المساعدة"
        ),
    ]

    await application.bot.set_my_commands(
        commands
    )

    print(
        "Telegram commands registered successfully."
    )


# ============================================================
# إنشاء التطبيق
# ============================================================

if not TOKEN:

    print(
        "ERROR: BOT_TOKEN is not set!"
    )


app = (
    ApplicationBuilder()
    .token(TOKEN)
    .post_init(setup_commands)
    .build()
)


# ============================================================
# الأوامر
# ============================================================

app.add_handler(
    CommandHandler(
        "start",
        start
    )
)

app.add_handler(
    CommandHandler(
        "help",
        help_command
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
        "mode",
        mode_command
    )
)

app.add_handler(
    CommandHandler(
        "all",
        all_command
    )
)

app.add_handler(
    CommandHandler(
        "single",
        single_command
    )
)


# ============================================================
# استقبال رسائل النزلاء
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

        process_message
    )
)


# ============================================================
# MAIN
# ============================================================

async def main():

    # --------------------------------------------------------
    # قاعدة البيانات
    # --------------------------------------------------------

    init_database()

    # --------------------------------------------------------
    # Web Server لـ Render
    # --------------------------------------------------------

    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    print(
        "Starting Telegram Bot..."
    )

    if not TOKEN:

        print(
            "ERROR: BOT_TOKEN is not set!"
        )

        return

    # --------------------------------------------------------
    # تشغيل Telegram
    # --------------------------------------------------------

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


# ============================================================
# تشغيل البرنامج
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
        )
