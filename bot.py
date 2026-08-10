import os
import re
import asyncio
import threading
from io import BytesIO
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
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

import arabic_reshaper
from bidi.algorithm import get_display


# =========================================================
# إعدادات
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

PAGE_WIDTH, PAGE_HEIGHT = A4


# =========================================================
# البحث عن خط عربي في Render
# =========================================================

def find_arabic_font():

    possible_fonts = [

        # DejaVu
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",

        # Noto
        "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",

        # Liberation
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",

    ]

    for font_path in possible_fonts:

        if os.path.exists(font_path):
            return font_path

    return None


ARABIC_FONT = find_arabic_font()


# =========================================================
# تحويل النص العربي إلى شكل مناسب لـ PDF
# =========================================================

def arabic_text(text):

    if not text:
        return ""

    try:

        reshaped = arabic_reshaper.reshape(
            str(text)
        )

        return get_display(
            reshaped
        )

    except Exception:

        return str(text)


# =========================================================
# خادم Render
# =========================================================

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

    def log_message(self, format, *args):
        pass


def run_web_server():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Render web server running on port {port}"
    )

    server.serve_forever()


# =========================================================
# تسجيل الخط
# =========================================================

def setup_font(pdf):

    if ARABIC_FONT:

        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        try:

            pdfmetrics.registerFont(
                TTFont(
                    "ArabicFont",
                    ARABIC_FONT
                )
            )

            return "ArabicFont"

        except Exception as e:

            print(
                "Font error:",
                e
            )

    return "Helvetica"


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "مرحباً بك في بوت تقارير الفنادق 📋\n\n"

        "يمكنك إرسال:\n"
        "• رسالة نصية\n"
        "• رسالة محولة من مجموعة\n"
        "• صورة مع نص\n"
        "• عدة نزلاء في رسالة واحدة\n\n"

        "سيقوم البوت باستخراج البيانات وإنشاء PDF تلقائياً.\n\n"

        "الحقول المطلوبة:\n"
        "الاسم الثلاثي\n"
        "اسم الأم\n"
        "مكان وتاريخ الولادة\n"
        "السكن الأصلي\n"
        "المحافظة\n"
        "اسم الفندق\n"
        "رقم الجناح\n"
        "رقم الغرفة\n"
        "تاريخ النزول\n"
        "مدة الإقامة\n"
        "سبب الإقامة\n\n"

        "يمكن وضع هذا الفاصل بين النزلاء:\n"
        "===================="

    )


# =========================================================
# استخراج قيمة حقل
# =========================================================

def extract_value(
    text,
    field
):

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

            return match.group(1).strip()

    return "غير مذكور"


# =========================================================
# تنظيف النص
# =========================================================

def clean_text(text):

    return re.sub(

        r"^\s*\d+\s*[-.)]\s*",

        "",

        text,

        flags=re.MULTILINE

    )


# =========================================================
# استخراج بيانات النزيل
# =========================================================

def parse_guest(text):

    text = clean_text(text)

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

    data = {}

    for key, names in fields.items():

        value = "غير مذكور"

        for name in names:

            value = extract_value(
                text,
                name
            )

            if value != "غير مذكور":
                break

        data[key] = value

    return data


# =========================================================
# تقسيم عدة نزلاء
# =========================================================

def split_guests(text):

    guests = re.split(

        r"\n\s*(?:={3,}|-{3,}|\*{3,})\s*\n",

        text

    )

    return [

        guest.strip()

        for guest in guests

        if guest.strip()

    ]


# =========================================================
# كتابة النص العربي داخل PDF
# =========================================================

def draw_rtl_text(
    pdf,
    text,
    x,
    y,
    font_name,
    font_size=11
):

    pdf.setFont(
        font_name,
        font_size
    )

    text = arabic_text(text)

    pdf.drawRightString(
        x,
        y,
        text
    )


# =========================================================
# إنشاء PDF
# =========================================================

def create_pdf(
    guests_data,
    images=None
):

    if images is None:
        images = []

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    font_name = setup_font(pdf)

    # -----------------------------------------------------
    # العنوان
    # -----------------------------------------------------

    y = PAGE_HEIGHT - 50

    draw_rtl_text(
        pdf,
        "مكتب أمن الفنادق والعقارات",
        PAGE_WIDTH - 50,
        y,
        font_name,
        18
    )

    y -= 30

    draw_rtl_text(
        pdf,
        "تقرير نزلاء الفنادق",
        PAGE_WIDTH - 50,
        y,
        font_name,
        15
    )

    y -= 35

    draw_rtl_text(
        pdf,
        f"عدد النزلاء: {len(guests_data)}",
        PAGE_WIDTH - 50,
        y,
        font_name,
        11
    )

    y -= 35

    # -----------------------------------------------------
    # بيانات النزلاء
    # -----------------------------------------------------

    for number, guest in enumerate(
        guests_data,
        start=1
    ):

        if y < 180:

            pdf.showPage()

            font_name = setup_font(pdf)

            y = PAGE_HEIGHT - 50

        # عنوان النزيل

        draw_rtl_text(
            pdf,
            f"النزيل رقم {number}",
            PAGE_WIDTH - 50,
            y,
            font_name,
            14
        )

        y -= 28

        # البيانات

        for key, value in guest.items():

            if y < 80:

                pdf.showPage()

                font_name = setup_font(pdf)

                y = PAGE_HEIGHT - 50

            line = f"{key}: {value}"

            draw_rtl_text(
                pdf,
                line,
                PAGE_WIDTH - 50,
                y,
                font_name,
                10
            )

            y -= 20

        # -------------------------------------------------
        # صورة النزيل
        # -------------------------------------------------

        if number <= len(images):

            image_data = images[number - 1]

            if image_data:

                try:

                    image_data.seek(0)

                    image = ImageReader(
                        image_data
                    )

                    img_width = 220
                    img_height = 165

                    if y < img_height + 50:

                        pdf.showPage()

                        font_name = setup_font(pdf)

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

                    y -= (
                        img_height + 25
                    )

                except Exception as e:

                    print(
                        "Image error:",
                        e
                    )

        # خط فاصل

        if y > 50:

            pdf.line(
                50,
                y,
                PAGE_WIDTH - 50,
                y
            )

            y -= 25

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# تحميل صورة من Telegram
# =========================================================

async def download_photo(
    update
):

    if not update.message:

        return None

    if not update.message.photo:

        return None

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
# معالجة الرسائل
# =========================================================

async def process_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:

        return

    message = update.message

    # -----------------------------------------------------
    # الحصول على النص
    # -----------------------------------------------------

    text = (
        message.text
        or
        message.caption
        or
        ""
    )

    # -----------------------------------------------------
    # إذا كانت الرسالة صورة بدون نص
    # -----------------------------------------------------

    if not text.strip():

        if message.photo:

            await message.reply_text(

                "🖼️ تم استلام الصورة.\n\n"

                "لكن لم أجد بيانات نصية معها.\n\n"

                "أرسل الصورة مع بيانات النزيل في Caption "
                "أو أرسل البيانات كنص."

            )

        else:

            await message.reply_text(
                "❌ لم أجد بيانات نصية."
            )

        return

    # -----------------------------------------------------
    # تقسيم النزلاء
    # -----------------------------------------------------

    guests_text = split_guests(text)

    if not guests_text:

        await message.reply_text(
            "❌ لم أتمكن من استخراج بيانات النزيل."
        )

        return

    # -----------------------------------------------------
    # استخراج البيانات
    # -----------------------------------------------------

    guests_data = []

    for guest_text in guests_text:

        guests_data.append(
            parse_guest(
                guest_text
            )
        )

    # -----------------------------------------------------
    # تحميل الصورة
    # -----------------------------------------------------

    images = []

    if message.photo:

        image = await download_photo(
            update
        )

        if image:

            # إذا كان هناك نزيل واحد
            # نربط الصورة به

            if len(guests_data) == 1:

                images.append(
                    image
                )

    # -----------------------------------------------------
    # إشعار المستخدم
    # -----------------------------------------------------

    await message.reply_text(

        f"⏳ جاري إنشاء التقرير...\n\n"
        f"عدد النزلاء: {len(guests_data)}"

    )

    # -----------------------------------------------------
    # إنشاء PDF
    # -----------------------------------------------------

    pdf_file = create_pdf(
        guests_data,
        images
    )

    # -----------------------------------------------------
    # اسم الملف
    # -----------------------------------------------------

    filename = (
        "hotel_guests_report.pdf"
    )

    # -----------------------------------------------------
    # إرسال PDF
    # -----------------------------------------------------

    await message.reply_document(

        document=pdf_file,

        filename=filename,

        caption=(

            "📋 تقرير نزلاء الفنادق\n\n"

            f"👤 عدد النزلاء: "
            f"{len(guests_data)}\n"

            f"🖼️ الصور: "
            f"{'نعم' if images else 'لا'}\n\n"

            "✅ تم إنشاء ملف PDF"

        )

    )


# =========================================================
# إلغاء
# =========================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "تم إلغاء العملية."
    )


# =========================================================
# إنشاء البوت
# =========================================================

app = ApplicationBuilder().token(
    TOKEN
).build()


app.add_handler(
    CommandHandler(
        "start",
        start
    )
)


app.add_handler(
    CommandHandler(
        "cancel",
        cancel
    )
)


# الرسائل النصية والصور
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
# تشغيل البوت
# =========================================================

async def main():

    # تشغيل منفذ Render
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

    if not ARABIC_FONT:

        print(
            "WARNING: Arabic font was not found."
        )

    else:

        print(
            f"Arabic font found: {ARABIC_FONT}"
        )

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
# البداية
# =========================================================

if __name__ == "__main__":

    asyncio.run(main())
