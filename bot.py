import os
import re
import sqlite3
import hashlib
import logging
import asyncio
import threading

from datetime import datetime, date
from pathlib import Path
from threading import Lock

from flask import Flask

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as RLImage,
    KeepTogether,
)

from PIL import Image as PILImage


# =========================================================
# الإعدادات
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# يتم أخذ ID المدير من Render Environment Variables
# ولا يوجد ID ثابت داخل الكود
ADMIN_ID_RAW = (
    os.getenv("ADMIN_ID", "").strip()
    or os.getenv("ADMIN_TELEGRAM_ID", "").strip()
)

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except Exception:
    ADMIN_ID = 0

PORT = int(os.getenv("PORT", "10000"))

DB_FILE = "hotel_bot.db"

FILES_DIR = Path("bot_files")
PDF_DIR = FILES_DIR / "pdf"
PHOTO_DIR = FILES_DIR / "photos"

FILES_DIR.mkdir(exist_ok=True)
PDF_DIR.mkdir(exist_ok=True)
PHOTO_DIR.mkdir(exist_ok=True)


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
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("hotel_bot")

DB_LOCK = Lock()


# =========================================================
# قاعدة البيانات
# =========================================================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return date.today().strftime("%Y-%m-%d")


def init_db():
    with DB_LOCK:
        conn = db()

        try:
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS hotel_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hotel_name TEXT NOT NULL,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS guests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hotel_id INTEGER,
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
                    front_photo TEXT,
                    back_photo TEXT,
                    created_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guest_id INTEGER,
                    hotel_id INTEGER,
                    is_read INTEGER DEFAULT 0,
                    created_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS hotels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT
                )
            """)

            conn.commit()

            for hotel in DEFAULT_HOTELS:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO hotels
                    (name, enabled, created_at)
                    VALUES (?, 1, ?)
                    """,
                    (hotel, now())
                )

            conn.commit()

        finally:
            conn.close()

    logger.info("✅ Database ready")


# =========================================================
# صلاحيات المدير
# =========================================================

def is_admin(user_id):
    if ADMIN_ID <= 0:
        return False

    try:
        return int(user_id) == ADMIN_ID
    except Exception:
        return False


# =========================================================
# كلمات المرور
# =========================================================

def hash_password(password):
    return hashlib.sha256(
        password.encode("utf-8")
    ).hexdigest()


# =========================================================
# الفنادق
# =========================================================

def get_hotels():
    conn = db()

    try:
        return conn.execute("""
            SELECT *
            FROM hotels
            WHERE enabled = 1
            ORDER BY id ASC
        """).fetchall()

    finally:
        conn.close()


def add_hotel(name):
    name = (name or "").strip()

    if not name:
        return False

    with DB_LOCK:
        conn = db()

        try:
            conn.execute(
                """
                INSERT INTO hotels
                (name, enabled, created_at)
                VALUES (?, 1, ?)
                """,
                (name, now())
            )

            conn.commit()
            return True

        except sqlite3.IntegrityError:
            conn.rollback()
            return False

        finally:
            conn.close()


# =========================================================
# حسابات الفنادق
# =========================================================

def create_hotel_account(
    hotel_name,
    username,
    password
):
    hotel_name = (hotel_name or "").strip()
    username = (username or "").strip()
    password = password or ""

    if not hotel_name:
        return False, "اسم الفندق غير موجود."

    if not username:
        return False, "اسم المستخدم غير موجود."

    if not password:
        return False, "كلمة المرور غير موجودة."

    password_hash = hash_password(password)

    with DB_LOCK:
        conn = db()

        try:
            hotel = conn.execute(
                """
                SELECT id
                FROM hotels
                WHERE name = ?
                """,
                (hotel_name,)
            ).fetchone()

            if not hotel:
                return False, "الفندق غير موجود."

            existing_username = conn.execute(
                """
                SELECT id
                FROM hotel_accounts
                WHERE username = ?
                """,
                (username,)
            ).fetchone()

            if existing_username:
                return False, "اسم المستخدم مستخدم مسبقاً."

            existing_hotel = conn.execute(
                """
                SELECT id, username
                FROM hotel_accounts
                WHERE hotel_name = ?
                """,
                (hotel_name,)
            ).fetchone()

            if existing_hotel:
                return (
                    False,
                    "هذا الفندق لديه حساب مسبقاً "
                    f"({existing_hotel['username']})."
                )

            conn.execute(
                """
                INSERT INTO hotel_accounts
                (
                    hotel_name,
                    username,
                    password_hash,
                    enabled,
                    created_at
                )
                VALUES (?, ?, ?, 1, ?)
                """,
                (
                    hotel_name,
                    username,
                    password_hash,
                    now()
                )
            )

            conn.commit()

            return True, "تم إنشاء الحساب."

        except sqlite3.IntegrityError:
            conn.rollback()
            return False, "اسم المستخدم مستخدم مسبقاً."

        except Exception:
            conn.rollback()
            logger.exception(
                "Error creating hotel account"
            )
            return False, "حدث خطأ أثناء إنشاء الحساب."

        finally:
            conn.close()


def login_hotel(username, password):
    conn = db()

    try:
        row = conn.execute(
            """
            SELECT *
            FROM hotel_accounts
            WHERE username = ?
            """,
            ((username or "").strip(),)
        ).fetchone()

        if not row:
            return None, "❌ اسم المستخدم أو كلمة المرور غير صحيحة."

        if row["enabled"] != 1:
            return None, "❌ هذا الحساب معطل من قبل الإدارة."

        if row["password_hash"] != hash_password(password or ""):
            return None, "❌ اسم المستخدم أو كلمة المرور غير صحيحة."

        return row, None

    finally:
        conn.close()


def get_hotel_accounts():
    conn = db()

    try:
        return conn.execute("""
            SELECT *
            FROM hotel_accounts
            ORDER BY hotel_name, username
        """).fetchall()

    finally:
        conn.close()


def set_hotel_account_status(account_id, status):
    with DB_LOCK:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE hotel_accounts
                SET enabled = ?
                WHERE id = ?
                """,
                (
                    1 if status else 0,
                    account_id
                )
            )

            conn.commit()

        finally:
            conn.close()


# =========================================================
# النزلاء
# =========================================================

def save_guest(
    hotel_id,
    hotel_name,
    data
):
    with DB_LOCK:
        conn = db()

        try:
            cur = conn.execute(
                """
                INSERT INTO guests
                (
                    hotel_id,
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
                    front_photo,
                    back_photo,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hotel_id,
                    hotel_name,
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
                    data.get("front_photo", ""),
                    data.get("back_photo", ""),
                    now()
                )
            )

            guest_id = cur.lastrowid

            conn.execute(
                """
                INSERT INTO inbox
                (
                    guest_id,
                    hotel_id,
                    is_read,
                    created_at
                )
                VALUES (?, ?, 0, ?)
                """,
                (
                    guest_id,
                    hotel_id,
                    now()
                )
            )

            conn.commit()

            return guest_id

        finally:
            conn.close()


def unread_count():
    conn = db()

    try:
        return conn.execute(
            """
            SELECT COUNT(*)
            FROM inbox
            WHERE is_read = 0
            """
        ).fetchone()[0]

    finally:
        conn.close()


def get_inbox(limit=20):
    conn = db()

    try:
        return conn.execute(
            """
            SELECT
                inbox.id AS inbox_id,
                inbox.is_read,
                inbox.created_at,
                guests.*,
                hotels.name AS hotel_display
            FROM inbox
            JOIN guests
                ON guests.id = inbox.guest_id
            LEFT JOIN hotels
                ON hotels.id = inbox.hotel_id
            ORDER BY inbox.id DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()

    finally:
        conn.close()


def mark_inbox_read(inbox_id):
    with DB_LOCK:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE inbox
                SET is_read = 1
                WHERE id = ?
                """,
                (inbox_id,)
            )

            conn.commit()

        finally:
            conn.close()


# =========================================================
# التقارير
# =========================================================

def report_data(
    start_date=None,
    end_date=None
):
    conn = db()

    try:
        condition = ""
        params = []

        if start_date and end_date:
            condition = """
                WHERE date(created_at)
                BETWEEN ? AND ?
            """
            params = [
                start_date,
                end_date
            ]

        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM guests
            {condition}
            """,
            params
        ).fetchone()[0]

        by_governorate = conn.execute(
            f"""
            SELECT governorate, COUNT(*) AS total
            FROM guests
            {condition}
            GROUP BY governorate
            ORDER BY total DESC
            """,
            params
        ).fetchall()

        by_hotel = conn.execute(
            f"""
            SELECT hotel_name, COUNT(*) AS total
            FROM guests
            {condition}
            GROUP BY hotel_name
            ORDER BY total DESC
            """,
            params
        ).fetchall()

        by_reason = conn.execute(
            f"""
            SELECT stay_reason, COUNT(*) AS total
            FROM guests
            {condition}
            GROUP BY stay_reason
            ORDER BY total DESC
            """,
            params
        ).fetchall()

        return {
            "total": total,
            "governorates": by_governorate,
            "hotels": by_hotel,
            "reasons": by_reason
        }

    finally:
        conn.close()


# =========================================================
# PDF
# =========================================================

def make_pdf(guest):
    filename = (
        f"guest_{guest['id']}_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    )

    path = PDF_DIR / filename

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ArabicTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=20,
        leading=25,
        spaceAfter=10,
    )

    subtitle_style = ParagraphStyle(
        "ArabicSubtitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=10,
        leading=15,
        spaceAfter=12,
    )

    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        alignment=TA_RIGHT,
        fontSize=9,
        leading=13,
    )

    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        alignment=TA_RIGHT,
        fontSize=10,
        leading=14,
    )

    story = [
        Paragraph(
            "نظام إدارة معلومات الفنادق",
            title_style
        ),
        Paragraph(
            "استمارة بيانات نزيل — تقرير رسمي",
            subtitle_style
        ),
        Paragraph(
            f"رقم التقرير: HR-{guest['id']:06d}",
            subtitle_style
        )
    ]

    data = [
        ["البيان", "المعلومات"],
        ["الاسم الثلاثي", guest["full_name"]],
        ["اسم الأم", guest["mother_name"]],
        [
            "مكان وتاريخ الولادة",
            guest["birth_place_date"]
        ],
        [
            "السكن الأصلي",
            guest["original_residence"]
        ],
        ["المحافظة", guest["governorate"]],
        ["اسم الفندق", guest["hotel_name"]],
        ["منطقة الفندق", guest["hotel_area"]],
        ["سبب الإقامة", guest["stay_reason"]],
        ["تاريخ النزول", guest["check_in_date"]],
        ["مدة الإقامة", guest["stay_duration"]],
        ["ملاحظات عامة", guest["notes"]],
        ["تاريخ التسجيل", guest["created_at"]],
    ]

    formatted = []

    for index, row in enumerate(data):
        if index == 0:
            formatted.append([
                Paragraph(row[0], label_style),
                Paragraph(row[1], label_style)
            ])
        else:
            formatted.append([
                Paragraph(
                    str(row[0]),
                    label_style
                ),
                Paragraph(
                    str(row[1] or "-"),
                    value_style
                )
            ])

    table = Table(
        formatted,
        colWidths=[
            45 * mm,
            125 * mm
        ],
        repeatRows=1
    )

    table.setStyle(
        TableStyle([
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
                0.4,
                colors.HexColor("#d1d5db")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),
            (
                "BACKGROUND",
                (0, 1),
                (0, -1),
                colors.HexColor("#f3f4f6")
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                7
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                7
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
        ])
    )

    story.append(table)
    story.append(Spacer(1, 12))

    front = guest["front_photo"]
    back = guest["back_photo"]

    if front and Path(front).exists():
        try:
            img = PILImage.open(front)
            img.thumbnail((900, 600))

            temp_front = (
                PHOTO_DIR /
                f"pdf_front_{guest['id']}.jpg"
            )

            img.convert("RGB").save(
                temp_front,
                "JPEG"
            )

            story.append(
                KeepTogether([
                    Paragraph(
                        "الهوية الشخصية — الوجه الأمامي",
                        subtitle_style
                    ),
                    Spacer(1, 4),
                    RLImage(
                        str(temp_front),
                        width=75 * mm,
                        height=50 * mm
                    ),
                    Spacer(1, 10)
                ])
            )

        except Exception:
            logger.exception(
                "Could not insert front photo"
            )

    if back and Path(back).exists():
        try:
            img = PILImage.open(back)
            img.thumbnail((900, 600))

            temp_back = (
                PHOTO_DIR /
                f"pdf_back_{guest['id']}.jpg"
            )

            img.convert("RGB").save(
                temp_back,
                "JPEG"
            )

            story.append(
                KeepTogether([
                    Paragraph(
                        "الهوية الشخصية — الوجه الخلفي",
                        subtitle_style
                    ),
                    Spacer(1, 4),
                    RLImage(
                        str(temp_back),
                        width=75 * mm,
                        height=50 * mm
                    ),
                    Spacer(1, 10)
                ])
            )

        except Exception:
            logger.exception(
                "Could not insert back photo"
            )

    story.append(
        Paragraph(
            "تم إنشاء هذا التقرير إلكترونياً بواسطة نظام إدارة معلومات الفنادق.",
            subtitle_style
        )
    )

    doc.build(story)

    return str(path)


# =========================================================
# لوحات المفاتيح
# =========================================================

def admin_menu():
    count = unread_count()

    inbox_text = (
        f"📥 الوارد ({count})"
        if count
        else
        "📥 الوارد"
    )

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🏨 إضافة حساب فندق",
                callback_data="admin_add_account"
            )
        ],
        [
            InlineKeyboardButton(
                "🔴 تعطيل حساب فندق",
                callback_data="admin_disable"
            )
        ],
        [
            InlineKeyboardButton(
                "🟢 تفعيل حساب فندق",
                callback_data="admin_enable"
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
            )
        ],
        [
            InlineKeyboardButton(
                "📊 التقرير الشهري",
                callback_data="report_monthly"
            )
        ],
    ])


def welcome_keyboard():
    return InlineKeyboardMarkup([
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
                "📋 عرض البيانات",
                callback_data="guest_preview"
            )
        ],
        [
            InlineKeyboardButton(
                "📤 إرسال للإدارة",
                callback_data="guest_send"
            )
        ],
        [
            InlineKeyboardButton(
                "🚪 تسجيل الخروج",
                callback_data="hotel_logout"
            )
        ],
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


# =========================================================
# START
# =========================================================

async def start(update, context):
    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    # لا نمسح جلسة المدير بشكل أعمى
    context.user_data.clear()

    logger.info(
        "START received | user_id=%s | configured_admin_id=%s | is_admin=%s",
        user.id,
        ADMIN_ID,
        is_admin(user.id)
    )

    if is_admin(user.id):
        context.user_data["role"] = "admin"

        await update.message.reply_text(
            "🌹 السلام عليكم ورحمة الله وبركاته\n\n"
            "👑 أهلاً بك أيها المدير.\n\n"
            "🏨 نظام إدارة معلومات الفنادق\n\n"
            "✅ تم التعرف على حساب المدير تلقائياً.\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=admin_menu()
        )

        return

    await update.message.reply_text(
        "🌹 السلام عليكم ورحمة الله وبركاته\n\n"
        "🏨 نظام إدارة معلومات الفنادق\n\n"
        "يرجى اختيار نوع الدخول:",
        reply_markup=welcome_keyboard()
    )


# =========================================================
# دخول المدير
# =========================================================

async def admin_login(update, context):
    query = update.callback_query

    if not is_admin(update.effective_user.id):
        await query.answer(
            "❌ هذا الخيار مخصص للمدير فقط.",
            show_alert=True
        )
        return

    await query.answer()

    context.user_data.clear()
    context.user_data["role"] = "admin"

    await query.edit_message_text(
        "👑 أهلاً بك أيها المدير.\n\n"
        "✅ تم التحقق من صلاحيات حسابك.",
        reply_markup=admin_menu()
    )


# =========================================================
# دخول الفندق
# =========================================================

async def hotel_login(update, context):
    query = update.callback_query

    await query.answer()

    context.user_data.clear()

    context.user_data["state"] = (
        "hotel_username"
    )

    await query.edit_message_text(
        "🏨 دخول الفندق\n\n"
        "أرسل اسم المستخدم:",
        reply_markup=back_button()
    )


# =========================================================
# حفظ الصور
# =========================================================

async def save_photo(
    update,
    context,
    side
):
    photo = update.message.photo[-1]

    file = await context.bot.get_file(
        photo.file_id
    )

    filename = (
        f"{update.effective_user.id}_"
        f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_"
        f"{side}.jpg"
    )

    path = PHOTO_DIR / filename

    await file.download_to_drive(
        custom_path=str(path)
    )

    return str(path)


# =========================================================
# خطوات النزيل
# =========================================================

GUEST_STEPS = [
    (
        "full_name",
        "1️⃣ الاسم الثلاثي:"
    ),
    (
        "mother_name",
        "2️⃣ اسم الأم:"
    ),
    (
        "birth_place_date",
        "3️⃣ مكان وتاريخ الولادة:"
    ),
    (
        "original_residence",
        "4️⃣ السكن الأصلي:"
    ),
    (
        "governorate",
        "5️⃣ المحافظة:"
    ),
    (
        "hotel_area",
        "6️⃣ منطقة الفندق:"
    ),
    (
        "stay_reason",
        "7️⃣ سبب الإقامة:"
    ),
    (
        "check_in_date",
        "8️⃣ تاريخ النزول:"
    ),
    (
        "stay_duration",
        "9️⃣ مدة الإقامة:"
    ),
    (
        "notes",
        "🔟 ملاحظات عامة:"
    ),
]


async def start_guest(update, context):
    query = update.callback_query

    await query.answer()

    if "hotel_id" not in context.user_data:
        await query.edit_message_text(
            "❌ يجب تسجيل الدخول إلى حساب الفندق أولاً.",
            reply_markup=welcome_keyboard()
        )
        return

    context.user_data["guest"] = {}
    context.user_data["guest_step"] = 0
    context.user_data["state"] = "guest_data"

    await query.edit_message_text(
        GUEST_STEPS[0][1],
        reply_markup=back_button()
    )


def guest_preview_text(guest):
    return (
        "📋 <b>بيانات النزيل</b>\n\n"
        f"👤 الاسم: {guest.get('full_name', '-')}\n"
        f"👩 الأم: {guest.get('mother_name', '-')}\n"
        f"📍 الولادة: {guest.get('birth_place_date', '-')}\n"
        f"🏠 السكن الأصلي: {guest.get('original_residence', '-')}\n"
        f"🏛 المحافظة: {guest.get('governorate', '-')}\n"
        f"🏨 الفندق: {guest.get('hotel_name', '-')}\n"
        f"📍 منطقة الفندق: {guest.get('hotel_area', '-')}\n"
        f"📝 السبب: {guest.get('stay_reason', '-')}\n"
        f"📅 تاريخ النزول: {guest.get('check_in_date', '-')}\n"
        f"⏳ مدة الإقامة: {guest.get('stay_duration', '-')}\n"
        f"📌 الملاحظات: {guest.get('notes', '-')}\n\n"
        "🪪 الهوية الأمامية: "
        + (
            "✅"
            if guest.get("front_photo")
            else "❌"
        )
        + "\n"
        "🪪 الهوية الخلفية: "
        + (
            "✅"
            if guest.get("back_photo")
            else "❌"
        )
    )


def preview_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📤 إرسال للإدارة",
                callback_data="guest_send"
            )
        ],
        [
            InlineKeyboardButton(
                "📝 تعديل البيانات",
                callback_data="guest_start"
            )
        ],
        [
            InlineKeyboardButton(
                "↩️ رجوع",
                callback_data="hotel_home"
            )
        ],
    ])


# =========================================================
# استقبال النصوص
# =========================================================

async def message_handler(update, context):
    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    text = (
        update.message.text or ""
    ).strip()

    state = context.user_data.get("state")

    # -----------------------------------------------------
    # تسجيل دخول الفندق
    # -----------------------------------------------------

    if state == "hotel_username":

        if not text:
            await update.message.reply_text(
                "❌ أرسل اسم المستخدم."
            )
            return

        context.user_data[
            "login_username"
        ] = text

        context.user_data[
            "state"
        ] = "hotel_password"

        await update.message.reply_text(
            "🔐 أرسل كلمة المرور:",
            reply_markup=back_button()
        )

        return

    if state == "hotel_password":

        username = context.user_data.get(
            "login_username",
            ""
        )

        row, error = login_hotel(
            username,
            text
        )

        if error:
            await update.message.reply_text(
                error + "\n\nحاول مرة أخرى:",
                reply_markup=back_button()
            )
            return

        # -------------------------------------------------
        # تم تسجيل الدخول للفندق
        # -------------------------------------------------

        context.user_data.clear()

        context.user_data["hotel_id"] = row["id"]
        context.user_data["hotel_name"] = row["hotel_name"]
        context.user_data["hotel_username"] = row["username"]

        context.user_data["logged_hotel"] = True
        context.user_data["role"] = "hotel"

        await update.message.reply_text(
            "✅ تم تسجيل الدخول بنجاح.\n\n"
            f"🏨 الفندق: {row['hotel_name']}\n"
            f"👤 المستخدم: {row['username']}\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_menu()
        )

        return

    # -----------------------------------------------------
    # بيانات النزيل
    # -----------------------------------------------------

    if context.user_data.get("logged_hotel"):

        if state == "guest_data":

            step = context.user_data.get(
                "guest_step",
                0
            )

            if step < len(GUEST_STEPS):

                key, _ = GUEST_STEPS[step]

                if "guest" not in context.user_data:
                    context.user_data["guest"] = {}

                context.user_data[
                    "guest"
                ][key] = text

                step += 1

                context.user_data[
                    "guest_step"
                ] = step

                if step < len(GUEST_STEPS):

                    await update.message.reply_text(
                        GUEST_STEPS[step][1],
                        reply_markup=back_button()
                    )

                    return

                context.user_data[
                    "state"
                ] = "front_photo"

                await update.message.reply_text(
                    "1️⃣1️⃣ أرسل صورة الهوية الشخصية "
                    "من الجهة الأمامية:",
                    reply_markup=back_button()
                )

                return

        if state == "front_photo":

            await update.message.reply_text(
                "📷 يرجى إرسال صورة الهوية كصورة."
            )

            return

        if state == "back_photo":

            await update.message.reply_text(
                "📷 يرجى إرسال صورة الهوية الخلفية."
            )

            return

    await update.message.reply_text(
        "ℹ️ اختر إحدى العمليات من القائمة.",
        reply_markup=(
            hotel_menu()
            if context.user_data.get("logged_hotel")
            else welcome_keyboard()
        )
    )


# =========================================================
# الصور
# =========================================================

async def photo_handler(update, context):
    if not update.message:
        return

    if not context.user_data.get(
        "logged_hotel"
    ):
        await update.message.reply_text(
            "❌ يجب تسجيل الدخول إلى حساب الفندق أولاً.",
            reply_markup=welcome_keyboard()
        )
        return

    state = context.user_data.get("state")

    if state == "front_photo":

        path = await save_photo(
            update,
            context,
            "front"
        )

        if "guest" not in context.user_data:
            context.user_data["guest"] = {}

        context.user_data[
            "guest"
        ]["front_photo"] = path

        context.user_data[
            "state"
        ] = "back_photo"

        await update.message.reply_text(
            "✅ تم استلام الوجه الأمامي.\n\n"
            "1️⃣2️⃣ الآن أرسل صورة الهوية "
            "من الجهة الخلفية:",
            reply_markup=back_button()
        )

        return

    if state == "back_photo":

        path = await save_photo(
            update,
            context,
            "back"
        )

        context.user_data[
            "guest"
        ]["back_photo"] = path

        context.user_data[
            "state"
        ] = "ready"

        guest = context.user_data[
            "guest"
        ]

        guest["hotel_name"] = (
            context.user_data[
                "hotel_name"
            ]
        )

        await update.message.reply_text(
            guest_preview_text(guest),
            parse_mode="HTML",
            reply_markup=preview_keyboard()
        )

        return

    await update.message.reply_text(
        "ℹ️ لا توجد عملية لرفع الصور حالياً."
    )


# =========================================================
# إرسال النزيل للإدارة
# =========================================================

async def send_guest_to_admin(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not context.user_data.get(
        "logged_hotel"
    ):
        await query.edit_message_text(
            "❌ يجب تسجيل الدخول أولاً.",
            reply_markup=welcome_keyboard()
        )
        return

    guest = context.user_data.get(
        "guest"
    )

    if not guest:
        await query.edit_message_text(
            "❌ لا توجد بيانات نزيل جاهزة.",
            reply_markup=hotel_menu()
        )
        return

    if (
        not guest.get("front_photo")
        or
        not guest.get("back_photo")
    ):
        await query.edit_message_text(
            "❌ يجب إرسال صورتي الهوية أولاً.",
            reply_markup=preview_keyboard()
        )
        return

    guest["hotel_name"] = (
        context.user_data[
            "hotel_name"
        ]
    )

    guest_id = save_guest(
        context.user_data["hotel_id"],
        context.user_data["hotel_name"],
        guest
    )

    conn = db()

    try:
        row = conn.execute(
            """
            SELECT *
            FROM guests
            WHERE id = ?
            """,
            (guest_id,)
        ).fetchone()

    finally:
        conn.close()

    try:
        pdf_path = make_pdf(row)

    except Exception:
        logger.exception(
            "PDF creation failed"
        )

        await query.edit_message_text(
            "❌ حدث خطأ أثناء إنشاء ملف PDF.",
            reply_markup=hotel_menu()
        )

        return

    try:
        with open(
            pdf_path,
            "rb"
        ) as pdf_file:

            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=pdf_file,
                caption=(
                    "📥 وارد جديد\n\n"
                    f"🏨 الفندق: {row['hotel_name']}\n"
                    f"👤 النزيل: {row['full_name']}\n"
                    f"📅 التاريخ: {row['created_at']}\n\n"
                    f"🆔 رقم التقرير: "
                    f"HR-{guest_id:06d}"
                )
            )

    except Exception:

        logger.exception(
            "Failed sending PDF to admin"
        )

        await query.edit_message_text(
            "❌ حدث خطأ أثناء إرسال التقرير للإدارة.\n"
            "تم حفظ البيانات ويمكن المحاولة لاحقاً.",
            reply_markup=hotel_menu()
        )

        return

    context.user_data.pop(
        "guest",
        None
    )

    context.user_data.pop(
        "guest_step",
        None
    )

    context.user_data["state"] = None

    await query.edit_message_text(
        "✅ تم إرسال بيانات النزيل إلى الإدارة بنجاح.\n\n"
        f"🆔 رقم التقرير: HR-{guest_id:06d}",
        reply_markup=hotel_menu()
    )


# =========================================================
# معاينة
# =========================================================

async def show_preview(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    guest = context.user_data.get(
        "guest"
    )

    if not guest:
        await query.edit_message_text(
            "📋 لا توجد بيانات نزيل حالياً.",
            reply_markup=hotel_menu()
        )
        return

    guest["hotel_name"] = (
        context.user_data.get(
            "hotel_name",
            ""
        )
    )

    await query.edit_message_text(
        guest_preview_text(guest),
        parse_mode="HTML",
        reply_markup=preview_keyboard()
    )


# =========================================================
# وارد المدير
# =========================================================

async def admin_inbox(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):
        await query.edit_message_text(
            "❌ غير مصرح.",
            reply_markup=welcome_keyboard()
        )
        return

    rows = get_inbox(15)

    if not rows:
        await query.edit_message_text(
            "📥 الوارد\n\n"
            "لا توجد رسائل حتى الآن.",
            reply_markup=admin_menu()
        )
        return

    buttons = []

    for row in rows:

        status = (
            "🔴"
            if row["is_read"] == 0
            else "⚪"
        )

        name = (
            row["full_name"] or "بدون اسم"
        )[:25]

        hotel = (
            row["hotel_name"]
            or "بدون فندق"
        )

        buttons.append([
            InlineKeyboardButton(
                f"{status} {name} — {hotel}",
                callback_data=(
                    f"inbox_{row['inbox_id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "↩️ رجوع",
            callback_data="admin_home"
        )
    ])

    await query.edit_message_text(
        "📥 الوارد\n\n"
        "اختر التقرير:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


async def open_inbox(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):
        return

    try:
        inbox_id = int(
            query.data.split(
                "_",
                1
            )[1]
        )

    except (
        ValueError,
        IndexError
    ):
        await query.edit_message_text(
            "❌ رقم التقرير غير صحيح.",
            reply_markup=admin_menu()
        )
        return

    mark_inbox_read(
        inbox_id
    )

    conn = db()

    try:
        row = conn.execute(
            """
            SELECT
                inbox.*,
                guests.*
            FROM inbox
            JOIN guests
                ON guests.id = inbox.guest_id
            WHERE inbox.id = ?
            """,
            (inbox_id,)
        ).fetchone()

    finally:
        conn.close()

    if not row:
        await query.edit_message_text(
            "❌ التقرير غير موجود.",
            reply_markup=admin_menu()
        )
        return

    text = (
        "📥 <b>بيانات وارد</b>\n\n"
        f"🆔 التقرير: HR-{row['guest_id']:06d}\n"
        f"🏨 الفندق: {row['hotel_name']}\n"
        f"👤 الاسم: {row['full_name']}\n"
        f"👩 الأم: {row['mother_name']}\n"
        f"📍 الولادة: {row['birth_place_date']}\n"
        f"🏠 السكن الأصلي: "
        f"{row['original_residence']}\n"
        f"🏛 المحافظة: {row['governorate']}\n"
        f"📍 منطقة الفندق: "
        f"{row['hotel_area']}\n"
        f"📝 السبب: {row['stay_reason']}\n"
        f"📅 تاريخ النزول: "
        f"{row['check_in_date']}\n"
        f"⏳ المدة: "
        f"{row['stay_duration']}\n"
        f"📌 الملاحظات: "
        f"{row['notes']}\n\n"
        "🪪 الهوية موجودة داخل ملف PDF."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📄 إرسال PDF مرة أخرى",
                    callback_data=(
                        f"resend_{row['guest_id']}"
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    "↩️ رجوع",
                    callback_data="admin_inbox"
                )
            ]
        ])
    )


async def resend_pdf(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):
        return

    try:
        guest_id = int(
            query.data.split(
                "_",
                1
            )[1]
        )

    except (
        ValueError,
        IndexError
    ):
        await query.edit_message_text(
            "❌ رقم التقرير غير صحيح.",
            reply_markup=admin_menu()
        )
        return

    conn = db()

    try:
        row = conn.execute(
            """
            SELECT *
            FROM guests
            WHERE id = ?
            """,
            (guest_id,)
        ).fetchone()

    finally:
        conn.close()

    if not row:
        await query.edit_message_text(
            "❌ التقرير غير موجود.",
            reply_markup=admin_menu()
        )
        return

    pdf_path = make_pdf(row)

    try:
        with open(
            pdf_path,
            "rb"
        ) as pdf:

            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=pdf,
                caption=(
                    f"📄 تقرير HR-{guest_id:06d}\n"
                    f"🏨 {row['hotel_name']}\n"
                    f"👤 {row['full_name']}"
                )
            )

    except Exception:
        logger.exception(
            "Resend PDF failed"
        )

        await query.edit_message_text(
            "❌ فشل إرسال الملف.",
            reply_markup=admin_menu()
        )

        return

    await query.edit_message_text(
        "✅ تمت إعادة إرسال ملف PDF.",
        reply_markup=admin_menu()
    )


# =========================================================
# إضافة حساب فندق
# =========================================================

async def admin_add_account(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):
        await query.edit_message_text(
            "❌ غير مصرح.",
            reply_markup=welcome_keyboard()
        )
        return

    # نحافظ على أن المستخدم مدير
    # لكن نبدأ حالة إنشاء الحساب
    context.user_data.clear()
    context.user_data["role"] = "admin"
    context.user_data[
        "state"
    ] = "admin_select_hotel"

    hotels = get_hotels()

    buttons = []

    for hotel in hotels:
        buttons.append([
            InlineKeyboardButton(
                f"🏨 {hotel['name']}",
                callback_data=(
                    f"selecthotel_{hotel['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "➕ إضافة فندق",
            callback_data="add_new_hotel"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "↩️ رجوع",
            callback_data="admin_home"
        )
    ])

    await query.edit_message_text(
        "🏨 إضافة حساب فندق\n\n"
        "اختر الفندق:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


async def select_hotel(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):
        await query.edit_message_text(
            "❌ غير مصرح.",
            reply_markup=welcome_keyboard()
        )
        return

    try:
        hotel_id = int(
            query.data.split(
                "_",
                1
            )[1]
        )

    except (
        ValueError,
        IndexError
    ):
        await query.edit_message_text(
            "❌ اختيار الفندق غير صحيح.",
            reply_markup=admin_menu()
        )
        return

    conn = db()

    try:
        row = conn.execute(
            """
            SELECT *
            FROM hotels
            WHERE id = ?
            """,
            (hotel_id,)
        ).fetchone()

    finally:
        conn.close()

    if not row:
        await query.edit_message_text(
            "❌ الفندق غير موجود.",
            reply_markup=admin_menu()
        )
        return

    # مهم:
    # لا نعتمد على جلسة مدير للحماية.
    # صلاحية المدير يتم التحقق منها دائماً من ADMIN_ID.
    context.user_data.clear()

    context.user_data["role"] = "admin"

    context.user_data[
        "admin_hotel_id"
    ] = row["id"]

    context.user_data[
        "admin_hotel_name"
    ] = row["name"]

    context.user_data[
        "state"
    ] = "admin_username"

    await query.edit_message_text(
        f"🏨 الفندق: {row['name']}\n\n"
        "👤 أرسل اسم المستخدم الذي تريد إنشاءه:",
        reply_markup=back_button()
    )


async def add_new_hotel(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):
        await query.edit_message_text(
            "❌ غير مصرح.",
            reply_markup=welcome_keyboard()
        )
        return

    context.user_data.clear()

    context.user_data["role"] = "admin"
    context.user_data[
        "state"
    ] = "admin_new_hotel"

    await query.edit_message_text(
        "➕ إضافة فندق جديد\n\n"
        "أرسل اسم الفندق:",
        reply_markup=back_button()
    )


# =========================================================
# تعطيل / تفعيل
# =========================================================

async def disable_accounts(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):
        await query.edit_message_text(
            "❌ غير مصرح.",
            reply_markup=welcome_keyboard()
        )
        return

    accounts = get_hotel_accounts()

    buttons = []

    active_found = False

    for account in accounts:

        if account["enabled"] == 1:

            active_found = True

            buttons.append([
                InlineKeyboardButton(
                    (
                        f"🔴 {account['hotel_name']}"
                        f" — {account['username']}"
                    ),
                    callback_data=(
                        f"disable_{account['id']}"
                    )
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "↩️ رجوع",
            callback_data="admin_home"
        )
    ])

    text = (
        "🔴 تعطيل حساب فندق\n\n"
        "اختر الحساب:"
        if active_found
        else
        "🔴 تعطيل حساب فندق\n\n"
        "لا توجد حسابات مفعلة."
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


async def enable_accounts(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):
        await query.edit_message_text(
            "❌ غير مصرح.",
            reply_markup=welcome_keyboard()
        )
        return

    accounts = get_hotel_accounts()

    buttons = []

    disabled_found = False

    for account in accounts:

        if account["enabled"] == 0:

            disabled_found = True

            buttons.append([
                InlineKeyboardButton(
                    (
                        f"🟢 {account['hotel_name']}"
                        f" — {account['username']}"
                    ),
                    callback_data=(
                        f"enable_{account['id']}"
                    )
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "↩️ رجوع",
            callback_data="admin_home"
        )
    ])

    text = (
        "🟢 تفعيل حساب فندق\n\n"
        "اختر الحساب:"
        if disabled_found
        else
        "🟢 تفعيل حساب فندق\n\n"
        "لا توجد حسابات معطلة."
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================================================
# التقارير النصية
# =========================================================

def format_report(
    title,
    data
):
    text = (
        f"{title}\n\n"
        f"👥 إجمالي النزلاء: "
        f"{data['total']}\n\n"
    )

    text += "🏛 حسب المحافظات:\n"

    if data["governorates"]:

        for row in data["governorates"]:

            text += (
                f"• "
                f"{row['governorate'] or 'غير محدد'}: "
                f"{row['total']}\n"
            )

    else:
        text += "• لا توجد بيانات\n"

    text += "\n🏨 حسب الفنادق:\n"

    if data["hotels"]:

        for row in data["hotels"]:

            text += (
                f"• "
                f"{row['hotel_name'] or 'غير محدد'}: "
                f"{row['total']}\n"
            )

    else:
        text += "• لا توجد بيانات\n"

    text += "\n📝 حسب سبب الإقامة:\n"

    if data["reasons"]:

        for row in data["reasons"]:

            text += (
                f"• "
                f"{row['stay_reason'] or 'غير محدد'}: "
                f"{row['total']}\n"
            )

    else:
        text += "• لا توجد بيانات\n"

    return text


async def daily_report(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):
        return

    data = report_data(
        today(),
        today()
    )

    await query.edit_message_text(
        format_report(
            "📊 التقرير اليومي",
            data
        ),
        reply_markup=admin_menu()
    )


async def monthly_report(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):
        return

    current = date.today()

    first = current.replace(
        day=1
    )

    data = report_data(
        first.strftime("%Y-%m-%d"),
        current.strftime("%Y-%m-%d")
    )

    await query.edit_message_text(
        format_report(
            "📊 التقرير الشهري",
            data
        ),
        reply_markup=admin_menu()
    )


# =========================================================
# Callback Handler
# =========================================================

async def callback_handler(
    update,
    context
):
    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    user = update.effective_user

    if not user:
        return

    try:
        await query.answer()
    except Exception:
        pass

    admin = is_admin(user.id)

    # -----------------------------------------------------
    # الدخول
    # -----------------------------------------------------

    if data == "login_admin":

        await admin_login(
            update,
            context
        )

        return

    if data == "login_hotel":

        await hotel_login(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # رجوع
    # -----------------------------------------------------

    if data == "back":

        context.user_data.clear()

        if admin:

            context.user_data["role"] = "admin"

            await query.edit_message_text(
                "👑 لوحة تحكم المدير\n\n"
                "اختر العملية المطلوبة:",
                reply_markup=admin_menu()
            )

        else:

            await query.edit_message_text(
                "اختر نوع الدخول:",
                reply_markup=welcome_keyboard()
            )

        return

    # -----------------------------------------------------
    # المدير
    # -----------------------------------------------------

    if admin:

        if data == "admin_home":

            context.user_data.clear()
            context.user_data["role"] = "admin"

            await query.edit_message_text(
                "👑 لوحة تحكم المدير\n\n"
                "اختر العملية المطلوبة:",
                reply_markup=admin_menu()
            )

            return

        if data == "admin_add_account":

            await admin_add_account(
                update,
                context
            )

            return

        if data == "admin_disable":

            await disable_accounts(
                update,
                context
            )

            return

        if data == "admin_enable":

            await enable_accounts(
                update,
                context
            )

            return

        if data == "admin_inbox":

            await admin_inbox(
                update,
                context
            )

            return

        if data == "report_daily":

            await daily_report(
                update,
                context
            )

            return

        if data == "report_monthly":

            await monthly_report(
                update,
                context
            )

            return

        if data.startswith(
            "selecthotel_"
        ):

            await select_hotel(
                update,
                context
            )

            return

        if data == "add_new_hotel":

            await add_new_hotel(
                update,
                context
            )

            return

        if data.startswith(
            "disable_"
        ):

            try:

                account_id = int(
                    data.split(
                        "_",
                        1
                    )[1]
                )

                set_hotel_account_status(
                    account_id,
                    False
                )

                await query.edit_message_text(
                    "✅ تم تعطيل حساب الفندق.",
                    reply_markup=admin_menu()
                )

            except Exception:

                await query.edit_message_text(
                    "❌ رقم الحساب غير صحيح.",
                    reply_markup=admin_menu()
                )

            return

        if data.startswith(
            "enable_"
        ):

            try:

                account_id = int(
                    data.split(
                        "_",
                        1
                    )[1]
                )

                set_hotel_account_status(
                    account_id,
                    True
                )

                await query.edit_message_text(
                    "✅ تم تفعيل حساب الفندق.",
                    reply_markup=admin_menu()
                )

            except Exception:

                await query.edit_message_text(
                    "❌ رقم الحساب غير صحيح.",
                    reply_markup=admin_menu()
                )

            return

        if data.startswith(
            "inbox_"
        ):

            await open_inbox(
                update,
                context
            )

            return

        if data.startswith(
            "resend_"
        ):

            await resend_pdf(
                update,
                context
            )

            return

    # -----------------------------------------------------
    # الفندق
    # -----------------------------------------------------

    if context.user_data.get(
        "logged_hotel"
    ):

        if data == "guest_start":

            await start_guest(
                update,
                context
            )

            return

        if data == "guest_preview":

            await show_preview(
                update,
                context
            )

            return

        if data == "guest_send":

            await send_guest_to_admin(
                update,
                context
            )

            return

        if data == "hotel_home":

            context.user_data[
                "state"
            ] = None

            await query.edit_message_text(
                "🏨 لوحة الفندق\n\n"
                "اختر العملية المطلوبة:",
                reply_markup=hotel_menu()
            )

            return

        if data == "hotel_logout":

            context.user_data.clear()

            await query.edit_message_text(
                "🚪 تم تسجيل الخروج.",
                reply_markup=welcome_keyboard()
            )

            return


# =========================================================
# خطوات المدير النصية
# =========================================================

async def admin_text_handler(
    update,
    context
):
    if not update.message:
        return False

    if not is_admin(
        update.effective_user.id
    ):
        return False

    state = context.user_data.get(
        "state"
    )

    text = (
        update.message.text or ""
    ).strip()

    # -----------------------------------------------------
    # إضافة فندق جديد
    # -----------------------------------------------------

    if state == "admin_new_hotel":

        if not text:
            await update.message.reply_text(
                "❌ أرسل اسم الفندق."
            )
            return True

        success = add_hotel(text)

        context.user_data.clear()
        context.user_data["role"] = "admin"

        if success:

            await update.message.reply_text(
                "✅ تمت إضافة الفندق:\n\n"
                f"🏨 {text}\n\n"
                "يمكنك الآن إنشاء حساب له.",
                reply_markup=admin_menu()
            )

        else:

            await update.message.reply_text(
                "❌ هذا الفندق موجود مسبقاً "
                "أو الاسم غير صالح.",
                reply_markup=admin_menu()
            )

        return True

    # -----------------------------------------------------
    # اسم المستخدم
    # -----------------------------------------------------

    if state == "admin_username":

        username = text

        if not re.match(
            r"^[A-Za-z0-9_.-]{3,32}$",
            username
        ):
            await update.message.reply_text(
                "❌ اسم المستخدم يجب أن يحتوي على "
                "3 إلى 32 حرفاً، ويمكن استخدام "
                "الأرقام والحروف و _ و - و .\n\n"
                "أرسل اسم مستخدم آخر:"
            )
            return True

        conn = db()

        try:

            existing = conn.execute(
                """
                SELECT id
                FROM hotel_accounts
                WHERE username = ?
                """,
                (username,)
            ).fetchone()

        finally:
            conn.close()

        if existing:

            await update.message.reply_text(
                "❌ اسم المستخدم مستخدم مسبقاً.\n\n"
                "أرسل اسم مستخدم آخر:"
            )

            return True

        context.user_data[
            "new_username"
        ] = username

        context.user_data[
            "state"
        ] = "admin_password"

        await update.message.reply_text(
            "🔐 أرسل كلمة المرور للحساب:"
        )

        return True

    # -----------------------------------------------------
    # كلمة المرور
    # -----------------------------------------------------

    if state == "admin_password":

        password = text

        if len(password) < 4:

            await update.message.reply_text(
                "❌ كلمة المرور قصيرة جداً.\n"
                "يجب أن تكون 4 أحرف على الأقل."
            )

            return True

        hotel_name = (
            context.user_data.get(
                "admin_hotel_name",
                ""
            )
        )

        username = (
            context.user_data.get(
                "new_username",
                ""
            )
        )

        success, message = (
            create_hotel_account(
                hotel_name,
                username,
                password
            )
        )

        context.user_data.clear()
        context.user_data["role"] = "admin"

        if success:

            await update.message.reply_text(
                "✅ تم إنشاء حساب الفندق بنجاح.\n\n"
                f"🏨 الفندق: {hotel_name}\n"
                f"👤 اسم المستخدم: {username}\n"
                f"🔐 كلمة المرور: {password}\n\n"
                "⚠️ احفظ بيانات الدخول وأرسلها "
                "للمستخدم المسؤول عن الفندق.",
                reply_markup=admin_menu()
            )

        else:

            await update.message.reply_text(
                f"❌ {message}",
                reply_markup=admin_menu()
            )

        return True

    return False


# =========================================================
# Router
# =========================================================

async def text_router(
    update,
    context
):
    handled = await admin_text_handler(
        update,
        context
    )

    if handled:
        return

    await message_handler(
        update,
        context
    )


# =========================================================
# Error Handler
# =========================================================

async def error_handler(
    update,
    context
):
    logger.error(
        "Unhandled Telegram error:",
        exc_info=context.error
    )


# =========================================================
# Flask
# =========================================================

flask_app = Flask(__name__)


@flask_app.route(
    "/",
    methods=["GET"]
)
def health():
    return (
        "Hotel Bot is running",
        200
    )


@flask_app.route(
    "/health",
    methods=["GET"]
)
def health2():
    return (
        "OK",
        200
    )


def run_flask():
    try:

        flask_app.run(
            host="0.0.0.0",
            port=PORT,
            threaded=True,
            use_reloader=False
        )

    except Exception:
        logger.exception(
            "Flask server stopped"
        )


# =========================================================
# إنشاء تطبيق Telegram
# =========================================================

def create_telegram_app():

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router
        )
    )

    application.add_error_handler(
        error_handler
    )

    return application


# =========================================================
# تشغيل Telegram Polling
# =========================================================

async def run_telegram():
    logger.info(
        "Starting Telegram application..."
    )

    application = create_telegram_app()

    # مهم جداً:
    # حذف أي Webhook قديم حتى لا يحدث Conflict
    try:

        await application.bot.delete_webhook(
            drop_pending_updates=False
        )

        logger.info(
            "✅ Old Telegram webhook removed"
        )

    except Exception:

        logger.exception(
            "Could not remove old webhook"
        )

    await application.initialize()

    await application.start()

    logger.info(
        "======================================"
    )

    logger.info(
        "🏨 HOTEL MANAGEMENT BOT"
    )

    logger.info(
        "✅ Telegram application started"
    )

    logger.info(
        "📡 Polling mode enabled"
    )

    logger.info(
        "👑 ADMIN_ID = %s",
        ADMIN_ID
    )

    logger.info(
        "======================================"
    )

    try:

        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False
        )

        logger.info(
            "✅ Telegram polling is running"
        )

        # يبقى التطبيق يعمل
        while True:
            await asyncio.sleep(3600)

    except asyncio.CancelledError:

        logger.info(
            "Telegram polling cancelled"
        )

    except Exception:

        logger.exception(
            "Telegram polling crashed"
        )

        raise

    finally:

        try:
            await application.updater.stop()
        except Exception:
            pass

        try:
            await application.stop()
        except Exception:
            pass

        try:
            await application.shutdown()
        except Exception:
            pass


# =========================================================
# Main
# =========================================================

def main():

    # -----------------------------------------------------
    # فحص BOT_TOKEN
    # -----------------------------------------------------

    if not BOT_TOKEN:

        logger.error(
            "❌ BOT_TOKEN غير موجود في Render."
        )

        return

    # -----------------------------------------------------
    # فحص ADMIN_ID
    # -----------------------------------------------------

    if ADMIN_ID <= 0:

        logger.error(
            "❌ ADMIN_ID غير موجود أو غير صحيح."
        )

        logger.error(
            "ضع ADMIN_ID في Render → Environment Variables"
        )

        return

    # -----------------------------------------------------
    # قاعدة البيانات
    # -----------------------------------------------------

    init_db()

    # -----------------------------------------------------
    # تشغيل Flask في Thread منفصل
    # -----------------------------------------------------

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    logger.info(
        "✅ Flask health server started"
    )

    # -----------------------------------------------------
    # تشغيل Telegram
    # -----------------------------------------------------

    try:

        asyncio.run(
            run_telegram()
        )

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped manually"
        )

    except Exception:

        logger.exception(
            "❌ Telegram application stopped"
        )


# =========================================================
# Start
# =========================================================

if __name__ == "__main__":
    main()
