import os
import sqlite3
import logging
import threading
import hashlib
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, HTTPServer

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
# إعدادات عامة
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip())
except ValueError:
    ADMIN_ID = 0

DB_FILE = "hotel_bot.db"

# صورة الترحيب
WELCOME_IMAGE = "welcome.jpg"

# مجلد مؤقت للصور وملفات PDF
FILES_DIR = "files"
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


def hash_password(password):
    return hashlib.sha256(
        password.encode("utf-8")
    ).hexdigest()


def init_db():

    conn = get_db()

    try:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS hotels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS hotel_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hotel_id INTEGER NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                telegram_id INTEGER UNIQUE,
                active INTEGER DEFAULT 1,
                created_at TEXT,
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

                id_front TEXT,
                id_back TEXT,

                created_at TEXT,

                FOREIGN KEY(hotel_id) REFERENCES hotels(id)
            )
        """)

        conn.commit()

        # -------------------------------------------------
        # إضافة الفنادق الافتراضية
        # -------------------------------------------------

        for hotel in DEFAULT_HOTELS:

            conn.execute("""
                INSERT OR IGNORE INTO hotels
                (
                    name,
                    active,
                    created_at
                )
                VALUES (?, 1, ?)
            """, (
                hotel,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))

        conn.commit()

        logger.info("✅ قاعدة البيانات جاهزة")

    finally:
        conn.close()


# =========================================================
# الفنادق
# =========================================================

def get_hotels(active_only=False):

    conn = get_db()

    try:

        if active_only:

            return conn.execute("""
                SELECT *
                FROM hotels
                WHERE active = 1
                ORDER BY name
            """).fetchall()

        return conn.execute("""
            SELECT *
            FROM hotels
            ORDER BY name
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
        """, (
            hotel_id,
        )).fetchone()

    finally:
        conn.close()


def get_hotel_by_name(name):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM hotels
            WHERE name = ?
        """, (
            name,
        )).fetchone()

    finally:
        conn.close()


def add_hotel(name):

    name = name.strip()

    if not name:
        return False

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO hotels
            (
                name,
                active,
                created_at
            )
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
# حسابات الفنادق
# =========================================================

def create_hotel_account(
    hotel_id,
    username,
    password
):

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO hotel_accounts
            (
                hotel_id,
                username,
                password_hash,
                active,
                created_at
            )
            VALUES (?, ?, ?, 1, ?)
        """, (
            hotel_id,
            username.strip(),
            hash_password(password),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

        conn.commit()

        return True

    except sqlite3.IntegrityError:

        return False

    finally:
        conn.close()


def get_account(username):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT
                a.*,
                h.name AS hotel_name,
                h.active AS hotel_active
            FROM hotel_accounts a
            JOIN hotels h
                ON h.id = a.hotel_id
            WHERE a.username = ?
        """, (
            username,
        )).fetchone()

    finally:
        conn.close()


def get_account_by_telegram(user_id):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT
                a.*,
                h.name AS hotel_name,
                h.active AS hotel_active
            FROM hotel_accounts a
            JOIN hotels h
                ON h.id = a.hotel_id
            WHERE a.telegram_id = ?
        """, (
            user_id,
        )).fetchone()

    finally:
        conn.close()


def set_account_telegram_id(account_id, telegram_id):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE hotel_accounts
            SET telegram_id = ?
            WHERE id = ?
        """, (
            telegram_id,
            account_id,
        ))

        conn.commit()

    finally:
        conn.close()


def set_hotel_account_status(account_id, active):

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


def get_all_accounts():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT
                a.*,
                h.name AS hotel_name,
                h.active AS hotel_active
            FROM hotel_accounts a
            JOIN hotels h
                ON h.id = a.hotel_id
            ORDER BY a.id DESC
        """).fetchall()

    finally:
        conn.close()


# =========================================================
# بيانات النزلاء
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

                id_front,
                id_back,

                created_at
            )
            VALUES (
                ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?
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

            data.get("id_front", ""),
            data.get("id_back", ""),

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
            SELECT
                g.*,
                h.name AS account_hotel
            FROM guests g
            LEFT JOIN hotels h
                ON h.id = g.hotel_id
            WHERE g.id = ?
        """, (
            guest_id,
        )).fetchone()

    finally:
        conn.close()


def get_latest_guest_for_user(user_id):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT
                g.*,
                h.name AS account_hotel
            FROM guests g
            LEFT JOIN hotels h
                ON h.id = g.hotel_id
            WHERE g.telegram_id = ?
            ORDER BY g.id DESC
            LIMIT 1
        """, (
            user_id,
        )).fetchone()

    finally:
        conn.close()


# =========================================================
# التقارير
# =========================================================

def get_report_rows(start_date=None, end_date=None):

    conn = get_db()

    try:

        query = """
            SELECT *
            FROM guests
            WHERE 1 = 1
        """

        params = []

        if start_date:

            query += """
                AND date(created_at) >= date(?)
            """

            params.append(start_date)

        if end_date:

            query += """
                AND date(created_at) <= date(?)
            """

            params.append(end_date)

        query += """
            ORDER BY id DESC
        """

        return conn.execute(
            query,
            params
        ).fetchall()

    finally:
        conn.close()


def count_items(rows, field):

    result = {}

    for row in rows:

        value = row[field] or "غير محدد"

        result[value] = result.get(value, 0) + 1

    return sorted(
        result.items(),
        key=lambda x: (-x[1], x[0])
    )


# =========================================================
# الصلاحيات
# =========================================================

def is_admin(user_id):

    return user_id == ADMIN_ID


def get_logged_hotel(context):

    return context.user_data.get(
        "hotel_account"
    )


def hotel_access(context):

    account = get_logged_hotel(context)

    if not account:
        return False

    current = get_account(
        account["username"]
    )

    if not current:
        return False

    if current["active"] != 1:
        return False

    if current["hotel_active"] != 1:
        return False

    return True


# =========================================================
# لوحات المدير
# =========================================================

def admin_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "➕ إضافة حساب فندق",
                callback_data="add_account"
            )
        ],

        [
            InlineKeyboardButton(
                "👥 حسابات الفنادق",
                callback_data="accounts"
            )
        ],

        [
            InlineKeyboardButton(
                "🏨 إدارة الفنادق",
                callback_data="manage_hotels"
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
                "📅 التقرير الشهري",
                callback_data="monthly_report"
            )
        ],

        [
            InlineKeyboardButton(
                "📋 آخر السجلات",
                callback_data="admin_records"
            )
        ],

        [
            InlineKeyboardButton(
                "🚪 تسجيل خروج",
                callback_data="admin_logout"
            )
        ],
    ])


# =========================================================
# لوحة الفندق
# =========================================================

def hotel_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📝 تسجيل بيانات النزيل",
                callback_data="add_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "👁️ عرض آخر بيانات سجلتها",
                callback_data="my_last_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "🚪 تسجيل خروج",
                callback_data="hotel_logout"
            )
        ],
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
# لوحة اختيار الفنادق
# =========================================================

def hotel_selection_keyboard():

    hotels = get_hotels(active_only=True)

    buttons = []

    for hotel in hotels:

        buttons.append([
            InlineKeyboardButton(
                f"🏨 {hotel['name']}",
                callback_data=f"select_hotel_{hotel['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "➕ إضافة فندق جديد",
            callback_data="new_hotel"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ رجوع",
            callback_data="admin_menu"
        )
    ])

    return InlineKeyboardMarkup(buttons)


# =========================================================
# رسالة الترحيب
# =========================================================

async def send_welcome(update, text, keyboard=None):

    if not update.message:
        return

    if os.path.exists(WELCOME_IMAGE):

        try:

            with open(WELCOME_IMAGE, "rb") as photo:

                await update.message.reply_photo(
                    photo=photo,
                    caption=text,
                    reply_markup=keyboard
                )

            return

        except Exception:

            logger.exception(
                "فشل إرسال صورة الترحيب"
            )

    await update.message.reply_text(
        text,
        reply_markup=keyboard
    )


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

    context.user_data.clear()

    # -----------------------------------------------------
    # المدير
    # -----------------------------------------------------

    if is_admin(user.id):

        await send_welcome(
            update,

            "👑 أهلاً وسهلاً بك أيها المدير\n\n"
            "🛡️ نظام إدارة معلومات الفنادق\n\n"
            "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
            "يمكنك من هنا إدارة حسابات الفنادق "
            "ومتابعة التقارير والسجلات.",

            admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # حساب فندق مسجل مسبقاً
    # -----------------------------------------------------

    account = get_account_by_telegram(
        user.id
    )

    if account:

        if (
            account["active"] != 1
            or account["hotel_active"] != 1
        ):

            await update.message.reply_text(
                "🔴 حساب الفندق معطل حالياً.\n\n"
                "يرجى التواصل مع الإدارة."
            )

            return

        context.user_data[
            "hotel_account"
        ] = dict(account)

        await update.message.reply_text(
            f"👋 أهلاً بك\n\n"
            f"🏨 الفندق: {account['hotel_name']}\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_keyboard()
        )

        return

    # -----------------------------------------------------
    # تسجيل دخول
    # -----------------------------------------------------

    await send_welcome(
        update,

        "🌹 مرحباً بك في نظام إدارة معلومات الفنادق\n\n"
        "🛡️ نظام مخصص لتسجيل بيانات النزلاء وإرسالها للإدارة.\n\n"
        "📖 ﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
        "🔐 للمتابعة، قم بتسجيل الدخول باسم المستخدم "
        "وكلمة المرور التي أنشأتها الإدارة.",

        InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔐 تسجيل الدخول",
                    callback_data="login"
                )
            ]
        ])
    )


# =========================================================
# تسجيل الدخول
# =========================================================

async def login_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    context.user_data.clear()

    context.user_data[
        "state"
    ] = "login_username"

    await query.edit_message_text(
        "🔐 تسجيل دخول حساب الفندق\n\n"
        "1️⃣ أرسل اسم المستخدم:",
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

    text = (
        update.message.text or ""
    ).strip()

    state = context.user_data.get(
        "state"
    )

    # =====================================================
    # تسجيل الدخول - اسم المستخدم
    # =====================================================

    if state == "login_username":

        account = get_account(text)

        if not account:

            await update.message.reply_text(
                "❌ اسم المستخدم غير موجود.\n\n"
                "أرسل اسم المستخدم مرة أخرى:"
            )

            return

        if (
            account["active"] != 1
            or account["hotel_active"] != 1
        ):

            await update.message.reply_text(
                "🔴 هذا الحساب معطل حالياً.\n\n"
                "يرجى التواصل مع الإدارة."
            )

            context.user_data.clear()

            return

        context.user_data[
            "login_username"
        ] = text

        context.user_data[
            "state"
        ] = "login_password"

        await update.message.reply_text(
            "2️⃣ أرسل كلمة المرور:"
        )

        return

    # =====================================================
    # تسجيل الدخول - كلمة المرور
    # =====================================================

    if state == "login_password":

        username = context.user_data.get(
            "login_username"
        )

        account = get_account(
            username
        )

        if not account:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ حدث خطأ.\n"
                "اضغط /start وحاول مرة أخرى."
            )

            return

        if (
            account["active"] != 1
            or account["hotel_active"] != 1
        ):

            context.user_data.clear()

            await update.message.reply_text(
                "🔴 هذا الحساب معطل."
            )

            return

        if hash_password(text) != account["password_hash"]:

            await update.message.reply_text(
                "❌ كلمة المرور غير صحيحة.\n\n"
                "حاول مرة أخرى:"
            )

            return

        set_account_telegram_id(
            account["id"],
            user.id
        )

        context.user_data.clear()

        account = get_account(
            username
        )

        context.user_data[
            "hotel_account"
        ] = dict(account)

        await update.message.reply_text(
            "✅ تم تسجيل الدخول بنجاح.\n\n"
            f"🏨 الفندق: {account['hotel_name']}\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_keyboard()
        )

        return

    # =====================================================
    # إضافة فندق
    # =====================================================

    if state == "new_hotel":

        if not is_admin(user.id):

            return

        if add_hotel(text):

            context.user_data.clear()

            await update.message.reply_text(
                "✅ تمت إضافة الفندق بنجاح.\n\n"
                f"🏨 {text}",
                reply_markup=admin_keyboard()
            )

        else:

            await update.message.reply_text(
                "❌ لم تتم إضافة الفندق.\n\n"
                "قد يكون الاسم موجوداً مسبقاً."
            )

        return

    # =====================================================
    # إنشاء حساب فندق - اسم المستخدم
    # =====================================================

    if state == "create_account_username":

        username = text

        if len(username) < 3:

            await update.message.reply_text(
                "❌ اسم المستخدم يجب أن يكون 3 أحرف على الأقل."
            )

            return

        if get_account(username):

            await update.message.reply_text(
                "❌ اسم المستخدم موجود مسبقاً.\n\n"
                "اختر اسم مستخدم آخر:"
            )

            return

        context.user_data[
            "new_account_username"
        ] = username

        context.user_data[
            "state"
        ] = "create_account_password"

        await update.message.reply_text(
            "🔑 أرسل كلمة المرور الجديدة:"
        )

        return

    # =====================================================
    # إنشاء حساب فندق - كلمة المرور
    # =====================================================

    if state == "create_account_password":

        if len(text) < 4:

            await update.message.reply_text(
                "❌ كلمة المرور يجب أن تكون 4 أحرف على الأقل."
            )

            return

        context.user_data[
            "new_account_password"
        ] = text

        hotel_id = context.user_data.get(
            "new_account_hotel_id"
        )

        username = context.user_data.get(
            "new_account_username"
        )

        if not hotel_id or not username:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ حدث خطأ.\n"
                "ابدأ العملية من جديد.",
                reply_markup=admin_keyboard()
            )

            return

        success = create_hotel_account(
            hotel_id,
            username,
            text
        )

        if success:

            hotel = get_hotel(
                hotel_id
            )

            context.user_data.clear()

            await update.message.reply_text(
                "✅ تم إنشاء حساب الفندق بنجاح.\n\n"
                f"🏨 الفندق: {hotel['name']}\n"
                f"👤 اسم المستخدم: {username}\n"
                "🔑 كلمة المرور: تم حفظها بنجاح\n\n"
                "⚠️ احتفظ ببيانات الدخول وأرسلها "
                "لمسؤول الفندق.",
                reply_markup=admin_keyboard()
            )

        else:

            await update.message.reply_text(
                "❌ تعذر إنشاء الحساب.\n"
                "قد يكون اسم المستخدم مستخدماً."
            )

        return

    # =====================================================
    # حماية حساب الفندق
    # =====================================================

    if not hotel_access(context):

        await update.message.reply_text(
            "🔒 يجب تسجيل الدخول أولاً أو أن الحساب معطل.\n\n"
            "اضغط /start"
        )

        return

    # =====================================================
    # بيانات النزيل - الاسم
    # =====================================================

    if state == "guest_full_name":

        context.user_data["guest"] = {
            "full_name": text
        }

        context.user_data[
            "state"
        ] = "guest_mother"

        await update.message.reply_text(
            "2️⃣ اسم الأم:"
        )

        return

    # =====================================================
    # اسم الأم
    # =====================================================

    if state == "guest_mother":

        context.user_data[
            "guest"
        ]["mother_name"] = text

        context.user_data[
            "state"
        ] = "guest_birth"

        await update.message.reply_text(
            "3️⃣ مكان وتاريخ الولادة:"
        )

        return

    # =====================================================
    # مكان وتاريخ الولادة
    # =====================================================

    if state == "guest_birth":

        context.user_data[
            "guest"
        ]["birth_place_date"] = text

        context.user_data[
            "state"
        ] = "guest_residence"

        await update.message.reply_text(
            "4️⃣ السكن الأصلي:\n\n"
            "مثال: إدلب / سوريا"
        )

        return

    # =====================================================
    # السكن الأصلي
    # =====================================================

    if state == "guest_residence":

        context.user_data[
            "guest"
        ]["original_residence"] = text

        context.user_data[
            "state"
        ] = "guest_governorate"

        await update.message.reply_text(
            "5️⃣ المحافظة:"
        )

        return

    # =====================================================
    # المحافظة
    # =====================================================

    if state == "guest_governorate":

        context.user_data[
            "guest"
        ]["governorate"] = text

        account = get_logged_hotel(
            context
        )

        context.user_data[
            "guest"
        ]["hotel_name"] = account[
            "hotel_name"
        ]

        context.user_data[
            "state"
        ] = "guest_area"

        await update.message.reply_text(
            "6️⃣ منطقة الفندق:"
        )

        return

    # =====================================================
    # منطقة الفندق
    # =====================================================

    if state == "guest_area":

        context.user_data[
            "guest"
        ]["hotel_area"] = text

        context.user_data[
            "state"
        ] = "guest_reason"

        await update.message.reply_text(
            "7️⃣ سبب الإقامة:"
        )

        return

    # =====================================================
    # سبب الإقامة
    # =====================================================

    if state == "guest_reason":

        context.user_data[
            "guest"
        ]["stay_reason"] = text

        context.user_data[
            "state"
        ] = "guest_checkin"

        await update.message.reply_text(
            "8️⃣ تاريخ النزول:\n\n"
            "مثال: 10/08/2026"
        )

        return

    # =====================================================
    # تاريخ النزول
    # =====================================================

    if state == "guest_checkin":

        context.user_data[
            "guest"
        ]["check_in_date"] = text

        context.user_data[
            "state"
        ] = "guest_duration"

        await update.message.reply_text(
            "9️⃣ مدة الإقامة:"
        )

        return

    # =====================================================
    # مدة الإقامة
    # =====================================================

    if state == "guest_duration":

        context.user_data[
            "guest"
        ]["stay_duration"] = text

        context.user_data[
            "state"
        ] = "guest_notes"

        await update.message.reply_text(
            "🔟 ملاحظات عامة:\n\n"
            "إذا لا توجد ملاحظات أرسل: لا يوجد"
        )

        return

    # =====================================================
    # الملاحظات
    # =====================================================

    if state == "guest_notes":

        context.user_data[
            "guest"
        ]["notes"] = text

        context.user_data[
            "state"
        ] = "guest_id_front"

        await update.message.reply_text(
            "📸 أرسل الآن صورة الهوية الشخصية "
            "من الجهة الأمامية:"
        )

        return

    # =====================================================
    # الصورة الأمامية
    # =====================================================

    if state == "guest_id_front":

        if not update.message.photo:

            await update.message.reply_text(
                "❌ يرجى إرسال صورة للهوية "
                "من الجهة الأمامية."
            )

            return

        photo = update.message.photo[-1]

        file = await photo.get_file()

        filename = os.path.join(
            FILES_DIR,
            f"front_{user.id}_{datetime.now().timestamp()}.jpg"
        )

        await file.download_to_drive(
            filename
        )

        context.user_data[
            "guest"
        ]["id_front"] = filename

        context.user_data[
            "state"
        ] = "guest_id_back"

        await update.message.reply_text(
            "📸 ممتاز.\n\n"
            "أرسل الآن صورة الهوية الشخصية "
            "من الجهة الخلفية:"
        )

        return

    # =====================================================
    # الصورة الخلفية
    # =====================================================

    if state == "guest_id_back":

        if not update.message.photo:

            await update.message.reply_text(
                "❌ يرجى إرسال صورة للهوية "
                "من الجهة الخلفية."
            )

            return

        photo = update.message.photo[-1]

        file = await photo.get_file()

        filename = os.path.join(
            FILES_DIR,
            f"back_{user.id}_{datetime.now().timestamp()}.jpg"
        )

        await file.download_to_drive(
            filename
        )

        context.user_data[
            "guest"
        ]["id_back"] = filename

        # -------------------------------------------------
        # حفظ البيانات
        # -------------------------------------------------

        guest = context.user_data[
            "guest"
        ]

        account = get_logged_hotel(
            context
        )

        guest_id = save_guest(
            user.id,
            account["hotel_id"],
            guest
        )

        guest = dict(
            get_guest(guest_id)
        )

        context.user_data.clear()

        context.user_data[
            "last_guest_id"
        ] = guest_id

        await update.message.reply_text(
            "✅ تم تسجيل بيانات النزيل بنجاح.\n\n"
            "يمكنك الآن:\n"
            "👁️ عرض البيانات\n"
            "📤 إرسالها للإدارة",
            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "👁️ عرض البيانات",
                        callback_data=f"view_guest_{guest_id}"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "📤 إرسال للإدارة",
                        callback_data=f"send_guest_{guest_id}"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "🏠 القائمة الرئيسية",
                        callback_data="hotel_menu"
                    )
                ],

            ])
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

def create_pdf(guest):

    try:

        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.utils import ImageReader

    except ImportError:

        logger.error(
            "reportlab غير مثبت"
        )

        return None

    pdf_path = os.path.join(
        FILES_DIR,
        f"guest_{guest['id']}.pdf"
    )

    # -----------------------------------------------------
    # الخط العربي
    # -----------------------------------------------------

    font_path = (
        "/usr/share/fonts/truetype/dejavu/"
        "DejaVuSans.ttf"
    )

    bold_font_path = (
        "/usr/share/fonts/truetype/dejavu/"
        "DejaVuSans-Bold.ttf"
    )

    font_name = "Helvetica"
    bold_name = "Helvetica-Bold"

    if os.path.exists(font_path):

        pdfmetrics.registerFont(
            TTFont(
                "ArabicFont",
                font_path
            )
        )

        font_name = "ArabicFont"

    if os.path.exists(bold_font_path):

        pdfmetrics.registerFont(
            TTFont(
                "ArabicBold",
                bold_font_path
            )
        )

        bold_name = "ArabicBold"

    # -----------------------------------------------------
    # محاولة دعم العربية
    # -----------------------------------------------------

    try:

        import arabic_reshaper
        from bidi.algorithm import get_display

        def ar(text):

            reshaped = arabic_reshaper.reshape(
                str(text)
            )

            return get_display(
                reshaped
            )

    except ImportError:

        def ar(text):
            return str(text)

    # -----------------------------------------------------
    # إنشاء PDF
    # -----------------------------------------------------

    c = canvas.Canvas(
        pdf_path,
        pagesize=A4
    )

    width, height = A4

    # رأس الصفحة
    c.setFont(
        bold_name,
        20
    )

    c.drawCentredString(
        width / 2,
        height - 50,
        ar("تقرير بيانات نزيل")
    )

    c.setFont(
        font_name,
        10
    )

    c.drawCentredString(
        width / 2,
        height - 70,
        ar("نظام إدارة معلومات الفنادق")
    )

    y = height - 110

    fields = [
        ("رقم السجل", guest["id"]),
        ("الاسم الثلاثي", guest["full_name"]),
        ("اسم الأم", guest["mother_name"]),
        ("مكان وتاريخ الولادة", guest["birth_place_date"]),
        ("السكن الأصلي", guest["original_residence"]),
        ("المحافظة", guest["governorate"]),
        ("اسم الفندق", guest["hotel_name"]),
        ("منطقة الفندق", guest["hotel_area"]),
        ("سبب الإقامة", guest["stay_reason"]),
        ("تاريخ النزول", guest["check_in_date"]),
        ("مدة الإقامة", guest["stay_duration"]),
        ("ملاحظات عامة", guest["notes"]),
        ("تاريخ التسجيل", guest["created_at"]),
    ]

    c.setFont(
        font_name,
        11
    )

    for label, value in fields:

        if y < 100:

            c.showPage()

            y = height - 60

            c.setFont(
                font_name,
                11
            )

        text = f"{label}: {value or 'غير محدد'}"

        c.drawRightString(
            width - 50,
            y,
            ar(text)
        )

        y -= 25

    # -----------------------------------------------------
    # صور الهوية
    # -----------------------------------------------------

    for title, path in [
        ("الهوية - الوجه الأمامي", guest["id_front"]),
        ("الهوية - الوجه الخلفي", guest["id_back"]),
    ]:

        if not path or not os.path.exists(path):
            continue

        if y < 300:

            c.showPage()

            y = height - 60

        c.setFont(
            bold_name,
            12
        )

        c.drawRightString(
            width - 50,
            y,
            ar(title)
        )

        y -= 20

        try:

            img = ImageReader(path)

            c.drawImage(
                img,
                50,
                y - 230,
                width=500,
                height=220,
                preserveAspectRatio=True,
                anchor="c"
            )

            y -= 260

        except Exception:

            logger.exception(
                "خطأ في إدراج صورة الهوية"
            )

    c.setFont(
        font_name,
        8
    )

    c.drawCentredString(
        width / 2,
        25,
        ar("تم إنشاء هذا التقرير آلياً من نظام إدارة معلومات الفنادق")
    )

    c.save()

    return pdf_path


# =========================================================
# إرسال PDF للإدارة
# =========================================================

async def send_guest_to_admin(
    update,
    context,
    guest_id
):

    guest = get_guest(
        guest_id
    )

    if not guest:

        await update.callback_query.edit_message_text(
            "❌ السجل غير موجود."
        )

        return

    pdf_path = create_pdf(
        guest
    )

    if not pdf_path:

        await update.callback_query.edit_message_text(
            "❌ تعذر إنشاء ملف PDF.\n"
            "تأكد من تثبيت مكتبات PDF."
        )

        return

    caption = (
        "📋 تقرير نزيل جديد\n\n"
        f"👤 الاسم: {guest['full_name']}\n"
        f"🏨 الفندق: {guest['hotel_name']}\n"
        f"🏛 المحافظة: {guest['governorate']}\n"
        f"📍 السكن الأصلي: {guest['original_residence']}\n"
        f"📝 سبب الإقامة: {guest['stay_reason']}\n"
        f"📅 تاريخ النزول: {guest['check_in_date']}\n"
        f"🕐 مدة الإقامة: {guest['stay_duration']}"
    )

    try:

        with open(
            pdf_path,
            "rb"
        ) as document:

            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=document,
                caption=caption
            )

        # إرسال صور الهوية أيضاً
        if guest["id_front"] and os.path.exists(
            guest["id_front"]
        ):

            with open(
                guest["id_front"],
                "rb"
            ) as photo:

                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=photo,
                    caption="📸 الهوية - الوجه الأمامي"
                )

        if guest["id_back"] and os.path.exists(
            guest["id_back"]
        ):

            with open(
                guest["id_back"],
                "rb"
            ) as photo:

                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=photo,
                    caption="📸 الهوية - الوجه الخلفي"
                )

        await update.callback_query.edit_message_text(
            "✅ تم إرسال بيانات النزيل إلى الإدارة بنجاح.\n\n"
            "📄 تم إرسال ملف PDF\n"
            "📸 وتم إرسال صور الهوية.",
            reply_markup=hotel_keyboard()
        )

    except Exception:

        logger.exception(
            "فشل إرسال التقرير للإدارة"
        )

        await update.callback_query.edit_message_text(
            "❌ حدث خطأ أثناء إرسال التقرير للإدارة."
        )


# =========================================================
# عرض بيانات نزيل
# =========================================================

def guest_text(guest):

    return (
        "📋 بيانات النزيل\n\n"

        f"👤 الاسم الثلاثي:\n"
        f"{guest['full_name']}\n\n"

        f"👩 اسم الأم:\n"
        f"{guest['mother_name']}\n\n"

        f"📍 مكان وتاريخ الولادة:\n"
        f"{guest['birth_place_date']}\n\n"

        f"🏠 السكن الأصلي:\n"
        f"{guest['original_residence']}\n\n"

        f"🏛 المحافظة:\n"
        f"{guest['governorate']}\n\n"

        f"🏨 الفندق:\n"
        f"{guest['hotel_name']}\n\n"

        f"📍 منطقة الفندق:\n"
        f"{guest['hotel_area']}\n\n"

        f"📝 سبب الإقامة:\n"
        f"{guest['stay_reason']}\n\n"

        f"📅 تاريخ النزول:\n"
        f"{guest['check_in_date']}\n\n"

        f"🕐 مدة الإقامة:\n"
        f"{guest['stay_duration']}\n\n"

        f"📌 ملاحظات:\n"
        f"{guest['notes'] or 'لا يوجد'}"
    )


# =========================================================
# التقرير
# =========================================================

async def send_report(
    update,
    context,
    period="daily"
):

    query = update.callback_query

    today = date.today()

    if period == "daily":

        start = today.strftime(
            "%Y-%m-%d"
        )

        end = start

        title = "📊 التقرير اليومي"

    else:

        start = today.replace(
            day=1
        ).strftime(
            "%Y-%m-%d"
        )

        end = today.strftime(
            "%Y-%m-%d"
        )

        title = "📅 التقرير الشهري"

    rows = get_report_rows(
        start,
        end
    )

    if not rows:

        await query.edit_message_text(
            f"{title}\n\n"
            "📭 لا توجد بيانات خلال هذه الفترة.",
            reply_markup=admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # المحافظة
    # -----------------------------------------------------

    governorates = count_items(
        rows,
        "governorate"
    )

    # -----------------------------------------------------
    # السكن الأصلي / الدولة
    # -----------------------------------------------------

    countries = count_items(
        rows,
        "original_residence"
    )

    # -----------------------------------------------------
    # الفنادق
    # -----------------------------------------------------

    hotels = count_items(
        rows,
        "hotel_name"
    )

    # -----------------------------------------------------
    # سبب الإقامة
    # -----------------------------------------------------

    reasons = count_items(
        rows,
        "stay_reason"
    )

    text = (
        f"{title}\n\n"
        f"👥 إجمالي النزلاء: {len(rows)}\n\n"
    )

    text += "🏛 حسب المحافظات:\n"

    for name, count in governorates:

        text += f"• {name}: {count}\n"

    text += "\n🌍 حسب السكن الأصلي / الدولة:\n"

    for name, count in countries:

        text += f"• {name}: {count}\n"

    text += "\n🏨 حسب الفنادق:\n"

    for name, count in hotels:

        text += f"• {name}: {count}\n"

    text += "\n📝 حسب سبب الإقامة:\n"

    for name, count in reasons:

        text += f"• {name}: {count}\n"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "⬅️ لوحة المدير",
                    callback_data="admin_menu"
                )
            ]

        ])
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
                "👑 لوحة تحكم المدير",
                reply_markup=admin_keyboard()
            )

        else:

            await query.edit_message_text(
                "❌ تم إلغاء العملية.\n\n"
                "اضغط /start للعودة."
            )

        return

    # =====================================================
    # لوحة المدير
    # =====================================================

    if data == "admin_menu":

        if not is_admin(user.id):
            return

        context.user_data.clear()

        await query.edit_message_text(
            "👑 لوحة تحكم المدير\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=admin_keyboard()
        )

        return

    # =====================================================
    # إضافة حساب فندق
    # =====================================================

    if data == "add_account":

        if not is_admin(user.id):

            await query.edit_message_text(
                "⛔ هذه العملية للمدير فقط."
            )

            return

        await query.edit_message_text(
            "🏨 اختر الفندق الذي تريد إنشاء حساب له:",
            reply_markup=hotel_selection_keyboard()
        )

        return

    # =====================================================
    # اختيار فندق لإنشاء حساب
    # =====================================================

    if data.startswith("select_hotel_"):

        if not is_admin(user.id):
            return

        try:

            hotel_id = int(
                data.replace(
                    "select_hotel_",
                    ""
                )
            )

        except ValueError:

            return

        hotel = get_hotel(
            hotel_id
        )

        if not hotel:

            await query.edit_message_text(
                "❌ الفندق غير موجود.",
                reply_markup=admin_keyboard()
            )

            return

        context.user_data.clear()

        context.user_data[
            "new_account_hotel_id"
        ] = hotel_id

        context.user_data[
            "state"
        ] = "create_account_username"

        await query.edit_message_text(
            f"🏨 الفندق: {hotel['name']}\n\n"
            "👤 أرسل اسم المستخدم الذي تريد إنشاءه:"
        )

        return

    # =====================================================
    # إضافة فندق جديد
    # =====================================================

    if data == "new_hotel":

        if not is_admin(user.id):
            return

        context.user_data.clear()

        context.user_data[
            "state"
        ] = "new_hotel"

        await query.edit_message_text(
            "➕ إضافة فندق جديد\n\n"
            "أرسل اسم الفندق:"
        )

        return

    # =====================================================
    # إدارة الفنادق
    # =====================================================

    if data == "manage_hotels":

        if not is_admin(user.id):
            return

        hotels = get_hotels()

        text = "🏨 الفنادق المسجلة:\n\n"

        for hotel in hotels:

            status = (
                "🟢 فعال"
                if hotel["active"] == 1
                else
                "🔴 معطل"
            )

            text += (
                f"• {hotel['name']} — {status}\n"
            )

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "➕ إضافة فندق",
                        callback_data="new_hotel"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ رجوع",
                        callback_data="admin_menu"
                    )
                ],

            ])
        )

        return

    # =====================================================
    # حسابات الفنادق
    # =====================================================

    if data == "accounts":

        if not is_admin(user.id):
            return

        rows = get_all_accounts()

        if not rows:

            await query.edit_message_text(
                "👥 لا توجد حسابات فنادق.",
                reply_markup=admin_keyboard()
            )

            return

        buttons = []

        for row in rows:

            status = (
                "🟢 فعال"
                if row["active"] == 1
                else
                "🔴 معطل"
            )

            buttons.append([

                InlineKeyboardButton(
                    f"{row['hotel_name']} | "
                    f"{row['username']} | {status}",
                    callback_data=f"account_status_{row['id']}"
                )

            ])

        buttons.append([

            InlineKeyboardButton(
                "⬅️ رجوع",
                callback_data="admin_menu"
            )

        ])

        await query.edit_message_text(
            "👥 حسابات الفنادق\n\n"
            "اضغط على الحساب لتفعيل أو تعطيل الحساب:",
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

        return

    # =====================================================
    # تفعيل / تعطيل حساب
    # =====================================================

    if data.startswith("account_status_"):

        if not is_admin(user.id):
            return

        try:

            account_id = int(
                data.replace(
                    "account_status_",
                    ""
                )
            )

        except ValueError:

            return

        conn = get_db()

        try:

            account = conn.execute("""
                SELECT
                    a.*,
                    h.name AS hotel_name
                FROM hotel_accounts a
                JOIN hotels h
                    ON h.id = a.hotel_id
                WHERE a.id = ?
            """, (
                account_id,
            )).fetchone()

        finally:
            conn.close()

        if not account:

            await query.edit_message_text(
                "❌ الحساب غير موجود.",
                reply_markup=admin_keyboard()
            )

            return

        new_status = (
            0
            if account["active"] == 1
            else
            1
        )

        set_hotel_account_status(
            account_id,
            new_status
        )

        if new_status == 1:

            text = (
                "🟢 تم تفعيل الحساب\n\n"
                f"🏨 الفندق: {account['hotel_name']}\n"
                f"👤 المستخدم: {account['username']}\n\n"
                "يمكن للحساب تسجيل الدخول واستخدام النظام."
            )

        else:

            text = (
                "🔴 تم تعطيل الحساب\n\n"
                f"🏨 الفندق: {account['hotel_name']}\n"
                f"👤 المستخدم: {account['username']}\n\n"
                "لن يستطيع الحساب تسجيل الدخول أو تسجيل "
                "بيانات نزلاء جديدة."
            )

        await query.edit_message_text(
            text,
            reply_markup=admin_keyboard()
        )

        return

    # =====================================================
    # التقرير اليومي
    # =====================================================

    if data == "daily_report":

        if not is_admin(user.id):
            return

        await send_report(
            update,
            context,
            "daily"
        )

        return

    # =====================================================
    # التقرير الشهري
    # =====================================================

    if data == "monthly_report":

        if not is_admin(user.id):
            return

        await send_report(
            update,
            context,
            "monthly"
        )

        return

    # =====================================================
    # آخر السجلات للمدير
    # =====================================================

    if data == "admin_records":

        if not is_admin(user.id):
            return

        rows = get_report_rows()

        rows = rows[:10]

        if not rows:

            text = "📋 لا توجد سجلات."

        else:

            text = "📋 آخر 10 سجلات:\n\n"

            for index, row in enumerate(
                rows,
                start=1
            ):

                text += (
                    f"{index}️⃣ "
                    f"👤 {row['full_name']}\n"
                    f"🏨 {row['hotel_name']}\n"
                    f"🏛 {row['governorate']}\n"
                    f"📝 {row['stay_reason']}\n"
                    f"🕐 {row['created_at']}\n\n"
                )

        await query.edit_message_text(
            text,
            reply_markup=admin_keyboard()
        )

        return

    # =====================================================
    # خروج المدير
    # =====================================================

    if data == "admin_logout":

        context.user_data.clear()

        await query.edit_message_text(
            "🚪 تم تسجيل الخروج.\n\n"
            "اضغط /start للعودة."
        )

        return

    # =====================================================
    # حماية حساب الفندق
    # =====================================================

    if not hotel_access(context):

        await query.edit_message_text(
            "🔴 الحساب غير متاح حالياً.\n\n"
            "قد يكون الحساب معطلاً من الإدارة.\n"
            "اضغط /start."
        )

        return

    # =====================================================
    # قائمة الفندق
    # =====================================================

    if data == "hotel_menu":

        await query.edit_message_text(
            "🏨 حساب الفندق\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_keyboard()
        )

        return

    # =====================================================
    # تسجيل نزيل
    # =====================================================

    if data == "add_guest":

        context.user_data[
            "state"
        ] = "guest_full_name"

        context.user_data[
            "guest"
        ] = {}

        await query.edit_message_text(
            "📝 تسجيل بيانات نزيل جديد\n\n"
            "1️⃣ الاسم الثلاثي:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # عرض آخر بيانات
    # =====================================================

    if data == "my_last_guest":

        guest = get_latest_guest_for_user(
            user.id
        )

        if not guest:

            await query.edit_message_text(
                "📭 لم تقم بتسجيل أي نزيل حتى الآن.",
                reply_markup=hotel_keyboard()
            )

            return

        await query.edit_message_text(
            guest_text(guest),

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "📤 إرسال للإدارة",
                        callback_data=f"send_guest_{guest['id']}"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "🏠 القائمة الرئيسية",
                        callback_data="hotel_menu"
                    )
                ],

            ])
        )

        return

    # =====================================================
    # عرض سجل محدد
    # =====================================================

    if data.startswith("view_guest_"):

        try:

            guest_id = int(
                data.replace(
                    "view_guest_",
                    ""
                )
            )

        except ValueError:
            return

        guest = get_guest(
            guest_id
        )

        if not guest:

            await query.edit_message_text(
                "❌ السجل غير موجود."
            )

            return

        await query.edit_message_text(
            guest_text(guest),

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "📤 إرسال للإدارة",
                        callback_data=f"send_guest_{guest_id}"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "🏠 القائمة الرئيسية",
                        callback_data="hotel_menu"
                    )
                ],

            ])
        )

        return

    # =====================================================
    # إرسال سجل للإدارة
    # =====================================================

    if data.startswith("send_guest_"):

        try:

            guest_id = int(
                data.replace(
                    "send_guest_",
                    ""
                )
            )

        except ValueError:
            return

        guest = get_guest(
            guest_id
        )

        if not guest:

            await query.edit_message_text(
                "❌ السجل غير موجود."
            )

            return

        # التأكد أن الفندق هو صاحب السجل
        account = get_logged_hotel(
            context
        )

        if guest["hotel_id"] != account["hotel_id"]:

            await query.edit_message_text(
                "⛔ لا يمكنك إرسال سجل تابع لفندق آخر."
            )

            return

        await query.edit_message_text(
            "⏳ جارٍ تجهيز ملف PDF وإرساله للإدارة..."
        )

        await send_guest_to_admin(
            update,
            context,
            guest_id
        )

        return

    # =====================================================
    # خروج الفندق
    # =====================================================

    if data == "hotel_logout":

        context.user_data.clear()

        await query.edit_message_text(
            "🚪 تم تسجيل الخروج من حساب الفندق.\n\n"
            "اضغط /start لتسجيل الدخول مرة أخرى."
        )

        return

    # =====================================================
    # زر غير معروف
    # =====================================================

    await query.edit_message_text(
        "❌ العملية غير معروفة.\n\n"
        "اضغط /start."
    )


# =========================================================
# الأخطاء
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "❌ حدث خطأ أثناء معالجة التحديث:",
        exc_info=context.error
    )


# =========================================================
# HTTP SERVER لـ Render
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

    except Exception:

        logger.exception(
            "❌ فشل إنشاء قاعدة البيانات."
        )

        return

    # -----------------------------------------------------
    # Health Server
    # -----------------------------------------------------

    try:

        thread = threading.Thread(
            target=start_health_server,
            daemon=True
        )

        thread.start()

    except Exception:

        logger.exception(
            "❌ فشل تشغيل Health Server."
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
    # تشغيل
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
            "❌ Telegram Bot توقف."
        )


# =========================================================
# البداية
# =========================================================

if __name__ == "__main__":

    main()
