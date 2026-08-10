import os
import sqlite3
import logging
import threading
import tempfile
import shutil
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    PageBreak,
)
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics


# =========================================================
# الإعدادات
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip())
except ValueError:
    ADMIN_ID = 0

DB_FILE = "hotel_bot.db"

# صورة الترحيب اختيارية
WELCOME_IMAGE = os.getenv("WELCOME_IMAGE", "welcome.jpg")

# مجلد الملفات المؤقتة
FILES_DIR = "bot_files"
os.makedirs(FILES_DIR, exist_ok=True)

# =========================================================
# Logging
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# الفنادق الافتراضية
# =========================================================

DEFAULT_HOTELS = [
    "قرطبة",
    "الحميدية",
    "النيل",
    "برج التجارة",
    "سرمدا",
    "باب الهوى",
    "دريم لاند",
    "مساكن سوريا",
]


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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS hotels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE,
                password TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                hotel_id INTEGER,
                logged_in INTEGER DEFAULT 0,
                login_time TEXT,
                FOREIGN KEY(hotel_id) REFERENCES hotels(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS guests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                hotel_id INTEGER,

                full_name TEXT,
                mother_name TEXT,
                birth_place_date TEXT,
                original_residence TEXT,
                governorate TEXT,
                hotel_name TEXT,
                hotel_area TEXT,
                stay_reason TEXT,
                check_in_date TEXT,
                stay_duration TEXT,
                notes TEXT,

                front_id TEXT,
                back_id TEXT,

                status TEXT DEFAULT 'pending',
                is_read INTEGER DEFAULT 0,

                created_at TEXT,

                FOREIGN KEY(hotel_id) REFERENCES hotels(id)
            )
        """)

        conn.commit()

        # إضافة الفنادق الافتراضية
        for hotel in DEFAULT_HOTELS:

            conn.execute("""
                INSERT OR IGNORE INTO hotels
                (name, created_at)
                VALUES (?, ?)
            """, (
                hotel,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))

        conn.commit()

    finally:
        conn.close()


# =========================================================
# المستخدم
# =========================================================

def register_user(user_id, username=""):

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO users
            (telegram_id, username)
            VALUES (?, ?)

            ON CONFLICT(telegram_id)
            DO UPDATE SET username = excluded.username
        """, (
            user_id,
            username
        ))

        conn.commit()

    finally:
        conn.close()


def set_user_login(user_id, hotel_id=None, status=True):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE users
            SET
                logged_in = ?,
                hotel_id = ?,
                login_time = ?
            WHERE telegram_id = ?
        """, (
            1 if status else 0,
            hotel_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if status else None,
            user_id
        ))

        conn.commit()

    finally:
        conn.close()


def logout_user(user_id):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE users
            SET
                logged_in = 0,
                hotel_id = NULL,
                login_time = NULL
            WHERE telegram_id = ?
        """, (user_id,))

        conn.commit()

    finally:
        conn.close()


def get_user_hotel(user_id):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT
                h.id,
                h.name,
                h.username,
                h.active
            FROM users u
            LEFT JOIN hotels h
                ON u.hotel_id = h.id
            WHERE u.telegram_id = ?
              AND u.logged_in = 1
        """, (user_id,)).fetchone()

        return row

    finally:
        conn.close()


# =========================================================
# الفنادق
# =========================================================

def get_hotels():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM hotels
            ORDER BY id
        """).fetchall()

    finally:
        conn.close()


def get_hotel(hotel_id):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM hotels
            WHERE id = ?
        """, (hotel_id,)).fetchone()

    finally:
        conn.close()


def create_hotel(name, username, password):

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO hotels
            (
                name,
                username,
                password,
                active,
                created_at
            )
            VALUES (?, ?, ?, 1, ?)
        """, (
            name,
            username,
            password,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        conn.commit()
        return True, "تم إنشاء الحساب بنجاح."

    except sqlite3.IntegrityError:

        return False, "اسم الفندق أو اسم المستخدم مستخدم مسبقاً."

    finally:
        conn.close()


def set_hotel_active(hotel_id, active):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE hotels
            SET active = ?
            WHERE id = ?
        """, (
            1 if active else 0,
            hotel_id
        ))

        conn.commit()

    finally:
        conn.close()


# =========================================================
# تسجيل دخول الفندق
# =========================================================

def hotel_login(username, password):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT *
            FROM hotels
            WHERE username = ?
              AND password = ?
        """, (
            username,
            password
        )).fetchone()

        if not row:
            return None

        if row["active"] != 1:
            return "DISABLED"

        return row

    finally:
        conn.close()


# =========================================================
# النزلاء
# =========================================================

def save_guest(user_id, hotel_id, data):

    conn = get_db()

    try:

        cursor = conn.execute("""
            INSERT INTO guests
            (
                telegram_id,
                hotel_id,

                full_name,
                mother_name,
                birth_place_date,
                original_residence,
                governorate,
                hotel_name,
                hotel_area,
                stay_reason,
                check_in_date,
                stay_duration,
                notes,

                front_id,
                back_id,

                status,
                is_read,
                created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, 'pending', 0, ?
            )
        """, (
            user_id,
            hotel_id,

            data.get("full_name", ""),
            data.get("mother_name", ""),
            data.get("birth_place_date", ""),
            data.get("original_residence", ""),
            data.get("governorate", ""),
            data.get("hotel_name", ""),
            data.get("hotel_area", ""),
            data.get("stay_reason", ""),
            data.get("check_in_date", ""),
            data.get("stay_duration", ""),
            data.get("notes", ""),

            data.get("front_id", ""),
            data.get("back_id", ""),

            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        conn.commit()

        return cursor.lastrowid

    finally:
        conn.close()


def get_unread_count():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT COUNT(*)
            FROM guests
            WHERE is_read = 0
        """).fetchone()[0]

    finally:
        conn.close()


def get_inbox():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT
                g.*,
                h.name AS hotel_display_name
            FROM guests g
            LEFT JOIN hotels h
                ON g.hotel_id = h.id
            ORDER BY g.id DESC
            LIMIT 30
        """).fetchall()

    finally:
        conn.close()


def mark_guest_read(guest_id):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE guests
            SET is_read = 1
            WHERE id = ?
        """, (guest_id,))

        conn.commit()

    finally:
        conn.close()


def get_guest(guest_id):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT
                g.*,
                h.name AS hotel_display_name
            FROM guests g
            LEFT JOIN hotels h
                ON g.hotel_id = h.id
            WHERE g.id = ?
        """, (guest_id,)).fetchone()

    finally:
        conn.close()


# =========================================================
# التقارير
# =========================================================

def report_data(start_date=None, end_date=None):

    conn = get_db()

    try:

        params = []
        where = ""

        if start_date and end_date:

            where = """
                WHERE date(g.created_at) >= date(?)
                  AND date(g.created_at) <= date(?)
            """

            params = [
                start_date,
                end_date
            ]

        rows = conn.execute(f"""
            SELECT
                g.governorate,
                g.original_residence,
                g.hotel_name,
                g.stay_reason,
                h.name AS hotel_display_name
            FROM guests g
            LEFT JOIN hotels h
                ON g.hotel_id = h.id
            {where}
            ORDER BY g.id DESC
        """, params).fetchall()

        return rows

    finally:
        conn.close()


# =========================================================
# واجهة المدير
# =========================================================

def admin_menu():

    unread = get_unread_count()

    inbox_text = (
        f"📨 الوارد ({unread})"
        if unread
        else
        "📨 الوارد"
    )

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "➕ إضافة حساب فندق",
                callback_data="admin_add_hotel"
            )
        ],

        [
            InlineKeyboardButton(
                "🏨 إدارة الفنادق",
                callback_data="admin_hotels"
            )
        ],

        [
            InlineKeyboardButton(
                inbox_text,
                callback_data="admin_inbox"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 التقرير اليومي",
                callback_data="report_daily"
            ),

            InlineKeyboardButton(
                "📈 التقرير الشهري",
                callback_data="report_monthly"
            )
        ],

        [
            InlineKeyboardButton(
                "🚪 تسجيل خروج",
                callback_data="admin_logout"
            )
        ]
    ])


# =========================================================
# واجهة الفندق
# =========================================================

def hotel_menu():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📝 تسجيل بيانات نزيل",
                callback_data="guest_start"
            )
        ],

        [
            InlineKeyboardButton(
                "📋 بياناتي",
                callback_data="my_records"
            )
        ],

        [
            InlineKeyboardButton(
                "🚪 تسجيل خروج",
                callback_data="hotel_logout"
            )
        ]
    ])


def back_button():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "↩️ رجوع",
                callback_data="back"
            )
        ]
    ])


def cancel_button():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="cancel"
            )
        ]
    ])


# =========================================================
# رسالة الترحيب
# =========================================================

async def send_welcome(update, is_admin_user=False):

    text = (
        "🦅 *نظام إدارة معلومات الفنادق*\n\n"
        "السلام عليكم ورحمة الله وبركاته\n\n"
        "أهلاً وسهلاً بكم في النظام الإلكتروني "
        "المخصص لتنظيم وإدارة بيانات النزلاء.\n\n"
        "📖 ﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
        "نرجو استخدام النظام وفق الصلاحيات الممنوحة لكم "
        "والمحافظة على سرية المعلومات.\n\n"
    )

    if is_admin_user:

        text += "👑 *صلاحية المدير*\n\nاختر العملية المطلوبة:"

        keyboard = admin_menu()

    else:

        text += "🔐 للمتابعة يرجى تسجيل الدخول."

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔐 تسجيل الدخول",
                    callback_data="login"
                )
            ]
        ])

    # -----------------------------------------------------
    # صورة الترحيب إن وجدت
    # -----------------------------------------------------

    if os.path.exists(WELCOME_IMAGE):

        with open(WELCOME_IMAGE, "rb") as photo:

            await update.message.reply_photo(
                photo=photo,
                caption=text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )

    else:

        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )


# =========================================================
# /start
# =========================================================

async def start(update, context):

    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    register_user(
        user.id,
        user.username or ""
    )

    context.user_data.clear()

    if user.id == ADMIN_ID:

        set_user_login(
            user.id,
            None,
            True
        )

        await send_welcome(
            update,
            True
        )

        return

    hotel = get_user_hotel(user.id)

    if hotel and hotel["active"] == 1:

        await update.message.reply_text(
            f"👋 أهلاً بك\n\n"
            f"🏨 الفندق: {hotel['name']}\n\n"
            f"اختر العملية المطلوبة:",
            reply_markup=hotel_menu()
        )

        return

    await send_welcome(
        update,
        False
    )


# =========================================================
# تسجيل الدخول
# =========================================================

async def login(update, context):

    query = update.callback_query

    user = update.effective_user

    context.user_data.clear()

    context.user_data["state"] = "login_username"

    await query.edit_message_text(
        "🔐 *تسجيل دخول حساب الفندق*\n\n"
        "أرسل اسم المستخدم:",
        parse_mode="Markdown",
        reply_markup=cancel_button()
    )


# =========================================================
# بدء تسجيل النزيل
# =========================================================

async def start_guest(update, context):

    query = update.callback_query

    context.user_data["state"] = "guest_full_name"
    context.user_data["guest"] = {}

    await query.edit_message_text(
        "📝 *تسجيل بيانات نزيل جديد*\n\n"
        "1️⃣ الاسم الثلاثي:",
        parse_mode="Markdown",
        reply_markup=cancel_button()
    )


# =========================================================
# استقبال الرسائل
# =========================================================

async def message_handler(update, context):

    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    text = (update.message.text or "").strip()

    state = context.user_data.get("state")

    # =====================================================
    # تسجيل الدخول - اسم المستخدم
    # =====================================================

    if state == "login_username":

        context.user_data["login_username"] = text
        context.user_data["state"] = "login_password"

        await update.message.reply_text(
            "🔑 أرسل كلمة المرور:",
            reply_markup=cancel_button()
        )

        return

    # =====================================================
    # تسجيل الدخول - كلمة المرور
    # =====================================================

    if state == "login_password":

        username = context.user_data.get(
            "login_username",
            ""
        )

        result = hotel_login(
            username,
            text
        )

        if result == "DISABLED":

            context.user_data.clear()

            await update.message.reply_text(
                "🚫 هذا الحساب معطل حالياً.\n\n"
                "يرجى مراجعة الإدارة."
            )

            return

        if not result:

            await update.message.reply_text(
                "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"
                "حاول مرة أخرى."
            )

            return

        hotel_id = result["id"]

        set_user_login(
            user.id,
            hotel_id,
            True
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ تم تسجيل الدخول بنجاح.\n\n"
            f"🏨 الفندق: {result['name']}\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_menu()
        )

        return

    # =====================================================
    # التحقق من تسجيل الدخول
    # =====================================================

    if user.id != ADMIN_ID:

        hotel = get_user_hotel(user.id)

        if not hotel:

            await update.message.reply_text(
                "🔒 يجب تسجيل الدخول أولاً.\n\n"
                "اضغط /start"
            )

            return

    # =====================================================
    # إضافة حساب فندق - الاسم
    # =====================================================

    if state == "admin_hotel_name":

        context.user_data["new_hotel_name"] = text
        context.user_data["state"] = "admin_hotel_username"

        await update.message.reply_text(
            "👤 أرسل اسم المستخدم للحساب:"
        )

        return

    # =====================================================
    # إضافة حساب فندق - username
    # =====================================================

    if state == "admin_hotel_username":

        context.user_data["new_hotel_username"] = text
        context.user_data["state"] = "admin_hotel_password"

        await update.message.reply_text(
            "🔑 أرسل كلمة المرور:"
        )

        return

    # =====================================================
    # إضافة حساب فندق - password
    # =====================================================

    if state == "admin_hotel_password":

        name = context.user_data.get(
            "new_hotel_name"
        )

        username = context.user_data.get(
            "new_hotel_username"
        )

        password = text

        ok, message = create_hotel(
            name,
            username,
            password
        )

        context.user_data.clear()

        await update.message.reply_text(
            ("✅ " if ok else "❌ ") + message,
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # بيانات النزيل
    # =====================================================

    guest = context.user_data.get("guest")

    if state == "guest_full_name":

        guest["full_name"] = text
        context.user_data["state"] = "guest_mother"

        await update.message.reply_text(
            "2️⃣ اسم الأم:"
        )

        return

    if state == "guest_mother":

        guest["mother_name"] = text
        context.user_data["state"] = "guest_birth"

        await update.message.reply_text(
            "3️⃣ مكان وتاريخ الولادة:"
        )

        return

    if state == "guest_birth":

        guest["birth_place_date"] = text
        context.user_data["state"] = "guest_residence"

        await update.message.reply_text(
            "4️⃣ السكن الأصلي:"
        )

        return

    if state == "guest_residence":

        guest["original_residence"] = text
        context.user_data["state"] = "guest_governorate"

        await update.message.reply_text(
            "5️⃣ المحافظة:"
        )

        return

    if state == "guest_governorate":

        guest["governorate"] = text
        context.user_data["state"] = "guest_hotel_area"

        await update.message.reply_text(
            "6️⃣ منطقة الفندق:"
        )

        return

    if state == "guest_hotel_area":

        guest["hotel_area"] = text

        hotel = get_user_hotel(user.id)

        guest["hotel_name"] = hotel["name"]

        context.user_data["state"] = "guest_reason"

        await update.message.reply_text(
            f"🏨 اسم الفندق: {hotel['name']}\n\n"
            "7️⃣ سبب الإقامة:"
        )

        return

    if state == "guest_reason":

        guest["stay_reason"] = text
        context.user_data["state"] = "guest_date"

        await update.message.reply_text(
            "8️⃣ تاريخ النزول:"
        )

        return

    if state == "guest_date":

        guest["check_in_date"] = text
        context.user_data["state"] = "guest_duration"

        await update.message.reply_text(
            "9️⃣ مدة الإقامة:"
        )

        return

    if state == "guest_duration":

        guest["stay_duration"] = text
        context.user_data["state"] = "guest_notes"

        await update.message.reply_text(
            "🔟 ملاحظات عامة:\n\n"
            "إذا لم توجد ملاحظات اكتب: لا يوجد"
        )

        return

    if state == "guest_notes":

        guest["notes"] = text
        context.user_data["state"] = "guest_front_id"

        await update.message.reply_text(
            "📷 أرسل صورة الهوية الشخصية "
            "من الجهة الأمامية:"
        )

        return

    # =====================================================
    # استقبال صورة الهوية الأمامية
    # =====================================================

    if state == "guest_front_id":

        if not update.message.photo:

            await update.message.reply_text(
                "⚠️ يرجى إرسال صورة واضحة للجهة الأمامية للهوية."
            )

            return

        photo = update.message.photo[-1]

        file = await photo.get_file()

        path = os.path.join(
            FILES_DIR,
            f"front_{user.id}_{datetime.now().timestamp()}.jpg"
        )

        await file.download_to_drive(path)

        guest["front_id"] = path

        context.user_data["state"] = "guest_back_id"

        await update.message.reply_text(
            "📷 الآن أرسل صورة الهوية الشخصية "
            "من الجهة الخلفية:"
        )

        return

    # =====================================================
    # استقبال صورة الهوية الخلفية
    # =====================================================

    if state == "guest_back_id":

        if not update.message.photo:

            await update.message.reply_text(
                "⚠️ يرجى إرسال صورة واضحة للجهة الخلفية للهوية."
            )

            return

        photo = update.message.photo[-1]

        file = await photo.get_file()

        path = os.path.join(
            FILES_DIR,
            f"back_{user.id}_{datetime.now().timestamp()}.jpg"
        )

        await file.download_to_drive(path)

        guest["back_id"] = path

        # -------------------------------------------------
        # حفظ البيانات
        # -------------------------------------------------

        hotel = get_user_hotel(user.id)

        guest_id = save_guest(
            user.id,
            hotel["id"],
            guest
        )

        # -------------------------------------------------
        # إنشاء PDF
        # -------------------------------------------------

        pdf_path = create_guest_pdf(
            guest_id,
            guest,
            hotel["name"]
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ تم حفظ بيانات النزيل بنجاح.\n\n"
            f"🔢 رقم المعاملة: #{guest_id}\n\n"
            "📨 تم إرسال البيانات إلى الإدارة ضمن الوارد.",
            reply_markup=hotel_menu()
        )

        # إرسال PDF للإدارة
        try:

            with open(pdf_path, "rb") as document:

                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=document,
                    caption=(
                        f"📨 طلب جديد من فندق {hotel['name']}\n\n"
                        f"🔢 رقم الطلب: #{guest_id}\n"
                        f"👤 النزيل: {guest['full_name']}\n\n"
                        "افتح قسم 📨 الوارد من لوحة المدير."
                    )
                )

        except Exception:

            logger.exception(
                "فشل إرسال PDF للإدارة"
            )

        return

    # =====================================================
    # لا توجد عملية
    # =====================================================

    await update.message.reply_text(
        "ℹ️ اختر العملية المطلوبة من القائمة."
    )


# =========================================================
# إنشاء PDF
# =========================================================

def register_arabic_font():

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    normal = candidates[0]
    bold = candidates[1]

    if os.path.exists(normal):

        try:
            pdfmetrics.registerFont(
                TTFont("ArabicFont", normal)
            )

            if os.path.exists(bold):

                pdfmetrics.registerFont(
                    TTFont("ArabicBold", bold)
                )

            return "ArabicFont", "ArabicBold"

        except Exception:
            pass

    return "Helvetica", "Helvetica-Bold"


def create_guest_pdf(guest_id, guest, hotel_name):

    normal_font, bold_font = register_arabic_font()

    pdf_path = os.path.join(
        FILES_DIR,
        f"guest_{guest_id}.pdf"
    )

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"Guest Report #{guest_id}"
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleArabic",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=18,
        leading=24,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#17202A"),
        spaceAfter=12,
    )

    normal_style = ParagraphStyle(
        "NormalArabic",
        parent=styles["Normal"],
        fontName=normal_font,
        fontSize=10.5,
        leading=17,
        alignment=TA_RIGHT,
    )

    small_style = ParagraphStyle(
        "SmallArabic",
        parent=styles["Normal"],
        fontName=normal_font,
        fontSize=8,
        leading=12,
        alignment=TA_RIGHT,
    )

    story = []

    story.append(
        Paragraph(
            "نظام إدارة معلومات الفنادق",
            title_style
        )
    )

    story.append(
        Paragraph(
            f"استمارة بيانات نزيل — رقم المعاملة #{guest_id}",
            normal_style
        )
    )

    story.append(Spacer(1, 8))

    # -----------------------------------------------------
    # رأس التقرير
    # -----------------------------------------------------

    header_data = [
        [
            Paragraph("<b>رقم المعاملة</b>", normal_style),
            Paragraph(f"#{guest_id}", normal_style)
        ],
        [
            Paragraph("<b>الفندق</b>", normal_style),
            Paragraph(hotel_name, normal_style)
        ],
        [
            Paragraph("<b>تاريخ التسجيل</b>", normal_style),
            Paragraph(
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                normal_style
            )
        ]
    ]

    header_table = Table(
        header_data,
        colWidths=[45 * mm, 115 * mm]
    )

    header_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, -1),
             colors.HexColor("#E8EEF3")),
            ("BOX", (0, 0), (-1, -1), 0.8,
             colors.HexColor("#34495E")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4,
             colors.HexColor("#BDC3C7")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ])
    )

    story.append(header_table)

    story.append(Spacer(1, 12))

    # -----------------------------------------------------
    # بيانات النزيل
    # -----------------------------------------------------

    fields = [
        ("الاسم الثلاثي", guest.get("full_name")),
        ("اسم الأم", guest.get("mother_name")),
        ("مكان وتاريخ الولادة", guest.get("birth_place_date")),
        ("السكن الأصلي", guest.get("original_residence")),
        ("المحافظة", guest.get("governorate")),
        ("اسم الفندق", guest.get("hotel_name")),
        ("منطقة الفندق", guest.get("hotel_area")),
        ("سبب الإقامة", guest.get("stay_reason")),
        ("تاريخ النزول", guest.get("check_in_date")),
        ("مدة الإقامة", guest.get("stay_duration")),
        ("ملاحظات عامة", guest.get("notes")),
    ]

    data = []

    for label, value in fields:

        data.append([
            Paragraph(
                str(value or ""),
                normal_style
            ),
            Paragraph(
                f"<b>{label}</b>",
                normal_style
            )
        ])

    table = Table(
        data,
        colWidths=[110 * mm, 50 * mm],
        repeatRows=0
    )

    table.setStyle(
        TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.8,
             colors.HexColor("#34495E")),
            ("INNERGRID", (0, 0), (-1, -1), 0.35,
             colors.HexColor("#BDC3C7")),
            ("BACKGROUND", (1, 0), (1, -1),
             colors.HexColor("#F4F6F7")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ])
    )

    story.append(table)

    story.append(Spacer(1, 15))

    # -----------------------------------------------------
    # الهوية
    # -----------------------------------------------------

    story.append(
        Paragraph(
            "صور وثائق الهوية",
            title_style
        )
    )

    images = []

    for path, title in [
        (
            guest.get("front_id"),
            "الجهة الأمامية"
        ),
        (
            guest.get("back_id"),
            "الجهة الخلفية"
        )
    ]:

        if path and os.path.exists(path):

            try:

                img = Image(
                    path,
                    width=75 * mm,
                    height=50 * mm
                )

                images.append([
                    Paragraph(
                        title,
                        normal_style
                    ),
                    img
                ])

            except Exception:

                logger.exception(
                    "تعذر إدراج صورة الهوية"
                )

    if images:

        image_table = Table(
            images,
            colWidths=[40 * mm, 90 * mm]
        )

        image_table.setStyle(
            TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.8,
                 colors.HexColor("#34495E")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5,
                 colors.HexColor("#BDC3C7")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ])
        )

        story.append(image_table)

    story.append(Spacer(1, 15))

    story.append(
        Paragraph(
            "هذه الوثيقة مخصصة للاستخدام الإداري الداخلي فقط.",
            small_style
        )
    )

    def footer(canvas, doc):

        canvas.saveState()

        canvas.setFont(
            normal_font,
            8
        )

        canvas.drawCentredString(
            A4[0] / 2,
            10 * mm,
            f"نظام إدارة معلومات الفنادق — الصفحة {doc.page}"
        )

        canvas.restoreState()

    doc.build(
        story,
        onFirstPage=footer,
        onLaterPages=footer
    )

    return pdf_path


# =========================================================
# معالجة الأزرار
# =========================================================

async def callback_handler(update, context):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = update.effective_user

    if not user:
        return

    data = query.data

    # =====================================================
    # LOGIN
    # =====================================================

    if data == "login":

        await login(
            update,
            context
        )

        return

    # =====================================================
    # CANCEL
    # =====================================================

    if data == "cancel":

        context.user_data.clear()

        if user.id == ADMIN_ID:

            await query.edit_message_text(
                "👑 تم إلغاء العملية.",
                reply_markup=admin_menu()
            )

        else:

            hotel = get_user_hotel(user.id)

            if hotel:

                await query.edit_message_text(
                    "❌ تم إلغاء العملية.",
                    reply_markup=hotel_menu()
                )

            else:

                await query.edit_message_text(
                    "❌ تم إلغاء العملية."
                )

        return

    # =====================================================
    # BACK
    # =====================================================

    if data == "back":

        context.user_data.clear()

        if user.id == ADMIN_ID:

            await query.edit_message_text(
                "👑 لوحة المدير",
                reply_markup=admin_menu()
            )

        else:

            await query.edit_message_text(
                "🏨 القائمة الرئيسية",
                reply_markup=hotel_menu()
            )

        return

    # =====================================================
    # حماية المدير
    # =====================================================

    if data.startswith("admin_") or data.startswith("report_"):

        if user.id != ADMIN_ID:

            await query.edit_message_text(
                "⛔ غير مصرح لك."
            )

            return

    # =====================================================
    # إضافة حساب فندق
    # =====================================================

    if data == "admin_add_hotel":

        context.user_data.clear()
        context.user_data["state"] = "admin_hotel_name"

        await query.edit_message_text(
            "➕ *إضافة حساب فندق*\n\n"
            "أرسل اسم الفندق:",
            parse_mode="Markdown",
            reply_markup=cancel_button()
        )

        return

    # =====================================================
    # إدارة الفنادق
    # =====================================================

    if data == "admin_hotels":

        hotels = get_hotels()

        buttons = []

        for hotel in hotels:

            status = "🟢" if hotel["active"] else "🔴"

            buttons.append([
                InlineKeyboardButton(
                    f"{status} {hotel['name']}",
                    callback_data=f"hotel_manage_{hotel['id']}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "↩️ رجوع",
                callback_data="back"
            )
        ])

        await query.edit_message_text(
            "🏨 *إدارة الفنادق*\n\n"
            "🟢 فعال\n"
            "🔴 معطل\n\n"
            "اختر الفندق:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    # =====================================================
    # إدارة فندق محدد
    # =====================================================

    if data.startswith("hotel_manage_"):

        hotel_id = int(
            data.replace(
                "hotel_manage_",
                ""
            )
        )

        hotel = get_hotel(hotel_id)

        if not hotel:
            return

        status = (
            "🟢 الحساب فعال"
            if hotel["active"]
            else
            "🔴 الحساب معطل"
        )

        action = (
            "🚫 تعطيل الحساب"
            if hotel["active"]
            else
            "🟢 تفعيل الحساب"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    action,
                    callback_data=f"hotel_toggle_{hotel_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "↩️ رجوع",
                    callback_data="admin_hotels"
                )
            ]
        ])

        await query.edit_message_text(
            f"🏨 *{hotel['name']}*\n\n"
            f"👤 اسم المستخدم: {hotel['username'] or 'غير محدد'}\n"
            f"📌 الحالة: {status}",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

        return

    # =====================================================
    # تعطيل / تفعيل الفندق
    # =====================================================

    if data.startswith("hotel_toggle_"):

        hotel_id = int(
            data.replace(
                "hotel_toggle_",
                ""
            )
        )

        hotel = get_hotel(hotel_id)

        if not hotel:
            return

        new_status = not bool(
            hotel["active"]
        )

        set_hotel_active(
            hotel_id,
            new_status
        )

        await query.edit_message_text(
            (
                "🟢 تم تفعيل حساب الفندق."
                if new_status
                else
                "🔴 تم تعطيل حساب الفندق."
            ),
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # الوارد
    # =====================================================

    if data == "admin_inbox":

        rows = get_inbox()

        if not rows:

            await query.edit_message_text(
                "📨 *الوارد*\n\n"
                "لا توجد طلبات حالياً.",
                parse_mode="Markdown",
                reply_markup=back_button()
            )

            return

        buttons = []

        for row in rows:

            status = (
                "🔵"
                if row["is_read"] == 0
                else
                "⚪"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"{status} #{row['id']} - {row['full_name']}",
                    callback_data=f"inbox_{row['id']}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "↩️ رجوع",
                callback_data="back"
            )
        ])

        await query.edit_message_text(
            f"📨 *الوارد*\n\n"
            f"🔵 غير مقروء: {get_unread_count()}\n"
            f"📋 إجمالي المعروض: {len(rows)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    # =====================================================
    # فتح رسالة من الوارد
    # =====================================================

    if data.startswith("inbox_"):

        guest_id = int(
            data.replace(
                "inbox_",
                ""
            )
        )

        guest = get_guest(
            guest_id
        )

        if not guest:
            await query.edit_message_text(
                "❌ الطلب غير موجود.",
                reply_markup=back_button()
            )
            return

        mark_guest_read(
            guest_id
        )

        text = (
            f"📨 *طلب رقم #{guest_id}*\n\n"
            f"🏨 الفندق: {guest['hotel_display_name']}\n"
            f"👤 الاسم: {guest['full_name']}\n"
            f"👩 اسم الأم: {guest['mother_name']}\n"
            f"📍 الولادة: {guest['birth_place_date']}\n"
            f"🏠 السكن الأصلي: {guest['original_residence']}\n"
            f"🏛 المحافظة: {guest['governorate']}\n"
            f"📍 منطقة الفندق: {guest['hotel_area']}\n"
            f"📝 السبب: {guest['stay_reason']}\n"
            f"📅 تاريخ النزول: {guest['check_in_date']}\n"
            f"⏳ مدة الإقامة: {guest['stay_duration']}\n"
            f"📌 الملاحظات: {guest['notes']}\n\n"
            f"🕐 وقت التسجيل: {guest['created_at']}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📄 إرسال PDF مرة أخرى",
                    callback_data=f"resend_pdf_{guest_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "↩️ رجوع للوارد",
                    callback_data="admin_inbox"
                )
            ]
        ])

        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )

        return

    # =====================================================
    # إعادة إرسال PDF
    # =====================================================

    if data.startswith("resend_pdf_"):

        guest_id = int(
            data.replace(
                "resend_pdf_",
                ""
            )
        )

        guest = get_guest(
            guest_id
        )

        if not guest:
            return

        data_dict = {
            "full_name": guest["full_name"],
            "mother_name": guest["mother_name"],
            "birth_place_date": guest["birth_place_date"],
            "original_residence": guest["original_residence"],
            "governorate": guest["governorate"],
            "hotel_name": guest["hotel_name"],
            "hotel_area": guest["hotel_area"],
            "stay_reason": guest["stay_reason"],
            "check_in_date": guest["check_in_date"],
            "stay_duration": guest["stay_duration"],
            "notes": guest["notes"],
            "front_id": guest["front_id"],
            "back_id": guest["back_id"],
        }

        pdf = create_guest_pdf(
            guest_id,
            data_dict,
            guest["hotel_display_name"]
        )

        with open(pdf, "rb") as document:

            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=document,
                caption=f"📄 نسخة PDF للطلب #{guest_id}"
            )

        await query.answer(
            "تم إرسال PDF",
            show_alert=True
        )

        return

    # =====================================================
    # التقرير اليومي
    # =====================================================

    if data == "report_daily":

        today = date.today().strftime(
            "%Y-%m-%d"
        )

        rows = report_data(
            today,
            today
        )

        await send_report(
            query,
            rows,
            f"📊 التقرير اليومي — {today}"
        )

        return

    # =====================================================
    # التقرير الشهري
    # =====================================================

    if data == "report_monthly":

        now = datetime.now()

        first = now.replace(
            day=1
        ).strftime("%Y-%m-%d")

        today = now.strftime(
            "%Y-%m-%d"
        )

        rows = report_data(
            first,
            today
        )

        await send_report(
            query,
            rows,
            "📈 التقرير الشهري"
        )

        return

    # =====================================================
    # بدء تسجيل النزيل
    # =====================================================

    if data == "guest_start":

        if user.id == ADMIN_ID:

            await query.answer(
                "هذا الخيار مخصص لحسابات الفنادق.",
                show_alert=True
            )

            return

        await start_guest(
            update,
            context
        )

        return

    # =====================================================
    # بيانات الفندق
    # =====================================================

    if data == "my_records":

        hotel = get_user_hotel(
            user.id
        )

        if not hotel:
            return

        conn = get_db()

        try:

            total = conn.execute("""
                SELECT COUNT(*)
                FROM guests
                WHERE hotel_id = ?
            """, (hotel["id"],)).fetchone()[0]

        finally:

            conn.close()

        await query.edit_message_text(
            f"🏨 *{hotel['name']}*\n\n"
            f"📋 عدد النزلاء الذين تم تسجيلهم: {total}",
            parse_mode="Markdown",
            reply_markup=back_button()
        )

        return

    # =====================================================
    # تسجيل خروج المدير
    # =====================================================

    if data == "admin_logout":

        logout_user(
            user.id
        )

        context.user_data.clear()

        await query.edit_message_text(
            "🚪 تم تسجيل خروج المدير.\n\n"
            "اضغط /start للعودة."
        )

        return

    # =====================================================
    # تسجيل خروج الفندق
    # =====================================================

    if data == "hotel_logout":

        logout_user(
            user.id
        )

        context.user_data.clear()

        await query.edit_message_text(
            "🚪 تم تسجيل الخروج.\n\n"
            "اضغط /start لتسجيل الدخول."
        )

        return


# =========================================================
# التقرير النصي
# =========================================================

async def send_report(query, rows, title):

    if not rows:

        await query.edit_message_text(
            f"{title}\n\n"
            "لا توجد بيانات ضمن الفترة المحددة.",
            reply_markup=back_button()
        )

        return

    governorates = {}
    countries = {}
    hotels = {}
    reasons = {}

    for row in rows:

        gov = row["governorate"] or "غير محدد"
        country = row["original_residence"] or "غير محدد"
        hotel = row["hotel_display_name"] or row["hotel_name"] or "غير محدد"
        reason = row["stay_reason"] or "غير محدد"

        governorates[gov] = governorates.get(gov, 0) + 1
        countries[country] = countries.get(country, 0) + 1
        hotels[hotel] = hotels.get(hotel, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1

    text = (
        f"{title}\n\n"
        f"👤 إجمالي النزلاء: {len(rows)}\n\n"
        "🏛 *حسب المحافظات:*\n"
    )

    for key, value in sorted(
        governorates.items(),
        key=lambda x: x[1],
        reverse=True
    ):
        text += f"• {key}: {value}\n"

    text += "\n🌍 *حسب السكن الأصلي / الدول:*\n"

    for key, value in sorted(
        countries.items(),
        key=lambda x: x[1],
        reverse=True
    ):
        text += f"• {key}: {value}\n"

    text += "\n🏨 *حسب الفنادق:*\n"

    for key, value in sorted(
        hotels.items(),
        key=lambda x: x[1],
        reverse=True
    ):
        text += f"• {key}: {value}\n"

    text += "\n📝 *حسب سبب الإقامة:*\n"

    for key, value in sorted(
        reasons.items(),
        key=lambda x: x[1],
        reverse=True
    ):
        text += f"• {key}: {value}\n"

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_button()
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

    def log_message(self, format, *args):
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
# التشغيل
# =========================================================

async def post_init(application):

    # إبقاء /start فقط
    await application.bot.set_my_commands([
        BotCommand(
            "start",
            "بدء استخدام النظام"
        )
    ])

    logger.info(
        "✅ تم ضبط أوامر Telegram — /start فقط"
    )


def main():

    if not BOT_TOKEN:

        logger.error(
            "❌ BOT_TOKEN غير موجود."
        )

        return

    if ADMIN_ID == 0:

        logger.error(
            "❌ ADMIN_ID غير موجود أو غير صحيح."
        )

        return

    try:

        init_db()

        logger.info(
            "✅ قاعدة البيانات جاهزة"
        )

    except Exception:

        logger.exception(
            "❌ فشل إنشاء قاعدة البيانات"
        )

        return

    # -----------------------------------------------------
    # HTTP لـ Render
    # -----------------------------------------------------

    try:

        thread = threading.Thread(
            target=start_health_server,
            daemon=True
        )

        thread.start()

    except Exception:

        logger.exception(
            "❌ فشل تشغيل HTTP Server"
        )

    # -----------------------------------------------------
    # Telegram
    # -----------------------------------------------------

    try:

        app = (
            ApplicationBuilder()
            .token(BOT_TOKEN)
            .post_init(post_init)
            .build()
        )

    except Exception:

        logger.exception(
            "❌ فشل إنشاء البوت"
        )

        return

    # /start فقط
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # الأزرار
    app.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # الرسائل والصور
    app.add_handler(
        MessageHandler(
            filters.TEXT | filters.PHOTO,
            message_handler
        )
    )

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
            "❌ توقف البوت"
        )


# =========================================================
# البداية
# =========================================================

if __name__ == "__main__":
    main()
