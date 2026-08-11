import os
import logging
import asyncio
from datetime import datetime
import psycopg2
from flask import Flask, request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# إعداد السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# المتغيرات البيئية
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "postgres://user:password@localhost:5432/dbname")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

# إعداد تطبيق Flask
app = Flask(__name__)

# --- إدارة قاعدة البيانات ---
def db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """إنشاء الجداول الأساسية في PostgreSQL إذا لم تكن موجودة"""
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

# تشغيل إنشاء الجداول عند بدء التشغيل
init_db()

# --- لوحة تحكم المدير ---
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
        if update.message:
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

# --- إدارة الفنادق والحسابات من قِبل المدير ---
async def admin_hotels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("➕ إضافة فندق وحساب جديد", callback_data="admin_add_hotel_prompt")],
        [InlineKeyboardButton("📋 عرض الفنادق المسجلة", callback_data="admin_list_hotels")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")]
    ]
    
    await query.edit_message_text(
        "🏨 **إدارة الفنادق والحسابات**\nاختر الإجراء المطلوب:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def admin_add_hotel_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['waiting_for_hotel_data'] = True
    
    keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="admin_hotels")]]
    await query.edit_message_text(
        "➕ **إضافة فندق وحساب جديد**\n\n"
        "الرجاء إرسال البيانات في رسالة واحدة بالتنسيق التالي:\n"
        "`اسم الفندق | الموقع | اسم المستخدم | كلمة المرور`\n\n"
        "مثال:\n"
        "`فندق الماسة | وسط المدينة | masa_hotel | 123456`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def admin_list_hotels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    with db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT h.name, h.location, a.username, a.is_active 
                FROM hotels h 
                LEFT JOIN hotel_accounts a ON h.id = a.hotel_id;
            """)
            rows = cursor.fetchall()
            
    if not rows:
        text = "🏨 **قائمة الفنادق:**\n\nلا توجد فنادق مسجلة حالياً."
    else:
        text = "🏨 **قائمة الفنادق والحسابات المسجلة:**\n\n"
        for r in rows:
            name, location, username, is_active = r
            status = "🟢 فعال" if is_active else "🔴 معطل"
            uname = username if username else "بدون حساب"
            loc = location if location else "بدون موقع"
            text += f"🏨 **{name}** ({loc})\n   👤 المستخدم: `{uname}` | الحالة: {status}\n\n"
            
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_hotels")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
        
    if context.user_data.get('waiting_for_hotel_data'):
        text = update.message.text.strip()
        parts = [p.strip() for p in text.split('|')]
        
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ التنسيق غير صحيح. يرجى إرسال البيانات بهذا الشكل تماماً:\n"
                "`اسم الفندق | الموقع | اسم المستخدم | كلمة المرور`",
                parse_mode="Markdown"
            )
            return
            
        hotel_name, location, username, password = parts
        
        try:
            with db() as conn:
                with conn.cursor() as cursor:
                    # إضافة الفندق
                    cursor.execute(
                        "INSERT INTO hotels (name, location) VALUES (%s, %s) RETURNING id;",
                        (hotel_name, location)
                    )
                    hotel_id = cursor.fetchone()[0]
                    
                    # إضافة الحساب المرتبط بالفندق
                    cursor.execute(
                        "INSERT INTO hotel_accounts (hotel_id, username, password_hash) VALUES (%s, %s, %s);",
                        (hotel_id, username, password)
                    )
                    conn.commit()
                    
            context.user_data['waiting_for_hotel_data'] = False
            await update.message.reply_text(
                f"✅ **تم إضافة الفندق والحساب بنجاح!**\n\n"
                f"🏨 الفندق: {hotel_name}\n"
                f"📍 الموقع: {location}\n"
                f"👤 اسم المستخدم: `{username}`",
                reply_markup=get_admin_main_menu(),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Error adding hotel: {e}")
            await update.message.reply_text(
                "❌ حدث خطأ أثناء الحفظ (ربما اسم المستخدم مستخدم مسبقاً أو هناك مشكلة في قاعدة البيانات)."
            )

# --- معالج قسم الجلسات النشطة ---
async def show_active_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
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

# --- معالج قسم الصادر والتعاميم ---
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك في نظام إدارة معلومات الفنادق.\nيرجى تسجيل الدخول باستخدام حسابك المعتمد."
    )

# --- تهيئة تطبيق تيليجرام والهاندلرز ---
telegram_app = Application.builder().token(BOT_TOKEN).build()

# تسجيل الهاندلرز
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_panel))
telegram_app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_back$"))
telegram_app.add_handler(CallbackQueryHandler(admin_hotels_menu, pattern="^admin_hotels$"))
telegram_app.add_handler(CallbackQueryHandler(admin_add_hotel_prompt, pattern="^admin_add_hotel_prompt$"))
telegram_app.add_handler(CallbackQueryHandler(admin_list_hotels, pattern="^admin_list_hotels$"))
telegram_app.add_handler(CallbackQueryHandler(show_active_sessions, pattern="^admin_sessions$"))
telegram_app.add_handler(CallbackQueryHandler(handle_session_action, pattern="^session_(toggle|kick)_"))
telegram_app.add_handler(CallbackQueryHandler(show_circulars_menu, pattern="^admin_circulars$"))

# استقبال مدخلات النصوص من المدير (مثل إضافة الفنادق)
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text_input))

# تعيين الـ Webhook تلقائياً إذا وُجد الرابط الخارجي
if RENDER_EXTERNAL_URL:
    webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/telegram/webhook"
    async def setup_wh():
        await telegram_app.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")
    asyncio.run(setup_wh())

# --- مسار استقبال التحديثات من تيليجرام (Flask Webhook) ---
@app.route('/telegram/webhook', methods=['POST'])
def webhook():
    if request.method == "POST":
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, telegram_app.bot)
        
        async def process():
            if not telegram_app.running:
                await telegram_app.initialize()
            await telegram_app.process_update(update)
            
        asyncio.run(process())
        return "OK", 200
    return "Forbidden", 403

@app.route('/')
def index():
    return "Bot is running successfully with Webhook!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
