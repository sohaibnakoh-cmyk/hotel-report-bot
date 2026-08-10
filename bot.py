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


TOKEN = os.getenv("BOT_TOKEN")


# ==========================================
# خادم Render
# ==========================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(
            b"Hotel Report Bot is running"
        )

    def log_message(self, format, *args):
        pass


def run_web_server():

    port = int(
        os.environ.get("PORT", 10000)
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"Web server running on port {port}")

    server.serve_forever()


# ==========================================
# START
# ==========================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "مرحباً بك في بوت تقارير الفنادق 📋\n\n"
        "أرسل بيانات نزيل واحد أو عدة نزلاء دفعة واحدة.\n\n"
        "يجب وضع هذا الفاصل بين كل نزيل وآخر:\n\n"
        "====================\n\n"
        "مثال:\n\n"
        "الاسم الثلاثي: محمد مصطفى توكاك\n"
        "اسم الأم: حورية\n"
        "مكان وتاريخ الولادة: تركيا - 1978\n"
        "السكن الأصلي: تركيا\n"
        "المحافظة: إدلب\n"
        "اسم الفندق: برج التجارة\n"
        "رقم الجناح: 5\n"
        "رقم الغرفة: 46\n"
        "تاريخ النزول: 2026/08/10\n"
        "مدة الإقامة: يوم\n"
        "سبب الإقامة: سفر"
    )


# ==========================================
# استخراج قيمة الحقل
# ==========================================

def extract_value(text, field):

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


# ==========================================
# تنظيف النص
# ==========================================

def clean_text(text):

    return re.sub(
        r"^\s*\d+\s*[-.)]\s*",
        "",
        text,
        flags=re.MULTILINE
    )


# ==========================================
# استخراج بيانات نزيل
# ==========================================

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

    for key, possible_names in fields.items():

        value = "غير مذكور"

        for field_name in possible_names:

            value = extract_value(
                text,
                field_name
            )

            if value != "غير مذكور":
                break

        data[key] = value

    return data


# ==========================================
# تقسيم عدة نزلاء
# ==========================================

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


# ==========================================
# إنشاء PDF
# ==========================================

def create_pdf(guests_data):

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    width, height = A4

    y = height - 50

    pdf.setFont(
        "Helvetica-Bold",
        16
    )

    pdf.drawCentredString(
        width / 2,
        y,
        "HOTEL GUESTS REPORT"
    )

    y -= 40

    pdf.setFont(
        "Helvetica",
        12
    )

    pdf.drawString(
        50,
        y,
        f"Total Guests: {len(guests_data)}"
    )

    y -= 35

    english_names = {

        "الاسم الثلاثي": "Full Name",
        "اسم الأم": "Mother Name",
        "مكان وتاريخ الولادة": "Place and Date of Birth",
        "السكن الأصلي": "Original Residence",
        "المحافظة": "Governorate",
        "اسم الفندق": "Hotel Name",
        "رقم الجناح": "Suite Number",
        "رقم الغرفة": "Room Number",
        "تاريخ النزول": "Check-in Date",
        "مدة الإقامة": "Duration of Stay",
        "سبب الإقامة": "Reason for Stay",

    }

    for number, guest in enumerate(
        guests_data,
        start=1
    ):

        if y < 150:

            pdf.showPage()

            y = height - 50

        pdf.setFont(
            "Helvetica-Bold",
            14
        )

        pdf.drawString(
            50,
            y,
            f"Guest No. {number}"
        )

        y -= 25

        pdf.setFont(
            "Helvetica",
            10
        )

        for key, value in guest.items():

            if y < 80:

                pdf.showPage()

                y = height - 50

            field_name = english_names.get(
                key,
                key
            )

            line = f"{field_name}: {value}"

            if len(line) > 100:
                line = line[:100]

            pdf.drawString(
                50,
                y,
                line
            )

            y -= 18

        y -= 5

        pdf.line(
            50,
            y,
            width - 50,
            y
        )

        y -= 25

    pdf.save()

    buffer.seek(0)

    return buffer


# ==========================================
# استقبال البيانات
# ==========================================

async def receive_data(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text

    guests_text = split_guests(text)

    if not guests_text:

        await update.message.reply_text(
            "❌ لم يتم العثور على بيانات."
        )

        return

    guests_data = []

    for guest_text in guests_text:

        guest_data = parse_guest(
            guest_text
        )

        guests_data.append(
            guest_data
        )

    await update.message.reply_text(
        f"⏳ جاري معالجة بيانات "
        f"{len(guests_data)} نزيل..."
    )

    pdf_file = create_pdf(
        guests_data
    )

    await update.message.reply_document(
        document=pdf_file,
        filename="hotel_guests_report.pdf",
        caption=(
            "📋 تقرير نزلاء الفنادق\n\n"
            f"عدد النزلاء: {len(guests_data)}\n\n"
            "تم إنشاء ملف PDF بنجاح ✅"
        )
    )


# ==========================================
# CANCEL
# ==========================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "تم إلغاء العملية."
    )


# ==========================================
# تشغيل البوت
# ==========================================

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


app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        receive_data
    )
)


# ==========================================
# Main
# ==========================================

async def main():

    # تشغيل منفذ Render
    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    print("Starting Telegram Bot...")

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


if __name__ == "__main__":

    asyncio.run(main())
