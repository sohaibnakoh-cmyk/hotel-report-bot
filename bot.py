import os
import re
import hashlib
import logging
import asyncio
import threading
from urllib.parse import urlparse

from datetime import datetime, date
from pathlib import Path
from threading import Lock

import psycopg2
from psycopg2.extras import RealDictCursor

from flask import Flask, request

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

# =========================================================
# دعم اللغة العربية في PDF
# =========================================================

import arabic_reshaper
from bidi.algorithm import get_display

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

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
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PORT = int(os.getenv("PORT", "10000"))

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
telegram_app = None
BOT_LOOP = None

# =========================================================
# Flask
# =========================================================

flask_app = Flask(__name__)

# =========================================================
# إعداد الخط العربي لـ ReportLab
# =========================================================

ARABIC_FONT_NAME = "ArabicFont"
ARABIC_FONT_BOLD_NAME = "ArabicFontBold"

def find_font(possible_paths):
    for path in possible_paths:
        try:
            p = Path(path)
            if p.exists():
                return str(p)
        except Exception:
            continue
    return None

def setup_arabic_fonts():
    regular_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    ]

    bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    ]

    regular_font = find_font(regular_candidates)
    bold_font = find_font(bold_candidates)

    if regular_font and not bold_font:
        bold_font = regular_font

    if not regular_font:
        logger.error("❌ لم يتم العثور على خط يدعم اللغة العربية.")
        return False

    try:
        pdfmetrics.registerFont(TTFont(ARABIC_FONT_NAME, regular_font))
        pdfmetrics.registerFont(TTFont(ARABIC_FONT_BOLD_NAME, bold_font))
        logger.info("✅ تم تحميل الخط العربي بنجاح.")
        return True
    except Exception:
        logger.exception("❌ فشل تسجيل الخطوط العربية.")
        return False

ARABIC_FONT_READY = setup_arabic_fonts()

def arabic_text(text):
    if text is None:
        return ""
    text = str(text)
    if not text:
        return ""
    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception:
        return text

def pdf_text(text):
    if text is None or not str(text).strip():
        return "-"
    return arabic_text(text)


# =========================================================
# قاعدة البيانات (PostgreSQL)
# =========================================================

def db():
    if not DATABASE_URL:
        raise ValueError("❌ DATABASE_URL غير معرف في متغيرات البيئة!")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def today():
    return date.today().strftime("%Y-%m-%d")

def init_db():
    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS hotels (
                        id SERIAL PRIMARY KEY,
                        name TEXT UNIQUE NOT NULL,
                        enabled INT DEFAULT 1,
                        created_at TEXT NOT NULL
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS hotel_accounts (
                        id SERIAL PRIMARY KEY,
                        hotel_id INT NOT NULL,
                        hotel_name TEXT NOT NULL,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        enabled INT DEFAULT 1,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (hotel_id) REFERENCES hotels(id) ON DELETE CASCADE
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS guests (
                        id SERIAL PRIMARY KEY,
                        hotel_id INT,
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
                        id SERIAL PRIMARY KEY,
                        guest_id INT,
                        hotel_id INT,
                        is_read INT DEFAULT 0,
                        created_at TEXT,
                        FOREIGN KEY (guest_id) REFERENCES guests(id) ON DELETE CASCADE
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS circulars (
                        id SERIAL PRIMARY KEY,
                        title TEXT,
                        content TEXT,
                        created_at TEXT
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        user_id BIGINT PRIMARY KEY,
                        hotel_account_id INT,
                        hotel_id INT,
                        hotel_name TEXT,
                        username TEXT,
                        state TEXT,
                        temp_data TEXT,
                        updated_at TEXT,
                        FOREIGN KEY (hotel_account_id) REFERENCES hotel_accounts(id) ON DELETE CASCADE
                    )
                """)

                for hotel in DEFAULT_HOTELS:
                    cur.execute(
                        "INSERT INTO hotels (name, enabled, created_at) VALUES (%s, 1, %s) ON CONFLICT (name) DO NOTHING",
                        (hotel, now())
                    )

                conn.commit()
        finally:
            conn.close()
    logger.info("✅ PostgreSQL Database Initialized Successfully!")


# =========================================================
# إدارة الجلسات (Sessions Management)
# =========================================================

def get_session(telegram_user_id):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sessions WHERE user_id = %s", (telegram_user_id,))
            row = cur.fetchone()
            if not row:
                return None
            
            if row["hotel_account_id"]:
                cur.execute("SELECT enabled FROM hotel_accounts WHERE id = %s", (row["hotel_account_id"],))
                acc = cur.fetchone()
                if not acc or acc["enabled"] != 1:
                    clear_session(telegram_user_id)
                    return None
            return dict(row)
    finally:
        conn.close()

def save_session(telegram_user_id, hotel_account_id, hotel_id, hotel_name, username, state="hotel_home", temp_data=""):
    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO sessions (user_id, hotel_account_id, hotel_id, hotel_name, username, state, temp_data, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        hotel_account_id=EXCLUDED.hotel_account_id,
                        hotel_id=EXCLUDED.hotel_id,
                        hotel_name=EXCLUDED.hotel_name,
                        username=EXCLUDED.username,
                        state=EXCLUDED.state,
                        temp_data=EXCLUDED.temp_data,
                        updated_at=EXCLUDED.updated_at
                """, (telegram_user_id, hotel_account_id, hotel_id, hotel_name, username, state, temp_data, now()))
                conn.commit()
        finally:
            conn.close()

def clear_session(telegram_user_id):
    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE user_id = %s", (telegram_user_id,))
                conn.commit()
        finally:
            conn.close()

def clear_session_by_hotel_account(hotel_account_id):
    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE hotel_account_id = %s", (hotel_account_id,))
                conn.commit()
        finally:
            conn.close()


# =========================================================
# الأمان والحسابات
# =========================================================

def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def is_admin(user_id):
    admin_id_raw = os.getenv("ADMIN_ID", "").strip()
    if not admin_id_raw:
        return False
    try:
        return int(user_id) == int(admin_id_raw)
    except Exception:
        return False

def get_hotels():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM hotels WHERE enabled = 1 ORDER BY id ASC")
            return cur.fetchall()
    finally:
        conn.close()

def add_hotel(name):
    name = name.strip()
    if not name:
        return False
    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO hotels (name, enabled, created_at) VALUES (%s, 1, %s)", (name, now()))
                conn.commit()
                return True
        except psycopg2.IntegrityError:
            return False
        finally:
            conn.close()

def create_hotel_account(hotel_name, username, password):
    hotel_name = hotel_name.strip()
    username = username.strip()
    password = password.strip()

    if not hotel_name or not username or not password:
        return False, "جميع الحقول مطلوبة."

    password_hash = hash_password(password)

    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM hotels WHERE name = %s AND enabled = 1", (hotel_name,))
                hotel = cur.fetchone()
                if not hotel:
                    return False, "الفندق المحدد غير موجود."

                cur.execute("SELECT * FROM hotel_accounts WHERE username = %s", (username,))
                if cur.fetchone():
                    return False, "اسم المستخدم مستخدم مسبقاً."

                cur.execute("""
                    INSERT INTO hotel_accounts (hotel_id, hotel_name, username, password_hash, enabled, created_at)
                    VALUES (%s, %s, %s, %s, 1, %s)
                """, (hotel["id"], hotel_name, username, password_hash, now()))

                conn.commit()
                return True, "تم إنشاء الحساب بنجاح."
        except psycopg2.IntegrityError:
            conn.rollback()
            return False, "اسم المستخدم مستخدم مسبقاً."
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()

def update_hotel_password(account_id, new_password):
    new_password = new_password.strip()
    if not new_password or len(new_password) < 4:
        return False, "كلمة المرور يجب أن تكون 4 خانات على الأقل."

    password_hash = hash_password(new_password)

    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE hotel_accounts SET password_hash = %s WHERE id = %s", (password_hash, account_id))
                conn.commit()
                return True, "تم تغيير كلمة المرور بنجاح."
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()

def login_hotel(username, password):
    username = username.strip()
    password = password.strip()
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM hotel_accounts WHERE username = %s", (username,))
            row = cur.fetchone()
            if not row or row["password_hash"] != hash_password(password):
                return None, "❌ اسم المستخدم أو كلمة المرور غير صحيحة."

            if row["enabled"] != 1:
                return None, "❌ هذا الحساب معطل حالياً من قبل الإدارة."

            return row, None
    finally:
        conn.close()

def get_hotel_accounts():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM hotel_accounts ORDER BY hotel_name, username")
            return cur.fetchall()
    finally:
        conn.close()

def set_hotel_account_status(account_id, status):
    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE hotel_accounts SET enabled = %s WHERE id = %s", (1 if status else 0, account_id))
                conn.commit()
        finally:
            conn.close()


# =========================================================
# النزلاء والتقارير والتعاميم
# =========================================================

def save_guest(hotel_id, hotel_name, data):
    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO guests (
                        hotel_id, hotel_name, full_name, mother_name, birth_place_date,
                        original_residence, governorate, hotel_area, stay_reason, check_in_date,
                        stay_duration, notes, front_photo, back_photo, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    hotel_id, hotel_name,
                    data.get("full_name", ""), data.get("mother_name", ""),
                    data.get("birth_place_date", ""), data.get("original_residence", ""),
                    data.get("governorate", ""), data.get("hotel_area", ""),
                    data.get("stay_reason", ""), data.get("check_in_date", ""),
                    data.get("stay_duration", ""), data.get("notes", ""),
                    data.get("front_photo", ""), data.get("back_photo", ""),
                    now()
                ))

                guest_id = cur.fetchone()["id"]
                cur.execute("INSERT INTO inbox (guest_id, hotel_id, is_read, created_at) VALUES (%s, %s, 0, %s)",
                             (guest_id, hotel_id, now()))

                conn.commit()
                return guest_id
        finally:
            conn.close()

def unread_count():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS count FROM inbox WHERE is_read = 0")
            return cur.fetchone()["count"]
    finally:
        conn.close()

def get_inbox(limit=20):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT inbox.id AS inbox_id, inbox.is_read, inbox.created_at, guests.*
                FROM inbox JOIN guests ON guests.id = inbox.guest_id
                ORDER BY inbox.id DESC LIMIT %s
            """, (limit,))
            return cur.fetchall()
    finally:
        conn.close()

def mark_inbox_read(inbox_id):
    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE inbox SET is_read = 1 WHERE id = %s", (inbox_id,))
                conn.commit()
        finally:
            conn.close()

def save_circular(title, content):
    with DB_LOCK:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO circulars (title, content, created_at) VALUES (%s, %s, %s) RETURNING id",
                            (title, content, now()))
                conn.commit()
                return cur.fetchone()["id"]
        finally:
            conn.close()

def get_circulars(limit=10):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM circulars ORDER BY id DESC LIMIT %s", (limit,))
            return cur.fetchall()
    finally:
        conn.close()

def report_data(start_date=None, end_date=None):
    conn = db()
    try:
        with conn.cursor() as cur:
            condition = ""
            params = []
            if start_date and end_date:
                condition = "WHERE DATE(created_at::timestamp) BETWEEN %s AND %s"
                params = [start_date, end_date]

            cur.execute(f"SELECT COUNT(*) AS total FROM guests {condition}", params)
            total = cur.fetchone()["total"]

            cur.execute(f"SELECT governorate, COUNT(*) AS total FROM guests {condition} GROUP BY governorate ORDER BY total DESC", params)
            by_governorate = cur.fetchall()

            cur.execute(f"SELECT hotel_name, COUNT(*) AS total FROM guests {condition} GROUP BY hotel_name ORDER BY total DESC", params)
            by_hotel = cur.fetchall()

            cur.execute(f"SELECT stay_reason, COUNT(*) AS total FROM guests {condition} GROUP BY stay_reason ORDER BY total DESC", params)
            by_reason = cur.fetchall()

            return {"total": total, "governorates": by_governorate, "hotels": by_hotel, "reasons": by_reason}
    finally:
        conn.close()

def format_report(title, data):
    text = f"📊 *{title}*\n\n👥 إجمالي النزلاء: *{data['total']}*\n\n🏛 **حسب المحافظات:**\n"
    if data["governorates"]:
        for row in data["governorates"]:
            text += f"• {row['governorate'] or 'غير محدد'}: {row['total']}\n"
    else:
        text += "• لا توجد بيانات\n"

    text += "\n🏨 **حسب الفنادق:**\n"
    if data["hotels"]:
        for row in data["hotels"]:
            text += f"• {row['hotel_name'] or 'غير محدد'}: {row['total']}\n"
    else:
        text += "• لا توجد بيانات\n"

    text += "\n📝 **حسب سبب الإقامة:**\n"
    if data["reasons"]:
        for row in data["reasons"]:
            text += f"• {row['stay_reason'] or 'غير محدد'}: {row['total']}\n"
    else:
        text += "• لا توجد بيانات\n"

    return text


# =========================================================
# PDF مع نظام التسمية باسم النزيل والتصحيح التلقائي
# =========================================================

def sanitize_filename(name):
    clean = re.sub(r'[\\/*?:"<>|]', '', str(name)).strip()
    return clean if clean else "نزيل"

def generate_pdf_document(guest, include_photos=True):
    guest_name = sanitize_filename(guest.get("full_name", "نزيل"))
    filename = f"تقرير النزيل - {guest_name}.pdf"
    path = PDF_DIR / filename

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=16 * mm, leftMargin=16 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()

    font_regular = ARABIC_FONT_NAME if ARABIC_FONT_READY else "Helvetica"
    font_bold = ARABIC_FONT_BOLD_NAME if ARABIC_FONT_READY else "Helvetica-Bold"

    title_style = ParagraphStyle("ArabicTitle", parent=styles["Title"], fontName=font_bold, alignment=TA_RIGHT, fontSize=18, leading=24)
    subtitle_style = ParagraphStyle("ArabicSubtitle", parent=styles["Normal"], fontName=font_regular, alignment=TA_RIGHT, fontSize=10, leading=16)
    center_style = ParagraphStyle("ArabicCenter", parent=styles["Normal"], fontName=font_regular, alignment=TA_CENTER, fontSize=10, leading=16)
    label_style = ParagraphStyle("ArabicLabel", parent=styles["Normal"], fontName=font_bold, alignment=TA_RIGHT, fontSize=9, leading=14)
    value_style = ParagraphStyle("ArabicValue", parent=styles["Normal"], fontName=font_regular, alignment=TA_RIGHT, fontSize=10, leading=15)

    def g(key):
        try:
            val = guest[key]
            return str(val) if val is not None else ""
        except Exception:
            return ""

    guest_id = guest.get("id", 0)

    story = [
        Paragraph(pdf_text("نظام إدارة معلومات الفنادق"), title_style),
        Paragraph(pdf_text("استمارة بيانات نزيل — تقرير رسمي"), subtitle_style),
        Paragraph(pdf_text(f"رقم التقرير: HR-{guest_id:06d}"), center_style),
        Spacer(1, 10)
    ]

    raw_data = [
        ["البيان", "المعلومات"],
        ["الاسم الثلاثي", g("full_name")],
        ["اسم الأم", g("mother_name")],
        ["مكان وتاريخ الولادة", g("birth_place_date")],
        ["السكن الأصلي", g("original_residence")],
        ["المحافظة", g("governorate")],
        ["اسم الفندق", g("hotel_name")],
        ["منطقة الفندق", g("hotel_area")],
        ["سبب الإقامة", g("stay_reason")],
        ["تاريخ النزول", g("check_in_date")],
        ["مدة الإقامة", g("stay_duration")],
        ["ملاحظات عامة", g("notes")],
        ["تاريخ التسجيل", g("created_at")],
    ]

    formatted = []
    for idx, row in enumerate(raw_data):
        st = label_style if idx == 0 else value_style
        formatted.append([Paragraph(pdf_text(row[0]), label_style), Paragraph(pdf_text(row[1]), st)])

    table = Table(formatted, colWidths=[45 * mm, 125 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#f3f4f6")),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    story.append(table)
    story.append(Spacer(1, 10))

    if include_photos:
        for side_key, title_str in [("front_photo", "الهوية الشخصية — الوجه الأمامي"), ("back_photo", "الهوية الشخصية — الوجه الخلفي")]:
            img_p = g(side_key)
            if img_p and Path(img_p).exists():
                try:
                    with PILImage.open(img_p) as img:
                        img = img.convert("RGB")
                        temp_p = PHOTO_DIR / f"temp_{side_key}_{guest_id}_{datetime.now().strftime('%H%M%S%f')}.jpg"
                        img.save(temp_p, "JPEG", quality=85)
                        
                        story.append(KeepTogether([
                            Paragraph(pdf_text(title_str), subtitle_style),
                            Spacer(1, 4),
                            RLImage(str(temp_p), width=75 * mm, height=50 * mm),
                            Spacer(1, 8)
                        ]))
                except Exception as e:
                    logger.warning(f"⚠️ تصحيح تلقائي: تعذر تضمين صورة {side_key}: {e}")

    story.append(Spacer(1, 10))
    story.append(Paragraph(pdf_text("تم إنشاء هذا التقرير إلكترونياً بواسطة نظام إدارة معلومات الفنادق."), center_style))

    doc.build(story)
    return str(path)


def make_pdf(guest):
    try:
        return generate_pdf_document(guest, include_photos=True)
    except Exception as primary_error:
        logger.error(f"⚠️ فشل توليد الـ PDF بالصور: {primary_error}. جاري التصحيح التلقائي المباشر...")
        try:
            return generate_pdf_document(guest, include_photos=False)
        except Exception as fallback_error:
            logger.critical(f"❌ فشل التصحيح التلقائي للـ PDF: {fallback_error}")
            raise fallback_error


# =========================================================
# لوحات المفاتيح (محدثة وشاملة لأزرار الصادر والجلسات)
# =========================================================

def admin_menu():
    count = unread_count()
    inbox_text = f"📥 الوارد ({count})" if count > 0 else "📥 الوارد"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(inbox_text, callback_data="admin_inbox"),
         InlineKeyboardButton("📤 الصادر والتعاميم", callback_data="admin_circulars")],
        [InlineKeyboardButton("🚪 جلسات الحسابات (طرد)", callback_data="admin_kick_list")],
        [InlineKeyboardButton("🏨 إضافة حساب فندق", callback_data="admin_add_account"),
         InlineKeyboardButton("📋 حسابات الفنادق", callback_data="admin_list_accounts")],
        [InlineKeyboardButton("🔑 تغيير كلمة مرور", callback_data="admin_change_pass")],
        [InlineKeyboardButton("🔴 تعطيل حساب", callback_data="admin_disable"),
         InlineKeyboardButton("🟢 تفعيل حساب", callback_data="admin_enable")],
        [InlineKeyboardButton("📊 التقرير اليومي", callback_data="report_daily"),
         InlineKeyboardButton("📊 التقرير الشهري", callback_data="report_monthly")],
    ])

def welcome_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 دخول المدير", callback_data="login_admin")],
        [InlineKeyboardButton("🏨 دخول الفندق", callback_data="login_hotel")]
    ])

def hotel_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 تسجيل بيانات نزيل", callback_data="guest_start")],
        [InlineKeyboardButton("📋 عرض البيانات الحالية", callback_data="guest_preview")],
        [InlineKeyboardButton("📤 إرسال للإدارة", callback_data="guest_send")],
        [InlineKeyboardButton("🚪 تسجيل الخروج", callback_data="hotel_logout")]
    ])

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع", callback_data="back")]])


# =========================================================
# الأوامر والأحداث الرئيسية
# =========================================================

async def start(update, context):
    if not update.message:
        return
    user = update.effective_user
    if not user:
        return

    context.user_data.clear()

    quran_msg = (
        "✨ *بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ*\n"
        "﴿ وَتَعَاوَنُوا عَلَى الْبِرِّ وَالتَّقْوَى ﴾\n\n"
        "🌹 *أهلاً وسهلاً بك في نظام إدارة معلومات الفنادق*\n"
        "──────────────────────\n"
    )

    if is_admin(user.id):
        await update.message.reply_text(
            f"{quran_msg}"
            "👑 **مرحباً بك أخي المدير العام.**\n"
            "✅ تم التعرف على حسابك وتأكيد الصلاحيات بنجاح.\n\n"
            "يرجى اختيار العملية المطلوبة من القائمة أدناه:",
            parse_mode="Markdown",
            reply_markup=admin_menu()
        )
        return

    session = get_session(user.id)
    if session:
        await update.message.reply_text(
            f"{quran_msg}"
            f"🏨 **مرحباً بك مجدداً في حساب:** `{session['hotel_name']}`\n"
            f"👤 **المستخدم:** `{session['username']}`\n\n"
            "✅ جلسة الدخول قائمة ومحفوظة بنجاح.\n"
            "اختر الخدمة المطلوبة من اللوحة:",
            parse_mode="Markdown",
            reply_markup=hotel_menu()
        )
        return

    await update.message.reply_text(
        f"{quran_msg}"
        "يرجى تحديد نوع تسجيل الدخول للمتابعة:",
        parse_mode="Markdown",
        reply_markup=welcome_keyboard()
    )


async def admin_login(update, context):
    query = update.callback_query
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text("❌ هذا الخيار مخصص للمدير فقط.", reply_markup=welcome_keyboard())
        return

    await query.edit_message_text(
        "👑 **أهلاً بك أيها المدير.**\n"
        "✅ تم التحقق من الصلاحيات.\n\n"
        "اختر العملية المطلوبة:",
        parse_mode="Markdown",
        reply_markup=admin_menu()
    )

async def hotel_login(update, context):
    query = update.callback_query
    context.user_data["state"] = "hotel_username"
    await query.edit_message_text("🏨 **تسجيل دخول الفندق**\n\nأرسل اسم المستخدم الخاص بك:", parse_mode="Markdown", reply_markup=back_button())


# =========================================================
# معالجة بيانات النزلاء والأسئلة الإجبارية
# =========================================================

GUEST_STEPS = [
    ("full_name", "1️⃣ الاسم الثلاثي للنزيل:", "💡 *مثال:* أحمد محمد العلي"),
    ("mother_name", "2️⃣ اسم الأم الثلاثي:", "💡 *مثال:* فاطمة خليل المحمود"),
    ("birth_place_date", "3️⃣ مكان وتاريخ الولادة:", "💡 *مثال:* دمشق - 1995/04/12"),
    ("original_residence", "4️⃣ مكان السكن الأصلي بالتفصيل:", "💡 *مثال:* حلب - حي الشهباء - شارع النيل"),
    ("governorate", "5️⃣ المحافظة التابع لها:", "💡 *مثال:* إدلب (أو دمشق، حلب...)"),
    ("hotel_area", "6️⃣ المنطقة أو الحي التابع له الفندق:", "💡 *مثال:* وسط المدينة / المربع الأمني"),
    ("stay_reason", "7️⃣ سبب الإقامة في الفندق:", "💡 *مثال:* علاج طبي / عمل تجاري / السياحة"),
    ("check_in_date", "8️⃣ تاريخ ووقت النزول بالفندق:", "💡 *مثال:* 2026/08/11 الساعة 02:00 ظهراً"),
    ("stay_duration", "9️⃣ مدة الإقامة المتوقعة:", "💡 *مثال:* 3 أيام / أسبوع واحد"),
    ("notes", "🔟 ملاحظات عامة (أو اكتب 'لا يوجد' إن لم تكن هناك ملاحظات):", "💡 *مثال:* لا يوجد / نزيل معه مرافق")
]

def format_step_prompt(step_idx):
    _, question, example = GUEST_STEPS[step_idx]
    return f"{question}\n\n{example}\n\n🔴 *تنبيه:* الإجابة على هذا السؤال إجبارية ولا يمكن تخطيه."

async def start_guest(update, context):
    query = update.callback_query
    user = update.effective_user
    session = get_session(user.id)

    if not session:
        await query.edit_message_text("❌ يجب تسجيل الدخول للحساب أولاً.", reply_markup=welcome_keyboard())
        return

    context.user_data["guest"] = {}
    context.user_data["guest_step"] = 0
    context.user_data["state"] = "guest_data"

    await query.edit_message_text(format_step_prompt(0), parse_mode="Markdown", reply_markup=back_button())

def guest_preview_text(guest):
    return (
        "📋 *بيانات النزيل المطلوبة*\n\n"
        f"👤 **الاسم:** {guest.get('full_name', '-')}\n"
        f"👩 **الأم:** {guest.get('mother_name', '-')}\n"
        f"📍 **الولادة:** {guest.get('birth_place_date', '-')}\n"
        f"🏠 **السكن الأصلي:** {guest.get('original_residence', '-')}\n"
        f"🏛 **المحافظة:** {guest.get('governorate', '-')}\n"
        f"🏨 **الفندق:** {guest.get('hotel_name', '-')}\n"
        f"📍 **منطقة الفندق:** {guest.get('hotel_area', '-')}\n"
        f"📝 **السبب:** {guest.get('stay_reason', '-')}\n"
        f"📅 **تاريخ النزول:** {guest.get('check_in_date', '-')}\n"
        f"⏳ **مدة الإقامة:** {guest.get('stay_duration', '-')}\n"
        f"📌 **الملاحظات:** {guest.get('notes', '-')}\n\n"
        f"🪪 **الهوية الأمامية:** {'✅ تم الرفع' if guest.get('front_photo') else '❌ لم ترفع'}\n"
        f"🪪 **الهوية الخلفية:** {'✅ تم الرفع' if guest.get('back_photo') else '❌ لم ترفع'}"
    )

def preview_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 إرسال للإدارة", callback_data="guest_send")],
        [InlineKeyboardButton("📝 تعديل البيانات", callback_data="guest_start")],
        [InlineKeyboardButton("↩️ العودة للرئيسية", callback_data="hotel_home")]
    ])


# =========================================================
# معالجة الرسائل
# =========================================================

async def save_photo(update, context, side):
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    filename = f"{update.effective_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{side}.jpg"
    path = PHOTO_DIR / filename
    await file.download_to_drive(custom_path=str(path))
    return str(path)

async def message_handler(update, context):
    if not update.message:
        return
    user = update.effective_user
    text = (update.message.text or "").strip()
    state = context.user_data.get("state")

    if state == "hotel_username":
        if not text:
            await update.message.reply_text("❌ أرسل اسم المستخدم.")
            return
        context.user_data["login_username"] = text
        context.user_data["state"] = "hotel_password"
        await update.message.reply_text("🔐 أرسل كلمة المرور:", reply_markup=back_button())
        return

    if state == "hotel_password":
        username = context.user_data.get("login_username", "")
        row, error = login_hotel(username, text)
        if error:
            await update.message.reply_text(f"{error}\n\nحاول مرة أخرى:", reply_markup=back_button())
            return

        context.user_data.clear()
        save_session(user.id, row["id"], row["hotel_id"], row["hotel_name"], row["username"], "hotel_home")

        await update.message.reply_text(
            "✅ **تم تسجيل الدخول بنجاح.**\n"
            "✨ الحساب سيبقى متصلاً باستمرار حتى تسجيل الخروج بنفسك أو الإيقاف.\n\n"
            f"🏨 **الفندق:** {row['hotel_name']}\n"
            f"👤 **المستخدم:** {row['username']}\n\n"
            "اختر العملية المطلوبة:",
            parse_mode="Markdown",
            reply_markup=hotel_menu()
        )
        return

    session = get_session(user.id)
    if session:
        if state == "guest_data":
            step = context.user_data.get("guest_step", 0)
            if step < len(GUEST_STEPS):
                key, _, _ = GUEST_STEPS[step]

                if not text or len(text) < 2:
                    await update.message.reply_text(
                        "⚠️ *إجابة غير مقبولة!*\n"
                        "الجواب على هذا السؤال إجباري، يرجى كتابة إجابة واضحة والصحيحة للمتابعة.",
                        parse_mode="Markdown"
                    )
                    return

                context.user_data.setdefault("guest", {})[key] = text
                step += 1
                context.user_data["guest_step"] = step

                if step < len(GUEST_STEPS):
                    await update.message.reply_text(format_step_prompt(step), parse_mode="Markdown", reply_markup=back_button())
                    return

                context.user_data["state"] = "front_photo"
                await update.message.reply_text(
                    "1️⃣1️⃣ أرسل صورة الهوية الشخصية (الجهة الأمامية):\n\n"
                    "💡 *مثال:* قم بتمويه البيانات الحساسة إن أردت ولكن يجب أن تكون الصورة واضحة.\n\n"
                    "🔴 *تنبيه:* رفع الصورة إجباري للمتابعة.",
                    parse_mode="Markdown",
                    reply_markup=back_button()
                )
                return

        if state in ["front_photo", "back_photo"]:
            await update.message.reply_text(
                "⚠️ *رفع الصورة إجباري!*\n"
                "يرجى إرسال الصورة كملف صورة وليس كنص للمتابعة.",
                parse_mode="Markdown"
            )
            return

    await update.message.reply_text(
        "ℹ️ يرجى اختيار عملية من القائمة.",
        reply_markup=hotel_menu() if session else welcome_keyboard()
    )


async def photo_handler(update, context):
    if not update.message:
        return
    user = update.effective_user
    session = get_session(user.id)

    if not session:
        await update.message.reply_text("❌ يجب تسجيل الدخول أولاً.", reply_markup=welcome_keyboard())
        return

    state = context.user_data.get("state")
    if state == "front_photo":
        path = await save_photo(update, context, "front")
        context.user_data.setdefault("guest", {})["front_photo"] = path
        context.user_data["state"] = "back_photo"
        await update.message.reply_text(
            "✅ تم استلام الوجه الأمامي.\n\n"
            "1️⃣2️⃣ أرسل الآن صورة الهوية (الجهة الخلفية):\n\n"
            "💡 *مثال:* صورة كاملة وواضحة للوجه الخلفي لبطاقة الهوية.\n\n"
            "🔴 *تنبيه:* رفع الصورة الخلفية إجباري.",
            parse_mode="Markdown",
            reply_markup=back_button()
        )
        return

    if state == "back_photo":
        path = await save_photo(update, context, "back")
        context.user_data.setdefault("guest", {})["back_photo"] = path
        context.user_data["state"] = "ready"

        guest = context.user_data["guest"]
        guest["hotel_name"] = session["hotel_name"]

        await update.message.reply_text(guest_preview_text(guest), parse_mode="Markdown", reply_markup=preview_keyboard())
        return

    await update.message.reply_text("ℹ️ لا توجد عملية حالية لرفع الصور.")


# =========================================================
# إرسال التقرير للإدارة
# =========================================================

async def send_guest_to_admin(update, context):
    query = update.callback_query
    user = update.effective_user
    session = get_session(user.id)

    if not session:
        await query.edit_message_text("❌ الجلسة منتهية أو الحساب معطل.", reply_markup=welcome_keyboard())
        return

    guest = context.user_data.get("guest")
    if not guest or not guest.get("front_photo") or not guest.get("back_photo"):
        await query.edit_message_text("❌ ينبغي إكمال إدخال البيانات وصورتي الهوية أولاً.", reply_markup=preview_keyboard())
        return

    guest["hotel_name"] = session["hotel_name"]

    try:
        guest_id = save_guest(session["hotel_id"], session["hotel_name"], guest)
    except Exception:
        logger.exception("فشل حفظ النزيل")
        await query.edit_message_text("❌ حدث خطأ أثناء حفظ البيانات.", reply_markup=hotel_menu())
        return

    context.user_data.pop("guest", None)
    context.user_data.pop("guest_step", None)
    context.user_data["state"] = "hotel_home"

    await query.edit_message_text(
        "✅ **تم إرسال بيانات النزيل والتقرير بنجاح إلى قسم الوارد.**\n\n"
        f"🆔 **رقم التقرير:** HR-{guest_id:06d}",
        parse_mode="Markdown",
        reply_markup=hotel_menu()
    )


# =========================================================
# عرض الحسابات الوارد والتحكم إدارياً
# =========================================================

async def admin_list_accounts(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    accounts = get_hotel_accounts()
    if not accounts:
        await query.edit_message_text("📋 لا توجد حسابات فنادق مسجلة بعد.", reply_markup=admin_menu())
        return

    msg = "📋 *قائمة حسابات الفنادق المسجلة:*\n\n"
    for acc in accounts:
        st = "🟢 مفعل" if acc["enabled"] == 1 else "🔴 معطل"
        msg += f"• **الفندق:** {acc['hotel_name']}\n  **المستخدم:** `{acc['username']}` | **الحالة:** {st}\n"
        msg += "───────────────\n"

    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=admin_menu())

async def admin_kick_list(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    accounts = get_hotel_accounts()
    if not accounts:
        await query.edit_message_text("🚪 لا توجد حسابات فنادق لطردها.", reply_markup=admin_menu())
        return

    buttons = []
    for acc in accounts:
        buttons.append([InlineKeyboardButton(f"🚪 طرد: {acc['hotel_name']} ({acc['username']})", callback_data=f"kick_{acc['id']}")])

    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_home")])
    await query.edit_message_text("🚪 **اختر الحساب المراد إخراجه وطرد صاحبه من الجلسة النشطة:**", reply_markup=InlineKeyboardMarkup(buttons))

async def show_preview(update, context):
    query = update.callback_query
    user = update.effective_user
    session = get_session(user.id)
    guest = context.user_data.get("guest")

    if not guest:
        await query.edit_message_text("📋 لا توجد بيانات نزيل جديدة مدخلة.", reply_markup=hotel_menu())
        return

    if session:
        guest["hotel_name"] = session["hotel_name"]

    await query.edit_message_text(guest_preview_text(guest), parse_mode="Markdown", reply_markup=preview_keyboard())


async def admin_inbox(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    rows = get_inbox(15)
    if not rows:
        await query.edit_message_text("📥 **الوارد**\n\nلا توجد رسائل أو تقارير جديدة.", reply_markup=admin_menu())
        return

    buttons = []
    for row in rows:
        st = "🔴 (جديد)" if row["is_read"] == 0 else "⚪"
        name = (row["full_name"] or "بدون اسم")[:25]
        buttons.append([InlineKeyboardButton(f"{st} {name} — {row['hotel_name']}", callback_data=f"inbox_{row['inbox_id']}")])

    buttons.append([InlineKeyboardButton("↩️ رجوع للرئيسية", callback_data="admin_home")])
    await query.edit_message_text("📥 **التقارير الواردة:**", reply_markup=InlineKeyboardMarkup(buttons))

async def open_inbox(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    try:
        inbox_id = int(query.data.split("_", 1)[1])
    except Exception:
        await query.edit_message_text("❌ رقم التقرير غير صحيح.", reply_markup=admin_menu())
        return

    mark_inbox_read(inbox_id)
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT inbox.*, guests.* FROM inbox JOIN guests ON guests.id = inbox.guest_id WHERE inbox.id = %s
            """, (inbox_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        await query.edit_message_text("❌ التقرير غير موجود.", reply_markup=admin_menu())
        return

    text = (
        "📥 *بيانات تقرير وارد*\n\n"
        f"🆔 **التقرير:** HR-{row['guest_id']:06d}\n"
        f"🏨 **الفندق:** {row['hotel_name']}\n"
        f"👤 **الاسم:** {row['full_name']}\n"
        f"👩 **الأم:** {row['mother_name']}\n"
        f"📍 **الولادة:** {row['birth_place_date']}\n"
        f"🏠 **السكن:** {row['original_residence']}\n"
        f"🏛 **المحافظة:** {row['governorate']}\n"
        f"📍 **منطقة الفندق:** {row['hotel_area']}\n"
        f"📝 **السبب:** {row['stay_reason']}\n"
        f"📅 **التاريخ:** {row['check_in_date']}\n"
        f"⏳ **المدة:** {row['stay_duration']}\n"
        f"📌 **الملاحظات:** {row['notes']}\n"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 تحميل ملف الـ PDF", callback_data=f"resend_{row['guest_id']}")],
            [InlineKeyboardButton("↩️ رجوع للوارد", callback_data="admin_inbox")]
        ])
    )

async def resend_pdf(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    try:
        guest_id = int(query.data.split("_", 1)[1])
    except Exception:
        return

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM guests WHERE id = %s", (guest_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return

    try:
        pdf_path = make_pdf(row)
        guest_name = row.get('full_name', 'النزيل')
        admin_id_raw = int(os.getenv("ADMIN_ID", "0").strip())
        with open(pdf_path, "rb") as pdf:
            await context.bot.send_document(
                chat_id=admin_id_raw,
                document=pdf,
                filename=f"تقرير النزيل - {guest_name}.pdf",
                caption=f"📄 تقرير HR-{guest_id:06d}\n🏨 {row['hotel_name']}\n👤 {row['full_name']}"
            )
        await query.edit_message_text("✅ تم تحميل وإرسال ملف PDF بنجاح.", reply_markup=admin_menu())
    except Exception:
        logger.exception("إرسال ملف PDF فشل")
        await query.edit_message_text("❌ فشل إنشاء وإرسال PDF.", reply_markup=admin_menu())


# =========================================================
# قسم الصادر / التعاميم
# =========================================================

async def admin_circulars(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    circulars = get_circulars(10)
    msg = "📤 **قسم الصادر والتعاميم:**\n\n"
    if circulars:
        for c in circulars:
            msg += f"📌 **{c['title']}**\n📅 {c['created_at']}\n{c['content'][:100]}...\n───────────────\n"
    else:
        msg += "لا توجد تعاميم صادرة مسبقاً.\n\n"

    buttons = [
        [InlineKeyboardButton("➕ إرسال تعميم جديد لكل الفنادق", callback_data="admin_new_circular")],
        [InlineKeyboardButton("↩️ رجوع للرئيسية", callback_data="admin_home")]
    ]
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def admin_new_circular_start(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    context.user_data["state"] = "admin_circular_text"
    await query.edit_message_text(
        "📢 **إرسال تعميم جديد**\n\n"
        "يرجى كتابة نص التعميم الرسمي المراد إرساله إلى جميع حسابات الفنادق المفعلة:",
        reply_markup=back_button()
    )


# =========================================================
# معالجة إضافة وتعديل وإدارة الحسابات
# =========================================================

async def admin_add_account(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    context.user_data.clear()
    hotels = get_hotels()
    buttons = []
    for h in hotels:
        buttons.append([InlineKeyboardButton(f"🏨 {h['name']}", callback_data=f"selecthotel_{h['id']}")])

    buttons.append([InlineKeyboardButton("➕ إضافة فندق جديد", callback_data="add_new_hotel")])
    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_home")])

    await query.edit_message_text("🏨 **إنشاء حساب فندق**\n\nاختر الفندق المراد إنشاء حساب له:", reply_markup=InlineKeyboardMarkup(buttons))

async def admin_change_pass_list(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    context.user_data.clear()
    accounts = get_hotel_accounts()
    if not accounts:
        await query.edit_message_text("🔑 لا توجد حسابات فنادق لتغيير كلمة مرورها.", reply_markup=admin_menu())
        return

    buttons = []
    for acc in accounts:
        buttons.append([InlineKeyboardButton(f"🔑 {acc['hotel_name']} ({acc['username']})", callback_data=f"changepass_{acc['id']}")])

    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_home")])
    await query.edit_message_text("🔑 **اختر الحساب المراد تغيير كلمة المرور له:**", reply_markup=InlineKeyboardMarkup(buttons))

async def admin_change_pass_select(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    try:
        acc_id = int(query.data.split("_", 1)[1])
    except Exception:
        return

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM hotel_accounts WHERE id = %s", (acc_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        await query.edit_message_text("❌ الحساب غير موجود.", reply_markup=admin_menu())
        return

    context.user_data.clear()
    context.user_data["edit_account_id"] = row["id"]
    context.user_data["edit_account_username"] = row["username"]
    context.user_data["edit_account_hotel"] = row["hotel_name"]
    context.user_data["state"] = "admin_new_password_only"

    await query.edit_message_text(
        f"🔑 **تعديل كلمة المرور لحساب:**\n\n"
        f"🏨 **الفندق:** {row['hotel_name']}\n"
        f"👤 **اسم المستخدم:** `{row['username']}`\n\n"
        "أرسل كلمة المرور الجديدة لهذا الحساب الآن:",
        parse_mode="Markdown",
        reply_markup=back_button()
    )

async def select_hotel(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    try:
        hotel_id = int(query.data.split("_", 1)[1])
    except Exception:
        return

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM hotels WHERE id = %s", (hotel_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return

    context.user_data.clear()
    context.user_data["admin_hotel_id"] = row["id"]
    context.user_data["admin_hotel_name"] = row["name"]
    context.user_data["state"] = "admin_username"

    await query.edit_message_text(f"🏨 **الفندق:** {row['name']}\n\n👤 أرسل اسم المستخدم الجديد:", reply_markup=back_button())

async def add_new_hotel(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    context.user_data.clear()
    context.user_data["state"] = "admin_new_hotel"
    await query.edit_message_text("➕ **إضافة فندق جديد**\n\nأرسل اسم الفندق الجديد:", reply_markup=back_button())

async def disable_accounts(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    accounts = get_hotel_accounts()
    buttons = []
    for acc in accounts:
        if acc["enabled"] == 1:
            buttons.append([InlineKeyboardButton(f"🔴 {acc['hotel_name']} — {acc['username']}", callback_data=f"disable_{acc['id']}")])

    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_home")])
    await query.edit_message_text("🔴 **اختر الحساب المراد تعطيله:**", reply_markup=InlineKeyboardMarkup(buttons))

async def enable_accounts(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    accounts = get_hotel_accounts()
    buttons = []
    for acc in accounts:
        if acc["enabled"] == 0:
            buttons.append([InlineKeyboardButton(f"🟢 {acc['hotel_name']} — {acc['username']}", callback_data=f"enable_{acc['id']}")])

    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_home")])
    await query.edit_message_text("🟢 **اختر الحساب المراد تفْعيله:**", reply_markup=InlineKeyboardMarkup(buttons))

async def daily_report(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    data = report_data(today(), today())
    await query.edit_message_text(format_report("التقرير اليومي", data), parse_mode="Markdown", reply_markup=admin_menu())

async def monthly_report(update, context):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    current = date.today()
    first = current.replace(day=1)
    data = report_data(first.strftime("%Y-%m-%d"), current.strftime("%Y-%m-%d"))
    await query.edit_message_text(format_report("التقرير الشهري", data), parse_mode="Markdown", reply_markup=admin_menu())


# =========================================================
# Callback Handler (مصحح ومفعل بالكامل للأزرار)
# =========================================================

async def callback_handler(update, context):
    query = update.callback_query
    if not query:
        return
    user = update.effective_user
    if not user:
        return

    data = query.data or ""
    try:
        await query.answer()
    except Exception:
        pass

    admin = is_admin(user.id)

    if data == "login_admin":
        await admin_login(update, context)
        return

    if data == "login_hotel":
        await hotel_login(update, context)
        return

    if data == "back":
        context.user_data.clear()
        if admin:
            await query.edit_message_text("👑 **لوحة تحكم المدير**", reply_markup=admin_menu())
        else:
            await query.edit_message_text("اختر نوع الدخول:", reply_markup=welcome_keyboard())
        return

    if admin:
        if data == "admin_home":
            context.user_data.clear()
            await query.edit_message_text("👑 **لوحة تحكم المدير**", reply_markup=admin_menu())
            return
        elif data == "admin_add_account":
            await admin_add_account(update, context)
            return
        elif data == "admin_change_pass":
            await admin_change_pass_list(update, context)
            return
        elif data == "admin_kick_list":
            await admin_kick_list(update, context)
            return
        elif data.startswith("changepass_"):
            await admin_change_pass_select(update, context)
            return
        elif data.startswith("kick_"):
            try:
                acc_id = int(data.split("_", 1)[1])
                clear_session_by_hotel_account(acc_id)
                await query.edit_message_text("🚪 تم طرد الفندق من الجلسة النشطة بنجاح.\nلن يتمكن من المتابعة إلا بإعادة تسجيل الدخول بالمعلومات.", reply_markup=admin_menu())
            except Exception:
                await query.edit_message_text("❌ حدث خطأ أثناء طرد الفندق.", reply_markup=admin_menu())
            return
        elif data == "admin_list_accounts":
            await admin_list_accounts(update, context)
            return
        elif data == "admin_disable":
            await disable_accounts(update, context)
            return
        elif data == "admin_enable":
            await enable_accounts(update, context)
            return
        elif data == "admin_inbox":
            await admin_inbox(update, context)
            return
        elif data == "admin_circulars":
            await admin_circulars(update, context)
            return
        elif data == "admin_new_circular":
            await admin_new_circular_start(update, context)
            return
        elif data == "report_daily":
            await daily_report(update, context)
            return
        elif data == "report_monthly":
            await monthly_report(update, context)
            return
        elif data.startswith("selecthotel_"):
            await select_hotel(update, context)
            return
        elif data == "add_new_hotel":
            await add_new_hotel(update, context)
            return
        elif data.startswith("disable_"):
            try:
                acc_id = int(data.split("_", 1)[1])
                set_hotel_account_status(acc_id, False)
                await query.edit_message_text("✅ تم تعطيل الحساب بنجاح.", reply_markup=admin_menu())
            except Exception:
                await query.edit_message_text("❌ حدث خطأ.", reply_markup=admin_menu())
            return
        elif data.startswith("enable_"):
            try:
                acc_id = int(data.split("_", 1)[1])
                set_hotel_account_status(acc_id, True)
                await query.edit_message_text("✅ تم تفعيل الحساب بنجاح.", reply_markup=admin_menu())
            except Exception:
                await query.edit_message_text("❌ حدث خطأ.", reply_markup=admin_menu())
            return
        elif data.startswith("inbox_"):
            await open_inbox(update, context)
            return
        elif data.startswith("resend_"):
            await resend_pdf(update, context)
            return

    session = get_session(user.id)
    if session:
        if data == "guest_start":
            await start_guest(update, context)
            return
        elif data == "guest_preview":
            await show_preview(update, context)
            return
        elif data == "guest_send":
            await send_guest_to_admin(update, context)
            return
        elif data == "hotel_home":
            context.user_data["state"] = "hotel_home"
            await query.edit_message_text("🏨 **لوحة تحكم الفندق**", reply_markup=hotel_menu())
            return
        elif data == "hotel_logout":
            clear_session(user.id)
            context.user_data.clear()
            await query.edit_message_text("🚪 تم تسجيل الخروج بنجاح.", reply_markup=welcome_keyboard())
            return


# =========================================================
# معالجة نصوص المدير
# =========================================================

async def admin_text_handler(update, context):
    if not update.message or not update.effective_user or not is_admin(update.effective_user.id):
        return False

    state = context.user_data.get("state")
    text = (update.message.text or "").strip()

    if state == "admin_circular_text":
        if not text:
            await update.message.reply_text("❌ يرجى كتابة نص التعميم.")
            return True

        title = f"تعميم {today()}"
        save_circular(title, text)

        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT user_id FROM sessions")
                sessions = cur.fetchall()
        finally:
            conn.close()

        sent_count = 0
        for s in sessions:
            try:
                await context.bot.send_message(
                    chat_id=s["user_id"],
                    text=f"📢 **تعميم إداري مهم**\n\n{text}",
                    parse_mode="Markdown"
                )
                sent_count += 1
            except Exception:
                pass

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ **تم نشر التعميم بنجاح.**\n\n👥 تم إرساله إلى {sent_count} مستخدم متصل حالياً.",
            parse_mode="Markdown",
            reply_markup=admin_menu()
        )
        return True

    if state == "admin_new_password_only":
        acc_id = context.user_data.get("edit_account_id")
        u_name = context.user_data.get("edit_account_username", "")
        h_name = context.user_data.get("edit_account_hotel", "")

        ok, msg = update_hotel_password(acc_id, text)
        context.user_data.clear()

        if ok:
            await update.message.reply_text(
                "✅ **تم تحديث كلمة المرور بنجاح.**\n\n"
                f"🏨 **الفندق:** {h_name}\n"
                f"👤 **المستخدم:** `{u_name}`\n"
                f"🔐 **كلمة المرور الجديدة:** `{text}`",
                parse_mode="Markdown",
                reply_markup=admin_menu()
            )
        else:
            await update.message.reply_text(f"❌ {msg}", reply_markup=admin_menu())
        return True

    if state == "admin_new_hotel":
        if not text:
            await update.message.reply_text("❌ أرسل اسم الفندق.")
            return True

        if add_hotel(text):
            context.user_data.clear()
            await update.message.reply_text(f"✅ تمت إضافة الفندق: *{text}*", parse_mode="Markdown", reply_markup=admin_menu())
        else:
            context.user_data.clear()
            await update.message.reply_text("❌ الفندق موجود مسبقاً أو أن الاسم غير صالح.", reply_markup=admin_menu())
        return True

    if state == "admin_username":
        if not re.match(r"^[A-Za-z0-9_.-]{3,32}$", text):
            await update.message.reply_text("❌ اسم المستخدم يجب أن يكون بين 3 إلى 32 حرفاً إنجليزياً وأرقاماً فقط.")
            return True

        context.user_data["new_username"] = text
        context.user_data["state"] = "admin_password"
        await update.message.reply_text("🔐 أرسل كلمة المرور للحساب:")
        return True

    if state == "admin_password":
        if len(text) < 4:
            await update.message.reply_text("❌ كلمة المرور يجب أن تكون 4 خانات على الأقل.")
            return True

        h_name = context.user_data.get("admin_hotel_name", "")
        u_name = context.user_data.get("new_username", "")

        ok, msg = create_hotel_account(h_name, u_name, text)
        context.user_data.clear()

        if ok:
            await update.message.reply_text(
                "✅ **تم إنشاء حساب الفندق بنجاح.**\n\n"
                f"🏨 **الفندق:** {h_name}\n"
                f"👤 **المستخدم:** `{u_name}`\n"
                f"🔐 **كلمة المرور:** `{text}`\n\n"
                "⚠️ الحساب أصبح جاهزاً للاستخدام والتسجيل في أي وقت.",
                parse_mode="Markdown",
                reply_markup=admin_menu()
            )
        else:
            await update.message.reply_text(f"❌ {msg}", reply_markup=admin_menu())
        return True

    return False

async def text_router(update, context):
    if await admin_text_handler(update, context):
        return
    asyncio.create_task(message_handler(update, context))

async def error_handler(update, context):
    logger.error("خطأ في تنفيذ البوت", exc_info=context.error)


# =========================================================
# Webhook & Server
# =========================================================

@flask_app.route("/", methods=["GET"])
def health():
    return "Hotel Bot is running healthy", 200

@flask_app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    global BOT_LOOP
    try:
        if telegram_app is None or BOT_LOOP is None:
            return "Not ready", 503

        data = request.get_json(force=True)
        update = Update.de_json(data, telegram_app.bot)
        future = asyncio.run_coroutine_threadsafe(telegram_app.process_update(update), BOT_LOOP)
        future.result(timeout=60)
        return "OK", 200
    except Exception:
        logger.exception("خطأ في Webhook")
        return "ERROR", 500

async def start_telegram():
    global telegram_app, BOT_LOOP
    BOT_LOOP = asyncio.get_running_loop()

    telegram_app = ApplicationBuilder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(callback_handler))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    telegram_app.add_error_handler(error_handler)

    await telegram_app.initialize()
    await telegram_app.start()

    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if not render_url:
        logger.warning("⚠️ RENDER_EXTERNAL_URL غير معرف، تأكد من ضبطه على المنصة لتفعيل الـ Webhook بنجاح.")
    else:
        webhook_url = render_url.rstrip("/") + "/telegram/webhook"
        try:
            await telegram_app.bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass

        await telegram_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        logger.info("Bot Webhook setup complete: %s", webhook_url)

    await asyncio.Event().wait()

def main():
    admin_id_raw = os.getenv("ADMIN_ID", "").strip()
    if not BOT_TOKEN or not admin_id_raw:
        logger.error("❌ BOT_TOKEN أو ADMIN_ID مفقود في متغيرات البيئة")
        return

    init_db()

    flask_thread = threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=PORT, threaded=True),
        daemon=True
    )
    flask_thread.start()

    try:
        asyncio.run(start_telegram())
    except KeyboardInterrupt:
        logger.info("تم إيقاف البوت")
    except Exception:
        logger.exception("خطأ غير متوقع")

if __name__ == "__main__":
    main()
