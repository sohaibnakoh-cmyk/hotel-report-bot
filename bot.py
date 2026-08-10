import os
import sqlite3
import logging
import threading
import asyncio
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
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
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
UPLOAD_DIR = "uploads"
PDF_DIR = "pdf_reports"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PDF_DIR, exist_ok=True)

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
            CREATE TABLE IF NOT EXISTS guests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hotel_id INTEGER,
                telegram_id INTEGER,
                full_name TEXT,
                mother_name TEXT,
                birth_place_date TEXT,
                original_residence TEXT,
                governorate TEXT,
                hotel_name TEXT,
                hotel_area TEXT,
                stay_reason TEXT,
                arrival_date TEXT,
                stay_duration TEXT,
                notes TEXT,
                id_front TEXT,
                id_back TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guest_id INTEGER,
                hotel_id INTEGER,
                telegram_id INTEGER,
                sent_at TEXT,
                is_read INTEGER DEFAULT 0
            )
        """)

        conn.commit()

        # إضافة الفنادق الافتراضية
        for hotel in DEFAULT_HOTELS:

            conn.execute("""
                INSERT OR IGNORE INTO hotels
                (name, active, created_at)
                VALUES (?, 1, ?)
            """, (
                hotel,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))

        conn.commit()

    finally:
        conn.close()


# =========================================================
# أدوات الفنادق
# =========================================================

def get_hotels(include_inactive=False):

    conn = get_db()

    try:

        if include_inactive:
            return conn.execute("""
                SELECT *
                FROM hotels
                ORDER BY name
            """).fetchall()

        return conn.execute("""
            SELECT *
            FROM hotels
            WHERE active = 1
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
        """, (hotel_id,)).fetchone()

    finally:
        conn.close()


def get_hotel_by_username(username):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM hotels
            WHERE username = ?
        """, (username,)).fetchone()

    finally:
        conn.close()


def get_hotel_by_user_id(user_id):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM hotels
            WHERE username = ?
        """, (str(user_id),)).fetchone()

    finally:
        conn.close()


def create_hotel(name, username, password):

    conn = get_db()

    try:

        # الاسم موجود
        existing_name = conn.execute("""
            SELECT *
            FROM hotels
            WHERE name = ?
        """, (name,)).fetchone()

        # اسم المستخدم موجود
        existing_username = conn.execute("""
            SELECT *
            FROM hotels
            WHERE username = ?
        """, (username,)).fetchone()

        # إذا الاسم موجود والحساب فعال
        if existing_name and existing_name["active"] == 1:
            return False, "hotel_exists"

        # اسم المستخدم مستخدم من حساب آخر
        if existing_username and (
            not existing_name
            or existing_username["id"] != existing_name["id"]
        ):
            return False, "username_exists"

        # إعادة تفعيل حساب الفندق الموجود
        if existing_name:

            conn.execute("""
                UPDATE hotels
                SET username = ?,
                    password = ?,
                    active = 1
                WHERE id = ?
            """, (
                username,
                password,
                existing_name["id"]
            ))

            conn.commit()

            return True, "reactivated"

        # إنشاء فندق جديد
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

        return True, "created"

    except sqlite3.IntegrityError:

        return False, "duplicate"

    finally:
        conn.close()


def set_hotel_status(hotel_id, active):

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
# التحقق من حساب الفندق
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
            return "disabled"

        return row

    finally:
        conn.close()


# =========================================================
# PDF
# =========================================================

def find_arabic_font():

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    ]

    for path in candidates:

        if os.path.exists(path):
            return path

    return None


ARABIC_FONT = find_arabic_font()

if ARABIC_FONT:

    try:
        pdfmetrics.registerFont(
            TTFont("ArabicFont", ARABIC_FONT)
        )
    except Exception:
        ARABIC_FONT = None


def create_guest_pdf(guest, front_path, back_path):

    filename = (
        f"guest_{guest['id']}_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    )

    path = os.path.join(
        PDF_DIR,
        filename
    )

    styles = getSampleStyleSheet()

    if ARABIC_FONT:

        title_style = ParagraphStyle(
            "ArabicTitle",
            parent=styles["Title"],
            fontName="ArabicFont",
            fontSize=20,
            leading=26,
            alignment=TA_CENTER,
            spaceAfter=15,
        )

        normal_style = ParagraphStyle(
            "ArabicNormal",
            parent=styles["Normal"],
            fontName="ArabicFont",
            fontSize=10,
            leading=18,
            alignment=TA_RIGHT,
        )

    else:

        title_style = ParagraphStyle(
            "TitleCustom",
            parent=styles["Title"],
            fontSize=20,
            alignment=TA_CENTER,
        )

        normal_style = ParagraphStyle(
            "NormalCustom",
            parent=styles["Normal"],
            fontSize=10,
            alignment=TA_RIGHT,
        )

    doc = SimpleDocTemplate(
        path,
        pagesize=A4,
        rightMargin=35,
        leftMargin=35,
        topMargin=40,
        bottomMargin=40,
    )

    story = []

    story.append(
        Paragraph(
            "نظام إدارة بيانات الفنادق",
            title_style
        )
    )

    story.append(
        Paragraph(
            f"رقم التقرير: {guest['id']:06d}",
            normal_style
        )
    )

    story.append(Spacer(1, 15))

    data = [
        ["البيان", "المعلومات"],
        ["الاسم الثلاثي", guest["full_name"] or ""],
        ["اسم الأم", guest["mother_name"] or ""],
        ["مكان وتاريخ الولادة", guest["birth_place_date"] or ""],
        ["السكن الأصلي", guest["original_residence"] or ""],
        ["المحافظة", guest["governorate"] or ""],
        ["اسم الفندق", guest["hotel_name"] or ""],
        ["منطقة الفندق", guest["hotel_area"] or ""],
        ["سبب الإقامة", guest["stay_reason"] or ""],
        ["تاريخ النزول", guest["arrival_date"] or ""],
        ["مدة الإقامة", guest["stay_duration"] or ""],
        ["ملاحظات", guest["notes"] or ""],
        ["تاريخ التسجيل", guest["created_at"] or ""],
    ]

    table = Table(
        data,
        colWidths=[150, 340],
        repeatRows=1,
    )

    table.setStyle(
        TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.6, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1),
             "ArabicFont" if ARABIC_FONT else "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f3f4f6")]),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ])
    )

    story.append(table)

    story.append(Spacer(1, 20))

    # -----------------------------------------------------
    # صور الهوية داخل PDF
    # -----------------------------------------------------

    story.append(
        Paragraph(
            "صور الهوية الشخصية",
            title_style
        )
    )

    for image_path, label in [
        (front_path, "الجهة الأمامية"),
        (back_path, "الجهة الخلفية"),
    ]:

        if image_path and os.path.exists(image_path):

            story.append(
                Paragraph(
                    label,
                    normal_style
                )
            )

            try:

                img = RLImage(
                    image_path,
                    width=350,
                    height=220,
                    kind="proportional",
                )

                story.append(img)
                story.append(Spacer(1, 15))

            except Exception:
                pass

    story.append(
        Spacer(1, 20)
    )

    story.append(
        Paragraph(
            "هذا المستند صادر إلكترونياً من نظام إدارة بيانات الفنادق.",
            normal_style
        )
    )

    doc.build(story)

    return path


# =========================================================
# لوحات المدير
# =========================================================

def admin_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🏨 إضافة حساب فندق",
                callback_data="admin_add_hotel"
            )
        ],

        [
            InlineKeyboardButton(
                "🔒 تعطيل / تفعيل حساب فندق",
                callback_data="admin_toggle_hotel"
            )
        ],

        [
            InlineKeyboardButton(
                "📥 الوارد",
                callback_data="admin_inbox"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 التقرير اليومي",
                callback_data="daily_report"
            ),
            InlineKeyboardButton(
                "📈 التقرير الشهري",
                callback_data="monthly_report"
            )
        ],

        [
            InlineKeyboardButton(
                "🏨 قائمة الفنادق",
                callback_data="hotel_list"
            )
        ],

        [
            InlineKeyboardButton(
                "🚪 تسجيل خروج",
                callback_data="admin_logout"
            )
        ],
    ])


def back_keyboard(target="admin_menu"):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "↩️ رجوع",
                callback_data=target
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
                "📋 بياناتي المسجلة",
                callback_data="my_guests"
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
# بداية البوت
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    context.user_data.clear()

    await update.message.reply_text(
        "بسم الله الرحمن الرحيم\n\n"
        "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
        "﴿إِنَّ اللَّهَ يَأْمُرُ بِالْعَدْلِ "
        "وَالْإِحْسَانِ﴾\n\n"
        "🌹 أهلاً وسهلاً بكم\n"
        "في نظام إدارة بيانات الفنادق.\n\n"
        "يرجى اختيار طريقة الدخول:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👑 دخول المدير",
                    callback_data="admin_login"
                )
            ],
            [
                InlineKeyboardButton(
                    "🏨 دخول الفندق",
                    callback_data="hotel_login"
                )
            ],
        ])
    )


# =========================================================
# دخول المدير
# =========================================================

async def admin_login(update, context):

    query = update.callback_query

    if update.effective_user.id != ADMIN_ID:

        await query.edit_message_text(
            "❌ هذا الحساب ليس حساب المدير."
        )

        return

    context.user_data.clear()
    context.user_data["role"] = "admin"

    await query.edit_message_text(
        "👑 مرحباً بك أيها المدير.\n\n"
        "تم التحقق من صلاحيات حسابك.\n\n"
        "اختر العملية المطلوبة:",
        reply_markup=admin_keyboard()
    )


# =========================================================
# دخول الفندق
# =========================================================

async def hotel_login_start(update, context):

    context.user_data.clear()

    context.user_data["state"] = "hotel_username"

    await update.callback_query.edit_message_text(
        "🏨 تسجيل دخول الفندق\n\n"
        "أرسل اسم المستخدم:",
        reply_markup=back_keyboard("start_menu")
    )


# =========================================================
# إنشاء حساب الفندق
# =========================================================

async def admin_add_hotel(update, context):

    query = update.callback_query

    await query.edit_message_text(
        "🏨 اختر الفندق الذي تريد إنشاء حساب له:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    hotel,
                    callback_data=f"select_hotel:{i}"
                )
            ]
            for i, hotel in enumerate(DEFAULT_HOTELS)
        ] + [
            [
                InlineKeyboardButton(
                    "➕ إضافة فندق جديد",
                    callback_data="new_hotel"
                )
            ],
            [
                InlineKeyboardButton(
                    "↩️ رجوع",
                    callback_data="admin_menu"
                )
            ],
        ])
    )


# =========================================================
# اختيار فندق
# =========================================================

async def select_hotel(update, context, hotel_name):

    context.user_data["selected_hotel"] = hotel_name
    context.user_data["state"] = "create_username"

    await update.callback_query.edit_message_text(
        f"🏨 الفندق: {hotel_name}\n\n"
        "أرسل اسم المستخدم الذي تريد إنشاءه لهذا الفندق:",
        reply_markup=back_keyboard("admin_add_hotel")
    )


# =========================================================
# إضافة فندق جديد
# =========================================================

async def new_hotel(update, context):

    context.user_data.clear()

    context.user_data["state"] = "new_hotel_name"

    await update.callback_query.edit_message_text(
        "➕ إضافة فندق جديد\n\n"
        "أرسل اسم الفندق:",
        reply_markup=back_keyboard("admin_add_hotel")
    )


# =========================================================
# تعطيل / تفعيل الفنادق
# =========================================================

async def admin_toggle_hotel(update, context):

    hotels = get_hotels(True)

    buttons = []

    for hotel in hotels:

        status = "🟢" if hotel["active"] else "🔴"

        buttons.append([
            InlineKeyboardButton(
                f"{status} {hotel['name']}",
                callback_data=f"toggle:{hotel['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "↩️ رجوع",
            callback_data="admin_menu"
        )
    ])

    await update.callback_query.edit_message_text(
        "🔒 تعطيل / تفعيل حسابات الفنادق\n\n"
        "🟢 فعال\n"
        "🔴 معطل\n\n"
        "اختر الفندق:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# الوارد
# =========================================================

def get_unread_count():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT COUNT(*)
            FROM inbox
            WHERE is_read = 0
        """).fetchone()[0]

    finally:
        conn.close()


async def admin_inbox(update, context):

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT
                inbox.id,
                guests.id AS guest_id,
                guests.full_name,
                guests.hotel_name,
                inbox.sent_at,
                inbox.is_read
            FROM inbox
            JOIN guests
                ON guests.id = inbox.guest_id
            ORDER BY inbox.id DESC
            LIMIT 20
        """).fetchall()

    finally:
        conn.close()

    if not rows:

        text = "📥 الوارد\n\nلا توجد رسائل حالياً."

    else:

        text = "📥 الوارد\n\n"

        for row in rows:

            status = "🆕" if row["is_read"] == 0 else "✅"

            text += (
                f"{status} رقم #{row['guest_id']}\n"
                f"👤 {row['full_name']}\n"
                f"🏨 {row['hotel_name']}\n"
                f"🕐 {row['sent_at']}\n\n"
            )

    conn = get_db()

    try:

        conn.execute("""
            UPDATE inbox
            SET is_read = 1
        """)

        conn.commit()

    finally:
        conn.close()

    await update.callback_query.edit_message_text(
        text,
        reply_markup=back_keyboard("admin_menu")
    )


# =========================================================
# إنشاء التقرير
# =========================================================

def report_data(start_date=None, end_date=None):

    conn = get_db()

    try:

        query = """
            SELECT *
            FROM guests
            WHERE 1=1
        """

        params = []

        if start_date:

            query += " AND DATE(created_at) >= ?"
            params.append(start_date)

        if end_date:

            query += " AND DATE(created_at) <= ?"
            params.append(end_date)

        rows = conn.execute(
            query,
            params
        ).fetchall()

        return rows

    finally:
        conn.close()


def make_report_text(rows, title):

    if not rows:

        return (
            f"{title}\n\n"
            "لا توجد بيانات خلال الفترة المحددة."
        )

    total = len(rows)

    governors = {}
    countries = {}
    hotels = {}
    reasons = {}

    for row in rows:

        governor = row["governorate"] or "غير محدد"
        hotel = row["hotel_name"] or "غير محدد"
        reason = row["stay_reason"] or "غير محدد"

        governors[governor] = governors.get(governor, 0) + 1
        hotels[hotel] = hotels.get(hotel, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1

        residence = row["original_residence"] or "غير محدد"

        countries[residence] = countries.get(
            residence,
            0
        ) + 1

    text = (
        f"{title}\n\n"
        f"👤 إجمالي النزلاء: {total}\n\n"
        "🏛 حسب المحافظات:\n"
    )

    for key, value in sorted(
        governors.items(),
        key=lambda x: x[1],
        reverse=True
    ):

        text += f"• {key}: {value}\n"

    text += "\n🌍 حسب السكن/الدول:\n"

    for key, value in sorted(
        countries.items(),
        key=lambda x: x[1],
        reverse=True
    ):

        text += f"• {key}: {value}\n"

    text += "\n🏨 حسب الفنادق:\n"

    for key, value in sorted(
        hotels.items(),
        key=lambda x: x[1],
        reverse=True
    ):

        text += f"• {key}: {value}\n"

    text += "\n📝 حسب سبب الإقامة:\n"

    for key, value in sorted(
        reasons.items(),
        key=lambda x: x[1],
        reverse=True
    ):

        text += f"• {key}: {value}\n"

    return text


async def daily_report(update, context):

    today = datetime.now().strftime("%Y-%m-%d")

    rows = report_data(
        today,
        today
    )

    text = make_report_text(
        rows,
        "📊 التقرير اليومي"
    )

    await update.callback_query.edit_message_text(
        text,
        reply_markup=back_keyboard("admin_menu")
    )


async def monthly_report(update, context):

    month = datetime.now().strftime("%Y-%m")

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT *
            FROM guests
            WHERE strftime('%Y-%m', created_at) = ?
        """, (month,)).fetchall()

    finally:
        conn.close()

    text = make_report_text(
        rows,
        "📈 التقرير الشهري"
    )

    await update.callback_query.edit_message_text(
        text,
        reply_markup=back_keyboard("admin_menu")
    )


# =========================================================
# قائمة الفنادق
# =========================================================

async def hotel_list(update, context):

    hotels = get_hotels(True)

    text = "🏨 قائمة الفنادق\n\n"

    for hotel in hotels:

        status = "🟢 فعال" if hotel["active"] else "🔴 معطل"

        username = hotel["username"] or "لم يتم إنشاء حساب"

        text += (
            f"🏨 {hotel['name']}\n"
            f"👤 المستخدم: {username}\n"
            f"📌 الحالة: {status}\n\n"
        )

    await update.callback_query.edit_message_text(
        text,
        reply_markup=back_keyboard("admin_menu")
    )


# =========================================================
# إضافة نزيل
# =========================================================

async def add_guest(update, context):

    context.user_data["state"] = "guest_full_name"
    context.user_data["guest"] = {}

    await update.callback_query.edit_message_text(
        "📝 تسجيل بيانات النزيل\n\n"
        "1️⃣ الاسم الثلاثي:",
        reply_markup=back_keyboard("hotel_menu")
    )


# =========================================================
# حفظ صور الهوية
# =========================================================

async def save_photo(update, context, side):

    photo = update.message.photo[-1]

    file = await context.bot.get_file(
        photo.file_id
    )

    user_id = update.effective_user.id

    filename = (
        f"{user_id}_{side}_"
        f"{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
    )

    path = os.path.join(
        UPLOAD_DIR,
        filename
    )

    await file.download_to_drive(path)

    context.user_data["guest"][side] = path


# =========================================================
# معالجة الرسائل
# =========================================================

async def message_handler(update, context):

    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    state = context.user_data.get("state")

    text = (
        update.message.text or ""
    ).strip()

    # -----------------------------------------------------
    # تسجيل دخول الفندق
    # -----------------------------------------------------

    if state == "hotel_username":

        context.user_data["login_username"] = text
        context.user_data["state"] = "hotel_password"

        await update.message.reply_text(
            "🔐 أرسل كلمة المرور:"
        )

        return

    if state == "hotel_password":

        username = context.user_data.get(
            "login_username"
        )

        result = hotel_login(
            username,
            text
        )

        if result == "disabled":

            context.user_data.clear()

            await update.message.reply_text(
                "🔴 هذا الحساب معطل من قبل الإدارة."
            )

            return

        if not result:

            context.user_data["state"] = "hotel_username"

            await update.message.reply_text(
                "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"
                "أرسل اسم المستخدم مرة أخرى:"
            )

            return

        context.user_data.clear()

        context.user_data["role"] = "hotel"
        context.user_data["hotel_id"] = result["id"]
        context.user_data["hotel_name"] = result["name"]

        await update.message.reply_text(
            f"🏨 أهلاً بك في حساب فندق {result['name']}\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_keyboard()
        )

        return

    # -----------------------------------------------------
    # إنشاء اسم مستخدم الفندق
    # -----------------------------------------------------

    if state == "create_username":

        username = text

        hotel_name = context.user_data.get(
            "selected_hotel"
        )

        conn = get_db()

        try:

            existing = conn.execute("""
                SELECT *
                FROM hotels
                WHERE username = ?
            """, (username,)).fetchone()

        finally:
            conn.close()

        if existing:

            # إذا كان نفس الفندق
            if existing["name"] == hotel_name:

                context.user_data["new_username"] = username
                context.user_data["state"] = "create_password"

                await update.message.reply_text(
                    "ℹ️ هذا الفندق لديه حساب بهذا الاسم.\n\n"
                    "أرسل كلمة المرور الجديدة لتحديث الحساب:"
                )

                return

            await update.message.reply_text(
                "❌ اسم المستخدم مستخدم مسبقاً.\n\n"
                "أرسل اسم مستخدم آخر:"
            )

            return

        context.user_data["new_username"] = username
        context.user_data["state"] = "create_password"

        await update.message.reply_text(
            "🔐 أرسل كلمة المرور للحساب:"
        )

        return

    # -----------------------------------------------------
    # كلمة مرور الفندق
    # -----------------------------------------------------

    if state == "create_password":

        hotel_name = context.user_data.get(
            "selected_hotel"
        )

        username = context.user_data.get(
            "new_username"
        )

        password = text

        ok, result = create_hotel(
            hotel_name,
            username,
            password
        )

        context.user_data.clear()

        if not ok:

            await update.message.reply_text(
                "❌ تعذر إنشاء الحساب.\n\n"
                "قد يكون اسم المستخدم مستخدماً من فندق آخر.",
                reply_markup=admin_keyboard()
            )

            return

        await update.message.reply_text(
            "✅ تم إنشاء/تحديث حساب الفندق بنجاح.\n\n"
            f"🏨 الفندق: {hotel_name}\n"
            f"👤 اسم المستخدم: {username}\n"
            f"🔐 كلمة المرور: {password}\n\n"
            "⚠️ احتفظ ببيانات الدخول بشكل آمن.",
            reply_markup=admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # اسم الفندق الجديد
    # -----------------------------------------------------

    if state == "new_hotel_name":

        name = text

        conn = get_db()

        try:

            exists = conn.execute("""
                SELECT *
                FROM hotels
                WHERE name = ?
            """, (name,)).fetchone()

        finally:
            conn.close()

        if exists:

            await update.message.reply_text(
                "❌ هذا الفندق موجود مسبقاً.\n\n"
                "أرسل اسم فندق آخر:"
            )

            return

        context.user_data["selected_hotel"] = name
        context.user_data["state"] = "create_username"

        await update.message.reply_text(
            f"🏨 الفندق: {name}\n\n"
            "أرسل اسم المستخدم:"
        )

        return

    # -----------------------------------------------------
    # بيانات النزيل
    # -----------------------------------------------------

    if context.user_data.get("role") != "hotel":

        return

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
        context.user_data["state"] = "guest_hotel_area"

        await update.message.reply_text(
            "6️⃣ منطقة الفندق:"
        )

        return

    if state == "guest_hotel_area":

        context.user_data["guest"]["hotel_area"] = text

        hotel_name = context.user_data.get(
            "hotel_name",
            ""
        )

        context.user_data["guest"]["hotel_name"] = hotel_name

        context.user_data["state"] = "guest_reason"

        await update.message.reply_text(
            "7️⃣ سبب الإقامة:"
        )

        return

    if state == "guest_reason":

        context.user_data["guest"]["stay_reason"] = text
        context.user_data["state"] = "guest_arrival"

        await update.message.reply_text(
            "8️⃣ تاريخ النزول:"
        )

        return

    if state == "guest_arrival":

        context.user_data["guest"]["arrival_date"] = text
        context.user_data["state"] = "guest_duration"

        await update.message.reply_text(
            "9️⃣ مدة الإقامة:"
        )

        return

    if state == "guest_duration":

        context.user_data["guest"]["stay_duration"] = text
        context.user_data["state"] = "guest_notes"

        await update.message.reply_text(
            "🔟 ملاحظات عامة:\n\n"
            "إذا لا توجد ملاحظات اكتب: لا يوجد"
        )

        return

    if state == "guest_notes":

        context.user_data["guest"]["notes"] = text
        context.user_data["state"] = "guest_id_front"

        await update.message.reply_text(
            "🪪 أرسل صورة الهوية الشخصية من الجهة الأمامية:"
        )

        return


# =========================================================
# استقبال الصور
# =========================================================

async def photo_handler(update, context):

    state = context.user_data.get("state")

    if state == "guest_id_front":

        await save_photo(
            update,
            context,
            "id_front"
        )

        context.user_data["state"] = "guest_id_back"

        await update.message.reply_text(
            "✅ تم استلام الجهة الأمامية.\n\n"
            "🪪 أرسل صورة الهوية من الجهة الخلفية:"
        )

        return

    if state == "guest_id_back":

        await save_photo(
            update,
            context,
            "id_back"
        )

        guest = context.user_data["guest"]

        hotel_id = context.user_data.get(
            "hotel_id"
        )

        guest["hotel_name"] = context.user_data.get(
            "hotel_name",
            ""
        )

        conn = get_db()

        try:

            cursor = conn.execute("""
                INSERT INTO guests
                (
                    hotel_id,
                    telegram_id,
                    full_name,
                    mother_name,
                    birth_place_date,
                    original_residence,
                    governorate,
                    hotel_name,
                    hotel_area,
                    stay_reason,
                    arrival_date,
                    stay_duration,
                    notes,
                    id_front,
                    id_back,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hotel_id,
                update.effective_user.id,
                guest.get("full_name", ""),
                guest.get("mother_name", ""),
                guest.get("birth_place_date", ""),
                guest.get("original_residence", ""),
                guest.get("governorate", ""),
                guest.get("hotel_name", ""),
                guest.get("hotel_area", ""),
                guest.get("stay_reason", ""),
                guest.get("arrival_date", ""),
                guest.get("stay_duration", ""),
                guest.get("notes", ""),
                guest.get("id_front", ""),
                guest.get("id_back", ""),
                "pending",
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            ))

            guest_id = cursor.lastrowid

            conn.commit()

            conn.execute("""
                INSERT INTO inbox
                (
                    guest_id,
                    hotel_id,
                    telegram_id,
                    sent_at,
                    is_read
                )
                VALUES (?, ?, ?, ?, 0)
            """, (
                guest_id,
                hotel_id,
                update.effective_user.id,
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            ))

            conn.commit()

            saved_guest = conn.execute("""
                SELECT *
                FROM guests
                WHERE id = ?
            """, (guest_id,)).fetchone()

        finally:
            conn.close()

        # إنشاء PDF
        pdf_path = create_guest_pdf(
            saved_guest,
            guest.get("id_front"),
            guest.get("id_back")
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ تم استلام بيانات النزيل بنجاح.\n\n"
            f"📄 رقم الطلب: #{guest_id}\n\n"
            "تم إرسال البيانات إلى الإدارة.",
            reply_markup=hotel_keyboard()
        )

        # إرسال PDF للإدارة
        if ADMIN_ID:

            try:

                with open(pdf_path, "rb") as pdf:

                    await context.bot.send_document(
                        chat_id=ADMIN_ID,
                        document=pdf,
                        caption=(
                            "📥 وارد جديد\n\n"
                            f"📄 رقم الطلب: #{guest_id}\n"
                            f"🏨 الفندق: {saved_guest['hotel_name']}\n"
                            f"👤 النزيل: {saved_guest['full_name']}"
                        )
                    )

            except Exception:

                logger.exception(
                    "فشل إرسال PDF للإدارة"
                )

        return


# =========================================================
# بيانات الفندق
# =========================================================

async def my_guests(update, context):

    hotel_id = context.user_data.get(
        "hotel_id"
    )

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT
                id,
                full_name,
                created_at
            FROM guests
            WHERE hotel_id = ?
            ORDER BY id DESC
            LIMIT 20
        """, (hotel_id,)).fetchall()

    finally:
        conn.close()

    if not rows:

        text = "📋 لا توجد بيانات مسجلة حتى الآن."

    else:

        text = "📋 آخر بيانات قمت بتسجيلها:\n\n"

        for row in rows:

            text += (
                f"📄 #{row['id']}\n"
                f"👤 {row['full_name']}\n"
                f"🕐 {row['created_at']}\n\n"
            )

    await update.callback_query.edit_message_text(
        text,
        reply_markup=back_keyboard("hotel_menu")
    )


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

    # -----------------------------------------------------
    # بداية
    # -----------------------------------------------------

    if data == "start_menu":

        await query.edit_message_text(
            "بسم الله الرحمن الرحيم\n\n"
            "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
            "🌹 أهلاً وسهلاً بكم في نظام إدارة بيانات الفنادق.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "👑 دخول المدير",
                        callback_data="admin_login"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏨 دخول الفندق",
                        callback_data="hotel_login"
                    )
                ],
            ])
        )

        return

    # -----------------------------------------------------
    # مدير
    # -----------------------------------------------------

    if data == "admin_login":

        await admin_login(
            update,
            context
        )

        return

    if data == "admin_menu":

        if user.id != ADMIN_ID:
            return

        context.user_data.clear()
        context.user_data["role"] = "admin"

        await query.edit_message_text(
            "👑 لوحة تحكم المدير\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # دخول الفندق
    # -----------------------------------------------------

    if data == "hotel_login":

        await hotel_login_start(
            update,
            context
        )

        return

    if data == "hotel_menu":

        if context.user_data.get("role") != "hotel":
            return

        await query.edit_message_text(
            f"🏨 حساب فندق {context.user_data.get('hotel_name', '')}\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=hotel_keyboard()
        )

        return

    # -----------------------------------------------------
    # إضافة حساب فندق
    # -----------------------------------------------------

    if data == "admin_add_hotel":

        if user.id != ADMIN_ID:
            return

        await admin_add_hotel(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # فندق جاهز
    # -----------------------------------------------------

    if data.startswith("select_hotel:"):

        if user.id != ADMIN_ID:
            return

        index = int(
            data.split(":")[1]
        )

        if index < 0 or index >= len(DEFAULT_HOTELS):
            return

        await select_hotel(
            update,
            context,
            DEFAULT_HOTELS[index]
        )

        return

    # -----------------------------------------------------
    # فندق جديد
    # -----------------------------------------------------

    if data == "new_hotel":

        if user.id != ADMIN_ID:
            return

        await new_hotel(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # تعطيل / تفعيل
    # -----------------------------------------------------

    if data == "admin_toggle_hotel":

        if user.id != ADMIN_ID:
            return

        await admin_toggle_hotel(
            update,
            context
        )

        return

    if data.startswith("toggle:"):

        if user.id != ADMIN_ID:
            return

        hotel_id = int(
            data.split(":")[1]
        )

        hotel = get_hotel(
            hotel_id
        )

        if not hotel:
            return

        new_status = 0 if hotel["active"] else 1

        set_hotel_status(
            hotel_id,
            new_status
        )

        status_text = (
            "🟢 تم تفعيل الحساب."
            if new_status
            else "🔴 تم تعطيل الحساب."
        )

        await query.edit_message_text(
            f"🏨 {hotel['name']}\n\n"
            f"{status_text}",
            reply_markup=back_keyboard(
                "admin_toggle_hotel"
            )
        )

        return

    # -----------------------------------------------------
    # الوارد
    # -----------------------------------------------------

    if data == "admin_inbox":

        if user.id != ADMIN_ID:
            return

        await admin_inbox(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # التقارير
    # -----------------------------------------------------

    if data == "daily_report":

        if user.id != ADMIN_ID:
            return

        await daily_report(
            update,
            context
        )

        return

    if data == "monthly_report":

        if user.id != ADMIN_ID:
            return

        await monthly_report(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # قائمة الفنادق
    # -----------------------------------------------------

    if data == "hotel_list":

        if user.id != ADMIN_ID:
            return

        await hotel_list(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # تسجيل نزيل
    # -----------------------------------------------------

    if data == "add_guest":

        if context.user_data.get("role") != "hotel":
            return

        await add_guest(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # بياناتي
    # -----------------------------------------------------

    if data == "my_guests":

        if context.user_data.get("role") != "hotel":
            return

        await my_guests(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # خروج المدير
    # -----------------------------------------------------

    if data == "admin_logout":

        context.user_data.clear()

        await query.edit_message_text(
            "🚪 تم تسجيل خروج المدير.\n\n"
            "استخدم /start للدخول مرة أخرى."
        )

        return

    # -----------------------------------------------------
    # خروج الفندق
    # -----------------------------------------------------

    if data == "hotel_logout":

        context.user_data.clear()

        await query.edit_message_text(
            "🚪 تم تسجيل الخروج.\n\n"
            "استخدم /start للدخول مرة أخرى."
        )

        return


# =========================================================
# الأخطاء
# =========================================================

async def error_handler(update, context):

    logger.error(
        "حدث خطأ:",
        exc_info=context.error
    )


# =========================================================
# Render Health Server
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
# تشغيل البوت
# =========================================================

def main():

    if not BOT_TOKEN:

        logger.error(
            "❌ BOT_TOKEN غير موجود"
        )

        return

    if ADMIN_ID == 0:

        logger.error(
            "❌ ADMIN_ID غير موجود أو غير صحيح"
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

    # Render يحتاج منفذ HTTP
    try:

        thread = threading.Thread(
            target=start_health_server,
            daemon=True
        )

        thread.start()

    except Exception:

        logger.exception(
            "❌ فشل HTTP Server"
        )

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    # /start فقط
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

    # الصور قبل النص
    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler
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


if __name__ == "__main__":
    main()
