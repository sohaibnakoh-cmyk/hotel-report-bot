import os
import sqlite3
import logging
import threading
import io
import hashlib
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

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as PDFImage,
    PageBreak,
)
from reportlab.lib.units import mm


# =========================================================
# الإعدادات
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip())
except Exception:
    ADMIN_ID = 0

DB_FILE = "hotel_bot.db"

PORT = int(os.getenv("PORT", "10000"))

LOGIN_PASSWORD = "123456"

# أسماء الفنادق الافتراضية
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
# قاعدة البيانات
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def init_db():

    conn = get_db()

    try:

        # -------------------------------------------------
        # المستخدمون
        # -------------------------------------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                role TEXT DEFAULT 'user',
                hotel_name TEXT,
                logged_in INTEGER DEFAULT 0,
                login_time TEXT
            )
        """)

        # -------------------------------------------------
        # حسابات الفنادق
        # -------------------------------------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS hotel_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hotel_name TEXT NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)

        # -------------------------------------------------
        # الفنادق
        # -------------------------------------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS hotels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)

        # -------------------------------------------------
        # النزلاء
        # -------------------------------------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS guests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                hotel_username TEXT,
                full_name TEXT,
                mother_name TEXT,
                birth_place_date TEXT,
                original_residence TEXT,
                governorate TEXT,
                country TEXT,
                hotel_name TEXT,
                hotel_area TEXT,
                stay_reason TEXT,
                check_in_date TEXT,
                stay_duration TEXT,
                notes TEXT,
                front_photo TEXT,
                back_photo TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)

        # -------------------------------------------------
        # إضافة الفنادق الافتراضية
        # -------------------------------------------------

        for hotel in DEFAULT_HOTELS:

            conn.execute("""
                INSERT OR IGNORE INTO hotels
                (name, active, created_at)
                VALUES (?, 1, ?)
            """, (
                hotel,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))

        conn.commit()

    finally:
        conn.close()


# =========================================================
# المستخدمون
# =========================================================

def register_telegram_user(user_id, username=""):

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
            DO UPDATE SET username = excluded.username
        """, (
            user_id,
            username,
        ))

        conn.commit()

    finally:
        conn.close()


def is_admin(user_id):
    return user_id == ADMIN_ID


def get_hotel_session(user_id):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT hotel_name, username
            FROM users
            WHERE telegram_id = ?
              AND role = 'hotel'
              AND logged_in = 1
        """, (user_id,)).fetchone()

        return row

    finally:
        conn.close()


def logout_user(user_id):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE users
            SET logged_in = 0,
                role = CASE
                    WHEN telegram_id = ? THEN role
                    ELSE role
                END
            WHERE telegram_id = ?
        """, (
            user_id,
            user_id,
        ))

        conn.commit()

    finally:
        conn.close()


# =========================================================
# حسابات الفنادق
# =========================================================

def create_hotel_account(hotel_name, username, password):

    conn = get_db()

    try:

        # التحقق من اسم المستخدم
        existing_username = conn.execute("""
            SELECT id
            FROM hotel_accounts
            WHERE LOWER(username) = LOWER(?)
        """, (username,)).fetchone()

        if existing_username:
            return False, "username"

        # التحقق من وجود نفس الفندق + اسم المستخدم
        existing = conn.execute("""
            SELECT id
            FROM hotel_accounts
            WHERE hotel_name = ?
        """, (hotel_name,)).fetchone()

        # نسمح بأكثر من حساب لنفس الفندق
        # بشرط أن يكون اسم المستخدم مختلفاً

        conn.execute("""
            INSERT INTO hotel_accounts
            (
                hotel_name,
                username,
                password_hash,
                active,
                created_at
            )
            VALUES (?, ?, ?, 1, ?)
        """, (
            hotel_name,
            username,
            hash_password(password),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

        conn.commit()

        return True, "ok"

    except sqlite3.IntegrityError:

        return False, "username"

    finally:
        conn.close()


def login_hotel(username, password):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT *
            FROM hotel_accounts
            WHERE LOWER(username) = LOWER(?)
        """, (username,)).fetchone()

        if not row:
            return None, "not_found"

        if row["active"] != 1:
            return None, "disabled"

        if row["password_hash"] != hash_password(password):
            return None, "wrong_password"

        return row, "ok"

    finally:
        conn.close()


def activate_hotel_account(account_id, active):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE hotel_accounts
            SET active = ?
            WHERE id = ?
        """, (
            1 if active else 0,
            account_id,
        ))

        conn.commit()

    finally:
        conn.close()


def get_hotel_accounts():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM hotel_accounts
            ORDER BY hotel_name ASC
        """).fetchall()

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
            ORDER BY name ASC
        """).fetchall()

    finally:
        conn.close()


def add_hotel(name):

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO hotels
            (name, active, created_at)
            VALUES (?, 1, ?)
        """, (
            name,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

        conn.commit()

        return True

    except sqlite3.IntegrityError:

        return False

    finally:
        conn.close()


# =========================================================
# النزلاء
# =========================================================

def save_guest(user_id, hotel_username, data):

    conn = get_db()

    try:

        cursor = conn.execute("""
            INSERT INTO guests
            (
                telegram_id,
                hotel_username,
                full_name,
                mother_name,
                birth_place_date,
                original_residence,
                governorate,
                country,
                hotel_name,
                hotel_area,
                stay_reason,
                check_in_date,
                stay_duration,
                notes,
                front_photo,
                back_photo,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            hotel_username,
            data.get("full_name", ""),
            data.get("mother_name", ""),
            data.get("birth_place_date", ""),
            data.get("original_residence", ""),
            data.get("governorate", ""),
            data.get("country", ""),
            data.get("hotel_name", ""),
            data.get("hotel_area", ""),
            data.get("stay_reason", ""),
            data.get("check_in_date", ""),
            data.get("stay_duration", ""),
            data.get("notes", ""),
            data.get("front_photo", ""),
            data.get("back_photo", ""),
            "pending",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

        conn.commit()

        return cursor.lastrowid

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


def get_pending_count():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT COUNT(*)
            FROM guests
            WHERE status = 'pending'
        """).fetchone()[0]

    finally:
        conn.close()


def get_pending_guests():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM guests
            WHERE status = 'pending'
            ORDER BY id DESC
        """).fetchall()

    finally:
        conn.close()


def mark_guest_received(guest_id):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE guests
            SET status = 'received'
            WHERE id = ?
        """, (guest_id,))

        conn.commit()

    finally:
        conn.close()


# =========================================================
# التقارير
# =========================================================

def get_report_rows(start_date=None, end_date=None):

    conn = get_db()

    try:

        if start_date and end_date:

            rows = conn.execute("""
                SELECT *
                FROM guests
                WHERE DATE(created_at) BETWEEN DATE(?) AND DATE(?)
                ORDER BY id DESC
            """, (
                start_date,
                end_date,
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


def build_statistics(rows):

    governorates = {}
    countries = {}
    hotels = {}
    reasons = {}

    for row in rows:

        gov = row["governorate"] or "غير محدد"
        country = row["country"] or "غير محدد"
        hotel = row["hotel_name"] or "غير محدد"
        reason = row["stay_reason"] or "غير محدد"

        governorates[gov] = governorates.get(gov, 0) + 1
        countries[country] = countries.get(country, 0) + 1
        hotels[hotel] = hotels.get(hotel, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1

    return {
        "governorates": governorates,
        "countries": countries,
        "hotels": hotels,
        "reasons": reasons,
    }


# =========================================================
# لوحات المدير
# =========================================================

def admin_menu():

    count = get_pending_count()

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "➕ إضافة حساب فندق",
                callback_data="admin_add_account"
            )
        ],

        [
            InlineKeyboardButton(
                "🏨 حسابات الفنادق",
                callback_data="admin_accounts"
            )
        ],

        [
            InlineKeyboardButton(
                f"📥 الوارد ({count})",
                callback_data="admin_inbox"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 التقرير اليومي",
                callback_data="report_daily"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 التقرير الشهري",
                callback_data="report_monthly"
            )
        ],

        [
            InlineKeyboardButton(
                "🏨 الفنادق",
                callback_data="admin_hotels"
            )
        ],

        [
            InlineKeyboardButton(
                "🚪 تسجيل خروج",
                callback_data="admin_logout"
            )
        ],
    ])


def back_button(target="admin_menu"):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "↩️ رجوع",
                callback_data=target
            )
        ]
    ])


def hotel_menu():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📝 تسجيل بيانات نزيل",
                callback_data="hotel_add_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "📋 بياناتي المسجلة",
                callback_data="hotel_my_guests"
            )
        ],

        [
            InlineKeyboardButton(
                "🚪 تسجيل خروج",
                callback_data="hotel_logout"
            )
        ],

    ])


# =========================================================
# الترحيب
# =========================================================

WELCOME_TEXT = """
بسم الله الرحمن الرحيم

﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾

السلام عليكم ورحمة الله وبركاته

مرحباً بكم في نظام إدارة بيانات الفنادق.

يرجى اختيار طريقة الدخول من القائمة أدناه.
"""


# =========================================================
# /start
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    register_telegram_user(
        user.id,
        user.username or ""
    )

    context.user_data.clear()

    # -----------------------------------------------------
    # المدير
    # -----------------------------------------------------

    if is_admin(user.id):

        await update.message.reply_text(
            WELCOME_TEXT +
            "\n👑 تم التعرف عليك كمدير للنظام.\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=admin_menu()
        )

        return

    # -----------------------------------------------------
    # هل لديه جلسة فندق؟
    # -----------------------------------------------------

    hotel = get_hotel_session(user.id)

    if hotel:

        await update.message.reply_text(
            WELCOME_TEXT +
            f"\n🏨 الفندق: {hotel['hotel_name']}\n\n"
            "تم تسجيل الدخول مسبقاً.\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_menu()
        )

        return

    # -----------------------------------------------------
    # شاشة الدخول
    # -----------------------------------------------------

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "👑 دخول المدير",
                callback_data="login_admin"
            )
        ],

        [
            InlineKeyboardButton(
                "🏨 دخول الفندق",
                callback_data="login_hotel"
            )
        ],

    ])

    await update.message.reply_text(
        WELCOME_TEXT +
        "\n🔐 اختر نوع الدخول:",
        reply_markup=keyboard
    )


# =========================================================
# إنشاء حساب فندق
# =========================================================

async def show_hotel_selection(update, context):

    query = update.callback_query

    hotels = get_hotels()

    buttons = []

    for hotel in hotels:

        if hotel["active"] == 1:

            buttons.append([
                InlineKeyboardButton(
                    f"🏨 {hotel['name']}",
                    callback_data=f"select_hotel:{hotel['id']}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "➕ إضافة فندق",
            callback_data="add_hotel_name"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "↩️ رجوع",
            callback_data="admin_menu"
        )
    ])

    await safe_edit(
        query,
        "🏨 اختر الفندق الذي تريد إنشاء حساب له:",
        InlineKeyboardMarkup(buttons)
    )


# =========================================================
# إنشاء حساب الفندق - اسم المستخدم
# =========================================================

async def begin_hotel_account(update, context, hotel_name):

    query = update.callback_query

    context.user_data.clear()

    context.user_data["state"] = "create_hotel_username"
    context.user_data["new_hotel_name"] = hotel_name

    await safe_edit(
        query,
        f"🏨 الفندق: {hotel_name}\n\n"
        "👤 أرسل اسم المستخدم الذي تريد إنشاءه لهذا الفندق:",
        back_button("admin_add_account")
    )


# =========================================================
# تسجيل دخول الفندق
# =========================================================

async def hotel_login_start(update, context):

    query = update.callback_query

    context.user_data.clear()

    context.user_data["state"] = "hotel_login_username"

    await safe_edit(
        query,
        "🏨 دخول الفندق\n\n"
        "👤 أرسل اسم المستخدم:",
        back_button("start_login")
    )


# =========================================================
# الدخول
# =========================================================

async def login_admin(update, context):

    query = update.callback_query

    if is_admin(update.effective_user.id):

        await safe_edit(
            query,
            "👑 تم تسجيل دخول المدير بنجاح.\n\n"
            "اختر العملية المطلوبة:",
            admin_menu()
        )

    else:

        await safe_edit(
            query,
            "❌ هذا الحساب ليس حساب المدير.",
            back_button("start_login")
        )


async def show_login_menu(update, context):

    query = update.callback_query

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "👑 دخول المدير",
                callback_data="login_admin"
            )
        ],

        [
            InlineKeyboardButton(
                "🏨 دخول الفندق",
                callback_data="login_hotel"
            )
        ],

        [
            InlineKeyboardButton(
                "↩️ رجوع",
                callback_data="start_login"
            )
        ],
    ])

    await safe_edit(
        query,
        "🔐 اختر نوع الدخول:",
        keyboard
    )


# =========================================================
# حماية callback
# =========================================================

async def callback_handler(update, context):

    query = update.callback_query

    if not query:
        return

    try:
        await query.answer()
    except Exception:
        pass

    user = update.effective_user

    if not user:
        return

    data = query.data or ""

    # -----------------------------------------------------
    # بداية الدخول
    # -----------------------------------------------------

    if data == "start_login":

        context.user_data.clear()

        keyboard = InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "👑 دخول المدير",
                    callback_data="login_admin"
                )
            ],

            [
                InlineKeyboardButton(
                    "🏨 دخول الفندق",
                    callback_data="login_hotel"
                )
            ],

        ])

        await safe_edit(
            query,
            WELCOME_TEXT +
            "\n🔐 اختر نوع الدخول:",
            keyboard
        )

        return

    # -----------------------------------------------------
    # دخول المدير
    # -----------------------------------------------------

    if data == "login_admin":

        await login_admin(update, context)
        return

    # -----------------------------------------------------
    # دخول الفندق
    # -----------------------------------------------------

    if data == "login_hotel":

        await hotel_login_start(update, context)
        return

    # -----------------------------------------------------
    # قائمة المدير
    # -----------------------------------------------------

    if data == "admin_menu":

        if not is_admin(user.id):
            return

        context.user_data.clear()

        await safe_edit(
            query,
            "👑 لوحة تحكم المدير\n\n"
            "اختر العملية المطلوبة:",
            admin_menu()
        )

        return

    # -----------------------------------------------------
    # إضافة حساب فندق
    # -----------------------------------------------------

    if data == "admin_add_account":

        if not is_admin(user.id):
            return

        await show_hotel_selection(update, context)
        return

    # -----------------------------------------------------
    # اختيار فندق
    # -----------------------------------------------------

    if data.startswith("select_hotel:"):

        if not is_admin(user.id):
            return

        try:

            hotel_id = int(
                data.split(":")[1]
            )

        except Exception:

            return

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

        await begin_hotel_account(
            update,
            context,
            hotel["name"]
        )

        return

    # -----------------------------------------------------
    # إضافة فندق جديد
    # -----------------------------------------------------

    if data == "add_hotel_name":

        if not is_admin(user.id):
            return

        context.user_data.clear()
        context.user_data["state"] = "new_hotel_name"

        await safe_edit(
            query,
            "➕ إضافة فندق جديد\n\n"
            "أرسل اسم الفندق:",
            back_button("admin_add_account")
        )

        return

    # -----------------------------------------------------
    # حسابات الفنادق
    # -----------------------------------------------------

    if data == "admin_accounts":

        if not is_admin(user.id):
            return

        accounts = get_hotel_accounts()

        if not accounts:

            text = "🏨 لا توجد حسابات فنادق حتى الآن."

        else:

            text = "🏨 حسابات الفنادق:\n\n"

            for account in accounts:

                status = (
                    "🟢 فعال"
                    if account["active"]
                    else "🔴 معطل"
                )

                text += (
                    f"🏨 {account['hotel_name']}\n"
                    f"👤 المستخدم: {account['username']}\n"
                    f"{status}\n"
                    f"🆔 رقم الحساب: {account['id']}\n\n"
                )

        buttons = []

        for account in accounts:

            if account["active"]:

                buttons.append([
                    InlineKeyboardButton(
                        f"🔴 تعطيل {account['hotel_name']} - {account['username']}",
                        callback_data=f"disable:{account['id']}"
                    )
                ])

            else:

                buttons.append([
                    InlineKeyboardButton(
                        f"🟢 تفعيل {account['hotel_name']} - {account['username']}",
                        callback_data=f"enable:{account['id']}"
                    )
                ])

        buttons.append([
            InlineKeyboardButton(
                "↩️ رجوع",
                callback_data="admin_menu"
            )
        ])

        await safe_edit(
            query,
            text,
            InlineKeyboardMarkup(buttons)
        )

        return

    # -----------------------------------------------------
    # تعطيل
    # -----------------------------------------------------

    if data.startswith("disable:"):

        if not is_admin(user.id):
            return

        account_id = int(data.split(":")[1])

        activate_hotel_account(
            account_id,
            False
        )

        await safe_edit(
            query,
            "🔴 تم تعطيل حساب الفندق بنجاح.",
            back_button("admin_accounts")
        )

        return

    # -----------------------------------------------------
    # تفعيل
    # -----------------------------------------------------

    if data.startswith("enable:"):

        if not is_admin(user.id):
            return

        account_id = int(data.split(":")[1])

        activate_hotel_account(
            account_id,
            True
        )

        await safe_edit(
            query,
            "🟢 تم تفعيل حساب الفندق بنجاح.",
            back_button("admin_accounts")
        )

        return

    # -----------------------------------------------------
    # الوارد
    # -----------------------------------------------------

    if data == "admin_inbox":

        if not is_admin(user.id):
            return

        rows = get_pending_guests()

        count = len(rows)

        if count == 0:

            await safe_edit(
                query,
                "📥 الوارد (0)\n\n"
                "لا توجد طلبات جديدة.",
                back_button("admin_menu")
            )

            return

        buttons = []

        for row in rows:

            buttons.append([
                InlineKeyboardButton(
                    f"👤 {row['full_name']} - {row['hotel_name']}",
                    callback_data=f"inbox:{row['id']}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "↩️ رجوع",
                callback_data="admin_menu"
            )
        ])

        await safe_edit(
            query,
            f"📥 الوارد ({count})\n\n"
            "اختر الطلب لعرض تفاصيله:",
            InlineKeyboardMarkup(buttons)
        )

        return

    # -----------------------------------------------------
    # عرض طلب
    # -----------------------------------------------------

    if data.startswith("inbox:"):

        if not is_admin(user.id):
            return

        guest_id = int(data.split(":")[1])

        guest = get_guest(guest_id)

        if not guest:
            return

        text = format_guest(guest)

        keyboard = InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "📄 إرسال PDF",
                    callback_data=f"pdf:{guest_id}"
                )
            ],

            [
                InlineKeyboardButton(
                    "✅ تعليم كمستلم",
                    callback_data=f"received:{guest_id}"
                )
            ],

            [
                InlineKeyboardButton(
                    "↩️ رجوع",
                    callback_data="admin_inbox"
                )
            ],
        ])

        await safe_edit(
            query,
            text,
            keyboard
        )

        return

    # -----------------------------------------------------
    # تعليم كمستلم
    # -----------------------------------------------------

    if data.startswith("received:"):

        if not is_admin(user.id):
            return

        guest_id = int(data.split(":")[1])

        mark_guest_received(guest_id)

        await safe_edit(
            query,
            "✅ تم تعليم الطلب كمستلم.",
            back_button("admin_inbox")
        )

        return

    # -----------------------------------------------------
    # PDF
    # -----------------------------------------------------

    if data.startswith("pdf:"):

        if not is_admin(user.id):
            return

        guest_id = int(data.split(":")[1])

        guest = get_guest(guest_id)

        if not guest:
            return

        pdf_path = create_guest_pdf(guest)

        try:

            await query.message.reply_document(
                document=InputFile(
                    pdf_path,
                    filename=f"guest_{guest_id}.pdf"
                ),
                caption=(
                    "📄 ملف بيانات النزيل\n"
                    f"👤 {guest['full_name']}\n"
                    f"🏨 {guest['hotel_name']}"
                )
            )

        finally:

            try:
                os.remove(pdf_path)
            except Exception:
                pass

        return

    # -----------------------------------------------------
    # التقرير اليومي
    # -----------------------------------------------------

    if data == "report_daily":

        if not is_admin(user.id):
            return

        today = date.today().strftime("%Y-%m-%d")

        rows = get_report_rows(
            today,
            today
        )

        text = create_report_text(
            rows,
            f"📊 التقرير اليومي\n📅 {today}"
        )

        await safe_edit(
            query,
            text,
            back_button("admin_menu")
        )

        return

    # -----------------------------------------------------
    # التقرير الشهري
    # -----------------------------------------------------

    if data == "report_monthly":

        if not is_admin(user.id):
            return

        today = date.today()

        first_day = today.replace(day=1)

        rows = get_report_rows(
            first_day.strftime("%Y-%m-%d"),
            today.strftime("%Y-%m-%d")
        )

        text = create_report_text(
            rows,
            "📊 التقرير الشهري"
        )

        await safe_edit(
            query,
            text,
            back_button("admin_menu")
        )

        return

    # -----------------------------------------------------
    # الفنادق
    # -----------------------------------------------------

    if data == "admin_hotels":

        if not is_admin(user.id):
            return

        hotels = get_hotels()

        text = "🏨 قائمة الفنادق:\n\n"

        for hotel in hotels:

            status = (
                "🟢 فعال"
                if hotel["active"]
                else "🔴 معطل"
            )

            text += (
                f"• {hotel['name']} — {status}\n"
            )

        await safe_edit(
            query,
            text,
            back_button("admin_menu")
        )

        return

    # -----------------------------------------------------
    # تسجيل خروج المدير
    # -----------------------------------------------------

    if data == "admin_logout":

        context.user_data.clear()

        await safe_edit(
            query,
            "🚪 تم تسجيل الخروج.\n\n"
            "اضغط /start للدخول مرة أخرى.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔐 /start",
                        callback_data="start_login"
                    )
                ]
            ])
        )

        return

    # -----------------------------------------------------
    # تسجيل نزيل للفندق
    # -----------------------------------------------------

    if data == "hotel_add_guest":

        hotel = get_hotel_session(user.id)

        if not hotel:
            return

        context.user_data.clear()

        context.user_data["state"] = "guest_full_name"

        context.user_data["guest"] = {}

        await safe_edit(
            query,
            "📝 تسجيل بيانات نزيل جديد\n\n"
            "1️⃣ الاسم الثلاثي:",
            back_button("hotel_menu")
        )

        return

    # -----------------------------------------------------
    # قائمة الفندق
    # -----------------------------------------------------

    if data == "hotel_menu":

        context.user_data.clear()

        hotel = get_hotel_session(user.id)

        if not hotel:
            return

        await safe_edit(
            query,
            f"🏨 حساب الفندق: {hotel['hotel_name']}\n\n"
            "اختر العملية المطلوبة:",
            hotel_menu()
        )

        return

    # -----------------------------------------------------
    # بيانات الفندق السابقة
    # -----------------------------------------------------

    if data == "hotel_my_guests":

        hotel = get_hotel_session(user.id)

        if not hotel:
            return

        conn = get_db()

        try:

            rows = conn.execute("""
                SELECT
                    id,
                    full_name,
                    created_at
                FROM guests
                WHERE telegram_id = ?
                ORDER BY id DESC
                LIMIT 20
            """, (user.id,)).fetchall()

        finally:

            conn.close()

        if not rows:

            text = "📋 لا توجد بيانات مسجلة من حسابك."

        else:

            text = "📋 آخر البيانات التي قمت بتسجيلها:\n\n"

            for row in rows:

                text += (
                    f"🆔 {row['id']}\n"
                    f"👤 {row['full_name']}\n"
                    f"🕐 {row['created_at']}\n\n"
                )

        await safe_edit(
            query,
            text,
            back_button("hotel_menu")
        )

        return

    # -----------------------------------------------------
    # خروج الفندق
    # -----------------------------------------------------

    if data == "hotel_logout":

        logout_user(user.id)

        context.user_data.clear()

        await safe_edit(
            query,
            "🚪 تم تسجيل خروج حساب الفندق.\n\n"
            "اضغط /start للدخول مرة أخرى.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔐 دخول",
                        callback_data="start_login"
                    )
                ]
            ])
        )

        return

    # -----------------------------------------------------
    # إلغاء
    # -----------------------------------------------------

    if data == "cancel":

        context.user_data.clear()

        if is_admin(user.id):

            await safe_edit(
                query,
                "👑 تم إلغاء العملية.",
                admin_menu()
            )

        else:

            hotel = get_hotel_session(user.id)

            if hotel:

                await safe_edit(
                    query,
                    "❌ تم إلغاء العملية.",
                    hotel_menu()
                )

            else:

                await safe_edit(
                    query,
                    "❌ تم إلغاء العملية.",
                    back_button("start_login")
                )

        return


# =========================================================
# معالجة الرسائل
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
    # إنشاء فندق جديد
    # =====================================================

    if state == "new_hotel_name":

        if not is_admin(user.id):
            return

        if len(text) < 2:

            await update.message.reply_text(
                "❌ اسم الفندق قصير جداً.\n\n"
                "أرسل اسم الفندق:"
            )

            return

        if add_hotel(text):

            context.user_data.clear()

            await update.message.reply_text(
                f"✅ تمت إضافة الفندق بنجاح:\n\n"
                f"🏨 {text}",
                reply_markup=admin_menu()
            )

        else:

            await update.message.reply_text(
                "❌ هذا الفندق موجود مسبقاً.\n\n"
                "أرسل اسم فندق آخر:"
            )

        return

    # =====================================================
    # إنشاء حساب الفندق - username
    # =====================================================

    if state == "create_hotel_username":

        if not is_admin(user.id):
            return

        username = text.replace(" ", "")

        if len(username) < 3:

            await update.message.reply_text(
                "❌ اسم المستخدم يجب أن يكون 3 أحرف على الأقل."
            )

            return

        context.user_data["new_username"] = username

        context.user_data["state"] = "create_hotel_password"

        await update.message.reply_text(
            "🔐 الآن أرسل كلمة المرور لحساب الفندق:"
        )

        return

    # =====================================================
    # إنشاء حساب الفندق - password
    # =====================================================

    if state == "create_hotel_password":

        if not is_admin(user.id):
            return

        password = text

        if len(password) < 4:

            await update.message.reply_text(
                "❌ كلمة المرور يجب أن تكون 4 أحرف أو أرقام على الأقل."
            )

            return

        hotel_name = context.user_data.get(
            "new_hotel_name",
            ""
        )

        username = context.user_data.get(
            "new_username",
            ""
        )

        success, reason = create_hotel_account(
            hotel_name,
            username,
            password
        )

        if not success:

            if reason == "username":

                await update.message.reply_text(
                    "❌ اسم المستخدم مستخدم مسبقاً.\n\n"
                    "أرسل اسم مستخدم آخر:"
                )

                context.user_data["state"] = "create_hotel_username"

                return

        context.user_data.clear()

        await update.message.reply_text(
            "✅ تم إنشاء حساب الفندق بنجاح.\n\n"
            f"🏨 الفندق: {hotel_name}\n"
            f"👤 اسم المستخدم: {username}\n"
            f"🔐 كلمة المرور: {password}\n\n"
            "⚠️ احتفظ بهذه البيانات وأرسلها لمسؤول الفندق.",
            reply_markup=admin_menu()
        )

        return

    # =====================================================
    # تسجيل دخول الفندق - username
    # =====================================================

    if state == "hotel_login_username":

        context.user_data["login_username"] = text

        context.user_data["state"] = "hotel_login_password"

        await update.message.reply_text(
            "🔐 أرسل كلمة المرور:"
        )

        return

    # =====================================================
    # تسجيل دخول الفندق - password
    # =====================================================

    if state == "hotel_login_password":

        username = context.user_data.get(
            "login_username",
            ""
        )

        password = text

        account, status = login_hotel(
            username,
            password
        )

        if status == "not_found":

            await update.message.reply_text(
                "❌ اسم المستخدم غير موجود.\n\n"
                "اضغط /start للمحاولة مرة أخرى."
            )

            context.user_data.clear()

            return

        if status == "disabled":

            await update.message.reply_text(
                "🔴 هذا الحساب معطل من قبل الإدارة.\n\n"
                "يرجى التواصل مع الإدارة."
            )

            context.user_data.clear()

            return

        if status == "wrong_password":

            await update.message.reply_text(
                "❌ كلمة المرور غير صحيحة."
            )

            return

        # إنشاء جلسة الفندق
        conn = get_db()

        try:

            conn.execute("""
                UPDATE users
                SET
                    role = 'hotel',
                    hotel_name = ?,
                    logged_in = 1,
                    login_time = ?
                WHERE telegram_id = ?
            """, (
                account["hotel_name"],
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user.id,
            ))

            conn.commit()

        finally:

            conn.close()

        context.user_data.clear()

        await update.message.reply_text(
            "✅ تم تسجيل الدخول بنجاح.\n\n"
            f"🏨 الفندق: {account['hotel_name']}\n\n"
            "يمكنك الآن تسجيل بيانات النزلاء.",
            reply_markup=hotel_menu()
        )

        return

    # =====================================================
    # بيانات النزيل
    # =====================================================

    if state == "guest_full_name":

        context.user_data["guest"]["full_name"] = text
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
        context.user_data["state"] = "guest_country"

        await update.message.reply_text(
            "6️⃣ الدولة:"
        )

        return

    if state == "guest_country":

        context.user_data["guest"]["country"] = text
        context.user_data["state"] = "guest_hotel"

        hotel = get_hotel_session(user.id)

        hotel_name = (
            hotel["hotel_name"]
            if hotel
            else ""
        )

        context.user_data["guest"]["hotel_name"] = hotel_name

        await update.message.reply_text(
            "7️⃣ اسم الفندق:\n\n"
            f"🏨 سيتم تسجيله باسم: {hotel_name}"
        )

        context.user_data["state"] = "guest_hotel_area"

        return

    if state == "guest_hotel_area":

        context.user_data["guest"]["hotel_area"] = text
        context.user_data["state"] = "guest_reason"

        await update.message.reply_text(
            "8️⃣ سبب الإقامة:"
        )

        return

    if state == "guest_reason":

        context.user_data["guest"]["stay_reason"] = text
        context.user_data["state"] = "guest_checkin"

        await update.message.reply_text(
            "9️⃣ تاريخ النزول:"
        )

        return

    if state == "guest_checkin":

        context.user_data["guest"]["check_in_date"] = text
        context.user_data["state"] = "guest_duration"

        await update.message.reply_text(
            "🔟 مدة الإقامة:"
        )

        return

    if state == "guest_duration":

        context.user_data["guest"]["stay_duration"] = text
        context.user_data["state"] = "guest_notes"

        await update.message.reply_text(
            "1️⃣1️⃣ ملاحظات عامة:\n\n"
            "إذا لا توجد ملاحظات اكتب: لا يوجد"
        )

        return

    if state == "guest_notes":

        context.user_data["guest"]["notes"] = text
        context.user_data["state"] = "guest_front_photo"

        await update.message.reply_text(
            "1️⃣2️⃣ أرسل صورة الهوية الشخصية من الجهة الأمامية:"
        )

        return

    # الصور تتم معالجتها في photo_handler
    # =====================================================

    await update.message.reply_text(
        "ℹ️ استخدم أزرار القائمة.\n\n"
        "اضغط /start للعودة."
    )


# =========================================================
# معالجة الصور
# =========================================================

async def photo_handler(update, context):

    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    state = context.user_data.get("state")

    if state not in (
        "guest_front_photo",
        "guest_back_photo",
    ):
        return

    photo = update.message.photo

    if not photo:
        return

    largest = photo[-1]

    file = await context.bot.get_file(
        largest.file_id
    )

    # حفظ مؤقت
    filename = (
        f"guest_{user.id}_"
        f"{'front' if state == 'guest_front_photo' else 'back'}.jpg"
    )

    await file.download_to_drive(filename)

    if state == "guest_front_photo":

        context.user_data["guest"]["front_photo"] = filename

        context.user_data["state"] = "guest_back_photo"

        await update.message.reply_text(
            "✅ تم استلام صورة الهوية الأمامية.\n\n"
            "1️⃣3️⃣ الآن أرسل صورة الهوية من الجهة الخلفية:"
        )

        return

    context.user_data["guest"]["back_photo"] = filename

    await update.message.reply_text(
        "✅ تم استلام صورة الهوية الخلفية.\n\n"
        "⏳ جاري تجهيز البيانات..."
    )

    await finish_guest_registration(
        update,
        context
    )


# =========================================================
# إنهاء تسجيل النزيل
# =========================================================

async def finish_guest_registration(update, context):

    user = update.effective_user

    hotel = get_hotel_session(user.id)

    if not hotel:

        await update.message.reply_text(
            "❌ انتهت جلسة الفندق.\n"
            "اضغط /start."
        )

        context.user_data.clear()

        return

    data = context.user_data["guest"]

    guest_id = save_guest(
        user.id,
        hotel["username"],
        data
    )

    context.user_data["last_guest_id"] = guest_id
    context.user_data["state"] = "guest_finished"

    await update.message.reply_text(
        "✅ تم حفظ بيانات النزيل بنجاح.\n\n"
        f"🆔 رقم الطلب: {guest_id}\n"
        f"👤 الاسم: {data['full_name']}\n"
        f"🏨 الفندق: {hotel['hotel_name']}\n\n"
        "يمكنك الآن مراجعة البيانات أو إرسالها للإدارة.",
        reply_markup=InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "👁️ عرض البيانات",
                    callback_data=f"view_guest:{guest_id}"
                )
            ],

            [
                InlineKeyboardButton(
                    "📤 إرسال للإدارة",
                    callback_data=f"send_guest:{guest_id}"
                )
            ],

            [
                InlineKeyboardButton(
                    "↩️ القائمة الرئيسية",
                    callback_data="hotel_menu"
                )
            ],

        ])
    )


# =========================================================
# عرض وإرسال البيانات
# =========================================================

async def guest_extra_callback(update, context):

    query = update.callback_query
    user = update.effective_user

    data = query.data

    if data.startswith("view_guest:"):

        guest_id = int(data.split(":")[1])

        guest = get_guest(guest_id)

        if not guest:
            return

        await safe_edit(
            query,
            format_guest(guest),
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📤 إرسال للإدارة",
                        callback_data=f"send_guest:{guest_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "↩️ رجوع",
                        callback_data="hotel_menu"
                    )
                ],
            ])
        )

        return

    if data.startswith("send_guest:"):

        guest_id = int(data.split(":")[1])

        guest = get_guest(guest_id)

        if not guest:
            return

        # إنشاء PDF
        pdf_path = create_guest_pdf(guest)

        # إرسال للإدارة
        if ADMIN_ID:

            try:

                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=InputFile(
                        pdf_path,
                        filename=f"guest_{guest_id}.pdf"
                    ),
                    caption=(
                        "📥 طلب جديد من الفندق\n\n"
                        f"🆔 رقم الطلب: {guest_id}\n"
                        f"🏨 الفندق: {guest['hotel_name']}\n"
                        f"👤 النزيل: {guest['full_name']}"
                    )
                )

            finally:

                try:
                    os.remove(pdf_path)
                except Exception:
                    pass

        await query.message.reply_text(
            "✅ تم إرسال بيانات النزيل إلى الإدارة بنجاح.\n\n"
            f"🆔 رقم الطلب: {guest_id}",
            reply_markup=hotel_menu()
        )

        return


# =========================================================
# تنسيق بيانات النزيل
# =========================================================

def format_guest(guest):

    return (
        "📋 بيانات النزيل\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 رقم الطلب: {guest['id']}\n"
        f"👤 الاسم الثلاثي: {guest['full_name']}\n"
        f"👩 اسم الأم: {guest['mother_name']}\n"
        f"📍 مكان وتاريخ الولادة: {guest['birth_place_date']}\n"
        f"🏠 السكن الأصلي: {guest['original_residence']}\n"
        f"🏛 المحافظة: {guest['governorate']}\n"
        f"🌍 الدولة: {guest['country']}\n"
        f"🏨 الفندق: {guest['hotel_name']}\n"
        f"📍 منطقة الفندق: {guest['hotel_area']}\n"
        f"📝 سبب الإقامة: {guest['stay_reason']}\n"
        f"📅 تاريخ النزول: {guest['check_in_date']}\n"
        f"⏳ مدة الإقامة: {guest['stay_duration']}\n"
        f"📌 الملاحظات: {guest['notes']}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕐 تاريخ التسجيل: {guest['created_at']}"
    )


# =========================================================
# نص التقرير
# =========================================================

def create_report_text(rows, title):

    stats = build_statistics(rows)

    text = (
        f"{title}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 إجمالي النزلاء: {len(rows)}\n\n"
    )

    # المحافظات
    text += "🏛 التوزيع حسب المحافظات:\n"

    if stats["governorates"]:

        for name, count in sorted(
            stats["governorates"].items(),
            key=lambda x: x[1],
            reverse=True
        ):

            text += f"• {name}: {count}\n"

    else:

        text += "• لا توجد بيانات\n"

    text += "\n🌍 التوزيع حسب الدول:\n"

    if stats["countries"]:

        for name, count in sorted(
            stats["countries"].items(),
            key=lambda x: x[1],
            reverse=True
        ):

            text += f"• {name}: {count}\n"

    else:

        text += "• لا توجد بيانات\n"

    text += "\n🏨 التوزيع حسب الفنادق:\n"

    if stats["hotels"]:

        for name, count in sorted(
            stats["hotels"].items(),
            key=lambda x: x[1],
            reverse=True
        ):

            text += f"• {name}: {count}\n"

    else:

        text += "• لا توجد بيانات\n"

    text += "\n📝 أسباب الإقامة:\n"

    if stats["reasons"]:

        for name, count in sorted(
            stats["reasons"].items(),
            key=lambda x: x[1],
            reverse=True
        ):

            text += f"• {name}: {count}\n"

    else:

        text += "• لا توجد بيانات\n"

    return text


# =========================================================
# إنشاء PDF
# =========================================================

def create_guest_pdf(guest):

    filename = (
        f"/tmp/guest_{guest['id']}_"
        f"{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
    )

    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ArabicTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=18,
        leading=24,
        textColor=colors.HexColor("#17365D"),
    )

    normal_style = ParagraphStyle(
        "ArabicNormal",
        parent=styles["Normal"],
        alignment=TA_RIGHT,
        fontSize=10,
        leading=17,
    )

    small_style = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=8,
        leading=12,
    )

    story = []

    # رأس التقرير
    story.append(
        Paragraph(
            "نظام إدارة معلومات الفنادق",
            title_style
        )
    )

    story.append(
        Paragraph(
            "تقرير بيانات نزيل",
            ParagraphStyle(
                "SubTitle",
                parent=title_style,
                fontSize=13,
                textColor=colors.HexColor("#555555"),
            )
        )
    )

    story.append(Spacer(1, 8 * mm))

    # رقم الطلب
    header_table = Table([
        [
            Paragraph(
                f"<b>رقم الطلب:</b> {guest['id']}",
                normal_style
            ),
            Paragraph(
                f"<b>تاريخ التسجيل:</b> {guest['created_at']}",
                normal_style
            ),
        ]
    ], colWidths=[85 * mm, 85 * mm])

    header_table.setStyle(TableStyle([
        (
            "BACKGROUND",
            (0, 0),
            (-1, -1),
            colors.HexColor("#EAF2F8")
        ),
        (
            "BOX",
            (0, 0),
            (-1, -1),
            0.7,
            colors.HexColor("#17365D")
        ),
        (
            "INNERGRID",
            (0, 0),
            (-1, -1),
            0.3,
            colors.grey
        ),
        (
            "VALIGN",
            (0, 0),
            (-1, -1),
            "MIDDLE"
        ),
        (
            "TOPPADDING",
            (0, 0),
            (-1, -1),
            7
        ),
        (
            "BOTTOMPADDING",
            (0, 0),
            (-1, -1),
            7
        ),
    ]))

    story.append(header_table)

    story.append(Spacer(1, 8 * mm))

    # البيانات
    data = [
        ["البيان", "المعلومات"],

        ["الاسم الثلاثي", guest["full_name"]],
        ["اسم الأم", guest["mother_name"]],
        ["مكان وتاريخ الولادة", guest["birth_place_date"]],
        ["السكن الأصلي", guest["original_residence"]],
        ["المحافظة", guest["governorate"]],
        ["الدولة", guest["country"]],
        ["اسم الفندق", guest["hotel_name"]],
        ["منطقة الفندق", guest["hotel_area"]],
        ["سبب الإقامة", guest["stay_reason"]],
        ["تاريخ النزول", guest["check_in_date"]],
        ["مدة الإقامة", guest["stay_duration"]],
        ["ملاحظات عامة", guest["notes"]],
    ]

    table = Table(
        data,
        colWidths=[55 * mm, 115 * mm],
        repeatRows=1
    )

    table.setStyle(TableStyle([

        (
            "BACKGROUND",
            (0, 0),
            (-1, 0),
            colors.HexColor("#17365D")
        ),

        (
            "TEXTCOLOR",
            (0, 0),
            (-1, 0),
            colors.white
        ),

        (
            "FONTNAME",
            (0, 0),
            (-1, 0),
            "Helvetica-Bold"
        ),

        (
            "GRID",
            (0, 0),
            (-1, -1),
            0.5,
            colors.HexColor("#AAAAAA")
        ),

        (
            "BACKGROUND",
            (0, 1),
            (0, -1),
            colors.HexColor("#F2F2F2")
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
            7
        ),

        (
            "BOTTOMPADDING",
            (0, 0),
            (-1, -1),
            7
        ),
    ]))

    story.append(table)

    story.append(PageBreak())

    # الهوية
    story.append(
        Paragraph(
            "صور الهوية الشخصية",
            title_style
        )
    )

    story.append(Spacer(1, 8 * mm))

    # الأمامية
    front = guest["front_photo"]

    if front and os.path.exists(front):

        story.append(
            Paragraph(
                "الجهة الأمامية",
                normal_style
            )
        )

        story.append(Spacer(1, 3 * mm))

        try:

            img = PDFImage(
                front,
                width=160 * mm,
                height=100 * mm
            )

            story.append(img)

        except Exception as e:

            story.append(
                Paragraph(
                    "تعذر إدراج الصورة الأمامية.",
                    normal_style
                )
            )

    story.append(Spacer(1, 10 * mm))

    # الخلفية
    back = guest["back_photo"]

    if back and os.path.exists(back):

        story.append(
            Paragraph(
                "الجهة الخلفية",
                normal_style
            )
        )

        story.append(Spacer(1, 3 * mm))

        try:

            img = PDFImage(
                back,
                width=160 * mm,
                height=100 * mm
            )

            story.append(img)

        except Exception:

            story.append(
                Paragraph(
                    "تعذر إدراج الصورة الخلفية.",
                    normal_style
                )
            )

    story.append(Spacer(1, 10 * mm))

    story.append(
        Paragraph(
            "هذا المستند مولد آلياً بواسطة نظام إدارة معلومات الفنادق.",
            small_style
        )
    )

    doc.build(story)

    return filename


# =========================================================
# safe_edit
# إصلاح خطأ:
# There is no text in the message to edit
# =========================================================

async def safe_edit(query, text, reply_markup=None):

    try:

        # إذا كانت الرسالة تحتوي نصاً
        if query.message and query.message.text is not None:

            await query.edit_message_text(
                text=text,
                reply_markup=reply_markup
            )

        else:

            await query.message.reply_text(
                text=text,
                reply_markup=reply_markup
            )

    except Exception as e:

        error_text = str(e)

        if "There is no text in the message to edit" in error_text:

            await query.message.reply_text(
                text=text,
                reply_markup=reply_markup
            )

        else:

            logger.exception(
                "خطأ في تعديل الرسالة"
            )


# =========================================================
# HTTP Server لـ Render
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

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    logger.info(
        f"HTTP server running on port {PORT}"
    )

    server.serve_forever()


# =========================================================
# معالج عام إضافي للأزرار الخاصة بالنزيل
# =========================================================

async def guest_callback_router(update, context):

    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    if (
        data.startswith("view_guest:")
        or data.startswith("send_guest:")
    ):

        try:
            await query.answer()
        except Exception:
            pass

        await guest_extra_callback(
            update,
            context
        )


# =========================================================
# الأخطاء
# =========================================================

async def error_handler(update, context):

    logger.error(
        "حدث خطأ:",
        exc_info=context.error
    )


# =========================================================
# تشغيل البوت
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
    # قاعدة البيانات
    # -----------------------------------------------------

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
    # HTTP Server
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
            .build()
        )

    except Exception:

        logger.exception(
            "❌ فشل إنشاء البوت"
        )

        return

    # -----------------------------------------------------
    # /start فقط
    # -----------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # -----------------------------------------------------
    # Callback الرئيسي
    # -----------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            callback_handler,
            pattern=r"^(?!view_guest:|send_guest:).+"
        )
    )

    # -----------------------------------------------------
    # Callback بيانات النزيل
    # -----------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            guest_callback_router,
            pattern=r"^(view_guest:|send_guest:)"
        )
    )

    # -----------------------------------------------------
    # الصور
    # -----------------------------------------------------

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler
        )
    )

    # -----------------------------------------------------
    # الرسائل النصية
    # -----------------------------------------------------

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        )
    )

    # -----------------------------------------------------
    # الأخطاء
    # -----------------------------------------------------

    app.add_error_handler(
        error_handler
    )

    # -----------------------------------------------------
    # التشغيل
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
