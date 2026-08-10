import os
import re
import asyncio
from io import BytesIO

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


TOKEN = os.getenv("BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحباً بك في بوت تقارير الفنادق 📋\n\n"
        "أرسل بيانات نزيل واحد أو عدة نزلاء دفعة واحدة.\n\n"
        "بعد الإرسال سيقوم البوت بـ:\n"
        "✅ استخراج البيانات تلقائياً\n"
        "✅ ترتيب بيانات النزلاء\n"
        "✅ إنشاء ملف PDF\n"
        "✅ إرسال ملف PDF إليك\n\n"
        "ضع بين كل نزيل وآخر:\n"
        "===================="
    )


def extract_value(text, field):
    """
    استخراج قيمة الحقل من النص.
    يدعم النقطتين : والشرطة -
    """

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


def clean_text(text):
    """
    حذف الترقيم الموجود في بداية السطور.
    مثال:
    1- الاسم الثلاثي
    2. اسم الأم
    """

    return re.sub(
        r"^\s*\d+\s*[-.)]\s*",
        "",
        text,
        flags=re.MULTILINE
    )


def parse_guest(text):
    """
    استخراج بيانات نزيل واحد.
    """

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
            "مكان وتاريخ الولادة"
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


def split_guests(text):
    """
    تقسيم الرسالة إلى عدة نزلاء.
    يجب وضع === بين كل تقرير وآخر.
    """

    guests = re.split(
        r"\n\s*(?:={3,}|-{3,}|\*{3,})\s*\n",
        text
    )

    return [
        guest.strip()
        for guest in guests
        if guest.strip()
    ]


def create_pdf(guests_data):
    """
    إنشاء ملف PDF يحتوي على بيانات جميع النزلاء.
    """

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    width, height = A4

    # اسم الخط
    font_name = "Helvetica"

    y = height - 50

    # عنوان التقرير
    pdf.setFont(
        font_name,
        16
    )

    pdf.drawCentredString(
        width / 2,
        y,
        "Hotel Guests Report"
    )

    y -= 40

    # عدد النزلاء
    pdf.setFont(
        font_name,
        12
    )

    pdf.drawString(
        50,
        y,
        f"Number of guests: {len(guests_data)}"
    )

    y -= 30

    # بيانات كل نزيل
    for number, guest in enumerate(
        guests_data,
        start=1
    ):

        # إنشاء صفحة جديدة إذا امتلأت الصفحة
        if y < 150:

            pdf.showPage()

            y = height - 50

        pdf.setFont(
            font_name,
            14
        )

        pdf.drawString(
            50,
            y,
            f"Guest No. {number}"
        )

        y -= 25

        pdf.setFont(
            font_name,
            10
        )

        for key, value in guest.items():

            # إنشاء صفحة جديدة عند الحاجة
            if y < 80:

                pdf.showPage()

                y = height - 50

            line = f"{key}: {value}"

            # التقرير النصي داخل PDF
            pdf.drawString(
                50,
                y,
                line[:100]
            )

            y -= 18

        y -= 15

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


async def receive_data(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text

    # تقسيم النزلاء
    guests_text = split_guests(text)

    if not guests_text:

        await update.message.reply_text(
            "❌ لم يتم العثور على بيانات."
        )

        return

    # استخراج البيانات
    guests_data = []

    for guest_text in guests_text:

        guest_data = parse_guest(
            guest_text
        )

        guests_data.append(
            guest_data
        )

    # إرسال تأكيد
    await update.message.reply_text(
        f"⏳ جاري معالجة بيانات "
        f"{len(guests_data)} نزيل..."
    )

    # إنشاء PDF
    pdf_file = create_pdf(
        guests_data
    )

    # إرسال الملف
    await update.message.reply_document(
        document=pdf_file,
        filename="hotel_guests_report.pdf",
        caption=(
            f"📋 تقرير النزلاء\n\n"
            f"عدد النزلاء: "
            f"{len(guests_data)}\n\n"
            f"تم إنشاء التقرير بنجاح ✅"
        )
    )


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "تم إلغاء العملية."
    )


app = ApplicationBuilder().token(
    TOKEN
).build()


# أمر البدء
app.add_handler(
    CommandHandler(
        "start",
        start
    )
)


# أمر الإلغاء
app.add_handler(
    CommandHandler(
        "cancel",
        cancel
    )
)


# استقبال البيانات
app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        receive_data
    )
)


async def main():

    await app.initialize()

    await app.start()

    await app.updater.start_polling()

    try:

        await asyncio.Event().wait()

    finally:

        await app.updater.stop()

        await app.stop()

        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
def extract_value(text, field):
    """
    استخراج قيمة الحقل من النص.
    يدعم النقطتين : والشرطة -
    """

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


def clean_text(text):
    """
    حذف الترقيم الموجود في بداية السطور.
    مثال:
    1- الاسم الثلاثي
    2. اسم الأم
    """

    return re.sub(
        r"^\s*\d+\s*[-.)]\s*",
        "",
        text,
        flags=re.MULTILINE
    )


def parse_guest(text):
    """
    استخراج بيانات نزيل واحد.
    """

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
            "مكان وتاريخ الولادة"
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


def split_guests(text):
    """
    تقسيم الرسالة إلى عدة نزلاء.
    يجب وضع === بين كل تقرير وآخر.
    """

    guests = re.split(
        r"\n\s*(?:={3,}|-{3,}|\*{3,})\s*\n",
        text
    )

    return [
        guest.strip()
        for guest in guests
        if guest.strip()
    ]


def create_pdf(guests_data):
    """
    إنشاء ملف PDF يحتوي على بيانات جميع النزلاء.
    """

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    width, height = A4

    # اسم الخط
    font_name = "Helvetica"

    y = height - 50

    # عنوان التقرير
    pdf.setFont(
        font_name,
        16
    )

    pdf.drawCentredString(
        width / 2,
        y,
        "Hotel Guests Report"
    )

    y -= 40

    # عدد النزلاء
    pdf.setFont(
        font_name,
        12
    )

    pdf.drawString(
        50,
        y,
        f"Number of guests: {len(guests_data)}"
    )

    y -= 30

    # بيانات كل نزيل
    for number, guest in enumerate(
        guests_data,
        start=1
    ):

        # إنشاء صفحة جديدة إذا امتلأت الصفحة
        if y < 150:

            pdf.showPage()

            y = height - 50

        pdf.setFont(
            font_name,
            14
        )

        pdf.drawString(
            50,
            y,
            f"Guest No. {number}"
        )

        y -= 25

        pdf.setFont(
            font_name,
            10
        )

        for key, value in guest.items():

            # إنشاء صفحة جديدة عند الحاجة
            if y < 80:

                pdf.showPage()

                y = height - 50

            line = f"{key}: {value}"

            # التقرير النصي داخل PDF
            pdf.drawString(
                50,
                y,
                line[:100]
            )

            y -= 18

        y -= 15

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


async def receive_data(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text

    # تقسيم النزلاء
    guests_text = split_guests(text)

    if not guests_text:

        await update.message.reply_text(
            "❌ لم يتم العثور على بيانات."
        )

        return

    # استخراج البيانات
    guests_data = []

    for guest_text in guests_text:

        guest_data = parse_guest(
            guest_text
        )

        guests_data.append(
            guest_data
        )

    # إرسال تأكيد
    await update.message.reply_text(
        f"⏳ جاري معالجة بيانات "
        f"{len(guests_data)} نزيل..."
    )

    # إنشاء PDF
    pdf_file = create_pdf(
        guests_data
    )

    # إرسال الملف
    await update.message.reply_document(
        document=pdf_file,
        filename="hotel_guests_report.pdf",
        caption=(
            f"📋 تقرير النزلاء\n\n"
            f"عدد النزلاء: "
            f"{len(guests_data)}\n\n"
            f"تم إنشاء التقرير بنجاح ✅"
        )
    )


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "تم إلغاء العملية."
    )


app = ApplicationBuilder().token(
    TOKEN
).build()


# أمر البدء
app.add_handler(
    CommandHandler(
        "start",
        start
    )
)


# أمر الإلغاء
app.add_handler(
    CommandHandler(
        "cancel",
        cancel
    )
)


# استقبال البيانات
app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        receive_data
    )
)


async def main():

    await app.initialize()

    await app.start()

    await app.updater.start_polling()

    try:

        await asyncio.Event().wait()

    finally:

        await app.updater.stop()

        await app.stop()

        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())    patterns = [
        rf"{field}\s*[:：]\s*(.+)",
        rf"{field}\s*[-–]\s*(.+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()

    return "غير مذكور"


def clean_text(text):
    # حذف الترقيم الموجود قبل اسم الحقل مثل:
    # 1- الاسم الثلاثي:
    text = re.sub(r"^\s*\d+\s*[-.)]\s*", "", text, flags=re.MULTILINE)
    return text


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
            "مكان الولادة"
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
        "رقم الغرفة": [
            "رقم الغرفة",
            "الغرفة"
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
            value = extract_value(text, field_name)

            if value != "غير مذكور":
                break

        data[key] = value

    return data


def split_guests(text):
    # الفاصل الأساسي بين النزلاء
    guests = re.split(
        r"\n\s*(?:={3,}|-{3,}|\*{3,})\s*\n",
        text
    )

    guests = [guest.strip() for guest in guests if guest.strip()]

    return guests


async def receive_data(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text

    guests = split_guests(text)

    reports = []

    for number, guest_text in enumerate(guests, start=1):
        data = parse_guest(guest_text)

        report = f"📋 النزيل رقم {number}\n\n"

        for key, value in data.items():
            report += f"{key}: {value}\n"

        reports.append(report)

    final_report = "\n\n====================\n\n".join(reports)

    # إذا كان التقرير طويلاً جداً يتم تقسيمه
    if len(final_report) > 4000:
        for i in range(0, len(final_report), 4000):
            await update.message.reply_text(
                final_report[i:i + 4000]
            )
    else:
        await update.message.reply_text(final_report)

    await update.message.reply_text(
        f"✅ تم استخراج وتسجيل بيانات {len(guests)} نزيل بنجاح."
    )


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text("تم إلغاء العملية.")


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(
    CommandHandler("start", start)
)

app.add_handler(
    CommandHandler("cancel", cancel)
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        receive_data
    )
)


async def main():
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["الاسم الثلاثي"] = update.message.text
    await update.message.reply_text("أدخل اسم الأم:")
    return MOTHER


async def get_mother(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["اسم الأم"] = update.message.text
    await update.message.reply_text("أدخل مكان وتاريخ الولادة:")
    return BIRTH


async def get_birth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["مكان وتاريخ الولادة"] = update.message.text
    await update.message.reply_text("أدخل السكن الأصلي:")
    return HOME


async def get_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["السكن الأصلي"] = update.message.text
    await update.message.reply_text("أدخل المحافظة:")
    return GOVERNORATE


async def get_governorate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["المحافظة"] = update.message.text
    await update.message.reply_text("أدخل اسم الفندق:")
    return HOTEL


async def get_hotel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["اسم الفندق"] = update.message.text
    await update.message.reply_text("أدخل رقم الغرفة:")
    return ROOM


async def get_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["رقم الغرفة"] = update.message.text
    await update.message.reply_text("أدخل مدة الإقامة:")
    return DURATION


async def get_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["مدة الإقامة"] = update.message.text
    await update.message.reply_text("أدخل سبب الإقامة:")
    return REASON


async def get_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["سبب الإقامة"] = update.message.text

    report = "📋 تقرير نزيل فندق\n\n"

    for key, value in context.user_data.items():
        report += f"{key}: {value}\n"

    await update.message.reply_text(
        report + "\n\nتم تسجيل البيانات بنجاح ✅"
    )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END


app = ApplicationBuilder().token(TOKEN).build()

conversation = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        MOTHER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_mother)],
        BIRTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_birth)],
        HOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_home)],
        GOVERNORATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_governorate)
        ],
        HOTEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_hotel)],
        ROOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_room)],
        DURATION: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_duration)
        ],
        REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_reason)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

app.add_handler(conversation)

import asyncio

async def main():
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
