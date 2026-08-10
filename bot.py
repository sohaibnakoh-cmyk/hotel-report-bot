import os
import re
import sqlite3
import asyncio
import threading

from io import BytesIO
from datetime import datetime, date, timedelta
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
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

WELCOME_IMAGE = "images.png"

PAGE_WIDTH, PAGE_HEIGHT = A4


# ============================================================
# البحث عن الخط العربي
# ============================================================

def find_arabic_font():

    fonts = [

        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",

        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",

        "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",

        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",

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
            f"Arabic font found: {ARABIC_FONT_PATH}"
        )

    except Exception as e:

        print(
            f"Arabic font error: {e}"
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

        reshaped = arabic_reshaper.reshape(text)

        return get_display(reshaped)

    except Exception:

        return text


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
        "Database initialized successfully."
    )


# ============================================================
# حفظ بيانات النزيل
# ============================================================

def save_guest(guest, update):

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

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

            username

        )
    )

    connection.commit()

    guest_id = cursor.lastrowid

    connection.close()

    return guest_id


# ============================================================
# جلب بيانات يوم
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

        (target_date,)
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# ============================================================
# جلب بيانات شهر
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

        (year_month,)
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# ============================================================
# اسم ملف آمن
# ============================================================

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


# ============================================================
# استخراج قيمة من النص
# ============================================================

def extract_value(text, names):

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
# استخراج بيانات النزيل
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
# تقسيم عدة نزلاء
# ============================================================

def split_guests(text):

    parts = re.split(
        r"\n\s*(?:={3,}|-{3,}|\*{3,})\s*\n",
        text
    )

    return [
        part.strip()
        for part in parts
        if part.strip()
    ]


# ============================================================
# Web Server لـ Render
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-type",
            "text/plain"
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
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Web server running on port {port}"
    )

    server.serve_forever()


# ============================================================
# إنشاء PDF للنزيل
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

    y = PAGE_HEIGHT - 50

    pdf.setFont(
        PDF_FONT,
        18
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(
            "مكتب أمن الفنادق والعقارات"
        )
    )

    y -= 35

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

    y -= 45

    pdf.setFont(
        PDF_FONT,
        11
    )

    for key, value in guest.items():

        if y < 150:

            pdf.showPage()

            pdf.setFont(
                PDF_FONT,
                11
            )

            y = PAGE_HEIGHT - 50

        line = f"{key}: {value}"

        pdf.drawRightString(
            PAGE_WIDTH - 50,
            y,
            arabic_text(line)
        )

        y -= 25

    # الصورة
    if image_data:

        try:

            image_data.seek(0)

            image = ImageReader(
                image_data
            )

            img_width = 250

            img_height = 190

            if y < img_height + 50:

                pdf.showPage()

                y = PAGE_HEIGHT - 50

            pdf.drawImage(
                image,
                50,
                y - img_height,
                width=img_width,
                height=img_height,
                preserveAspectRatio=True,
                anchor="sw"
            )

        except Exception as e:

            print(
                f"Image error: {e}"
            )

    pdf.save()

    buffer.seek(0)

    return buffer


# ============================================================
# PDF التقرير اليومي
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

    def new_page():

        nonlocal y

        pdf.showPage()

        pdf.setFont(
            PDF_FONT,
            11
        )

        y = PAGE_HEIGHT - 50

    # العنوان

    pdf.setFont(
        PDF_FONT,
        19
    )

    pdf.drawRightString(
        PAGE_WIDTH - 50,
        y,
        arabic_text(title)
    )

    y -= 35

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

    # الإحصائيات

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

    # الإجمالي

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

    y -= 35

    # قسم الإحصائيات

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

        y -= 25

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

            y -= 22

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

            y -= 20

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

    # التحليل السردي

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

        f"بلغ إجمالي عدد النزلاء الذين تم تسجيل بياناتهم خلال يوم {target_date} عدد {total} نزيلاً.",

        f"وبحسب التوزيع الجغرافي، جاءت محافظة {top_governorate} في المرتبة الأولى بعدد {gov_count} نزلاء.",

        f"أما على مستوى الفنادق، فقد سجل فندق {top_hotel} العدد الأكبر من النزلاء بواقع {hotel_count} نزلاء.",

        f"وكان سبب الإقامة الأكثر تكراراً هو {top_reason} بعدد {reason_count} نزلاء.",

        "وتوضح البيانات المسجلة خلال اليوم حركة النزلاء وتوزعهم على الفنادق والمحافظات وأسباب الإقامة، مما يساعد على تنظيم ومتابعة المعلومات اليومية لقسم معلومات الفنادق."

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

        telegram_file = await photo.get_file()

        image_buffer = BytesIO()

        await telegram_file.download_to_memory(
            image_buffer
        )

        image_buffer.seek(0)

        return image_buffer

    except Exception as e:

        print(
            f"Photo error: {e}"
        )

        return None


# ============================================================
# قائمة أوامر البوت
# ============================================================

async def set_commands(application):

    commands = [

        BotCommand(
            "start",
            "بدء استخدام البوت"
        ),

        BotCommand(
            "daily",
            "تقرير عمل اليوم"
        ),

        BotCommand(
            "yesterday",
            "تقرير عمل أمس"
        ),

        BotCommand(
            "monthly",
            "التقرير الشهري"
        ),

        BotCommand(
            "help",
            "شرح طريقة استخدام البوت"
        ),

    ]

    await application.bot.set_my_commands(
        commands
    )

    print(
        "Bot commands registered successfully."
    )


# ============================================================
# رسالة الترحيب
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    welcome_text = (

        "السلام عليكم ورحمة الله وبركاته 🌹\n\n"

        "أهلاً وسهلاً ومرحباً بك في\n"

        "🏨 قسم معلومات الفنادق 🏨\n\n"

        "📋 هذا البوت مخصص لتنظيم وتسجيل "
        "بيانات النزلاء وإعداد التقارير.\n\n"

        "يمكنك تحويل رسالة النزيل من المجموعة "
        "إلى البوت مباشرة، سواء كانت الرسالة "
        "نصية أو تحتوي على صورة.\n\n"

        "⚙️ يقوم البوت تلقائياً بـ:\n"

        "✅ استخراج بيانات النزيل\n"
        "✅ حفظ البيانات في قاعدة البيانات\n"
        "✅ إنشاء ملف PDF باسم النزيل\n"
        "✅ إرفاق صورة النزيل داخل التقرير\n"
        "✅ إدخال البيانات ضمن الإحصائيات اليومية\n"
        "✅ إعداد التقارير اليومية والشهرية\n\n"

        "📊 التقارير المتاحة:\n"

        "• /daily — تقرير اليوم\n"
        "• /yesterday — تقرير أمس\n"
        "• /monthly — التقرير الشهري\n"
        "• /help — طريقة الاستخدام\n\n"

        "🔹 لبدء العمل استخدم /start\n\n"

        "مع تمنياتنا لكم بالتوفيق والنجاح 🌷"

    )

    # إرسال الصورة إذا كانت موجودة

    if os.path.exists(WELCOME_IMAGE):

        try:

            with open(
                WELCOME_IMAGE,
                "rb"
            ) as photo:

                await update.message.reply_photo(
                    photo=photo,
                    caption=welcome_text
                )

                return

        except Exception as e:

            print(
                f"Welcome image error: {e}"
            )

    # إذا لم توجد الصورة

    await update.message.reply_text(
        welcome_text
    )


# ============================================================
# المساعدة
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (

        "📋 طريقة استخدام بوت قسم معلومات الفنادق\n\n"

        "1️⃣ قم بتحويل رسالة النزيل من المجموعة إلى البوت.\n\n"

        "2️⃣ يجب أن تحتوي الرسالة قدر الإمكان على:\n\n"

        "• الاسم الثلاثي\n"
        "• اسم الأم\n"
        "• مكان وتاريخ الولادة\n"
        "• السكن الأصلي\n"
        "• المحافظة\n"
        "• اسم الفندق\n"
        "• رقم الجناح\n"
        "• رقم الغرفة\n"
        "• تاريخ النزول\n"
        "• مدة الإقامة\n"
        "• سبب الإقامة\n\n"

        "3️⃣ سيقوم البوت باستخراج البيانات وحفظها.\n\n"

        "4️⃣ سيُرسل ملف PDF باسم النزيل.\n\n"

        "📊 الأوامر:\n\n"

        "/daily\n"
        "إصدار تقرير اليوم.\n\n"

        "/yesterday\n"
        "إصدار تقرير أمس.\n\n"

        "/monthly\n"
        "إصدار التقرير الشهري.\n\n"

        "/start\n"
        "إعادة رسالة الترحيب.\n\n"

        "/help\n"
        "عرض طريقة الاستخدام."

    )

    await update.message.reply_text(
        text
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

    # النص أو وصف الصورة

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
            "تحتوي على بيانات."

        )

        return

    # تقسيم عدة نزلاء

    guests_text = split_guests(
        text
    )

    # الصورة

    image = await get_photo(
        update
    )

    saved_count = 0

    # معالجة كل نزيل

    for guest_text in guests_text:

        guest = parse_guest(
            guest_text
        )

        # حفظ البيانات

        save_guest(
            guest,
            update
        )

        saved_count += 1

        # إنشاء PDF

        pdf_file = create_guest_pdf(
            guest,
            image
        )

        # اسم الملف باسم النزيل

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

                "📋 تم تسجيل النزيل بنجاح ✅\n\n"

                f"👤 الاسم: {guest_name}\n"

                f"🏨 الفندق: "
                f"{guest.get('اسم الفندق', 'غير مذكور')}\n"

                f"🚪 الغرفة: "
                f"{guest.get('رقم الغرفة', 'غير مذكور')}\n"

                f"🛏 الجناح: "
                f"{guest.get('رقم الجناح', 'غير مذكور')}\n"

                f"📅 تاريخ النزول: "
                f"{guest.get('تاريخ النزول', 'غير مذكور')}\n"

                f"⏳ مدة الإقامة: "
                f"{guest.get('مدة الإقامة', 'غير مذكور')}\n"

                f"🎯 سبب الإقامة: "
                f"{guest.get('سبب الإقامة', 'غير مذكور')}\n\n"

                "💾 تم حفظ البيانات في قاعدة البيانات."

            )

        )

        await asyncio.sleep(
            0.5
        )

    # رسالة نهائية

    await message.reply_text(

        f"✅ تم تسجيل {saved_count} نزيل بنجاح.\n\n"

        "📊 تم إدخال البيانات ضمن إحصائيات اليوم.\n\n"

        "للحصول على التقرير اليومي استخدم:\n"

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

    # السرد

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

    # إرسال التقرير النصي

    await update.message.reply_text(
        text
    )

    # PDF

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

            f"📋 لا توجد بيانات مسجلة بتاريخ "
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

    current_month = (
        date.today().strftime("%Y-%m")
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

    # PDF شهري

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
# تشغيل التطبيق
# ============================================================

app = ApplicationBuilder().token(
    TOKEN
).build()


# ============================================================
# إضافة الأوامر
# ============================================================

app.add_handler(
    CommandHandler(
        "start",
        start
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
        "help",
        help_command
    )
)


# ============================================================
# استقبال الرسائل
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

    # تهيئة قاعدة البيانات

    init_database()

    # التحقق من التوكن

    if not TOKEN:

        print(
            "ERROR: BOT_TOKEN is not set!"
        )

        return

    # تشغيل Web Server لـ Render

    threading.Thread(

        target=run_web_server,

        daemon=True

    ).start()

    print(
        "Starting Telegram Bot..."
    )

    # تهيئة التطبيق

    await app.initialize()

    # تسجيل قائمة الأوامر

    await set_commands(
        app
    )

    # تشغيل التطبيق

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
# START PROGRAM
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
