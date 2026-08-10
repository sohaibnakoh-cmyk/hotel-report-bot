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
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import arabic_reshaper
from bidi.algorithm import get_display


# =========================================================
# TOKEN
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")


# =========================================================
# إعدادات الصفحة
# =========================================================

PAGE_WIDTH, PAGE_HEIGHT = A4


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

    for font in fonts:

        if os.path.exists(font):
            return font

    return None


ARABIC_FONT_PATH = find_arabic_font()


# =========================================================
# تسجيل الخط
# =========================================================

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
            "Arabic font registration error:",
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

    if not text:
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
# تنظيف اسم الملف
# =========================================================

def safe_filename(name):

    if not name:

        name = "تقرير_نزيل"

    # إزالة الرموز التي لا يسمح بها اسم الملف
    name = re.sub(
        r'[\\/:*?"<>|]',
        "",
        name
    )

    # إزالة الفراغات الزائدة
    name = re.sub(
        r"\s+",
        "_",
        name.strip()
    )

    if not name:

        name = "تقرير_نزيل"

    return name + ".pdf"


# =========================================================
# استخراج البيانات
# =========================================================

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

    return [

        part.strip()

        for part in parts

        if part.strip()

    ]


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


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "مرحباً بك في بوت تقارير الفنادق 📋\n\n"

        "أرسل رسالة النزيل مباشرة أو قم بعمل "
        "Forward لرسالة من مجموعة.\n\n"

        "يمكن أن تحتوي الرسالة على صورة مع البيانات.\n\n"

        "سيتم إنشاء ملف PDF باسم النزيل تلقائياً.\n\n"

        "الحقول:\n"

        "1- الاسم الثلاثي\n"
        "2- اسم الأم\n"
        "3- مكان وتاريخ الولادة\n"
        "4- السكن الأصلي\n"
        "5- المحافظة\n"
        "6- اسم الفندق\n"
        "7- رقم الجناح\n"
        "8- رقم الغرفة\n"
        "9- تاريخ النزول\n"
        "10- مدة الإقامة\n"
        "11- سبب الإقامة\n\n"

        "لعدة نزلاء استخدم:\n"
        "===================="

    )


# =========================================================
# إنشاء PDF
# =========================================================

def create_pdf(
    guest,
    image_data=None
):

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    y = PAGE_HEIGHT - 50

    # -----------------------------------------------------
    # العنوان
    # -----------------------------------------------------

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

    y -= 40

    # -----------------------------------------------------
    # بيانات النزيل
    # -----------------------------------------------------

    pdf.setFont(
        PDF_FONT,
        11
    )

    for key, value in guest.items():

        # إذا اقتربنا من نهاية الصفحة
        if y < 120:

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

            arabic_text(
                line
            )

        )

        y -= 25

    # -----------------------------------------------------
    # الصورة
    # -----------------------------------------------------

    if image_data:

        try:

            image_data.seek(0)

            image = ImageReader(
                image_data
            )

            img_width = 250
            img_height = 190

            if y < img_height + 60:

                pdf.showPage()

                pdf.setFont(
                    PDF_FONT,
                    11
                )

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
                img_height + 30
            )

        except Exception as e:

            print(
                "Image error:",
                e
            )

    # -----------------------------------------------------
    # نهاية التقرير
    # -----------------------------------------------------

    pdf.setFont(
        PDF_FONT,
        9
    )

    pdf.drawRightString(

        PAGE_WIDTH - 50,

        35,

        arabic_text(
            "تم إنشاء التقرير بواسطة بوت تقارير الفنادق"
        )

    )

    pdf.save()

    buffer.seek(0)

    return buffer


# =========================================================
# تحميل صورة Telegram
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
            "Photo download error:",
            e
        )

        return None


# =========================================================
# معالجة رسالة Forward
# =========================================================

async def process_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.message

    if not message:

        return

    # -----------------------------------------------------
    # النص أو Caption
    # -----------------------------------------------------

    text = (

        message.text

        if message.text

        else

        message.caption

        if message.caption

        else

        ""

    )

    # -----------------------------------------------------
    # إذا لا يوجد نص
    # -----------------------------------------------------

    if not text.strip():

        if message.photo:

            await message.reply_text(

                "🖼️ وصلت الصورة، لكن لا يوجد معها نص بيانات.\n\n"

                "قم بتحويل الرسالة التي تحتوي على بيانات "
                "النزيل والصورة معاً."

            )

        else:

            await message.reply_text(
                "❌ لم أجد بيانات في الرسالة."
            )

        return

    # -----------------------------------------------------
    # تقسيم النزلاء
    # -----------------------------------------------------

    guests = split_guests(
        text
    )

    # -----------------------------------------------------
    # الصورة
    # -----------------------------------------------------

    image = await get_photo(
        update
    )

    # -----------------------------------------------------
    # إذا كان هناك أكثر من نزيل
    # -----------------------------------------------------

    if len(guests) > 1:

        await message.reply_text(

            "📋 تم العثور على "
            f"{len(guests)} نزلاء.\n\n"
            "حالياً سيتم إنشاء ملف مستقل لكل نزيل."

        )

    # -----------------------------------------------------
    # إنشاء ملف لكل نزيل
    # -----------------------------------------------------

    for index, guest_text in enumerate(
        guests
    ):

        guest = parse_guest(
            guest_text
        )

        # إنشاء PDF
        pdf_file = create_pdf(
            guest,
            image
        )

        # اسم النزيل
        guest_name = guest.get(
            "الاسم الثلاثي",
            "تقرير_نزيل"
        )

        filename = safe_filename(
            guest_name
        )

        # -------------------------------------------------
        # إرسال PDF
        # -------------------------------------------------

        await message.reply_document(

            document=pdf_file,

            filename=filename,

            caption=(

                "📋 تقرير نزيل فندق\n\n"

                f"👤 الاسم: {guest_name}\n"

                f"🏨 الفندق: "
                f"{guest.get('اسم الفندق', 'غير مذكور')}\n"

                f"🚪 الغرفة: "
                f"{guest.get('رقم الغرفة', 'غير مذكور')}\n"

                f"🛏 الجناح: "
                f"{guest.get('رقم الجناح', 'غير مذكور')}\n\n"

                "✅ تم إنشاء الملف"

            )

        )

        # منع الإرسال السريع جداً
        await asyncio.sleep(
            0.5
        )


# =========================================================
# CANCEL
# =========================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "تم إلغاء العملية."
    )


# =========================================================
# إنشاء التطبيق
# =========================================================

app = ApplicationBuilder().token(
    TOKEN
).build()


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
        "cancel",
        cancel
    )
)


# =========================================================
# استقبال النص والصور
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

    # تشغيل Render Web Server
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
# START PROGRAM
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
