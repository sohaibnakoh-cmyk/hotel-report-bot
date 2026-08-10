import os
import sqlite3
import logging
import hashlib
import secrets
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "ضع_توكن_البوت_هنا")

# حسابات الدخول
# غيّر اسم المستخدم وكلمة المرور كما تريد
LOGIN_USERNAME = os.getenv("LOGIN_USERNAME", "admin")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "123456")

DB_FILE = "hotel_bot.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
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


def register_user(telegram_id, username=""):
    conn = get_db()
    conn.execute("""
        INSERT INTO users (telegram_id, username, logged_in, login_time)
        VALUES (?, ?, 0, NULL)
        ON CONFLICT(telegram_id)
        DO UPDATE SET username=excluded.username
    """, (telegram_id, username))
    conn.commit()
    conn.close()


def set_login(telegram_id, status):
    conn = get_db()

    if status:
        conn.execute("""
            UPDATE users
            SET logged_in=1, login_time=?
            WHERE telegram_id=?
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            telegram_id
        ))
    else:
        conn.execute("""
            UPDATE users
            SET logged_in=0
            WHERE telegram_id=?
        """, (telegram_id,))

    conn.commit()
    conn.close()


def is_logged_in(telegram_id):
    conn = get_db()

    row = conn.execute("""
        SELECT logged_in
        FROM users
        WHERE telegram_id=?
    """, (telegram_id,)).fetchone()

    conn.close()

    return bool(row and row["logged_in"] == 1)


def save_guest(telegram_id, data):
    conn = get_db()

    conn.execute("""
        INSERT INTO guests (
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        telegram_id,
        data.get("full_name", ""),
        data.get("mother_name", ""),
        data.get("birth_place_date", ""),
        data.get("original_residence", ""),
        data.get("governorate", ""),
        data.get("hotel_name", ""),
        data.get("suite_number", ""),
        data.get("room_number", ""),
        data.get("stay_duration", ""),
        data.get("stay_reason", ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))

    conn.commit()
    conn.close()


# =========================================================
# لوحات الأزرار
# =========================================================

def start_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔐 تسجيل دخول",
                callback_data="login"
            )
        ]
    ])


def main_menu_keyboard():
    return InlineKeyboardMarkup([
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
    ])


def cancel_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="cancel"
            )
        ]
    ])


# =========================================================
# /start
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    register_user(
        user.id,
        user.username or ""
    )

    # تنظيف أي حالة سابقة
    context.user_data.clear()

    # إذا كان مسجلاً مسبقاً
    if is_logged_in(user.id):
        await update.message.reply_text(
            "👋 أهلاً بك مجدداً.\n\n"
            "تم تسجيل دخولك مسبقاً.\n"
            "اختر العملية المطلوبة من القائمة:",
            reply_markup=main_menu_keyboard()
        )
        return

    welcome_text = (
        "🌹 *مرحباً بك في نظام إدارة معلومات الفنادق*\n\n"
        "نسأل الله أن يوفقنا وإياكم لما فيه الخير.\n\n"
        "📖 قال الله تعالى:\n"
        "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
        "🔐 للمتابعة يجب تسجيل الدخول أولاً."
    )

    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=start_keyboard()
    )


# =========================================================
# زر تسجيل الدخول
# =========================================================

async def login_button(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    context.user_data.clear()
    context.user_data["state"] = "login_username"

    await query.edit_message_text(
        "🔐 *تسجيل الدخول*\n\n"
        "أرسل اسم المستخدم:",
        parse_mode="Markdown",
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

    # -----------------------------------------------------
    # اسم المستخدم
    # -----------------------------------------------------

    if state == "login_username":

        context.user_data["login_username"] = text
        context.user_data["state"] = "login_password"

        await update.message.reply_text(
            "🔑 أرسل كلمة المرور:",
            reply_markup=cancel_keyboard()
        )

        return

    # -----------------------------------------------------
    # كلمة المرور
    # -----------------------------------------------------

    if state == "login_password":

        username = context.user_data.get("login_username", "")
        password = text

        if (
            username == LOGIN_USERNAME
            and password == LOGIN_PASSWORD
        ):

            set_login(user.id, True)

            context.user_data.clear()

            await update.message.reply_text(
                "✅ *تم تسجيل الدخول بنجاح*\n\n"
                "مرحباً بك في لوحة التحكم.\n"
                "اختر العملية المطلوبة:",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )

        else:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"
                "اضغط /start للمحاولة مرة أخرى."
            )

        return

    # -----------------------------------------------------
    # التأكد من تسجيل الدخول
    # -----------------------------------------------------

    if not is_logged_in(user.id):

        await update.message.reply_text(
            "🔒 يجب تسجيل الدخول أولاً.\n\n"
            "اضغط /start."
        )

        return

    # -----------------------------------------------------
    # إضافة نزيل
    # -----------------------------------------------------

    if state == "guest_full_name":

        context.user_data["guest"] = {
            "full_name": text
        }

        context.user_data["state"] = "guest_mother_name"

        await update.message.reply_text(
            "2️⃣ اسم الأم:"
        )

        return

    if state == "guest_mother_name":

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
            "7️⃣ رقم الجناح:\n"
            "إذا لم يوجد اكتب: ×"
        )

        return

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

        guest = context.user_data["guest"]

        save_guest(
            user.id,
            guest
        )

        context.user_data.clear()

        summary = (
            "✅ *تم تسجيل بيانات النزيل بنجاح*\n\n"
            f"1️⃣ الاسم: {guest['full_name']}\n"
            f"2️⃣ اسم الأم: {guest['mother_name']}\n"
            f"3️⃣ مكان وتاريخ الولادة: {guest['birth_place_date']}\n"
            f"4️⃣ السكن الأصلي: {guest['original_residence']}\n"
            f"5️⃣ المحافظة: {guest['governorate']}\n"
            f"6️⃣ الفندق: {guest['hotel_name']}\n"
            f"7️⃣ الجناح: {guest['suite_number']}\n"
            f"8️⃣ الغرفة: {guest['room_number']}\n"
            f"9️⃣ مدة الإقامة: {guest['stay_duration']}\n"
            f"🔟 سبب الإقامة: {guest['stay_reason']}\n\n"
            "📌 تم حفظ البيانات في قاعدة البيانات."
        )

        await update.message.reply_text(
            summary,
            parse_mode="Markdown",
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

    # -----------------------------------------------------
    # تسجيل الدخول
    # -----------------------------------------------------

    if data == "login":

        context.user_data.clear()
        context.user_data["state"] = "login_username"

        await query.edit_message_text(
            "🔐 *تسجيل الدخول*\n\n"
            "أرسل اسم المستخدم:",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )

        return

    # -----------------------------------------------------
    # إلغاء
    # -----------------------------------------------------

    if data == "cancel":

        context.user_data.clear()

        if is_logged_in(user.id):

            await query.edit_message_text(
                "تم إلغاء العملية.\n\n"
                "اختر من القائمة:",
                reply_markup=main_menu_keyboard()
            )

        else:

            await query.edit_message_text(
                "تم إلغاء العملية.\n\n"
                "يمكنك تسجيل الدخول من هنا:",
                reply_markup=start_keyboard()
            )

        return

    # -----------------------------------------------------
    # حماية جميع العمليات
    # -----------------------------------------------------

    if not is_logged_in(user.id):

        await query.edit_message_text(
            "🔒 يجب تسجيل الدخول أولاً.",
            reply_markup=start_keyboard()
        )

        return

    # -----------------------------------------------------
    # تسجيل نزيل
    # -----------------------------------------------------

    if data == "add_guest":

        context.user_data.clear()
        context.user_data["state"] = "guest_full_name"

        await query.edit_message_text(
            "📝 *تسجيل نزيل جديد*\n\n"
            "1️⃣ أرسل الاسم الثلاثي:",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )

        return

    # -----------------------------------------------------
    # الإحصائيات
    # -----------------------------------------------------

    if data == "statistics":

        conn = get_db()

        total = conn.execute(
            "SELECT COUNT(*) AS c FROM guests"
        ).fetchone()["c"]

        today = datetime.now().strftime("%Y-%m-%d")

        today_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM guests
            WHERE created_at LIKE ?
            """,
            (today + "%",)
        ).fetchone()["c"]

        conn.close()

        await query.edit_message_text(
            "📊 *الإحصائيات*\n\n"
            f"👤 إجمالي النزلاء المسجلين: {total}\n"
            f"📅 المسجلون اليوم: {today_count}",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # آخر السجلات
    # -----------------------------------------------------

    if data == "last_records":

        conn = get_db()

        rows = conn.execute("""
            SELECT
                id,
                full_name,
                hotel_name,
                room_number,
                created_at
            FROM guests
            ORDER BY id DESC
            LIMIT 10
        """).fetchall()

        conn.close()

        if not rows:

            text = "📋 لا توجد سجلات حتى الآن."

        else:

            lines = ["📋 *آخر 10 سجلات:*", ""]

            for row in rows:

                lines.append(
                    f"#{row['id']} — {row['full_name']}\n"
                    f"🏨 {row['hotel_name']} | غرفة {row['room_number']}\n"
                    f"🕐 {row['created_at']}\n"
                )

            text = "\n".join(lines)

        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # تسجيل خروج
    # -----------------------------------------------------

    if data == "logout":

        set_login(user.id, False)
        context.user_data.clear()

        await query.edit_message_text(
            "🚪 *تم تسجيل الخروج بنجاح.*\n\n"
            "لاستخدام النظام يجب تسجيل الدخول مرة أخرى.",
            parse_mode="Markdown",
            reply_markup=start_keyboard()
        )

        return


# =========================================================
# الأخطاء
# =========================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):

    logger.error(
        "حدث خطأ أثناء معالجة الطلب:",
        exc_info=context.error
    )

    try:

        if isinstance(update, Update) and update.effective_message:

            await update.effective_message.reply_text(
                "⚠️ حدث خطأ غير متوقع.\n"
                "يرجى الضغط على /start والمحاولة مرة أخرى."
            )

    except Exception:

        pass


# =========================================================
# تشغيل البوت
# =========================================================

def main():

    if not BOT_TOKEN or BOT_TOKEN == "ضع_توكن_البوت_هنا":

        print(
            "❌ لم يتم وضع BOT_TOKEN.\n"
            "ضع توكن البوت داخل المتغير BOT_TOKEN."
        )

        return

    init_db()

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    # /start
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # الأزرار
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # الرسائل النصية
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        )
    )

    # الأخطاء
    application.add_error_handler(
        error_handler
    )

    print("===================================")
    print("✅ البوت يعمل الآن")
    print("✅ /start -> تسجيل دخول")
    print("===================================")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
