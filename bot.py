import os
import sqlite3
import logging
import threading
import tempfile
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as RLImage,
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import arabic_reshaper
from bidi.algorithm import get_display


# =========================================================
# الإعدادات
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip())
except ValueError:
    ADMIN_ID = 0

DB_FILE = "hotel_bot.db"

LOGIN_PASSWORD = "123456"

WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "").strip()

PORT = int(os.getenv("PORT", "10000"))


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
# Logging
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# أدوات النص العربي
# =========================================================

def ar(text):
    """
    تجهيز النص العربي لعرضه بشكل صحيح داخل PDF.
    """
    if text is None:
        return ""

    text = str(text)

    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception:
        return text


# =========================================================
# الخط العربي للـ PDF
# =========================================================

def setup_pdf_font():

    possible_fonts = [
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    ]

    for font_path in possible_fonts:

        if os.path.exists(font_path):

            try:
                pdfmetrics.registerFont(
                    TTFont("ArabicFont", font_path)
                )

                logger.info(
                    f"✅ تم تحميل الخط: {font_path}"
                )

                return "ArabicFont"

            except Exception:
                pass

    logger.warning(
        "⚠️ لم يتم العثور على خط عربي للـ PDF"
    )

    return "Helvetica"


PDF_FONT = setup_pdf_font()


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

        # -----------------------------------------------
        # المستخدمون
        # -----------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                role TEXT DEFAULT 'user',
                hotel_name TEXT,
                password TEXT,
                logged_in INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)

        # -----------------------------------------------
        # الفنادق
        # -----------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS hotels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                created_at TEXT
            )
        """)

        # -----------------------------------------------
        # النزلاء
        # -----------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS guests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                telegram_id INTEGER,

                hotel_name TEXT,

                full_name TEXT,
                mother_name TEXT,
                birth_place_date TEXT,
                original_residence TEXT,
                governorate TEXT,

                hotel_area TEXT,
                stay_reason TEXT,
                check_in_date TEXT,
                stay_duration TEXT,
                notes TEXT,

                id_front_file_id TEXT,
                id_back_file_id TEXT,

                status TEXT DEFAULT 'draft',

                created_at TEXT,
                sent_at TEXT
            )
        """)

        # -----------------------------------------------
        # إضافة الفنادق الافتراضية
        # -----------------------------------------------

        for hotel in DEFAULT_HOTELS:

            cur.execute("""
                INSERT OR IGNORE INTO hotels
                (name, created_at)
                VALUES (?, ?)
            """, (
                hotel,
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            ))

        conn.commit()

        logger.info(
            "✅ قاعدة البيانات جاهزة"
        )

    finally:

        conn.close()


# =========================================================
# المستخدمون
# =========================================================

def register_user(
    user_id,
    username="",
):

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO users
            (
                telegram_id,
                username,
                created_at
            )
            VALUES (?, ?, ?)

            ON CONFLICT(telegram_id)
            DO UPDATE SET
                username = excluded.username
        """, (
            user_id,
            username,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ))

        conn.commit()

    finally:

        conn.close()


def create_hotel_account(
    user_id,
    username,
    hotel_name,
    password,
):

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO users
            (
                telegram_id,
                username,
                role,
                hotel_name,
                password,
                logged_in,
                created_at
            )
            VALUES (?, ?, 'hotel', ?, ?, 0, ?)

            ON CONFLICT(telegram_id)
            DO UPDATE SET
                username = excluded.username,
                role = 'hotel',
                hotel_name = excluded.hotel_name,
                password = excluded.password
        """, (
            user_id,
            username,
            hotel_name,
            password,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ))

        conn.commit()

    finally:

        conn.close()


def get_user(user_id):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM users
            WHERE telegram_id = ?
        """, (user_id,)).fetchone()

    finally:

        conn.close()


def set_login(user_id, status):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE users
            SET logged_in = ?
            WHERE telegram_id = ?
        """, (
            1 if status else 0,
            user_id,
        ))

        conn.commit()

    finally:

        conn.close()


def is_admin(user_id):

    return user_id == ADMIN_ID


def is_hotel_user(user_id):

    user = get_user(user_id)

    return bool(
        user and user["role"] == "hotel"
    )


def has_access(user_id):

    if is_admin(user_id):
        return True

    user = get_user(user_id)

    return bool(
        user
        and user["role"] == "hotel"
        and user["logged_in"] == 1
    )


# =========================================================
# الفنادق
# =========================================================

def add_hotel(name):

    conn = get_db()

    try:

        conn.execute("""
            INSERT OR IGNORE INTO hotels
            (name, created_at)
            VALUES (?, ?)
        """, (
            name,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ))

        conn.commit()

    finally:

        conn.close()


def get_hotels():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM hotels
            ORDER BY id ASC
        """).fetchall()

    finally:

        conn.close()


# =========================================================
# النزلاء
# =========================================================

def save_guest(data, user_id):

    conn = get_db()

    try:

        cur = conn.execute("""
            INSERT INTO guests
            (
                telegram_id,
                hotel_name,

                full_name,
                mother_name,
                birth_place_date,
                original_residence,
                governorate,

                hotel_area,
                stay_reason,
                check_in_date,
                stay_duration,
                notes,

                id_front_file_id,
                id_back_file_id,

                status,
                created_at
            )
            VALUES (
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                'draft',
                ?
            )
        """, (

            user_id,
            data.get("hotel_name", ""),

            data.get("full_name", ""),
            data.get("mother_name", ""),
            data.get("birth_place_date", ""),
            data.get("original_residence", ""),
            data.get("governorate", ""),

            data.get("hotel_area", ""),
            data.get("stay_reason", ""),
            data.get("check_in_date", ""),
            data.get("stay_duration", ""),
            data.get("notes", ""),

            data.get("id_front_file_id", ""),
            data.get("id_back_file_id", ""),

            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ))

        conn.commit()

        return cur.lastrowid

    finally:

        conn.close()


def get_guest(guest_id):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM guests
            WHERE id = ?
        """, (guest_id,)).fetchone()

    finally:

        conn.close()


def get_last_guest(user_id):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM guests
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,)).fetchone()

    finally:

        conn.close()


def mark_guest_sent(guest_id):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE guests
            SET
                status = 'sent',
                sent_at = ?
            WHERE id = ?
        """, (
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            guest_id,
        ))

        conn.commit()

    finally:

        conn.close()


# =========================================================
# لوحة المدير
# =========================================================

def admin_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🏨 إضافة حساب فندق",
                callback_data="admin_add_account"
            )
        ],

        [
            InlineKeyboardButton(
                "🏢 الفنادق",
                callback_data="admin_hotels"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 التقرير اليومي",
                callback_data="daily_report"
            )
        ],

        [
            InlineKeyboardButton(
                "📈 التقرير الشهري",
                callback_data="monthly_report"
            )
        ],

        [
            InlineKeyboardButton(
                "👥 حسابات الفنادق",
                callback_data="hotel_accounts"
            )
        ],

        [
            InlineKeyboardButton(
                "🚪 تسجيل الخروج",
                callback_data="logout"
            )
        ]

    ])


# =========================================================
# لوحة الفندق
# =========================================================

def hotel_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📝 تسجيل بيانات نزيل",
                callback_data="add_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "👁 عرض آخر بيانات",
                callback_data="view_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "📤 إرسال للإدارة",
                callback_data="send_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "🚪 تسجيل الخروج",
                callback_data="logout"
            )
        ]

    ])


# =========================================================
# قائمة الفنادق
# =========================================================

def hotels_keyboard():

    buttons = []

    hotels = get_hotels()

    for hotel in hotels:

        buttons.append([
            InlineKeyboardButton(
                f"🏨 {hotel['name']}",
                callback_data=f"select_hotel:{hotel['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "➕ إضافة فندق",
            callback_data="new_hotel"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 رجوع",
            callback_data="admin_menu"
        )
    ])

    return InlineKeyboardMarkup(buttons)


# =========================================================
# تسجيل الدخول
# =========================================================

def login_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🔐 تسجيل الدخول",
                callback_data="login"
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

    register_user(
        user.id,
        user.username or ""
    )

    context.user_data.clear()

    # المدير
    if is_admin(user.id):

        set_login(
            user.id,
            True
        )

        await update.message.reply_text(
            "👑 أهلاً بك أيها المدير\n\n"
            "مرحباً بك في لوحة إدارة الفنادق.\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=admin_keyboard()
        )

        return

    # الفندق
    db_user = get_user(user.id)

    if (
        db_user
        and db_user["role"] == "hotel"
        and db_user["logged_in"] == 1
    ):

        await update.message.reply_text(
            f"🏨 أهلاً بك في حساب فندق {db_user['hotel_name']}\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_keyboard()
        )

        return

    # تسجيل الدخول
    await update.message.reply_text(
        "🌹 أهلاً وسهلاً بك\n\n"
        "📖 ﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
        "🔐 للمتابعة يرجى تسجيل الدخول.",
        reply_markup=login_keyboard()
    )


# =========================================================
# تسجيل الدخول
# =========================================================

async def login_button(
    update,
    context
):

    query = update.callback_query

    user = update.effective_user

    if not user:
        return

    if is_admin(user.id):

        set_login(
            user.id,
            True
        )

        await query.edit_message_text(
            "👑 تم التعرف عليك كمدير.\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=admin_keyboard()
        )

        return

    context.user_data.clear()

    context.user_data["state"] = "hotel_login_id"

    await query.edit_message_text(
        "🔐 تسجيل الدخول\n\n"
        "أرسل Telegram ID الخاص بحساب الفندق:"
    )


# =========================================================
# بدء تسجيل نزيل
# =========================================================

async def start_guest(
    query,
    context,
    user_id
):

    user = get_user(user_id)

    if not user:
        return

    context.user_data.clear()

    context.user_data["state"] = "guest_full_name"

    context.user_data["guest"] = {
        "hotel_name": user["hotel_name"]
    }

    await query.edit_message_text(
        "📝 تسجيل بيانات نزيل جديد\n\n"
        "1️⃣ الاسم الثلاثي:"
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

    text = (
        update.message.text or ""
    ).strip()

    state = context.user_data.get("state")

    # =====================================================
    # تسجيل دخول الفندق - ID
    # =====================================================

    if state == "hotel_login_id":

        try:

            login_id = int(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Telegram ID غير صحيح.\n\n"
                "أرسل الرقم فقط:"
            )

            return

        account = get_user(login_id)

        if (
            not account
            or account["role"] != "hotel"
        ):

            await update.message.reply_text(
                "❌ لم يتم العثور على حساب فندق بهذا ID.\n\n"
                "حاول مرة أخرى:"
            )

            return

        context.user_data["login_id"] = login_id
        context.user_data["state"] = "hotel_login_password"

        await update.message.reply_text(
            "🔑 أرسل كلمة مرور حساب الفندق:"
        )

        return

    # =====================================================
    # تسجيل دخول الفندق - كلمة المرور
    # =====================================================

    if state == "hotel_login_password":

        login_id = context.user_data.get(
            "login_id"
        )

        account = get_user(login_id)

        if (
            not account
            or account["password"] != text
        ):

            await update.message.reply_text(
                "❌ كلمة المرور غير صحيحة.\n\n"
                "حاول مرة أخرى:"
            )

            return

        # نسمح بالدخول للحساب من الجهاز الحالي
        # ونربطه بالـ Telegram ID الحالي
        conn = get_db()

        try:

            conn.execute("""
                UPDATE users
                SET
                    telegram_id = ?,
                    logged_in = 1
                WHERE telegram_id = ?
            """, (
                user.id,
                login_id,
            ))

            conn.commit()

        except sqlite3.IntegrityError:

            await update.message.reply_text(
                "❌ هذا الحساب مرتبط مسبقاً بحساب Telegram آخر."
            )

            return

        finally:

            conn.close()

        context.user_data.clear()

        account = get_user(user.id)

        await update.message.reply_text(
            f"✅ تم تسجيل الدخول بنجاح\n\n"
            f"🏨 الفندق: {account['hotel_name']}\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_keyboard()
        )

        return

    # =====================================================
    # كلمة مرور عامة قديمة
    # =====================================================

    if state == "login":

        if text == LOGIN_PASSWORD:

            context.user_data.clear()

            await update.message.reply_text(
                "⚠️ هذا الرمز مخصص للنظام القديم.\n\n"
                "يجب أن يستخدم الفندق بيانات الحساب التي ينشئها المدير."
            )

        return

    # =====================================================
    # حماية
    # =====================================================

    if not has_access(user.id):

        await update.message.reply_text(
            "🔒 يجب تسجيل الدخول أولاً.\n\n"
            "اضغط /start"
        )

        return

    # =====================================================
    # إضافة فندق
    # =====================================================

    if state == "new_hotel":

        add_hotel(text)

        context.user_data.clear()

        await update.message.reply_text(
            f"✅ تمت إضافة الفندق:\n\n"
            f"🏨 {text}",
            reply_markup=admin_keyboard()
        )

        return

    # =====================================================
    # اسم مستخدم حساب الفندق
    # =====================================================

    if state == "account_username":

        context.user_data["account_username"] = text

        context.user_data["state"] = "account_password"

        await update.message.reply_text(
            "🔑 أرسل كلمة المرور التي تريدها للحساب:"
        )

        return

    # =====================================================
    # كلمة مرور حساب الفندق
    # =====================================================

    if state == "account_password":

        hotel_name = context.user_data.get(
            "account_hotel"
        )

        username = context.user_data.get(
            "account_username"
        )

        password = text

        # الحساب يحصل على Telegram ID مؤقت
        # ويتم استبداله عند تسجيل الدخول لأول مرة
        temp_id = int(
            f"9{int(datetime.now().timestamp())}"
        ) % 2147483647

        create_hotel_account(
            temp_id,
            username,
            hotel_name,
            password
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ تم إنشاء حساب الفندق بنجاح\n\n"
            f"🏨 الفندق: {hotel_name}\n"
            f"👤 اسم الحساب: {username}\n"
            f"🔑 كلمة المرور: {password}\n"
            f"🆔 معرف الحساب: {temp_id}\n\n"
            "📌 أرسل هذه البيانات لمسؤول الفندق.",
            reply_markup=admin_keyboard()
        )

        return

    # =====================================================
    # تسجيل النزيل
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

        context.user_data["state"] = "guest_area"

        await update.message.reply_text(
            "6️⃣ منطقة الفندق:"
        )

        return

    if state == "guest_area":

        guest["hotel_area"] = text

        context.user_data["state"] = "guest_reason"

        await update.message.reply_text(
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
            "إذا لا توجد ملاحظات اكتب: لا يوجد"
        )

        return

    if state == "guest_notes":

        guest["notes"] = text

        context.user_data["state"] = "guest_id_front"

        await update.message.reply_text(
            "🪪 أرسل صورة الهوية الشخصية\n"
            "من الجهة الأمامية:"
        )

        return

    # =====================================================
    # صورة الهوية الأمامية
    # =====================================================

    if state == "guest_id_front":

        if not update.message.photo:

            await update.message.reply_text(
                "❌ يرجى إرسال صورة الهوية كصورة."
            )

            return

        photo = update.message.photo[-1]

        guest["id_front_file_id"] = photo.file_id

        context.user_data["state"] = "guest_id_back"

        await update.message.reply_text(
            "🪪 ممتاز.\n\n"
            "الآن أرسل صورة الهوية من الجهة الخلفية:"
        )

        return

    # =====================================================
    # صورة الهوية الخلفية
    # =====================================================

    if state == "guest_id_back":

        if not update.message.photo:

            await update.message.reply_text(
                "❌ يرجى إرسال صورة الهوية كصورة."
            )

            return

        photo = update.message.photo[-1]

        guest["id_back_file_id"] = photo.file_id

        guest_id = save_guest(
            guest,
            user.id
        )

        context.user_data.clear()

        context.user_data["last_guest_id"] = guest_id

        await update.message.reply_text(
            "✅ تم تجهيز بيانات النزيل بنجاح.\n\n"
            "يمكنك الآن عرض البيانات أو إرسالها للإدارة.",
            reply_markup=guest_after_keyboard()
        )

        return


# =========================================================
# أزرار بعد التسجيل
# =========================================================

def guest_after_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "👁 عرض البيانات",
                callback_data="view_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "📤 إرسال للإدارة",
                callback_data="send_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "📝 تسجيل نزيل آخر",
                callback_data="add_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "🔙 القائمة الرئيسية",
                callback_data="hotel_menu"
            )
        ]

    ])


# =========================================================
# عرض بيانات النزيل
# =========================================================

def guest_text(guest):

    return (
        "📋 بيانات النزيل\n\n"
        f"👤 الاسم: {guest['full_name']}\n"
        f"👩 اسم الأم: {guest['mother_name']}\n"
        f"📍 الولادة: {guest['birth_place_date']}\n"
        f"🏠 السكن الأصلي: {guest['original_residence']}\n"
        f"🏛 المحافظة: {guest['governorate']}\n"
        f"🏨 الفندق: {guest['hotel_name']}\n"
        f"📍 منطقة الفندق: {guest['hotel_area']}\n"
        f"📝 سبب الإقامة: {guest['stay_reason']}\n"
        f"📅 تاريخ النزول: {guest['check_in_date']}\n"
        f"⏱ مدة الإقامة: {guest['stay_duration']}\n"
        f"📌 الملاحظات: {guest['notes']}\n"
    )


# =========================================================
# إنشاء PDF
# =========================================================

async def create_guest_pdf(
    bot,
    guest
):

    temp_dir = tempfile.mkdtemp()

    pdf_path = os.path.join(
        temp_dir,
        f"guest_{guest['id']}.pdf"
    )

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleArabic",
        parent=styles["Title"],
        fontName=PDF_FONT,
        fontSize=20,
        leading=28,
        alignment=TA_CENTER,
        spaceAfter=20,
    )

    normal_style = ParagraphStyle(
        "NormalArabic",
        parent=styles["Normal"],
        fontName=PDF_FONT,
        fontSize=11,
        leading=18,
        alignment=TA_RIGHT,
    )

    story = []

    story.append(
        Paragraph(
            ar("تقرير بيانات نزيل"),
            title_style
        )
    )

    story.append(
        Paragraph(
            ar(
                f"الفندق: {guest['hotel_name']}"
            ),
            normal_style
        )
    )

    story.append(Spacer(1, 15))

    data = [

        [
            ar("البيان"),
            ar("المعلومات")
        ],

        [
            ar("الاسم الثلاثي"),
            ar(guest["full_name"])
        ],

        [
            ar("اسم الأم"),
            ar(guest["mother_name"])
        ],

        [
            ar("مكان وتاريخ الولادة"),
            ar(guest["birth_place_date"])
        ],

        [
            ar("السكن الأصلي"),
            ar(guest["original_residence"])
        ],

        [
            ar("المحافظة"),
            ar(guest["governorate"])
        ],

        [
            ar("الفندق"),
            ar(guest["hotel_name"])
        ],

        [
            ar("منطقة الفندق"),
            ar(guest["hotel_area"])
        ],

        [
            ar("سبب الإقامة"),
            ar(guest["stay_reason"])
        ],

        [
            ar("تاريخ النزول"),
            ar(guest["check_in_date"])
        ],

        [
            ar("مدة الإقامة"),
            ar(guest["stay_duration"])
        ],

        [
            ar("ملاحظات عامة"),
            ar(guest["notes"])
        ],

        [
            ar("تاريخ التسجيل"),
            ar(guest["created_at"])
        ],

    ]

    table = Table(
        data,
        colWidths=[150, 330],
        repeatRows=1,
    )

    table.setStyle(
        TableStyle([

            (
                "FONTNAME",
                (0, 0),
                (-1, -1),
                PDF_FONT
            ),

            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.HexColor("#1f2937")
            ),

            (
                "TEXTCOLOR",
                (0, 0),
                (-1, 0),
                colors.white
            ),

            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.grey
            ),

            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),

            (
                "ALIGN",
                (0, 0),
                (-1, -1),
                "RIGHT"
            ),

            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                8
            ),

            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                8
            ),

        ])
    )

    story.append(table)

    story.append(Spacer(1, 20))

    story.append(
        Paragraph(
            ar(
                "تم إنشاء هذا التقرير إلكترونياً بواسطة نظام إدارة الفنادق."
            ),
            normal_style
        )
    )

    doc.build(story)

    return pdf_path


# =========================================================
# التقرير
# =========================================================

def get_report_rows(
    start_date=None,
    end_date=None
):

    conn = get_db()

    try:

        if start_date and end_date:

            rows = conn.execute("""
                SELECT *
                FROM guests
                WHERE date(created_at) BETWEEN date(?) AND date(?)
                ORDER BY id DESC
            """, (
                start_date,
                end_date
            )).fetchall()

        else:

            rows = conn.execute("""
                SELECT *
                FROM guests
                ORDER BY id DESC
            """).fetchall()

        return rows

    finally:

        conn.close()


def build_report_summary(rows):

    hotels = {}
    governors = {}
    residences = {}
    reasons = {}

    for row in rows:

        hotel = row["hotel_name"] or "غير محدد"
        governor = row["governorate"] or "غير محدد"
        residence = row["original_residence"] or "غير محدد"
        reason = row["stay_reason"] or "غير محدد"

        hotels[hotel] = hotels.get(hotel, 0) + 1
        governors[governor] = governors.get(governor, 0) + 1
        residences[residence] = residences.get(residence, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1

    return (
        hotels,
        governors,
        residences,
        reasons
    )


def report_text(
    title,
    rows
):

    hotels, governors, residences, reasons = (
        build_report_summary(rows)
    )

    text = (
        f"{title}\n\n"
        f"👤 إجمالي النزلاء: {len(rows)}\n\n"
        "🏨 حسب الفنادق:\n"
    )

    for name, count in hotels.items():

        text += (
            f"• {name}: {count}\n"
        )

    text += "\n🏛 حسب المحافظات:\n"

    for name, count in governors.items():

        text += (
            f"• {name}: {count}\n"
        )

    text += "\n🌍 حسب السكن الأصلي:\n"

    for name, count in residences.items():

        text += (
            f"• {name}: {count}\n"
        )

    text += "\n📝 حسب سبب الإقامة:\n"

    for name, count in reasons.items():

        text += (
            f"• {name}: {count}\n"
        )

    return text


# =========================================================
# Callback Handler
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = update.effective_user

    if not user:
        return

    data = query.data

    # =====================================================
    # Login
    # =====================================================

    if data == "login":

        await login_button(
            update,
            context
        )

        return

    # =====================================================
    # القائمة الإدارية
    # =====================================================

    if data == "admin_menu":

        await query.edit_message_text(
            "👑 لوحة المدير\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=admin_keyboard()
        )

        return

    # =====================================================
    # قائمة الفندق
    # =====================================================

    if data == "hotel_menu":

        account = get_user(user.id)

        if not account:
            return

        await query.edit_message_text(
            f"🏨 فندق {account['hotel_name']}\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_keyboard()
        )

        return

    # =====================================================
    # إضافة حساب فندق
    # =====================================================

    if data == "admin_add_account":

        if not is_admin(user.id):
            return

        await query.edit_message_text(
            "🏨 اختر الفندق الذي تريد إنشاء حساب له:",
            reply_markup=hotels_keyboard()
        )

        return

    # =====================================================
    # اختيار الفندق
    # =====================================================

    if data.startswith("select_hotel:"):

        if not is_admin(user.id):
            return

        hotel_id = data.split(":")[1]

        conn = get_db()

        try:

            hotel = conn.execute("""
                SELECT *
                FROM hotels
                WHERE id = ?
            """, (hotel_id,)).fetchone()

        finally:

            conn.close()

        if not hotel:
            return

        context.user_data.clear()

        context.user_data["account_hotel"] = (
            hotel["name"]
        )

        context.user_data["state"] = (
            "account_username"
        )

        await query.edit_message_text(
            f"🏨 الفندق: {hotel['name']}\n\n"
            "👤 أرسل اسم المستخدم للحساب:"
        )

        return

    # =====================================================
    # إضافة فندق
    # =====================================================

    if data == "new_hotel":

        if not is_admin(user.id):
            return

        context.user_data.clear()

        context.user_data["state"] = "new_hotel"

        await query.edit_message_text(
            "➕ إضافة فندق جديد\n\n"
            "أرسل اسم الفندق:"
        )

        return

    # =====================================================
    # إضافة نزيل
    # =====================================================

    if data == "add_guest":

        if not is_hotel_user(user.id):
            return

        await start_guest(
            query,
            context,
            user.id
        )

        return

    # =====================================================
    # عرض النزيل
    # =====================================================

    if data == "view_guest":

        if not is_hotel_user(user.id):
            return

        guest_id = context.user_data.get(
            "last_guest_id"
        )

        if not guest_id:

            guest = get_last_guest(user.id)

        else:

            guest = get_guest(
                guest_id
            )

        if not guest:

            await query.edit_message_text(
                "📋 لا توجد بيانات نزيل حالياً.",
                reply_markup=hotel_keyboard()
            )

            return

        await query.edit_message_text(
            guest_text(guest),
            reply_markup=guest_after_keyboard()
        )

        return

    # =====================================================
    # إرسال للإدارة
    # =====================================================

    if data == "send_guest":

        if not is_hotel_user(user.id):
            return

        guest_id = context.user_data.get(
            "last_guest_id"
        )

        guest = (
            get_guest(guest_id)
            if guest_id
            else get_last_guest(user.id)
        )

        if not guest:

            await query.edit_message_text(
                "❌ لا توجد بيانات جاهزة للإرسال.",
                reply_markup=hotel_keyboard()
            )

            return

        try:

            pdf_path = await create_guest_pdf(
                context.bot,
                guest
            )

            caption = (
                "📥 تقرير نزيل جديد\n\n"
                f"🏨 الفندق: {guest['hotel_name']}\n"
                f"👤 الاسم: {guest['full_name']}\n"
                f"📅 التاريخ: {guest['created_at']}"
            )

            with open(
                pdf_path,
                "rb"
            ) as pdf_file:

                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=pdf_file,
                    filename=(
                        f"guest_{guest['id']}.pdf"
                    ),
                    caption=caption
                )

            # صورة الهوية الأمامية
            if guest["id_front_file_id"]:

                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=guest["id_front_file_id"],
                    caption=(
                        f"🪪 الهوية - الوجه الأمامي\n"
                        f"👤 {guest['full_name']}\n"
                        f"🏨 {guest['hotel_name']}"
                    )
                )

            # صورة الهوية الخلفية
            if guest["id_back_file_id"]:

                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=guest["id_back_file_id"],
                    caption=(
                        f"🪪 الهوية - الوجه الخلفي\n"
                        f"👤 {guest['full_name']}\n"
                        f"🏨 {guest['hotel_name']}"
                    )
                )

            mark_guest_sent(
                guest["id"]
            )

            await query.edit_message_text(
                "✅ تم إرسال بيانات النزيل إلى الإدارة بنجاح.\n\n"
                "📄 تم إرسال ملف PDF\n"
                "🪪 وتم إرسال صور الهوية.",
                reply_markup=hotel_keyboard()
            )

        except Exception:

            logger.exception(
                "خطأ أثناء إرسال تقرير النزيل"
            )

            await query.edit_message_text(
                "❌ حدث خطأ أثناء إرسال البيانات للإدارة.\n"
                "حاول مرة أخرى."
            )

        return

    # =====================================================
    # التقرير اليومي
    # =====================================================

    if data == "daily_report":

        if not is_admin(user.id):
            return

        today = date.today().strftime(
            "%Y-%m-%d"
        )

        rows = get_report_rows(
            today,
            today
        )

        text = report_text(
            "📊 التقرير اليومي",
            rows
        )

        await query.edit_message_text(
            text,
            reply_markup=admin_keyboard()
        )

        return

    # =====================================================
    # التقرير الشهري
    # =====================================================

    if data == "monthly_report":

        if not is_admin(user.id):
            return

        today = date.today()

        first_day = today.replace(
            day=1
        ).strftime(
            "%Y-%m-%d"
        )

        rows = get_report_rows(
            first_day,
            today.strftime("%Y-%m-%d")
        )

        text = report_text(
            "📈 التقرير الشهري",
            rows
        )

        await query.edit_message_text(
            text,
            reply_markup=admin_keyboard()
        )

        return

    # =====================================================
    # الفنادق
    # =====================================================

    if data == "admin_hotels":

        if not is_admin(user.id):
            return

        hotels = get_hotels()

        text = "🏨 الفنادق المسجلة:\n\n"

        for i, hotel in enumerate(
            hotels,
            start=1
        ):

            text += (
                f"{i}. {hotel['name']}\n"
            )

        await query.edit_message_text(
            text,
            reply_markup=admin_keyboard()
        )

        return

    # =====================================================
    # حسابات الفنادق
    # =====================================================

    if data == "hotel_accounts":

        if not is_admin(user.id):
            return

        conn = get_db()

        try:

            accounts = conn.execute("""
                SELECT *
                FROM users
                WHERE role = 'hotel'
                ORDER BY id
            """).fetchall()

        finally:

            conn.close()

        if not accounts:

            text = (
                "👥 لا توجد حسابات فنادق حالياً."
            )

        else:

            text = "👥 حسابات الفنادق:\n\n"

            for account in accounts:

                text += (
                    f"🏨 {account['hotel_name']}\n"
                    f"👤 {account['username']}\n"
                    f"🆔 {account['telegram_id']}\n"
                    "────────────\n"
                )

        await query.edit_message_text(
            text,
            reply_markup=admin_keyboard()
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
            "اضغط /start للعودة.",
            reply_markup=login_keyboard()
        )

        return


# =========================================================
# HTTP Server لـ Render
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

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

    server = HTTPServer(
        (
            "0.0.0.0",
            PORT
        ),
        HealthHandler
    )

    logger.info(
        f"HTTP server running on port {PORT}"
    )

    server.serve_forever()


# =========================================================
# الأخطاء
# =========================================================

async def error_handler(
    update,
    context
):

    logger.error(
        "حدث خطأ:",
        exc_info=context.error
    )


# =========================================================
# main
# =========================================================

def main():

    # -----------------------------------------------------
    # BOT TOKEN
    # -----------------------------------------------------

    if not BOT_TOKEN:

        logger.error(
            "❌ BOT_TOKEN غير موجود."
        )

        return

    # -----------------------------------------------------
    # ADMIN ID
    # -----------------------------------------------------

    if ADMIN_ID == 0:

        logger.error(
            "❌ ADMIN_ID غير موجود أو غير صحيح."
        )

        return

    # -----------------------------------------------------
    # DB
    # -----------------------------------------------------

    try:

        init_db()

    except Exception:

        logger.exception(
            "❌ فشل إنشاء قاعدة البيانات."
        )

        return

    # -----------------------------------------------------
    # Render health server
    # -----------------------------------------------------

    threading.Thread(
        target=start_health_server,
        daemon=True
    ).start()

    # -----------------------------------------------------
    # Telegram
    # -----------------------------------------------------

    try:

        app = (
            ApplicationBuilder()
            .token(BOT_TOKEN)
            .build()
        )

    except Exception:

        logger.exception(
            "❌ فشل إنشاء Telegram Application."
        )

        return

    # -----------------------------------------------------
    # Handlers
    # -----------------------------------------------------

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
            filters.PHOTO,
            message_handler
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

    # -----------------------------------------------------
    # Start
    # -----------------------------------------------------

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

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


# =========================================================
# تشغيل البرنامج
# =========================================================

if __name__ == "__main__":
    main()
