import os
import re
import asyncio

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحباً بك في بوت تقارير الفنادق 📋\n\n"
        "أرسل بيانات نزيل أو عدة نزلاء دفعة واحدة.\n\n"
        "مثال:\n\n"
        "الاسم الثلاثي: محمد أحمد محمد\n"
        "اسم الأم: فاطمة\n"
        "مكان وتاريخ الولادة: حلب - 1990\n"
        "السكن الأصلي: حلب\n"
        "المحافظة: إدلب\n"
        "اسم الفندق: برج التجارة\n"
        "رقم الغرفة: 46\n"
        "مدة الإقامة: يومين\n"
        "سبب الإقامة: سفر\n\n"
        "يمكنك إرسال عدة تقارير، مع وضع فاصل بين كل تقرير مثل:\n"
        "===================="
    )


def extract_value(text, field):
    patterns = [
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
