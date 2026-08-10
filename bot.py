import os
import sqlite3
import logging
import threading
import uuid
import io
from datetime import datetime, date
from collections import Counter

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
    Image as PDFImage,
    PageBreak,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


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

UPLOAD_DIR = "uploads"
PDF_DIR = "pdf_reports"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PDF_DIR, exist_ok=True)


# =========================================================
# Logging
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# الخط العربي للـ PDF
# =========================================================

ARABIC_FONT = None

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

try:
    if os.path.exists(FONT_PATHS[0]):
        pdfmetrics.registerFont(
            TTFont("ArabicFont", FONT_PATHS[0])
        )
        ARABIC_FONT = "ArabicFont"
except Exception:
    ARABIC_FONT = None


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
        # المستخدمون
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
        # حسابات الفنادق
        # -------------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS hotel_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hotel_name TEXT UNIQUE,
                username TEXT UNIQUE,
                password TEXT,
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
                hotel_account_id INTEGER,
                report_number TEXT UNIQUE,

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

                created_at TEXT
            )
        """)

        conn.commit()

    finally:
        conn.close()


# =========================================================
# المستخدمين
# =========================================================

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


# =========================================================
# حسابات الفنادق
# =========================================================

def create_hotel_account(hotel_name, username, password):

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO hotel_accounts
            (
                hotel_name,
                username,
                password,
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

        return True, None

    except sqlite3.IntegrityError:

        return False, "اسم الفندق أو اسم المستخدم موجود مسبقاً."

    finally:

        conn.close()


def get_hotel_by_username(username):

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM hotel_accounts
            WHERE username = ?
        """, (username,)).fetchone()

    finally:
        conn.close()


def authenticate_hotel(username, password):

    row = get_hotel_by_username(username)

    if not row:
        return None

    if row["active"] != 1:
        return "DISABLED"

    if row["password"] != password:
        return None

    return row


def set_hotel_status(hotel_id, active):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE hotel_accounts
            SET active = ?
            WHERE id = ?
        """, (
            1 if active else 0,
            hotel_id,
        ))

        conn.commit()

    finally:
        conn.close()


def get_all_hotels():

    conn = get_db()

    try:

        return conn.execute("""
            SELECT *
            FROM hotel_accounts
            ORDER BY id ASC
        """).fetchall()

    finally:
        conn.close()


# =========================================================
# صلاحيات
# =========================================================

def is_admin(user_id):

    return user_id == ADMIN_ID


def is_hotel_logged_in(context):

    return bool(
        context.user_data.get("hotel_logged_in")
    )


def get_logged_hotel(context):

    return context.user_data.get("hotel_account")


# =========================================================
# لوحة المدير
# =========================================================

def admin_menu():

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
                callback_data="last_records"
            )
        ],
    ])


# =========================================================
# لوحة الفندق
# =========================================================

def hotel_menu():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📝 تسجيل بيانات نزيل",
                callback_data="add_guest"
            )
        ],
        [
            InlineKeyboardButton(
                "📄 عرض بياناتي الحالية",
                callback_data="preview_guest"
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
# إنشاء رقم التقرير
# =========================================================

def generate_report_number():

    conn = get_db()

    try:

        count = conn.execute(
            "SELECT COUNT(*) FROM guests"
        ).fetchone()[0]

    finally:
        conn.close()

    return f"HR-{datetime.now().year}-{count + 1:06d}"


# =========================================================
# حفظ النزيل
# =========================================================

def save_guest(user_id, hotel_account_id, data):

    report_number = generate_report_number()

    conn = get_db()

    try:

        cursor = conn.execute("""
            INSERT INTO guests
            (
                telegram_id,
                hotel_account_id,
                report_number,

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
            VALUES
            (
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?,
                ?, ?,
                ?
            )
        """, (
            user_id,
            hotel_account_id,
            report_number,

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

        return cursor.lastrowid, report_number

    finally:
        conn.close()


# =========================================================
# آخر نزيل
# =========================================================

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


# =========================================================
# PDF
# =========================================================

def safe_pdf_text(value):

    if value is None:
        return ""

    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_pdf(guest):

    report_number = guest["report_number"]

    pdf_path = os.path.join(
        PDF_DIR,
        f"Hotel_Report_{report_number}.pdf"
    )

    font = ARABIC_FONT or "Helvetica"

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Hotel Report {report_number}",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleArabic",
        parent=styles["Title"],
        fontName=font,
        fontSize=20,
        alignment=TA_CENTER,
        spaceAfter=8,
    )

    subtitle_style = ParagraphStyle(
        "SubtitleArabic",
        parent=styles["Normal"],
        fontName=font,
        fontSize=10,
        alignment=TA_CENTER,
        spaceAfter=12,
    )

    normal_style = ParagraphStyle(
        "NormalArabic",
        parent=styles["Normal"],
        fontName=font,
        fontSize=10,
        leading=16,
        alignment=TA_RIGHT,
    )

    small_style = ParagraphStyle(
        "SmallArabic",
        parent=styles["Normal"],
        fontName=font,
        fontSize=8,
        alignment=TA_CENTER,
    )

    story = []

    # -----------------------------------------------------
    # رأس التقرير
    # -----------------------------------------------------

    story.append(
        Paragraph(
            "نظام إدارة معلومات الفنادق",
            title_style
        )
    )

    story.append(
        Paragraph(
            "تقرير بيانات نزيل",
            subtitle_style
        )
    )

    header_data = [
        [
            Paragraph(
                f"<b>رقم التقرير:</b><br/>{safe_pdf_text(report_number)}",
                normal_style
            ),
            Paragraph(
                f"<b>تاريخ التقرير:</b><br/>{safe_pdf_text(guest['created_at'])}",
                normal_style
            ),
        ]
    ]

    header_table = Table(
        header_data,
        colWidths=[85 * mm, 85 * mm]
    )

    header_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EAF1F8")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#244A73")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#AAB7C4")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ])
    )

    story.append(header_table)
    story.append(Spacer(1, 8 * mm))

    # -----------------------------------------------------
    # بيانات النزيل
    # -----------------------------------------------------

    story.append(
        Paragraph(
            "بيانات النزيل",
            ParagraphStyle(
                "Section",
                parent=normal_style,
                fontSize=13,
                alignment=TA_RIGHT,
                textColor=colors.HexColor("#244A73"),
                spaceAfter=6,
            )
        )
    )

    guest_data = [
        ["البيان", "المعلومات"],
        ["الاسم الثلاثي", safe_pdf_text(guest["full_name"])],
        ["اسم الأم", safe_pdf_text(guest["mother_name"])],
        ["مكان وتاريخ الولادة", safe_pdf_text(guest["birth_place_date"])],
        ["السكن الأصلي", safe_pdf_text(guest["original_residence"])],
        ["المحافظة", safe_pdf_text(guest["governorate"])],
        ["سبب الإقامة", safe_pdf_text(guest["stay_reason"])],
        ["تاريخ النزول", safe_pdf_text(guest["check_in_date"])],
        ["مدة الإقامة", safe_pdf_text(guest["stay_duration"])],
        ["ملاحظات عامة", safe_pdf_text(guest["notes"])],
    ]

    guest_table = Table(
        guest_data,
        colWidths=[50 * mm, 120 * mm],
        repeatRows=1,
    )

    guest_table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#244A73")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B8C4CF")),
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
                colors.white,
                colors.HexColor("#F5F8FA"),
            ]),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ])
    )

    story.append(guest_table)
    story.append(Spacer(1, 8 * mm))

    # -----------------------------------------------------
    # بيانات الفندق
    # -----------------------------------------------------

    story.append(
        Paragraph(
            "بيانات الفندق",
            ParagraphStyle(
                "HotelSection",
                parent=normal_style,
                fontSize=13,
                alignment=TA_RIGHT,
                textColor=colors.HexColor("#244A73"),
                spaceAfter=6,
            )
        )
    )

    hotel_data = [
        ["البيان", "المعلومات"],
        ["اسم الفندق", safe_pdf_text(guest["hotel_name"])],
        ["منطقة الفندق", safe_pdf_text(guest["hotel_area"])],
    ]

    hotel_table = Table(
        hotel_data,
        colWidths=[50 * mm, 120 * mm],
    )

    hotel_table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#244A73")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B8C4CF")),
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ])
    )

    story.append(hotel_table)

    # -----------------------------------------------------
    # صور الهوية
    # -----------------------------------------------------

    front = guest["id_front"]
    back = guest["id_back"]

    if front or back:

        story.append(PageBreak())

        story.append(
            Paragraph(
                "مرفقات الهوية الشخصية",
                title_style
            )
        )

        story.append(
            Paragraph(
                "تم إدراج صور الهوية ضمن التقرير ولا يتم إرسالها كمرفقات منفصلة.",
                subtitle_style
            )
        )

        for label, image_path in [
            ("الجهة الأمامية", front),
            ("الجهة الخلفية", back),
        ]:

            if image_path and os.path.exists(image_path):

                story.append(
                    Paragraph(
                        label,
                        ParagraphStyle(
                            "ImageTitle",
                            parent=normal_style,
                            fontSize=12,
                            alignment=TA_CENTER,
                            textColor=colors.HexColor("#244A73"),
                            spaceAfter=5,
                        )
                    )
                )

                try:

                    img = PDFImage(
                        image_path,
                        width=165 * mm,
                        height=100 * mm,
                        kind="proportional",
                    )

                    story.append(img)
                    story.append(Spacer(1, 8 * mm))

                except Exception as e:

                    logger.error(
                        f"خطأ في إدراج الصورة: {e}"
                    )

    # -----------------------------------------------------
    # توقيع التقرير
    # -----------------------------------------------------

    story.append(Spacer(1, 10 * mm))

    story.append(
        Paragraph(
            f"رقم التقرير: {safe_pdf_text(report_number)}",
            small_style
        )
    )

    story.append(
        Paragraph(
            "هذا التقرير منشأ إلكترونياً بواسطة نظام إدارة معلومات الفنادق.",
            small_style
        )
    )

    def footer(canvas, doc):

        canvas.saveState()

        canvas.setFont(
            font,
            8
        )

        canvas.setFillColor(
            colors.HexColor("#666666")
        )

        canvas.drawCentredString(
            A4[0] / 2,
            8 * mm,
            f"{report_number}  |  صفحة {doc.page}"
        )

        canvas.restoreState()

    doc.build(
        story,
        onFirstPage=footer,
        onLaterPages=footer,
    )

    return pdf_path


# =========================================================
# ترحيب الفندق
# =========================================================

async def send_hotel_welcome(update, hotel):

    await update.message.reply_text(
        "🏨 أهلاً بكم في نظام إدارة معلومات الفنادق\n\n"
        f"🏨 الفندق: {hotel['hotel_name']}\n\n"
        "يمكنكم الآن تسجيل بيانات النزيل وإرسال التقرير للإدارة.",
        reply_markup=hotel_menu()
    )


# =========================================================
# /start
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

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

        await update.message.reply_text(
            "🦅 أهلاً بك أيها المدير\n\n"
            "﴿وَقُلْ رَبِّ زِدْنِي عِلْمًا﴾\n\n"
            "👑 تم التعرف عليك كمدير.\n\n"
            "اختر العملية المطلوبة:",
            reply_markup=admin_menu()
        )

        return

    # الفندق
    await update.message.reply_text(
        "🏨 نظام إدارة معلومات الفنادق\n\n"
        "🔐 لتسجيل الدخول أرسل اسم المستخدم وكلمة المرور.\n\n"
        "صيغة الدخول:\n"
        "اسم المستخدم\n"
        "كلمة المرور",
        reply_markup=cancel_keyboard()
    )

    context.user_data["state"] = "hotel_username"


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
    # تسجيل دخول الفندق - اسم المستخدم
    # =====================================================

    if state == "hotel_username":

        context.user_data["login_username"] = text
        context.user_data["state"] = "hotel_password"

        await update.message.reply_text(
            "🔑 أرسل كلمة المرور:",
            reply_markup=cancel_keyboard()
        )

        return

    # =====================================================
    # تسجيل دخول الفندق - كلمة المرور
    # =====================================================

    if state == "hotel_password":

        username = context.user_data.get(
            "login_username",
            ""
        )

        result = authenticate_hotel(
            username,
            text
        )

        if result == "DISABLED":

            context.user_data.clear()

            await update.message.reply_text(
                "🚫 هذا الحساب معطل حالياً.\n\n"
                "يرجى التواصل مع الإدارة."
            )

            return

        if not result:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ اسم المستخدم أو كلمة المرور غير صحيحة.\n\n"
                "استخدم /start للمحاولة مرة أخرى."
            )

            return

        context.user_data.clear()

        context.user_data["hotel_logged_in"] = True
        context.user_data["hotel_account"] = dict(result)

        await send_hotel_welcome(
            update,
            result
        )

        return

    # =====================================================
    # حماية
    # =====================================================

    if not is_hotel_logged_in(context):

        await update.message.reply_text(
            "🔒 يجب تسجيل الدخول أولاً.\n\n"
            "اضغط /start"
        )

        return

    # =====================================================
    # إضافة نزيل
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
        context.user_data["state"] = "guest_area"

        await update.message.reply_text(
            "7️⃣ منطقة الفندق:"
        )

        return

    if state == "guest_area":

        context.user_data["guest"]["hotel_area"] = text
        context.user_data["state"] = "guest_reason"

        await update.message.reply_text(
            "8️⃣ سبب الإقامة:"
        )

        return

    if state == "guest_reason":

        context.user_data["guest"]["stay_reason"] = text
        context.user_data["state"] = "guest_date"

        await update.message.reply_text(
            "9️⃣ تاريخ النزول:"
        )

        return

    if state == "guest_date":

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
            "إذا لم توجد ملاحظات اكتب: لا يوجد"
        )

        return

    if state == "guest_notes":

        context.user_data["guest"]["notes"] = text
        context.user_data["state"] = "guest_id_front"

        await update.message.reply_text(
            "📷 أرسل صورة الهوية الشخصية من الجهة الأمامية."
        )

        return

    # =====================================================
    # لا توجد حالة
    # =====================================================

    await update.message.reply_text(
        "ℹ️ اختر إحدى العمليات من القائمة.",
        reply_markup=hotel_menu()
    )


# =========================================================
# استقبال الصور
# =========================================================

async def photo_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not is_hotel_logged_in(context):

        await update.message.reply_text(
            "🔒 يجب تسجيل الدخول أولاً."
        )

        return

    state = context.user_data.get("state")

    if not update.message.photo:

        return

    photo = update.message.photo[-1]

    file = await context.bot.get_file(
        photo.file_id
    )

    filename = (
        f"{uuid.uuid4().hex}.jpg"
    )

    path = os.path.join(
        UPLOAD_DIR,
        filename
    )

    await file.download_to_drive(path)

    # -----------------------------------------------------
    # الوجه الأمامي
    # -----------------------------------------------------

    if state == "guest_id_front":

        context.user_data["guest"]["id_front"] = path

        context.user_data["state"] = "guest_id_back"

        await update.message.reply_text(
            "✅ تم استلام الجهة الأمامية.\n\n"
            "📷 الآن أرسل صورة الهوية من الجهة الخلفية."
        )

        return

    # -----------------------------------------------------
    # الوجه الخلفي
    # -----------------------------------------------------

    if state == "guest_id_back":

        context.user_data["guest"]["id_back"] = path

        context.user_data["state"] = None

        await update.message.reply_text(
            "✅ تم استلام صورتي الهوية.\n\n"
            "يمكنك الآن عرض البيانات ثم إرسالها للإدارة.",
            reply_markup=hotel_menu()
        )

        return


# =========================================================
# عرض البيانات
# =========================================================

def guest_preview_text(guest):

    return (
        "📋 بيانات النزيل الحالية\n\n"
        f"👤 الاسم: {guest.get('full_name', '')}\n"
        f"👩 اسم الأم: {guest.get('mother_name', '')}\n"
        f"📍 مكان وتاريخ الولادة: {guest.get('birth_place_date', '')}\n"
        f"🏠 السكن الأصلي: {guest.get('original_residence', '')}\n"
        f"🏛 المحافظة: {guest.get('governorate', '')}\n"
        f"🏨 الفندق: {guest.get('hotel_name', '')}\n"
        f"📍 منطقة الفندق: {guest.get('hotel_area', '')}\n"
        f"📝 سبب الإقامة: {guest.get('stay_reason', '')}\n"
        f"📅 تاريخ النزول: {guest.get('check_in_date', '')}\n"
        f"⏳ مدة الإقامة: {guest.get('stay_duration', '')}\n"
        f"📌 ملاحظات: {guest.get('notes', '')}\n"
        f"🪪 الهوية الأمامية: {'✅' if guest.get('id_front') else '❌'}\n"
        f"🪪 الهوية الخلفية: {'✅' if guest.get('id_back') else '❌'}"
    )


# =========================================================
# إرسال التقرير للإدارة
# =========================================================

async def send_guest_to_admin(
    update,
    context
):

    query = update.callback_query

    guest = context.user_data.get("guest")

    if not guest:

        await query.edit_message_text(
            "❌ لا توجد بيانات نزيل جاهزة للإرسال.",
            reply_markup=hotel_menu()
        )

        return

    if not guest.get("id_front") or not guest.get("id_back"):

        await query.edit_message_text(
            "❌ يجب إرسال صور الهوية الأمامية والخلفية أولاً.",
            reply_markup=hotel_menu()
        )

        return

    hotel = get_logged_hotel(context)

    if not hotel:

        await query.edit_message_text(
            "❌ انتهت جلسة تسجيل الدخول.",
        )

        return

    guest_id, report_number = save_guest(
        update.effective_user.id,
        hotel["id"],
        guest
    )

    conn = get_db()

    try:

        saved_guest = conn.execute("""
            SELECT *
            FROM guests
            WHERE id = ?
        """, (guest_id,)).fetchone()

    finally:
        conn.close()

    pdf_path = build_pdf(saved_guest)

    caption = (
        "📄 تقرير نزيل جديد\n\n"
        f"🔢 رقم التقرير: {report_number}\n"
        f"🏨 الفندق: {guest['hotel_name']}\n"
        f"👤 الاسم: {guest['full_name']}\n"
        f"📅 التاريخ: {guest['check_in_date']}\n\n"
        "🪪 صور الهوية مدمجة داخل ملف PDF."
    )

    with open(pdf_path, "rb") as pdf_file:

        await context.bot.send_document(
            chat_id=ADMIN_ID,
            document=pdf_file,
            filename=os.path.basename(pdf_path),
            caption=caption
        )

    context.user_data.pop("guest", None)
    context.user_data["state"] = None

    await query.edit_message_text(
        "✅ تم إرسال التقرير للإدارة بنجاح.\n\n"
        f"🔢 رقم التقرير: {report_number}\n\n"
        "📄 تم إرسال ملف PDF فقط، "
        "والصور مدمجة داخله.",
        reply_markup=hotel_menu()
    )


# =========================================================
# Callback
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user = update.effective_user

    if not user:
        return

    data = query.data

    # =====================================================
    # إلغاء
    # =====================================================

    if data == "cancel":

        context.user_data.clear()

        await query.edit_message_text(
            "❌ تم إلغاء العملية."
        )

        return

    # =====================================================
    # المدير
    # =====================================================

    if is_admin(user.id):

        # إضافة حساب فندق
        if data == "admin_add_hotel":

            context.user_data.clear()
            context.user_data["state"] = "new_hotel_name"

            await query.edit_message_text(
                "🏨 إضافة حساب فندق\n\n"
                "أرسل اسم الفندق:"
            )

            return

        # قائمة الفنادق
        if data == "admin_hotels":

            hotels = get_all_hotels()

            if not hotels:

                await query.edit_message_text(
                    "🏨 لا توجد حسابات فنادق.",
                    reply_markup=admin_menu()
                )

                return

            keyboard = []

            for hotel in hotels:

                status = (
                    "🟢"
                    if hotel["active"]
                    else "🔴"
                )

                keyboard.append([
                    InlineKeyboardButton(
                        f"{status} {hotel['hotel_name']}",
                        callback_data=f"hotel_toggle_{hotel['id']}"
                    )
                ])

            keyboard.append([
                InlineKeyboardButton(
                    "🔙 العودة",
                    callback_data="admin_back"
                )
            ])

            await query.edit_message_text(
                "🏨 حسابات الفنادق\n\n"
                "🟢 فعال\n"
                "🔴 معطل\n\n"
                "اضغط على الفندق لتغيير حالته.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            return

        # تفعيل / تعطيل
        if data.startswith("hotel_toggle_"):

            hotel_id = int(
                data.replace(
                    "hotel_toggle_",
                    ""
                )
            )

            conn = get_db()

            try:

                hotel = conn.execute("""
                    SELECT *
                    FROM hotel_accounts
                    WHERE id = ?
                """, (hotel_id,)).fetchone()

            finally:
                conn.close()

            if hotel:

                new_status = 0 if hotel["active"] else 1

                set_hotel_status(
                    hotel_id,
                    new_status
                )

                status_text = (
                    "🟢 تم تفعيل الحساب."
                    if new_status
                    else
                    "🔴 تم تعطيل الحساب."
                )

                await query.edit_message_text(
                    f"{status_text}\n\n"
                    f"🏨 الفندق: {hotel['hotel_name']}\n"
                    f"👤 المستخدم: {hotel['username']}",
                    reply_markup=admin_menu()
                )

            return

        # العودة
        if data == "admin_back":

            await query.edit_message_text(
                "👑 لوحة المدير\n\n"
                "اختر العملية المطلوبة:",
                reply_markup=admin_menu()
            )

            return

        # التقارير
        if data in ("daily_report", "monthly_report"):

            await generate_statistics_report(
                update,
                context,
                data
            )

            return

        # آخر السجلات
        if data == "last_records":

            conn = get_db()

            try:

                rows = conn.execute("""
                    SELECT
                        report_number,
                        full_name,
                        hotel_name,
                        governorate,
                        created_at
                    FROM guests
                    ORDER BY id DESC
                    LIMIT 10
                """).fetchall()

            finally:
                conn.close()

            if not rows:

                text = "📋 لا توجد سجلات."

            else:

                text = "📋 آخر 10 سجلات:\n\n"

                for i, row in enumerate(rows, 1):

                    text += (
                        f"{i}. {row['report_number']}\n"
                        f"👤 {row['full_name']}\n"
                        f"🏨 {row['hotel_name']}\n"
                        f"🏛 {row['governorate']}\n"
                        f"🕐 {row['created_at']}\n\n"
                    )

            await query.edit_message_text(
                text,
                reply_markup=admin_menu()
            )

            return

        return

    # =====================================================
    # الفندق
    # =====================================================

    if not is_hotel_logged_in(context):

        await query.edit_message_text(
            "🔒 يجب تسجيل الدخول أولاً."
        )

        return

    # إضافة نزيل
    if data == "add_guest":

        context.user_data["guest"] = {}
        context.user_data["state"] = "guest_full_name"

        await query.edit_message_text(
            "📝 تسجيل بيانات نزيل جديد\n\n"
            "1️⃣ الاسم الثلاثي:",
            reply_markup=cancel_keyboard()
        )

        return

    # عرض البيانات
    if data == "preview_guest":

        guest = context.user_data.get("guest")

        if not guest:

            await query.edit_message_text(
                "📋 لا توجد بيانات نزيل حالياً.",
                reply_markup=hotel_menu()
            )

            return

        await query.edit_message_text(
            guest_preview_text(guest),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📤 إرسال للإدارة",
                        callback_data="send_guest"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 العودة",
                        callback_data="hotel_back"
                    )
                ],
            ])
        )

        return

    # إرسال
    if data == "send_guest":

        await send_guest_to_admin(
            update,
            context
        )

        return

    # العودة
    if data == "hotel_back":

        await query.edit_message_text(
            "🏨 القائمة الرئيسية:",
            reply_markup=hotel_menu()
        )

        return

    # تسجيل الخروج
    if data == "hotel_logout":

        context.user_data.clear()

        await query.edit_message_text(
            "🚪 تم تسجيل الخروج.\n\n"
            "اضغط /start لتسجيل الدخول مجدداً."
        )

        return


# =========================================================
# التقارير الإحصائية
# =========================================================

async def generate_statistics_report(
    update,
    context,
    report_type
):

    query = update.callback_query

    conn = get_db()

    try:

        if report_type == "daily_report":

            today = date.today().strftime("%Y-%m-%d")

            rows = conn.execute("""
                SELECT *
                FROM guests
                WHERE substr(created_at, 1, 10) = ?
                ORDER BY id DESC
            """, (today,)).fetchall()

            title = f"📊 التقرير اليومي - {today}"

        else:

            month = datetime.now().strftime("%Y-%m")

            rows = conn.execute("""
                SELECT *
                FROM guests
                WHERE substr(created_at, 1, 7) = ?
                ORDER BY id DESC
            """, (month,)).fetchall()

            title = f"📅 التقرير الشهري - {month}"

    finally:
        conn.close()

    if not rows:

        await query.edit_message_text(
            title + "\n\nلا توجد بيانات.",
            reply_markup=admin_menu()
        )

        return

    governorates = Counter(
        row["governorate"]
        for row in rows
        if row["governorate"]
    )

    hotels = Counter(
        row["hotel_name"]
        for row in rows
        if row["hotel_name"]
    )

    reasons = Counter(
        row["stay_reason"]
        for row in rows
        if row["stay_reason"]
    )

    text = (
        f"{title}\n\n"
        f"👤 إجمالي النزلاء: {len(rows)}\n\n"
        "🏛 التوزع حسب المحافظات:\n"
    )

    for name, count in governorates.most_common():

        text += f"• {name}: {count}\n"

    text += "\n🏨 حسب الفنادق:\n"

    for name, count in hotels.most_common():

        text += f"• {name}: {count}\n"

    text += "\n📝 حسب سبب الإقامة:\n"

    for name, count in reasons.most_common():

        text += f"• {name}: {count}\n"

    await query.edit_message_text(
        text,
        reply_markup=admin_menu()
    )


# =========================================================
# معالجة إنشاء حساب الفندق
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

    state = context.user_data.get("state")

    if state == "new_hotel_name":

        context.user_data["new_hotel_name"] = (
            update.message.text.strip()
        )

        context.user_data["state"] = "new_hotel_username"

        await update.message.reply_text(
            "👤 أرسل اسم المستخدم للحساب:"
        )

        return True

    if state == "new_hotel_username":

        context.user_data["new_hotel_username"] = (
            update.message.text.strip()
        )

        context.user_data["state"] = "new_hotel_password"

        await update.message.reply_text(
            "🔑 أرسل كلمة المرور للحساب:"
        )

        return True

    if state == "new_hotel_password":

        hotel_name = context.user_data.get(
            "new_hotel_name"
        )

        username = context.user_data.get(
            "new_hotel_username"
        )

        password = update.message.text.strip()

        success, error = create_hotel_account(
            hotel_name,
            username,
            password
        )

        context.user_data.clear()

        if success:

            await update.message.reply_text(
                "✅ تم إنشاء حساب الفندق بنجاح.\n\n"
                f"🏨 الفندق: {hotel_name}\n"
                f"👤 اسم المستخدم: {username}\n"
                f"🔑 كلمة المرور: {password}\n\n"
                "⚠️ احتفظ ببيانات الدخول بشكل آمن.",
                reply_markup=admin_menu()
            )

        else:

            await update.message.reply_text(
                f"❌ فشل إنشاء الحساب.\n\n{error}",
                reply_markup=admin_menu()
            )

        return True

    return False


# =========================================================
# دمج استقبال الرسائل
# =========================================================

async def universal_message_handler(
    update,
    context
):

    if await admin_text_handler(
        update,
        context
    ):
        return

    await message_handler(
        update,
        context
    )


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
# main
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
            "❌ خطأ في قاعدة البيانات"
        )

        return

    # Render health server

    try:

        thread = threading.Thread(
            target=start_health_server,
            daemon=True
        )

        thread.start()

    except Exception:

        logger.exception(
            "❌ خطأ في HTTP server"
        )

    # Telegram

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
            photo_handler
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            universal_message_handler
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

    try:

        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )

    except Exception:

        logger.exception(
            "❌ البوت توقف"
        )


# =========================================================
# البداية
# =========================================================

if __name__ == "__main__":

    main()
