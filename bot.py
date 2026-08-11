import os
import logging
import asyncio
import threading
from datetime import datetime
import psycopg2
from flask import Flask, request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# إعداد السجلات (Logging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# المتغيرات البيئية
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "postgres://user:password@localhost:5432/dbname")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
PORT = int(os.getenv("PORT", "5000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

# قفل لتنفيذ عمليات قاعدة البيانات بأمان
DB_LOCK = threading.Lock()

# إعداد تطبيق Flask للـ Webhook
app = Flask(__name__)

# --- إدارة قاعدة البيانات ---
def db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """إنشاء الجداول الأساسية في PostgreSQL إذا لم تكن موجودة"""
    with DB_LOCK:
        with db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS hotels (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL,
                        location TEXT
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS hotel_accounts (
                        id SERIAL PRIMARY KEY,
                        hotel_id INT REFERENCES hotels(id) ON DELETE CASCADE,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        is_active BOOLEAN DEFAULT TRUE
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id SERIAL PRIMARY KEY,
                        hotel_name TEXT,
                        user_id BIGINT UNIQUE,
                        status TEXT DEFAULT 'active',
                        last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS circulars (
                        id SERIAL PRIMARY KEY,
                        content TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS inbox (
                        id SERIAL PRIMARY KEY,
                        hotel_name TEXT,
                        guest_data TEXT,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.commit()

init_db()

# --- توليد ملفات PDF ---
def generate_guest_pdf(guest_info: dict, filename: str = "guest_report.pdf"):
    """توليد استمارة رسمية بصيغة PDF مع دعم الخطوط العربية"""
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import arabic_reshaper
    from bidi.algorithm import get_display

    def ar(text):
        if not text:
            return ""
        reshaped = arabic_reshaper.reshape(str(text))
        return get_display(reshaped)

    # محاولة تسجيل خط عربي متوفر في النظام أو محلياً
    font_name = "Helvetica"
    font_paths = [
        "Cairo-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("ArabicFont", path))
                font_name = "ArabicFont"
                break
            except Exception:
                pass

    doc = SimpleDocTemplate(filename, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'ArabicTitle',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=18,
        alignment=1,
        textColor=colors.HexColor("#1A365D")
    )
    
    body_style = ParagraphStyle(
        'ArabicBody',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=12,
        alignment=2,
        textColor=colors.HexColor("#2D3748")
    )

    story = [
        Paragraph(ar("استمارة تسجيل نزيل رسمية"), title_style),
        Spacer(1, 15),
        Paragraph(ar(f"اسم النزيل: {guest_info.get('name', 'غير متوفر')}"), body_style),
        Spacer(1, 10),
        Paragraph(ar(f"اسم الأم: {guest_info.get('mother_name', 'غير متوفر')}"), body_style),
        Spacer(1, 10),
        Paragraph(ar(f"تاريخ الولادة: {guest_info.get('birth_date', 'غير متوفر')}"), body_style),
        Spacer(1, 10),
        Paragraph(ar(f"سبب الإقامة: {guest_info.get('reason', 'غير متوفر')}"), body_style),
        Spacer(1, 15)
    ]

    try:
        doc.build(story)
        return filename
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
        return None

# --- واجهة وتوابع لوحة المدير ---

def get_admin_main_menu():
    """لوحة التحكم الرئيسية للمدير"""
    keyboard = [
        [InlineKeyboardButton("📋 طلبات النزلاء الواردة", callback_data="admin_inbox")],
        [InlineKeyboardButton("🏨 إدارة الفنادق والحسابات", callback_data="admin_hotels")],
        [InlineKeyboardButton("📢 الصادر والتعاميم", callback_data="admin_circulars")],
        [InlineKeyboardButton("💻 الجلسات النشطة", callback_data="admin_sessions")],
        [InlineKeyboardButton("📊 التقارير والإحصاءات", callback_data="admin_reports")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("عذراً، هذا الأمر مخصص للمدير فقط.")
        return
    
    if update.message:
        await update.message.reply_text(
            "🎛 **لوحة تحكم الإدارة الرئيسية**\nاختر القسم المطلوب:",
            reply_markup=get_admin_main_menu(),
            parse_mode="Markdown"
        )
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "🎛 **لوحة تحكم الإدارة الرئيسية**\nاختر القسم المطلوب:",
            reply_markup=get_admin_main_menu(),
            parse_mode="Markdown"
        )

# معالج قسم الجلسات النشطة
async def show_active_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    with DB_LOCK:
        with db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, hotel_name, user_id, status, last_login FROM sessions ORDER BY last_login DESC;")
                sessions = cursor.fetchall()
                
    if not sessions:
        await query.edit_message_text(
            "💻 **إدارة الجلسات النشطة**\n\nلا توجد جلسات مسجلة حالياً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")]])
        )
        return

    keyboard = []
    text = "💻 **إدارة الجلسات النشطة:**\nاختر الجلسة للتحكم بها (طرد أو تعطيل/تفعيل):\n\n"
    
    for s in sessions:
        s_id, hotel_name, user_id, status, last_login = s
        status_icon = "🟢 نشط" if status == "active" else "🔴 معطل"
        text += f"🏨 {hotel_name} (ID: {user_id}) - {status_icon}\n"
        
        toggle_text = "🔒 تعطيل" if status == "active" else "🔓 تفعيل"
        keyboard.append([
            InlineKeyboardButton(f"{hotel_name} ({status_icon})", callback_data=f"noop_{s_id}")
        ])
        keyboard.append([
            InlineKeyboardButton(toggle_text, callback_data=f"session_toggle_{s_id}"),
            InlineKeyboardButton("❌ طرد نهائي", callback_data=f"session_kick_{s_id}")
        ])
        
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_session_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = data.split("_")
    action = parts[1] # toggle أو kick
    session_id = int(parts[2])
    
    with DB_LOCK:
        with db() as conn:
            with conn.cursor() as cursor:
                if action == "kick":
                    cursor.execute("DELETE FROM sessions WHERE id = %s;", (session_id,))
                    conn.commit()
                    await query.answer("تم طرد الجلسة بنجاح وحذفها.", show_alert=True)
                elif action == "toggle":
                    cursor.execute("SELECT status FROM sessions WHERE id = %s;", (session_id,))
                    res = cursor.fetchone()
                    if res:
                        new_status = "disabled" if res[0] == "active" else "active"
                        cursor.execute("UPDATE sessions SET status = %s WHERE id = %s;", (new_status, session_id))
                        conn.commit()
                        await query.answer(f"تم تغيير حالة الجلسة إلى: {new_status}", show_alert=True)
                        
    await show_active_sessions(update, context)

# معالج قسم الصادر والتعاميم
async def show_circulars_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("✍️ إرسال تعميم جديد لجميع الفنادق", callback_data="circular_new")],
        [InlineKeyboardButton("📜 عرض التعاميم السابقة", callback_data="circular_list")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")]
    ]
    
    await query.edit_message_text(
        "📢 **نظام الصادر والتعاميم الرسمية**\n\nقم باختيار الإجراء المطلوب:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# --- إعداد البوت والمسارات الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك في نظام إدارة معلومات الفنادق.\nيرجى تسجيل الدخول باستخدام حسابك المعتمد."
    )

# --- مسار الـ Webhook الخاص بـ Flask ---
@app.route('/telegram/webhook', methods=['POST'])
def webhook():
    if request.method == "POST":
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, telegram_app.bot)
        telegram_app.update_queue.put_nowil(update) if hasattr(telegram_app, 'update_queue') else asyncio.run_coroutine_threadsafe(
            telegram_app.process_update(update), telegram_loop
        )
        return "OK", 200
    return "Forbidden", 403

@app.route('/')
def index():
    return "Bot is running successfully!", 200

# دالة تهيئة وبدء تشغيل البوت
telegram_app = None
telegram_loop = None

def main():
    global telegram_app, telegram_loop
    telegram_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telegram_loop)

    telegram_app = Application.builder().token(BOT_TOKEN).build()

    # تسجيل الهاندلرز الأساسية والإدارية
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("admin", admin_panel))
    
    # معالجات الأزرار الإدارية
    telegram_app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_back$"))
    telegram_app.add_handler(CallbackQueryHandler(show_active_sessions, pattern="^admin_sessions$"))
    telegram_app.add_handler(CallbackQueryHandler(handle_session_action, pattern="^session_(toggle|kick)_"))
    telegram_app.add_handler(CallbackQueryHandler(show_circulars_menu, pattern="^admin_circulars$"))

    # إعداد الـ Webhook أو التشغيل المحلي
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/telegram/webhook"
        telegram_loop.run_until_complete(telegram_app.bot.set_webhook(url=webhook_url))
        logger.info(f"Webhook set to: {webhook_url}")
    
    # تشغيل بوت تيليجرام في الخلفية
    telegram_app.run_polling()

if __name__ == '__main__':
    # تشغيل سيرفر Flask بالتزامن مع البوت إذا لزم الأمر، أو الاعتماد على WSGI (مثل Gunicorn)
    app.run(host="0.0.0.0", port=PORT)
