import os
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")

(
    NAME,
    MOTHER,
    BIRTH,
    HOME,
    GOVERNORATE,
    HOTEL,
    ROOM,
    DURATION,
    REASON,
) = range(9)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "مرحباً بك في بوت تقارير الفنادق 📋\n\n"
        "سنقوم بإدخال بيانات النزيل خطوة بخطوة.\n\n"
        "أدخل الاسم الثلاثي:"
    )
    return NAME


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
