import os
import sqlite3
import logging
import threading

from http.server import BaseHTTPRequestHandler, HTTPServer
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip())
except ValueError:
    ADMIN_ID = 0

DB_FILE = "hotel_bot.db"

# رمز دخول المستخدمين العاديين
LOGIN_PASSWORD = "123456"


# =========================================================
# Logging
# =========================================================

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

    try:

        cur = conn.cursor()

        # -------------------------------------------------
        # جدول المستخدمين
        # -------------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                logged_in INTEGER DEFAULT 0,
                login_time TEXT
            )
        """)

        # -------------------------------------------------
        # جدول النزلاء
        # -------------------------------------------------

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

    finally:

        conn.close()


def register_user(user_id, username=""):

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO users
            (
                telegram_id,
                username
            )
            VALUES (?, ?)

            ON CONFLICT(telegram_id)
            DO UPDATE SET
                username = excluded.username
        """, (
            user_id,
            username,
        ))

        conn.commit()

    finally:

        conn.close()


def set_login(user_id, status):

    conn = get_db()

    try:

        login_time = (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if status
            else None
        )

        conn.execute("""
            UPDATE users
            SET
                logged_in = ?,
                login_time = ?
            WHERE telegram_id = ?
        """, (
            1 if status else 0,
            login_time,
            user_id,
        ))

        conn.commit()

    finally:

        conn.close()


def is_logged_in(user_id):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT logged_in
            FROM users
            WHERE telegram_id = ?
        """, (user_id,)).fetchone()

        return bool(
            row and row["logged_in"] == 1
        )

    finally:

        conn.close()


def save_guest(user_id, data):

    conn = get_db()

    try:

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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
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

    finally:

        conn.close()


# =========================================================
# التحقق من المدير والصلاحيات
# =========================================================

def is_admin(user_id):

    return user_id == ADMIN_ID


def has_access(user_id):

    # المدير لديه صلاحية مباشرة
    if is_admin(user_id):
        return True

    # المستخدم العادي يجب أن يكون مسجلاً للدخول
    return is_logged_in(user_id)


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

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    try:

        register_user(
            user.id,
            user.username or ""
        )

        context.user_data.clear()

        # -------------------------------------------------
        # المدير
        # -------------------------------------------------

        if is_admin(user.id):

            set_login(user.id, True)

            await update.message.reply_text(
                "👑 أهلاً بك أيها المدير\n\n"
                "🆔 تم التعرف على حسابك كمدير بواسطة Telegram ID.\n\n"
                "اختر العملية المطلوبة:",
                reply_markup=main_menu_keyboard()
            )

            return

        # -------------------------------------------------
        # مستخدم مسجل دخول
        # -------------------------------------------------

        if is_logged_in(user.id):

            await update.message.reply_text(
                "👋 أهلاً بك مجدداً.\n\n"
                "اختر العملية المطلوبة:",
                reply_markup=main_menu_keyboard()
            )

            return

        # -------------------------------------------------
        # مستخدم غير مسجل
        # -------------------------------------------------

        await update.message.reply_text(
            "🌹 مرحباً بك في نظام إدارة معلومات الفنادق\n\n"
            "📖 قال الله تعالى:\n"
            "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
            "🔐 للمتابعة يجب تسجيل الدخول أولاً.",
            reply_markup=start_keyboard()
        )

    except Exception:

        logger.exception("خطأ في /start")

        await update.message.reply_text(
            "❌ حدث خطأ أثناء تشغيل البوت.\n"
            "حاول مرة أخرى."
        )


# =========================================================
# تسجيل الدخول
# =========================================================

async def login_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    user = update.effective_user

    if not user:
        return

    # المدير لا يحتاج تسجيل دخول
    if is_admin(user.id):

        set_login(user.id, True)

        await query.edit_message_text(
            "👑 أنت المدير.\n\n"
            "تم التعرف عليك تلقائياً بواسطة ID.",
            reply_markup=main_menu_keyboard()
        )

        return

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

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    text = (update.message.text or "").strip()

    state = context.user_data.get("state")

    # =====================================================
    # تسجيل الدخول
    # =====================================================

    if state == "login":

        # المدير
        if is_admin(user.id):

            set_login(user.id, True)

            context.user_data.clear()

            await update.message.reply_text(
                "👑 تم التعرف عليك كمدير.\n\n"
                "اختر العملية المطلوبة:",
                reply_markup=main_menu_keyboard()
            )

            return

        # الرمز الصحيح
        if text == LOGIN_PASSWORD:

            set_login(user.id, True)

            context.user_data.clear()

            await update.message.reply_text(
                "✅ تم تسجيل الدخول بنجاح.\n\n"
                "اختر العملية المطلوبة:",
                reply_markup=main_menu_keyboard()
            )

        else:

            await update.message.reply_text(
                "❌ رمز الدخول غير صحيح.\n\n"
                "حاول مرة أخرى:"
            )

        return

    # =====================================================
    # حماية النظام
    # =====================================================

    if not has_access(user.id):

        await update.message.reply_text(
            "🔒 يجب تسجيل الدخول أولاً.\n\n"
            "اضغط /start"
        )

        return

    # =====================================================
    # تسجيل نزيل - الاسم
    # =====================================================

    if state == "guest_full_name":

        context.user_data["guest"] = {
            "full_name": text
        }

        context.user_data["state"] = "guest_mother"

        await update.message.reply_text(
            "2️⃣ اسم الأم:"
        )

        return

    # =====================================================
    # اسم الأم
    # =====================================================

    if state == "guest_mother":

        context.user_data["guest"]["mother_name"] = text

        context.user_data["state"] = "guest_birth"

        await update.message.reply_text(
            "3️⃣ مكان وتاريخ الولادة:"
        )

        return

    # =====================================================
    # مكان وتاريخ الولادة
    # =====================================================

    if state == "guest_birth":

        context.user_data["guest"]["birth_place_date"] = text

        context.user_data["state"] = "guest_residence"

        await update.message.reply_text(
            "4️⃣ السكن الأصلي:"
        )

        return

    # =====================================================
    # السكن الأصلي
    # =====================================================

    if state == "guest_residence":

        context.user_data["guest"]["original_residence"] = text

        context.user_data["state"] = "guest_governorate"

        await update.message.reply_text(
            "5️⃣ المحافظة:"
        )

        return

    # =====================================================
    # المحافظة
    # =====================================================

    if state == "guest_governorate":

        context.user_data["guest"]["governorate"] = text

        context.user_data["state"] = "guest_hotel"

        await update.message.reply_text(
            "6️⃣ اسم الفندق:"
        )

        return

    # =====================================================
    # الفندق
    # =====================================================

    if state == "guest_hotel":

        context.user_data["guest"]["hotel_name"] = text

        context.user_data["state"] = "guest_suite"

        await update.message.reply_text(
            "7️⃣ رقم الجناح:"
        )

        return

    # =====================================================
    # الجناح
    # =====================================================

    if state == "guest_suite":

        context.user_data["guest"]["suite_number"] = text

        context.user_data["state"] = "guest_room"

        await update.message.reply_text(
            "8️⃣ رقم الغرفة:"
        )

        return

    # =====================================================
    # الغرفة
    # =====================================================

    if state == "guest_room":

        context.user_data["guest"]["room_number"] = text

        context.user_data["state"] = "guest_duration"

        await update.message.reply_text(
            "9️⃣ مدة الإقامة:"
        )

        return

    # =====================================================
    # مدة الإقامة
    # =====================================================

    if state == "guest_duration":

        context.user_data["guest"]["stay_duration"] = text

        context.user_data["state"] = "guest_reason"

        await update.message.reply_text(
            "🔟 سبب الإقامة:"
        )

        return

    # =====================================================
    # سبب الإقامة
    # =====================================================

    if state == "guest_reason":

        context.user_data["guest"]["stay_reason"] = text

        guest = context.user_data["guest"]

        save_guest(
            user.id,
            guest
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ تم تسجيل النزيل بنجاح\n\n"
            f"👤 الاسم: {guest['full_name']}\n"
            f"👩 اسم الأم: {guest['mother_name']}\n"
            f"📍 الولادة: {guest['birth_place_date']}\n"
            f"🏠 السكن الأصلي: {guest['original_residence']}\n"
            f"🏛 المحافظة: {guest['governorate']}\n"
            f"🏨 الفندق: {guest['hotel_name']}\n"
            f"🚪 الجناح: {guest['suite_number']}\n"
            f"🚪 الغرفة: {guest['room_number']}\n"
            f"📅 مدة الإقامة: {guest['stay_duration']}\n"
            f"📝 سبب الإقامة: {guest['stay_reason']}",
            reply_markup=main_menu_keyboard()
        )

        return

    # =====================================================
    # لا توجد عملية
    # =====================================================

    await update.message.reply_text(
        "ℹ️ اختر إحدى العمليات من القائمة الرئيسية.\n\n"
        "اضغط /start للعودة."
    )


# =========================================================
# معالجة الأزرار
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    # الإجابة على Callback مرة واحدة فقط
    await query.answer()

    user = update.effective_user

    if not user:
        return

    data = query.data

    # =====================================================
    # تسجيل الدخول
    # =====================================================

    if data == "login":

        await login_button(
            update,
            context
        )

        return

    # =====================================================
    # إلغاء
    # =====================================================

    if data == "cancel":

        context.user_data.clear()

        if is_admin(user.id):

            await query.edit_message_text(
                "👑 تم إلغاء العملية.\n\n"
                "اختر العملية المطلوبة:",
                reply_markup=main_menu_keyboard()
            )

        else:

            await query.edit_message_text(
                "❌ تم إلغاء العملية.",
                reply_markup=start_keyboard()
            )

        return

    # =====================================================
    # حماية الأزرار
    # =====================================================

    if not has_access(user.id):

        await query.edit_message_text(
            "🔒 يجب تسجيل الدخول أولاً.",
            reply_markup=start_keyboard()
        )

        return

    # =====================================================
    # تسجيل نزيل
    # =====================================================

    if data == "add_guest":

        context.user_data.clear()

        context.user_data["state"] = "guest_full_name"

        await query.edit_message_text(
            "📝 تسجيل نزيل جديد\n\n"
            "1️⃣ الاسم الثلاثي:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # الإحصائيات
    # =====================================================

    if data == "statistics":

        conn = get_db()

        try:

            total = conn.execute(
                "SELECT COUNT(*) FROM guests"
            ).fetchone()[0]

        finally:

            conn.close()

        await query.edit_message_text(
            "📊 الإحصائيات\n\n"
            f"👤 عدد النزلاء المسجلين: {total}",
            reply_markup=main_menu_keyboard()
        )

        return

    # =====================================================
    # آخر السجلات
    # =====================================================

    if data == "last_records":

        conn = get_db()

        try:

            rows = conn.execute("""
                SELECT
                    full_name,
                    hotel_name,
                    room_number,
                    created_at
                FROM guests
                ORDER BY id DESC
                LIMIT 10
            """).fetchall()

        finally:

            conn.close()

        if not rows:

            text = "📋 لا توجد سجلات حتى الآن."

        else:

            text = "📋 آخر 10 سجلات:\n\n"

            for index, row in enumerate(rows, start=1):

                text += (
                    f"{index}️⃣ 👤 {row['full_name']}\n"
                    f"🏨 الفندق: {row['hotel_name']}\n"
                    f"🚪 الغرفة: {row['room_number']}\n"
                    f"🕐 {row['created_at']}\n\n"
                )

        await query.edit_message_text(
            text,
            reply_markup=main_menu_keyboard()
        )

        return

    # =====================================================
    # تسجيل الخروج
    # =====================================================

    if data == "logout":

        set_login(
            user.id,
            False
        )

        context.user_data.clear()

        await query.edit_message_text(
            "🚪 تم تسجيل الخروج.\n\n"
            "يمكنك الضغط على /start للعودة.",
            reply_markup=start_keyboard()
        )

        return

    # =====================================================
    # زر غير معروف
    # =====================================================

    await query.edit_message_text(
        "❌ العملية غير معروفة.\n\n"
        "اضغط /start للعودة."
    )


# =========================================================
# معالجة الأخطاء
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "حدث خطأ أثناء معالجة التحديث:",
        exc_info=context.error
    )


# =========================================================
# HTTP SERVER لـ Render
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Hotel Bot is running"
        )

    def log_message(
        self,
        format,
        *args
    ):

        return


def start_health_server():

    port = int(
        os.getenv(
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

    logger.info(
        f"HTTP server running on port {port}"
    )

    server.serve_forever()


# =========================================================
# تشغيل البوت
# =========================================================

def main():

    # =====================================================
    # التحقق من BOT_TOKEN
    # =====================================================

    if not BOT_TOKEN:

        logger.error(
            "❌ BOT_TOKEN غير موجود في Environment Variables"
        )

        return

    # =====================================================
    # التحقق من ADMIN_ID
    # =====================================================

    if ADMIN_ID == 0:

        logger.error(
            "❌ ADMIN_ID غير موجود أو غير صحيح"
        )

        return

    # =====================================================
    # إنشاء قاعدة البيانات
    # =====================================================

    try:

        init_db()

        logger.info(
            "✅ قاعدة البيانات جاهزة"
        )

    except Exception:

        logger.exception(
            "❌ خطأ أثناء إنشاء قاعدة البيانات"
        )

        return

    # =====================================================
    # تشغيل HTTP Server
    # =====================================================

    try:

        health_thread = threading.Thread(
            target=start_health_server,
            daemon=True
        )

        health_thread.start()

    except Exception:

        logger.exception(
            "❌ فشل تشغيل HTTP Server"
        )

    # =====================================================
    # إنشاء Telegram Application
    # =====================================================

    try:

        app = (
            ApplicationBuilder()
            .token(BOT_TOKEN)
            .build()
        )

    except Exception:

        logger.exception(
            "❌ فشل إنشاء Telegram Application"
        )

        return

    # =====================================================
    # تسجيل Handlers
    # =====================================================

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

    # =====================================================
    # تشغيل
    # =====================================================

    logger.info(
        "======================================"
    )

    logger.info(
        "✅ Hotel Telegram Bot Starting..."
    )

    logger.info(
        f"👑 ADMIN_ID = {ADMIN_ID}"
    )

    logger.info(
        "📡 Telegram Polling enabled"
    )

    logger.info(
        "======================================"
    )

    try:

        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )

    except Exception:

        logger.exception(
            "❌ Telegram Bot توقف بسبب خطأ"
        )


# =========================================================
# بداية البرنامج
# =========================================================

if __name__ == "__main__":

    main()
