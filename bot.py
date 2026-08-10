import os
import sqlite3
import logging
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# الإعدادات
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DB_FILE = "hotel_bot.db"


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# =========================================================
# قاعدة البيانات
# =========================================================

def get_db():

    conn = sqlite3.connect(DB_FILE)

    conn.row_factory = sqlite3.Row

    return conn



def init_db():

    conn = get_db()

    cur = conn.cursor()


    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (

        telegram_id INTEGER PRIMARY KEY,

        username TEXT,

        logged_in INTEGER DEFAULT 0,

        login_time TEXT

    )
    """)


    cur.execute("""
    CREATE TABLE IF NOT EXISTS guests (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        telegram_id INTEGER,

        full_name TEXT,

        mother_name TEXT,

        birth_place_date TEXT,

        original_residence TEXT,

        governorate TEXT,

        hotel_name TEXT,

        suite_number TEXT,

        room_number TEXT,

        stay_duration TEXT,

        stay_reason TEXT,

        created_at TEXT

    )
    """)


    conn.commit()

    conn.close()



def register_user(user_id, username=""):

    conn = get_db()

    conn.execute("""
    INSERT INTO users
    (telegram_id, username)

    VALUES (?,?)

    ON CONFLICT(telegram_id)

    DO UPDATE SET username=excluded.username

    """,
    (
        user_id,
        username
    ))

    conn.commit()

    conn.close()



def set_login(user_id, status):

    conn = get_db()


    conn.execute("""
    UPDATE users

    SET logged_in=?,
    login_time=?

    WHERE telegram_id=?

    """,
    (
        1 if status else 0,

        datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ) if status else None,

        user_id
    ))


    conn.commit()

    conn.close()



def is_logged_in(user_id):

    conn = get_db()


    row = conn.execute(
        """
        SELECT logged_in
        FROM users
        WHERE telegram_id=?
        """,
        (user_id,)
    ).fetchone()


    conn.close()


    return bool(
        row and row["logged_in"] == 1
    )



def save_guest(user_id, data):

    conn = get_db()


    conn.execute("""
    INSERT INTO guests

    (
    telegram_id,
    full_name,
    mother_name,
    birth_place_date,
    original_residence,
    governorate,
    hotel_name,
    suite_number,
    room_number,
    stay_duration,
    stay_reason,
    created_at
    )

    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)

    """,
    (
        user_id,
        data.get("full_name",""),
        data.get("mother_name",""),
        data.get("birth_place_date",""),
        data.get("original_residence",""),
        data.get("governorate",""),
        data.get("hotel_name",""),
        data.get("suite_number",""),
        data.get("room_number",""),
        data.get("stay_duration",""),
        data.get("stay_reason",""),
        datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    ))


    conn.commit()

    conn.close()
    # =========================================================
# لوحات الأزرار
# =========================================================

def start_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔐 تسجيل دخول",
                    callback_data="login"
                )
            ]
        ]
    )



def main_menu_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📝 تسجيل نزيل",
                    callback_data="add_guest"
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 الإحصائيات",
                    callback_data="statistics"
                )
            ],
            [
                InlineKeyboardButton(
                    "📋 آخر السجلات",
                    callback_data="last_records"
                )
            ],
            [
                InlineKeyboardButton(
                    "🚪 تسجيل خروج",
                    callback_data="logout"
                )
            ]
        ]
    )



def cancel_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data="cancel"
                )
            ]
        ]
    )


# =========================================================
# أمر البداية
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user


    register_user(
        user.id,
        user.username or ""
    )


    context.user_data.clear()


    # دخول المدير مباشرة عن طريق ID

    if user.id == ADMIN_ID:

        set_login(
            user.id,
            True
        )


        await update.message.reply_text(
            "👑 أهلاً بك المدير\n\n"
            "تم التعرف على حساب الإدارة تلقائياً.\n"
            "اختر العملية المطلوبة:",
            reply_markup=main_menu_keyboard()
        )

        return



    if is_logged_in(user.id):

        await update.message.reply_text(
            "👋 أهلاً بك مجدداً.\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=main_menu_keyboard()
        )

        return



    await update.message.reply_text(
        "🌹 مرحباً بك في نظام إدارة معلومات الفنادق\n\n"
        "📖 قال الله تعالى:\n"
        "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
        "🔐 للمتابعة يجب تسجيل الدخول أولاً.",
        reply_markup=start_keyboard()
    )



# =========================================================
# زر تسجيل الدخول
# =========================================================

async def login_button(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()


    context.user_data.clear()


    context.user_data["state"] = "login"



    await query.edit_message_text(
        "🔐 تسجيل الدخول\n\n"
        "أرسل رمز الدخول:",
        reply_markup=cancel_keyboard()
    )



# =========================================================
# استقبال الرسائل
# =========================================================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return


    user = update.effective_user

    text = update.message.text.strip()


    state = context.user_data.get("state")



    # تسجيل دخول المستخدمين العاديين

    if state == "login":


        # رمز دخول افتراضي
        # يمكن تغييره لاحقاً

        if text == "123456":


            set_login(
                user.id,
                True
            )


            context.user_data.clear()


            await update.message.reply_text(
                "✅ تم تسجيل الدخول بنجاح\n\n"
                "اختر العملية المطلوبة:",
                reply_markup=main_menu_keyboard()
            )

        else:


            await update.message.reply_text(
                "❌ رمز الدخول غير صحيح"
            )


        return



    # حماية النظام

    if not is_logged_in(user.id) and user.id != ADMIN_ID:


        await update.message.reply_text(
            "🔒 يجب تسجيل الدخول أولاً.\n"
            "اضغط /start"
        )

        return



    # بدء تسجيل نزيل


    if state == "guest_full_name":


        context.user_data["guest"] = {

            "full_name": text

        }


        context.user_data["state"] = "guest_mother"


        await update.message.reply_text(
            "2️⃣ اسم الأم:"
        )

        return



    if state == "guest_mother":


        context.user_data["guest"]["mother_name"] = text

        context.user_data["state"] = "guest_birth"


        await update.message.reply_text(
            "3️⃣ مكان وتاريخ الولادة:"
        )

        return



    if state == "guest_birth":


        context.user_data["guest"]["birth_place_date"] = text

        context.user_data["state"] = "guest_residence"


        await update.message.reply_text(
            "4️⃣ السكن الأصلي:"
        )

        return



    if state == "guest_residence":


        context.user_data["guest"]["original_residence"] = text

        context.user_data["state"] = "guest_governorate"


        await update.message.reply_text(
            "5️⃣ المحافظة:"
        )

        return



    if state == "guest_governorate":


        context.user_data["guest"]["governorate"] = text

        context.user_data["state"] = "guest_hotel"


        await update.message.reply_text(
            "6️⃣ اسم الفندق:"
        )

        return



    if state == "guest_hotel":


        context.user_data["guest"]["hotel_name"] = text

        context.user_data["state"] = "guest_suite"


        await update.message.reply_text(
            "7️⃣ رقم الجناح:"
        )

        return



يتبع الجزء الثالث (إكمال تسجيل النزيل + الأزرار + تشغيل Render).
    if state == "guest_suite":

        context.user_data["guest"]["suite_number"] = text

        context.user_data["state"] = "guest_room"

        await update.message.reply_text(
            "8️⃣ رقم الغرفة:"
        )

        return



    if state == "guest_room":

        context.user_data["guest"]["room_number"] = text

        context.user_data["state"] = "guest_duration"

        await update.message.reply_text(
            "9️⃣ مدة الإقامة:"
        )

        return



    if state == "guest_duration":

        context.user_data["guest"]["stay_duration"] = text

        context.user_data["state"] = "guest_reason"

        await update.message.reply_text(
            "🔟 سبب الإقامة:"
        )

        return



    if state == "guest_reason":

        context.user_data["guest"]["stay_reason"] = text


        save_guest(
            user.id,
            context.user_data["guest"]
        )


        guest = context.user_data["guest"]

        context.user_data.clear()


        await update.message.reply_text(
            "✅ تم تسجيل النزيل بنجاح\n\n"
            f"👤 الاسم: {guest['full_name']}\n"
            f"👩 اسم الأم: {guest['mother_name']}\n"
            f"🏨 الفندق: {guest['hotel_name']}\n"
            f"🚪 الغرفة: {guest['room_number']}\n"
            f"📅 مدة الإقامة: {guest['stay_duration']}\n"
            f"📝 السبب: {guest['stay_reason']}",
            reply_markup=main_menu_keyboard()
        )

        return



# =========================================================
# الأزرار
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()


    user = update.effective_user

    data = query.data



    if data == "login":

        context.user_data.clear()

        context.user_data["state"] = "login"


        await query.edit_message_text(
            "🔐 تسجيل الدخول\n\n"
            "أرسل رمز الدخول:",
            reply_markup=cancel_keyboard()
        )

        return



    if data == "cancel":

        context.user_data.clear()


        await query.edit_message_text(
            "تم إلغاء العملية.",
            reply_markup=start_keyboard()
        )

        return



    if not is_logged_in(user.id) and user.id != ADMIN_ID:

        await query.edit_message_text(
            "🔒 يجب تسجيل الدخول أولاً.",
            reply_markup=start_keyboard()
        )

        return



    if data == "add_guest":

        context.user_data.clear()

        context.user_data["state"] = "guest_full_name"


        await query.edit_message_text(
            "📝 تسجيل نزيل جديد\n\n"
            "1️⃣ الاسم الثلاثي:",
            reply_markup=cancel_keyboard()
        )

        return



    if data == "statistics":

        conn = get_db()


        total = conn.execute(
            "SELECT COUNT(*) FROM guests"
        ).fetchone()[0]


        conn.close()


        await query.edit_message_text(
            f"📊 الإحصائيات\n\n"
            f"👤 عدد النزلاء: {total}",
            reply_markup=main_menu_keyboard()
        )

        return



    if data == "last_records":

        conn = get_db()


        rows = conn.execute(
            """
            SELECT full_name,hotel_name,room_number
            FROM guests
            ORDER BY id DESC
            LIMIT 10
            """
        ).fetchall()


        conn.close()


        if not rows:

            text = "لا توجد سجلات."

        else:

            text = "📋 آخر السجلات:\n\n"

            for r in rows:

                text += (
                    f"👤 {r['full_name']}\n"
                    f"🏨 {r['hotel_name']}\n"
                    f"🚪 غرفة {r['room_number']}\n\n"
                )


        await query.edit_message_text(
            text,
            reply_markup=main_menu_keyboard()
        )

        return



    if data == "logout":

        set_login(
            user.id,
            False
        )


        context.user_data.clear()


        await query.edit_message_text(
            "🚪 تم تسجيل الخروج.",
            reply_markup=start_keyboard()
        )

        return



# =========================================================
# معالجة الأخطاء
# =========================================================

async def error_handler(update, context):

    logger.error(
        "Error:",
        exc_info=context.error
    )



# =========================================================
# التشغيل
# =========================================================

def main():

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN غير موجود"
        )

        return



    init_db()



    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )


    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )


    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        )
    )


    app.add_error_handler(
        error_handler
    )


    print("==============================")
    print("✅ البوت يعمل")
    print("✅ المدير يدخل عن طريق ADMIN_ID")
    print("==============================")


    app.run_polling(
        drop_pending_updates=True
    )



if __name__ == "__main__":

    main()
