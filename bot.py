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

# صورة العقاب
EAGLE_IMAGE = (
    "https://commons.wikimedia.org/wiki/Special:Redirect/"
    "file/Eagle_%D8%B9%D9%82%D8%A7%D8%A8_13.jpg"
)


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
        # المستخدمون / حسابات الفنادق
        # -------------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                logged_in INTEGER DEFAULT 0,
                login_time TEXT,
                role TEXT DEFAULT 'hotel',
                hotel_id INTEGER
            )
        """)

        # -------------------------------------------------
        # الفنادق
        # -------------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS hotels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hotel_name TEXT NOT NULL,
                login_username TEXT UNIQUE NOT NULL,
                login_password TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)

        # -------------------------------------------------
        # النزلاء
        # -------------------------------------------------

        cur.execute("""
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


# =========================================================
# المستخدمين
# =========================================================

def register_user(
    user_id,
    username=""
):

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


def set_login(
    user_id,
    status
):

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


def get_user_hotel(user_id):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT hotel_id
            FROM users
            WHERE telegram_id = ?
        """, (user_id,)).fetchone()

        if not row:

            return None

        return row["hotel_id"]

    finally:

        conn.close()


# =========================================================
# الفنادق
# =========================================================

def create_hotel(
    hotel_name,
    username,
    password
):

    conn = get_db()

    try:

        cursor = conn.execute("""
            INSERT INTO hotels
            (
                hotel_name,
                login_username,
                login_password,
                active,
                created_at
            )
            VALUES (?, ?, ?, 1, ?)
        """, (
            hotel_name,
            username,
            password,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

        conn.commit()

        return cursor.lastrowid

    finally:

        conn.close()


def get_hotel_by_login(username):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT *
            FROM hotels
            WHERE login_username = ?
            AND active = 1
        """, (username,)).fetchone()

        return row

    finally:

        conn.close()


def get_hotel(hotel_id):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT *
            FROM hotels
            WHERE id = ?
        """, (hotel_id,)).fetchone()

        return row

    finally:

        conn.close()


def get_all_hotels():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM hotels
            ORDER BY id DESC
        """).fetchall()

    finally:

        conn.close()


def delete_hotel(hotel_id):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE hotels
            SET active = 0
            WHERE id = ?
        """, (hotel_id,))

        conn.commit()

    finally:

        conn.close()


# =========================================================
# حفظ النزيل
# =========================================================

def save_guest(
    user_id,
    hotel_id,
    data
):

    conn = get_db()

    try:

        hotel = get_hotel(hotel_id)

        hotel_name = (
            hotel["hotel_name"]
            if hotel
            else ""
        )

        conn.execute("""
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
                suite_number,
                room_number,
                stay_duration,
                stay_reason,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            hotel_id,
            data.get("full_name", ""),
            data.get("mother_name", ""),
            data.get("birth_place_date", ""),
            data.get("original_residence", ""),
            data.get("governorate", ""),
            hotel_name,
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
# الصلاحيات
# =========================================================

def is_admin(user_id):

    return user_id == ADMIN_ID


def is_hotel_user(user_id):

    if is_admin(user_id):

        return False

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT role, logged_in, hotel_id
            FROM users
            WHERE telegram_id = ?
        """, (user_id,)).fetchone()

        return bool(
            row
            and row["role"] == "hotel"
            and row["logged_in"] == 1
            and row["hotel_id"]
        )

    finally:

        conn.close()


# =========================================================
# لوحات المدير
# =========================================================

def admin_menu_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🏨 إضافة حساب فندق",
                callback_data="admin_add_hotel"
            )
        ],

        [
            InlineKeyboardButton(
                "🏨 حسابات الفنادق",
                callback_data="admin_hotels"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 الإحصائيات",
                callback_data="admin_statistics"
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

def hotel_menu_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📝 تسجيل بيانات نزيل",
                callback_data="hotel_add_guest"
            )
        ],

        [
            InlineKeyboardButton(
                "📋 سجلات الفندق",
                callback_data="hotel_records"
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
# أزرار عامة
# =========================================================

def start_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🔐 تسجيل الدخول",
                callback_data="login"
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
# رسالة الترحيب
# =========================================================

WELCOME_TEXT = (
    "🦅 أهلاً وسهلاً بكم\n\n"
    "🏨 نظام إدارة معلومات الفنادق\n\n"
    "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
    "🔐 للمتابعة يرجى تسجيل الدخول."
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

    register_user(
        user.id,
        user.username or ""
    )

    context.user_data.clear()

    # -----------------------------------------------------
    # المدير
    # -----------------------------------------------------

    if is_admin(user.id):

        set_login(
            user.id,
            True
        )

        try:

            await update.message.reply_photo(
                photo=EAGLE_IMAGE,
                caption=(
                    "👑 أهلاً بك أيها المدير\n\n"
                    "🏨 نظام إدارة معلومات الفنادق\n\n"
                    "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
                    "اختر العملية المطلوبة:"
                ),
                reply_markup=admin_menu_keyboard()
            )

        except Exception:

            logger.exception(
                "تعذر إرسال صورة الترحيب"
            )

            await update.message.reply_text(
                WELCOME_TEXT,
                reply_markup=admin_menu_keyboard()
            )

        return

    # -----------------------------------------------------
    # فندق مسجل دخول
    # -----------------------------------------------------

    if is_hotel_user(user.id):

        hotel_id = get_user_hotel(user.id)

        hotel = get_hotel(hotel_id)

        hotel_name = (
            hotel["hotel_name"]
            if hotel
            else "الفندق"
        )

        await update.message.reply_text(
            "🏨 أهلاً بك\n\n"
            f"🏢 الحساب: {hotel_name}\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # مستخدم غير مسجل
    # -----------------------------------------------------

    try:

        await update.message.reply_photo(
            photo=EAGLE_IMAGE,
            caption=WELCOME_TEXT,
            reply_markup=start_keyboard()
        )

    except Exception:

        await update.message.reply_text(
            WELCOME_TEXT,
            reply_markup=start_keyboard()
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
            reply_markup=admin_menu_keyboard()
        )

        return

    context.user_data.clear()

    context.user_data["state"] = "login_username"

    await query.edit_message_text(
        "🔐 تسجيل الدخول\n\n"
        "أرسل اسم المستخدم الخاص بالفندق:",
        reply_markup=cancel_keyboard()
    )


# =========================================================
# إنشاء حساب فندق - الخطوة 1
# =========================================================

async def start_add_hotel(
    query,
    context
):

    context.user_data.clear()

    context.user_data["state"] = "hotel_create_name"

    await query.edit_message_text(
        "🏨 إنشاء حساب فندق جديد\n\n"
        "1️⃣ أرسل اسم الفندق:",
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
    # تسجيل دخول الفندق - اسم المستخدم
    # =====================================================

    if state == "login_username":

        context.user_data["login_username"] = text

        context.user_data["state"] = "login_password"

        await update.message.reply_text(
            "🔑 أرسل كلمة المرور:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # تسجيل دخول الفندق - كلمة المرور
    # =====================================================

    if state == "login_password":

        username = context.user_data.get(
            "login_username"
        )

        password = text

        hotel = get_hotel_by_login(
            username
        )

        if not hotel:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ اسم المستخدم غير موجود أو الحساب غير فعال.\n\n"
                "اضغط /start وحاول مرة أخرى.",
                reply_markup=start_keyboard()
            )

            return

        if hotel["login_password"] != password:

            await update.message.reply_text(
                "❌ كلمة المرور غير صحيحة.\n\n"
                "حاول مرة أخرى:"
            )

            return

        # -------------------------------------------------
        # تسجيل الدخول بنجاح
        # -------------------------------------------------

        register_user(
            user.id,
            user.username or ""
        )

        conn = get_db()

        try:

            conn.execute("""
                UPDATE users
                SET
                    logged_in = 1,
                    login_time = ?,
                    role = 'hotel',
                    hotel_id = ?
                WHERE telegram_id = ?
            """, (
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                hotel["id"],
                user.id,
            ))

            conn.commit()

        finally:

            conn.close()

        context.user_data.clear()

        await update.message.reply_text(
            "✅ تم تسجيل الدخول بنجاح\n\n"
            f"🏨 الفندق: {hotel['hotel_name']}\n\n"
            "يمكنك الآن تسجيل بيانات النزلاء.",
            reply_markup=hotel_menu_keyboard()
        )

        return

    # =====================================================
    # المدير - إنشاء حساب فندق
    # =====================================================

    if state == "hotel_create_name":

        if not is_admin(user.id):

            context.user_data.clear()

            await update.message.reply_text(
                "❌ ليس لديك صلاحية."
            )

            return

        context.user_data["new_hotel_name"] = text

        context.user_data["state"] = "hotel_create_username"

        await update.message.reply_text(
            "2️⃣ أرسل اسم المستخدم للحساب:\n\n"
            "مثال:\n"
            "sham_hotel",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # المدير - اسم المستخدم
    # =====================================================

    if state == "hotel_create_username":

        if not is_admin(user.id):

            context.user_data.clear()

            return

        username = text.lower().replace(" ", "_")

        existing = get_hotel_by_login(
            username
        )

        if existing:

            await update.message.reply_text(
                "❌ اسم المستخدم مستخدم مسبقاً.\n\n"
                "اختر اسماً آخر:"
            )

            return

        context.user_data["new_hotel_username"] = username

        context.user_data["state"] = "hotel_create_password"

        await update.message.reply_text(
            "3️⃣ أرسل كلمة المرور للحساب:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # المدير - كلمة مرور الفندق
    # =====================================================

    if state == "hotel_create_password":

        if not is_admin(user.id):

            context.user_data.clear()

            return

        hotel_name = context.user_data.get(
            "new_hotel_name"
        )

        username = context.user_data.get(
            "new_hotel_username"
        )

        password = text

        try:

            hotel_id = create_hotel(
                hotel_name,
                username,
                password
            )

        except sqlite3.IntegrityError:

            await update.message.reply_text(
                "❌ اسم المستخدم موجود مسبقاً.\n\n"
                "أرسل اسم مستخدم آخر:"
            )

            context.user_data["state"] = (
                "hotel_create_username"
            )

            return

        context.user_data.clear()

        await update.message.reply_text(
            "✅ تم إنشاء حساب الفندق بنجاح\n\n"
            f"🏨 الفندق: {hotel_name}\n"
            f"👤 اسم المستخدم: {username}\n"
            f"🔑 كلمة المرور: {password}\n"
            f"🆔 رقم الحساب: {hotel_id}\n\n"
            "⚠️ احتفظ ببيانات الدخول وأرسلها لإدارة الفندق.",
            reply_markup=admin_menu_keyboard()
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
            "2️⃣ اسم الأم:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # اسم الأم
    # =====================================================

    if state == "guest_mother":

        context.user_data["guest"]["mother_name"] = text

        context.user_data["state"] = "guest_birth"

        await update.message.reply_text(
            "3️⃣ مكان وتاريخ الولادة:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # مكان وتاريخ الولادة
    # =====================================================

    if state == "guest_birth":

        context.user_data["guest"]["birth_place_date"] = text

        context.user_data["state"] = "guest_residence"

        await update.message.reply_text(
            "4️⃣ السكن الأصلي:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # السكن الأصلي
    # =====================================================

    if state == "guest_residence":

        context.user_data["guest"]["original_residence"] = text

        context.user_data["state"] = "guest_governorate"

        await update.message.reply_text(
            "5️⃣ المحافظة:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # المحافظة
    # =====================================================

    if state == "guest_governorate":

        context.user_data["guest"]["governorate"] = text

        context.user_data["state"] = "guest_suite"

        await update.message.reply_text(
            "6️⃣ رقم الجناح:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # الجناح
    # =====================================================

    if state == "guest_suite":

        context.user_data["guest"]["suite_number"] = text

        context.user_data["state"] = "guest_room"

        await update.message.reply_text(
            "7️⃣ رقم الغرفة:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # الغرفة
    # =====================================================

    if state == "guest_room":

        context.user_data["guest"]["room_number"] = text

        context.user_data["state"] = "guest_duration"

        await update.message.reply_text(
            "8️⃣ مدة الإقامة:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # مدة الإقامة
    # =====================================================

    if state == "guest_duration":

        context.user_data["guest"]["stay_duration"] = text

        context.user_data["state"] = "guest_reason"

        await update.message.reply_text(
            "9️⃣ سبب الإقامة:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # سبب الإقامة
    # =====================================================

    if state == "guest_reason":

        context.user_data["guest"]["stay_reason"] = text

        hotel_id = get_user_hotel(
            user.id
        )

        if not hotel_id:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ لم يتم التعرف على حساب الفندق.\n\n"
                "اضغط /start وحاول تسجيل الدخول مرة أخرى."
            )

            return

        guest = context.user_data["guest"]

        save_guest(
            user.id,
            hotel_id,
            guest
        )

        context.user_data.clear()

        hotel = get_hotel(
            hotel_id
        )

        hotel_name = (
            hotel["hotel_name"]
            if hotel
            else "الفندق"
        )

        await update.message.reply_text(
            "✅ تم تسجيل بيانات النزيل بنجاح\n\n"
            f"🏨 الفندق: {hotel_name}\n"
            f"👤 الاسم: {guest['full_name']}\n"
            f"👩 اسم الأم: {guest['mother_name']}\n"
            f"📍 الولادة: {guest['birth_place_date']}\n"
            f"🏠 السكن الأصلي: {guest['original_residence']}\n"
            f"🏛 المحافظة: {guest['governorate']}\n"
            f"🚪 الجناح: {guest['suite_number']}\n"
            f"🚪 الغرفة: {guest['room_number']}\n"
            f"📅 مدة الإقامة: {guest['stay_duration']}\n"
            f"📝 سبب الإقامة: {guest['stay_reason']}",
            reply_markup=hotel_menu_keyboard()
        )

        return

    # =====================================================
    # لا توجد عملية
    # =====================================================

    if is_admin(user.id):

        await update.message.reply_text(
            "👑 أنت المدير.\n\n"
            "استخدم القائمة الرئيسية.",
            reply_markup=admin_menu_keyboard()
        )

        return

    if is_hotel_user(user.id):

        await update.message.reply_text(
            "🏨 استخدم القائمة الرئيسية.",
            reply_markup=hotel_menu_keyboard()
        )

        return

    await update.message.reply_text(
        "🔒 يجب تسجيل الدخول أولاً.\n\n"
        "اضغط /start",
        reply_markup=start_keyboard()
    )


# =========================================================
# سجلات الفندق
# =========================================================

async def hotel_records(
    query,
    user_id
):

    hotel_id = get_user_hotel(
        user_id
    )

    if not hotel_id:

        await query.edit_message_text(
            "❌ لم يتم العثور على حساب الفندق."
        )

        return

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT
                full_name,
                room_number,
                stay_duration,
                created_at
            FROM guests
            WHERE hotel_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (
            hotel_id,
        )).fetchall()

    finally:

        conn.close()

    hotel = get_hotel(
        hotel_id
    )

    hotel_name = (
        hotel["hotel_name"]
        if hotel
        else "الفندق"
    )

    if not rows:

        text = (
            f"📋 سجلات {hotel_name}\n\n"
            "لا توجد سجلات حتى الآن."
        )

    else:

        text = (
            f"📋 آخر سجلات {hotel_name}\n\n"
        )

        for index, row in enumerate(
            rows,
            start=1
        ):

            text += (
                f"{index}️⃣ "
                f"👤 {row['full_name']}\n"
                f"🚪 الغرفة: {row['room_number']}\n"
                f"📅 المدة: {row['stay_duration']}\n"
                f"🕐 {row['created_at']}\n\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=hotel_menu_keyboard()
    )


# =========================================================
# إحصائيات المدير
# =========================================================

async def admin_statistics(
    query
):

    conn = get_db()

    try:

        hotels = conn.execute("""
            SELECT COUNT(*)
            FROM hotels
            WHERE active = 1
        """).fetchone()[0]

        guests = conn.execute("""
            SELECT COUNT(*)
            FROM guests
        """).fetchone()[0]

    finally:

        conn.close()

    await query.edit_message_text(
        "📊 إحصائيات النظام\n\n"
        f"🏨 عدد الفنادق: {hotels}\n"
        f"👤 عدد النزلاء: {guests}",
        reply_markup=admin_menu_keyboard()
    )


# =========================================================
# آخر سجلات المدير
# =========================================================

async def admin_records(
    query
):

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
            LIMIT 15
        """).fetchall()

    finally:

        conn.close()

    if not rows:

        text = "📋 لا توجد سجلات حتى الآن."

    else:

        text = "📋 آخر 15 سجل:\n\n"

        for index, row in enumerate(
            rows,
            start=1
        ):

            text += (
                f"{index}️⃣ 👤 {row['full_name']}\n"
                f"🏨 الفندق: {row['hotel_name']}\n"
                f"🚪 الغرفة: {row['room_number']}\n"
                f"🕐 {row['created_at']}\n\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=admin_menu_keyboard()
    )


# =========================================================
# قائمة الفنادق للمدير
# =========================================================

async def admin_hotels(
    query
):

    hotels = get_all_hotels()

    if not hotels:

        text = (
            "🏨 حسابات الفنادق\n\n"
            "لا توجد حسابات فنادق حتى الآن."
        )

    else:

        text = "🏨 حسابات الفنادق:\n\n"

        for index, hotel in enumerate(
            hotels,
            start=1
        ):

            status = (
                "🟢 فعال"
                if hotel["active"]
                else "🔴 متوقف"
            )

            text += (
                f"{index}️⃣ {hotel['hotel_name']}\n"
                f"👤 المستخدم: {hotel['login_username']}\n"
                f"{status}\n\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=admin_menu_keyboard()
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
                "👑 تم إلغاء العملية.",
                reply_markup=admin_menu_keyboard()
            )

        else:

            await query.edit_message_text(
                "❌ تم إلغاء العملية.",
                reply_markup=start_keyboard()
            )

        return

    # =====================================================
    # المدير - إضافة فندق
    # =====================================================

    if data == "admin_add_hotel":

        if not is_admin(user.id):

            await query.edit_message_text(
                "❌ ليس لديك صلاحية."
            )

            return

        await start_add_hotel(
            query,
            context
        )

        return

    # =====================================================
    # المدير - الفنادق
    # =====================================================

    if data == "admin_hotels":

        if not is_admin(user.id):

            return

        await admin_hotels(
            query
        )

        return

    # =====================================================
    # المدير - الإحصائيات
    # =====================================================

    if data == "admin_statistics":

        if not is_admin(user.id):

            return

        await admin_statistics(
            query
        )

        return

    # =====================================================
    # المدير - السجلات
    # =====================================================

    if data == "admin_records":

        if not is_admin(user.id):

            return

        await admin_records(
            query
        )

        return

    # =====================================================
    # المدير - خروج
    # =====================================================

    if data == "admin_logout":

        if is_admin(user.id):

            set_login(
                user.id,
                False
            )

        context.user_data.clear()

        await query.edit_message_text(
            "🚪 تم تسجيل الخروج.\n\n"
            "اضغط /start للعودة.",
            reply_markup=start_keyboard()
        )

        return

    # =====================================================
    # الفندق - إضافة نزيل
    # =====================================================

    if data == "hotel_add_guest":

        if not is_hotel_user(user.id):

            await query.edit_message_text(
                "🔒 يجب تسجيل الدخول بحساب الفندق.",
                reply_markup=start_keyboard()
            )

            return

        context.user_data.clear()

        context.user_data["state"] = (
            "guest_full_name"
        )

        await query.edit_message_text(
            "📝 تسجيل بيانات نزيل جديد\n\n"
            "1️⃣ الاسم الثلاثي:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # الفندق - السجلات
    # =====================================================

    if data == "hotel_records":

        if not is_hotel_user(user.id):

            return

        await hotel_records(
            query,
            user.id
        )

        return

    # =====================================================
    # الفندق - خروج
    # =====================================================

    if data == "hotel_logout":

        set_login(
            user.id,
            False
        )

        conn = get_db()

        try:

            conn.execute("""
                UPDATE users
                SET hotel_id = NULL,
                    role = 'hotel'
                WHERE telegram_id = ?
            """, (
                user.id,
            ))

            conn.commit()

        finally:

            conn.close()

        context.user_data.clear()

        await query.edit_message_text(
            "🚪 تم تسجيل الخروج من حساب الفندق.\n\n"
            "اضغط /start للعودة.",
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
# معالجة الأوامر
# =========================================================

async def add_hotel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:

        return

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ هذا الأمر متاح للمدير فقط."
        )

        return

    context.user_data.clear()

    context.user_data["state"] = (
        "hotel_create_name"
    )

    await update.message.reply_text(
        "🏨 إنشاء حساب فندق جديد\n\n"
        "1️⃣ أرسل اسم الفندق:",
        reply_markup=cancel_keyboard()
    )


async def add_guest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:

        return

    if not is_hotel_user(user.id):

        await update.message.reply_text(
            "🔒 يجب تسجيل الدخول بحساب الفندق أولاً.\n\n"
            "اضغط /start"
        )

        return

    context.user_data.clear()

    context.user_data["state"] = (
        "guest_full_name"
    )

    await update.message.reply_text(
        "📝 تسجيل بيانات نزيل جديد\n\n"
        "1️⃣ الاسم الثلاثي:",
        reply_markup=cancel_keyboard()
    )


async def statistics_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "❌ هذا الأمر للمدير فقط."
        )

        return

    conn = get_db()

    try:

        hotels = conn.execute("""
            SELECT COUNT(*)
            FROM hotels
            WHERE active = 1
        """).fetchone()[0]

        guests = conn.execute("""
            SELECT COUNT(*)
            FROM guests
        """).fetchone()[0]

    finally:

        conn.close()

    await update.message.reply_text(
        "📊 إحصائيات النظام\n\n"
        f"🏨 عدد الفنادق: {hotels}\n"
        f"👤 عدد النزلاء: {guests}",
        reply_markup=admin_menu_keyboard()
    )


async def logout_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:

        return

    set_login(
        user.id,
        False
    )

    context.user_data.clear()

    await update.message.reply_text(
        "🚪 تم تسجيل الخروج.\n\n"
        "اضغط /start للعودة.",
        reply_markup=start_keyboard()
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

        logger.info(
            "✅ قاعدة البيانات جاهزة"
        )

    except Exception:

        logger.exception(
            "❌ خطأ في قاعدة البيانات"
        )

        return

    # -----------------------------------------------------
    # HTTP Server
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # Telegram Application
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # الأوامر
    # -----------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "add_hotel",
            add_hotel_command
        )
    )

    app.add_handler(
        CommandHandler(
            "add_guest",
            add_guest_command
        )
    )

    app.add_handler(
        CommandHandler(
            "statistics",
            statistics_command
        )
    )

    app.add_handler(
        CommandHandler(
            "logout",
            logout_command
        )
    )

    # -----------------------------------------------------
    # الأزرار
    # -----------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # -----------------------------------------------------
    # الرسائل
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
            "❌ Telegram Bot توقف"
        )


# =========================================================
# البداية
# =========================================================

if __name__ == "__main__":

    main()
